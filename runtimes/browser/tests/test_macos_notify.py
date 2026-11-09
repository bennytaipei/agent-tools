"""Request handling for the macOS handoff listener. osascript and open are stubbed."""

from __future__ import annotations

import importlib.util
import json
import plistlib
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
    def __init__(self, codes: list[int] | None = None, stderrs: list[bytes] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.codes = list(codes or [])
        self.stderrs = list(stderrs or [])

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(list(args))
        code = self.codes.pop(0) if self.codes else 0
        stderr = self.stderrs.pop(0) if self.stderrs else b""
        return subprocess.CompletedProcess(args, code, b"", stderr)


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
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.apple.safari")
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_DIALOG", raising=False)
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
        ["open", "-b", "com.apple.safari", url],
        ["open", "-b", "com.apple.safari"],
    ]
    assert calls.commands[0][0] == "osascript"
    assert reason in calls.commands[0]
    assert "Browser handoff" in calls.commands[0][2]


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


def _plist(path: Path, handlers: list[dict]) -> Path:
    with path.open("wb") as handle:
        plistlib.dump({"LSHandlers": handlers}, handle)
    return path


def test_default_http_browser_from_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_BROWSER", raising=False)
    path = _plist(
        tmp_path / "handlers.plist",
        [
            {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.https.only"},
            {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "com.example.http"},
        ],
    )
    assert macos_notify.default_http_browser(path) == "com.example.http"
    viewer = _plist(
        tmp_path / "viewer.plist",
        [{"LSHandlerURLScheme": "http", "LSHandlerRoleViewer": "com.viewer.browser"}],
    )
    assert macos_notify.default_http_browser(viewer) == "com.viewer.browser"


def test_default_http_browser_falls_back_to_safari(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_BROWSER", raising=False)
    assert macos_notify.default_http_browser(tmp_path / "missing.plist") == "com.apple.safari"
    empty = _plist(tmp_path / "empty.plist", [])
    assert macos_notify.default_http_browser(empty) == "com.apple.safari"


def test_notify_browser_env_overrides_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _plist(
        tmp_path / "handlers.plist",
        [{"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "com.from.plist"}],
    )
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.from.env")
    assert macos_notify.default_http_browser(path) == "com.from.env"


def test_post_opens_default_bundle_and_logs(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.example.browser")
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_DIALOG", raising=False)
    httpd, calls = _serve(monkeypatch)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    reason = "login wall"
    try:
        status = _post(
            port,
            {"reason": reason, "url": url, "expires_at": "2026-10-09T00:00:00+00:00"},
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 204
    assert calls.commands[0][0] == "osascript"
    assert "Browser handoff" in calls.commands[0][2]
    assert reason in calls.commands[0]
    assert calls.commands[1] == ["open", "-b", "com.example.browser", url]
    assert calls.commands[2] == ["open", "-b", "com.example.browser"]
    err = capfd.readouterr().err
    assert url in err
    assert "exit=0" in err
    assert "com.example.browser" in err


def test_activation_failure_logs_stderr_without_a_dialog(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.example.browser")
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_DIALOG", raising=False)
    calls = _Calls(codes=[0, 0, 1], stderrs=[b"", b"", b"focus denied"])
    monkeypatch.setattr(macos_notify.subprocess, "run", calls.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(
            port,
            {"reason": "login wall", "url": url, "expires_at": "2026-10-09T00:00:00+00:00"},
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 204
    assert calls.commands[1] == ["open", "-b", "com.example.browser", url]
    assert calls.commands[2] == ["open", "-b", "com.example.browser"]
    assert all("display dialog" not in " ".join(cmd) for cmd in calls.commands)
    err = capfd.readouterr().err
    assert "exit=1" in err
    assert "focus denied" in err


def test_dialog_only_when_enabled_and_activation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.example.browser")
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_DIALOG", "1")
    calls = _Calls(codes=[0, 0, 1, 0])
    monkeypatch.setattr(macos_notify.subprocess, "run", calls.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        status = _post(
            port,
            {
                "reason": "login wall",
                "url": "http://127.0.0.1:8765/h/token",
                "expires_at": "2026-10-09T00:00:00+00:00",
            },
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 204
    dialogs = [cmd for cmd in calls.commands if any("display dialog" in part for part in cmd)]
    assert len(dialogs) == 1
    assert "Open" in dialogs[0][2]
