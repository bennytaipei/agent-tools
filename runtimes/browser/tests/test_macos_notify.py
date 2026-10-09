"""Request handling for the macOS handoff listener. osascript and open are stubbed."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "contrib" / "macos_notify.py"
_SPEC = importlib.util.spec_from_file_location("macos_notify", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
macos_notify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(macos_notify)


class _Calls:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(list(args))
        return subprocess.CompletedProcess(args, 0)


def _serve(monkeypatch: pytest.MonkeyPatch) -> tuple[object, _Calls]:
    calls = _Calls()
    monkeypatch.setattr(macos_notify.subprocess, "run", calls.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, calls


def _post(port: int, payload: object, raw: bytes | None = None) -> int:
    if raw is None:
        raw = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.read() == b""
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_post_notifies_and_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    httpd, calls = _serve(monkeypatch)
    port = httpd.server_address[1]
    assert httpd.server_address[0] == "127.0.0.1"
    reason = 'login wall "now"'
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(
            port,
            {"reason": reason, "url": url, "expires_at": "2026-10-09T00:00:00+00:00"},
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 204
    assert calls.commands == [
        macos_notify.notification_command(reason),
        macos_notify.open_command(url),
    ]
    assert calls.commands[0][0] == "osascript"
    assert reason in calls.commands[0]
    assert "Browser handoff" in calls.commands[0][2]
    assert calls.commands[1] == ["open", url]


def test_post_rejects_bad_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    httpd, calls = _serve(monkeypatch)
    port = httpd.server_address[1]
    try:
        missing = _post(port, {"reason": "login wall", "url": "http://127.0.0.1/h/t"})
        bad_url = _post(
            port,
            {
                "reason": "login wall",
                "url": "file:///etc/passwd",
                "expires_at": "2026-10-09T00:00:00+00:00",
            },
        )
        invalid = _post(port, None, raw=b"not-json")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")
        try:
            urllib.request.urlopen(request, timeout=5)
            get_status = 200
        except urllib.error.HTTPError as exc:
            get_status = exc.code
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert missing == 400
    assert bad_url == 400
    assert invalid == 400
    assert get_status == 405
    assert calls.commands == []


def test_resolve_port_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(macos_notify.PORT_ENV, "8791")
    assert macos_notify.resolve_port([]) == 8791
    monkeypatch.delenv(macos_notify.PORT_ENV, raising=False)
    assert macos_notify.resolve_port([]) == macos_notify.DEFAULT_PORT
    assert macos_notify.resolve_port(["--port", "8792"]) == 8792
