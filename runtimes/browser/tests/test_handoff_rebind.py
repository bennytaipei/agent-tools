"""Fixed handoff ports can be bound again after every end, including a stop still in progress."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import urllib.request
from pathlib import Path
from queue import Queue

from agent_tools_browser.handoff import HANDOFF_PORT_ENV, NOVNC_PORT_ENV, HandoffShare, ShareError
from agent_tools_browser.processes import port_is_open, reap, stop_tracked
from agent_tools_browser.session import BrowserSession, _start_display

from test_runtime import _free_ports, _start_webhook


def _hold(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def _post(url: str, action: str) -> None:
    request = urllib.request.Request(url + action, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204


def test_fixed_ports_survive_done_cancel_timeout_and_overlap(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    markers = [str(state.resolve())]
    display, auth, xvfb = _start_display(state, markers)
    page_port, novnc_port = _free_ports(2)
    previous = {
        HANDOFF_PORT_ENV: os.environ.get(HANDOFF_PORT_ENV),
        NOVNC_PORT_ENV: os.environ.get(NOVNC_PORT_ENV),
    }
    os.environ[HANDOFF_PORT_ENV] = str(page_port)
    os.environ[NOVNC_PORT_ENV] = str(novnc_port)
    try:
        for action, status in (("/done", "done"), ("/cancel", "cancelled")):
            share = HandoffShare(display, auth, state, markers)
            share.start("login wall")
            assert port_is_open(page_port)
            assert port_is_open(novnc_port)
            _post(share.url, action)
            assert share.user_result() == status
            share.stop()
            assert not port_is_open(page_port)
            assert not port_is_open(novnc_port)

        share = HandoffShare(display, auth, state, markers)
        share.start("login wall")
        share.stop()
        assert not port_is_open(page_port)
        assert not port_is_open(novnc_port)

        share = HandoffShare(display, auth, state, markers)
        share.start("overlap")

        def _stop_soon() -> None:
            time.sleep(0.05)
            share.stop()

        stopper = threading.Thread(target=_stop_soon)
        stopper.start()
        time.sleep(0.01)
        other = HandoffShare(display, auth, state, markers)
        other.start("during stop")
        assert port_is_open(page_port)
        assert port_is_open(novnc_port)
        other.stop()
        stopper.join(timeout=10)
        assert not port_is_open(page_port)
        assert not port_is_open(novnc_port)

        held = _hold(page_port)
        try:
            failed = HandoffShare(display, auth, state, markers)
            try:
                failed.start("blocked")
            except ShareError as exc:
                text = str(exc)
            else:
                failed.stop()
                raise AssertionError("start succeeded while the page port was held")
            assert str(page_port) in text
            assert "EADDRINUSE" in text or "Address already in use" in text
            assert not port_is_open(novnc_port)
        finally:
            held.close()

        again = HandoffShare(display, auth, state, markers)
        again.start("after held port")
        again.stop()
        assert not port_is_open(page_port)
        assert not port_is_open(novnc_port)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        stop_tracked(xvfb, markers)
        reap(markers)


def test_cancel_mid_handoff_then_bind_again(tmp_path: Path) -> None:
    asyncio.run(_cancel_mid_handoff(tmp_path))


async def _cancel_mid_handoff(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    state = profile.parent / f"{profile.name}.runtime"
    markers = [str(profile.resolve()), str(state.resolve())]
    page_port, novnc_port = _free_ports(2)
    received: Queue[dict] = Queue()
    webhook = _start_webhook(received)
    hook_port = webhook.server_address[1]
    keys = {
        "AGENT_TOOLS_BROWSER_PROFILE": str(profile),
        "AGENT_TOOLS_BROWSER_CHANNEL": "chromium",
        "AGENT_TOOLS_NOTIFY_WEBHOOK": f"http://127.0.0.1:{hook_port}/hook",
        HANDOFF_PORT_ENV: str(page_port),
        NOVNC_PORT_ENV: str(novnc_port),
    }
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(keys)
    session = BrowserSession()
    try:
        task = asyncio.create_task(session.handoff("login wall", 30))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not port_is_open(page_port):
            if task.done():
                raise AssertionError(task.exception() or await task)
            await asyncio.sleep(0.05)
        assert port_is_open(page_port)
        assert port_is_open(novnc_port)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not port_is_open(page_port)
        assert not port_is_open(novnc_port)
        await asyncio.sleep(0.2)
        while not received.empty():
            received.get_nowait()

        again = asyncio.create_task(session.handoff("login wall", 30))
        payload = await asyncio.to_thread(received.get, True, 30)
        assert payload["url"].startswith("http://")
        request = urllib.request.Request(payload["url"] + "/done", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 204
        result = await again
        assert result == {"status": "done", "completed_by": "user"}
        assert not port_is_open(page_port)
        assert not port_is_open(novnc_port)
    finally:
        await session.aclose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        webhook.shutdown()
        webhook.server_close()
        reap(markers)
