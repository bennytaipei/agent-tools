"""Behavior tests for the browser MCP runtime.

They drive the real server over stdio against a local site and a local webhook.
No internet.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlsplit

import pytest
import yaml
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from agent_tools_browser.processes import cmdline, pids_matching, port_is_open, reap
from agent_tools_browser.validate import validator_for

REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / "catalog" / "browser"
MARKER = "AGENT_TOOLS_MARKER"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
TOOL_NAMES = (
    "browser.navigate",
    "browser.read",
    "browser.screenshot",
    "browser.handoff",
)


def _manifests() -> dict[str, dict]:
    found = {}
    for path in sorted(CATALOG.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        found[data["name"]] = data
    return found


MANIFESTS = _manifests()


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return


def _start_site() -> ThreadingHTTPServer:
    class Handler(_QuietHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            port = self.server.server_address[1]
            if path == "/go":
                location = f"http://127.0.0.1:{port}/landed"
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/landed":
                body = (
                    "<!DOCTYPE html><html><body><p>" + MARKER + "</p></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

    return _serve(Handler)


def _start_webhook(received: Queue[dict]) -> ThreadingHTTPServer:
    class Handler(_QuietHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            payload = json.loads(self.rfile.read(length).decode())
            received.put(payload)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return _serve(Handler)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return port


def _error_code(result) -> str:
    assert result.is_error is True
    body = json.loads(result.content[0].text)
    assert list(body) == ["code"]
    return body["code"]


def _payload(result) -> dict:
    assert result.is_error is False
    body = json.loads(result.content[0].text)
    assert body == result.structured_content
    return body


def _assert_output(name: str, payload: dict) -> None:
    validator_for(MANIFESTS[name]["outputs"]).validate(payload)


def _assert_declared(name: str, code: str) -> None:
    codes = [item["code"] for item in MANIFESTS[name]["errors"]]
    assert code in codes


async def _webhook_or_result(task: asyncio.Task, received: Queue[dict]) -> dict:
    getter = asyncio.create_task(asyncio.to_thread(received.get, True, 30))
    done, _pending = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
    if getter not in done:
        getter.cancel()
        result = await task
        raise AssertionError(f"handoff finished before the webhook: {result}")
    return getter.result()


def _websocket_status(port: int, path: str) -> int:
    """Return the HTTP status from a WebSocket upgrade, or 0 if the socket dies first."""

    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        key = base64.b64encode(b"0123456789abcdef").decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode())
        try:
            data = sock.recv(128)
        except OSError:
            return 0
    if not data.startswith(b"HTTP/"):
        return 0
    return int(data.split(b" ", 2)[1])


def _assert_closed(port: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not port_is_open(port):
            return
        time.sleep(0.05)
    raise AssertionError(f"port {port} is still open")


def _runtime_env(profile: Path, webhook: str | None) -> dict[str, str]:
    env = {
        "AGENT_TOOLS_BROWSER_PROFILE": str(profile),
        "AGENT_TOOLS_ROOT": str(REPO),
    }
    if webhook is not None:
        env["AGENT_TOOLS_NOTIFY_WEBHOOK"] = webhook
    return env


def _client(profile: Path, webhook: str | None) -> Client:
    import sys

    return Client(
        StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_tools_browser"],
            env=_runtime_env(profile, webhook),
            cwd=str(REPO),
        ),
        read_timeout_seconds=120,
    )


async def _call(client: Client, name: str, arguments: dict | None = None):
    return await client.call_tool(name, arguments or {})


def test_browser_runtime(tmp_path: Path) -> None:
    asyncio.run(_browser_runtime(tmp_path))


def test_notify_failed_when_webhook_unset(tmp_path: Path) -> None:
    asyncio.run(_notify_failed(tmp_path))


def test_notify_failed_when_post_fails(tmp_path: Path) -> None:
    asyncio.run(_notify_post_fails(tmp_path))


async def _browser_runtime(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    state = profile.parent / f"{profile.name}.runtime"
    markers = [str(profile.resolve()), str(state.resolve())]
    received: Queue[dict] = Queue()
    site = _start_site()
    webhook = _start_webhook(received)
    site_port = site.server_address[1]
    hook_port = webhook.server_address[1]
    landed = f"http://127.0.0.1:{site_port}/landed"
    try:
        async with _client(profile, f"http://127.0.0.1:{hook_port}/hook") as client:
            await _assert_tools(client)
            await _assert_no_page(client)
            await _assert_invalid_url(client)
            await _assert_navigation_failed(client)
            await _assert_navigate_read_screenshot(client, site_port, landed)
            await _assert_handoff(client, received, "login wall", "done", "/done", 30)
            await _assert_handoff(client, received, "login wall", "cancelled", "/cancel", 30)
            await _assert_handoff(client, received, "login wall", "timeout", None, 1)
    finally:
        site.shutdown()
        webhook.shutdown()
        site.server_close()
        webhook.server_close()
        reap(markers)


async def _notify_failed(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    state = profile.parent / f"{profile.name}.runtime"
    markers = [str(profile.resolve()), str(state.resolve())]
    try:
        async with _client(profile, None) as client:
            result = await _call(
                client,
                "browser.handoff",
                {"reason": "login wall", "timeout": 30},
            )
            code = _error_code(result)
            assert code == "notify_failed"
            _assert_declared("browser.handoff", code)
            assert pids_matching(markers) == []
    finally:
        reap(markers)


async def _notify_post_fails(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    state = profile.parent / f"{profile.name}.runtime"
    markers = [str(profile.resolve()), str(state.resolve())]
    closed = _closed_port()
    try:
        async with _client(profile, f"http://127.0.0.1:{closed}/hook") as client:
            result = await _call(
                client,
                "browser.handoff",
                {"reason": "login wall", "timeout": 30},
            )
            code = _error_code(result)
            assert code == "notify_failed"
            _assert_declared("browser.handoff", code)
            commands = [cmdline(pid) for pid in pids_matching(markers)]
            assert commands
            assert all("websockify" not in command and "x11vnc" not in command for command in commands)
    finally:
        reap(markers)


async def _assert_tools(client: Client) -> None:
    listed = await client.list_tools()
    names = [tool.name for tool in listed.tools]
    assert names == list(TOOL_NAMES)
    for tool in listed.tools:
        manifest = MANIFESTS[tool.name]
        assert tool.description == manifest["when_to_use"]
        assert tool.input_schema == manifest["inputs"]
        assert tool.output_schema == manifest["outputs"]


async def _assert_no_page(client: Client) -> None:
    for name in ("browser.read", "browser.screenshot"):
        result = await _call(client, name, {})
        code = _error_code(result)
        assert code == "no_page"
        _assert_declared(name, code)


async def _assert_invalid_url(client: Client) -> None:
    result = await _call(client, "browser.navigate", {"url": "not-a-url"})
    code = _error_code(result)
    assert code == "invalid_url"
    _assert_declared("browser.navigate", code)


async def _assert_navigation_failed(client: Client) -> None:
    port = _closed_port()
    result = await _call(client, "browser.navigate", {"url": f"http://127.0.0.1:{port}/missing"})
    code = _error_code(result)
    assert code == "navigation_failed"
    _assert_declared("browser.navigate", code)


async def _assert_navigate_read_screenshot(client: Client, site_port: int, landed: str) -> None:
    navigated = _payload(await _call(client, "browser.navigate", {"url": f"http://127.0.0.1:{site_port}/go"}))
    assert navigated == {"url": landed}
    _assert_output("browser.navigate", navigated)

    read = _payload(await _call(client, "browser.read", {}))
    assert read["url"] == landed
    assert MARKER in read["text"]
    _assert_output("browser.read", read)

    shot = _payload(await _call(client, "browser.screenshot", {}))
    raw = base64.b64decode(shot["image"], validate=True)
    assert raw.startswith(PNG_MAGIC)
    _assert_output("browser.screenshot", shot)


async def _assert_handoff(
    client: Client,
    received: Queue[dict],
    reason: str,
    status: str,
    action: str | None,
    timeout: int,
) -> None:
    task = asyncio.create_task(
        _call(client, "browser.handoff", {"reason": reason, "timeout": timeout})
    )
    payload = await _webhook_or_result(task, received)
    assert list(payload) == ["reason", "url", "expires_at"]
    assert payload["reason"] == reason
    assert payload["url"].startswith("http://127.0.0.1:")
    assert "/h/" in payload["url"]
    parsed_expiry = payload["expires_at"]
    assert "T" in parsed_expiry and parsed_expiry.endswith("+00:00")

    page = urllib.request.urlopen(payload["url"], timeout=5).read().decode()
    assert ">Done<" in page
    assert ">Cancel<" in page
    novnc_port = int(page.split('data-novnc-port="', 1)[1].split('"', 1)[0])
    assert port_is_open(novnc_port)
    novnc = urllib.request.urlopen(f"http://127.0.0.1:{novnc_port}/vnc.html", timeout=5)
    assert b"noVNC" in novnc.read()
    if action == "/done":
        token = page.split("token%3D", 1)[1].split('"', 1)[0]
        assert _websocket_status(novnc_port, f"/websockify?token={token}") == 101
        assert _websocket_status(novnc_port, "/websockify") != 101
    page_port = urlsplit(payload["url"]).port
    assert page_port is not None
    assert port_is_open(page_port)

    if action is not None:
        request = urllib.request.Request(payload["url"] + action, data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 204

    result = _payload(await task)
    assert result == {"status": status}
    _assert_output("browser.handoff", result)
    _assert_closed(novnc_port)
    _assert_closed(page_port)
    with pytest.raises(Empty):
        received.get_nowait()
