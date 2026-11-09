"""Request handling for the macOS handoff listener. osascript and open are stubbed."""

from __future__ import annotations

import importlib.util
import json
import plistlib
import re
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "contrib" / "macos_notify.py"
_SPEC = importlib.util.spec_from_file_location("macos_notify", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
macos_notify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(macos_notify)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _fast_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(macos_notify.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(macos_notify.time, "sleep", clock.sleep)
    return clock


class _Calls:
    def __init__(self, codes: list[int] | None = None, stderrs: list[bytes] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.codes = list(codes or [])
        self.stderrs = list(stderrs or [])

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(list(args))
        if args and args[0] == "ioreg":
            body = plistlib.dumps([{"CGSSessionScreenIsLocked": False}])
            return subprocess.CompletedProcess(args, 0, body, b"")
        if args and args[0] == "lsappinfo":
            return subprocess.CompletedProcess(args, 0, b"Terminal\n", b"")
        code = self.codes.pop(0) if self.codes else 0
        stderr = self.stderrs.pop(0) if self.stderrs else b""
        return subprocess.CompletedProcess(args, code, b"", stderr)


def _serve(monkeypatch: pytest.MonkeyPatch) -> tuple[object, _Calls]:
    monkeypatch.delenv(macos_notify.HANDOFF_BROWSER_ENV, raising=False)
    calls = _Calls()
    monkeypatch.setattr(macos_notify.subprocess, "run", calls.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, calls


def _post(
    port: int,
    payload: object,
    raw: bytes | None = None,
    *,
    wait: bool = True,
    timeout: float = 5,
) -> int:
    if raw is None:
        raw = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            assert response.read() == b""
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if wait:
        assert macos_notify.wait_for_opens()
    return status


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
        ["ioreg", "-n", "Root", "-d1", "-a"],
        ["lsappinfo", "front"],
        ["lsappinfo", "info"],
        ["open", "-b", "com.apple.safari", url],
        ["open", "-b", "com.apple.safari"],
    ]
    assert all("System Events" not in " ".join(cmd) for cmd in calls.commands)
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
    opens = [cmd for cmd in calls.commands if cmd and cmd[0] == "open"]
    assert opens[0] == ["open", "-b", "com.example.browser", url]
    assert opens[1] == ["open", "-b", "com.example.browser"]
    err = capfd.readouterr().err
    assert url in err
    assert "exit=0" in err
    assert "com.example.browser" in err


def test_activation_failure_logs_stderr_without_a_dialog(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.example.browser")
    monkeypatch.delenv(macos_notify.HANDOFF_BROWSER_ENV, raising=False)
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
    opens = [cmd for cmd in calls.commands if cmd and cmd[0] == "open"]
    assert opens[0] == ["open", "-b", "com.example.browser", url]
    assert opens[1] == ["open", "-b", "com.example.browser"]
    assert all("display dialog" not in " ".join(cmd) for cmd in calls.commands)
    err = capfd.readouterr().err
    assert "exit=1" in err
    assert "focus denied" in err


def test_dialog_only_when_enabled_and_activation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.example.browser")
    monkeypatch.delenv(macos_notify.HANDOFF_BROWSER_ENV, raising=False)
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


def _payload(url: str = "http://127.0.0.1:8765/h/token") -> dict[str, str]:
    return {
        "reason": "login wall",
        "url": url,
        "expires_at": "2026-10-09T00:00:00+00:00",
    }


def _url_opens(commands: list[list[str]], url: str) -> list[list[str]]:
    return [cmd for cmd in commands if cmd and cmd[0] == "open" and url in cmd]


class _Plan:
    """Scripted ioreg, lsappinfo, and open results."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.frontmost = b"Terminal\n"
        self.lock_states: list[bool] = []
        self._lock_at = 0
        self.url_open_codes: list[int] = []

    def _lock_body(self) -> bytes:
        if not self.lock_states:
            locked = False
        else:
            index = min(self._lock_at, len(self.lock_states) - 1)
            self._lock_at += 1
            locked = self.lock_states[index]
        return plistlib.dumps([{"CGSSessionScreenIsLocked": locked}])

    def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(list(args))
        if args and args[0] == "ioreg":
            return subprocess.CompletedProcess(args, 0, self._lock_body(), b"")
        if args and args[0] == "lsappinfo":
            return subprocess.CompletedProcess(args, 0, self.frontmost, b"")
        if args and args[0] == "open" and len(args) >= 4:
            code = self.url_open_codes.pop(0) if self.url_open_codes else 0
            return subprocess.CompletedProcess(args, code, b"", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")


def _serve_plan(
    monkeypatch: pytest.MonkeyPatch, plan: _Plan
) -> object:
    monkeypatch.delenv(macos_notify.HANDOFF_BROWSER_ENV, raising=False)
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_DIALOG", raising=False)
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.apple.safari")
    monkeypatch.setattr(macos_notify.subprocess, "run", plan.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _stop(httpd: object) -> None:
    assert macos_notify.wait_for_opens()
    httpd.shutdown()
    httpd.server_close()


def test_successful_open_does_not_open_another_browser(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    plan = _Plan()
    plan.frontmost = b'ASN:0x0-0x1234: "Google Chrome"\n'
    httpd = _serve_plan(monkeypatch, plan)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(port, _payload(url))
    finally:
        _stop(httpd)
    assert status == 204
    assert _url_opens(plan.commands, url) == [["open", "-a", "Google Chrome", url]]
    assert ["open", "-b", "com.apple.safari", url] not in plan.commands
    assert ["open", "-a", "Firefox", url] not in plan.commands
    assert all("System Events" not in " ".join(cmd) for cmd in plan.commands)
    err = capfd.readouterr().err
    assert "open fallback" not in err
    assert "-> fallback" not in err


def test_open_failure_falls_back_once(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    plan = _Plan()
    plan.frontmost = b'ASN:0x0-0x1234: "Google Chrome"\n'
    plan.url_open_codes = [1, 0]
    httpd = _serve_plan(monkeypatch, plan)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(port, _payload(url))
    finally:
        _stop(httpd)
    assert status == 204
    assert _url_opens(plan.commands, url) == [
        ["open", "-a", "Google Chrome", url],
        ["open", "-b", "com.apple.safari", url],
    ]
    assert ["open", "-a", "Google Chrome"] not in plan.commands
    assert ["open", "-a", "Firefox", url] not in plan.commands
    assert ["open", "-a", "Arc", url] not in plan.commands
    err = capfd.readouterr().err
    assert "open failed app=Google Chrome exit=1" in err
    assert "open fallback app=Safari" in err


def test_handoff_browser_env_is_tried_first(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    plan = _Plan()
    plan.frontmost = b'ASN:0x0-0x1234: "Google Chrome"\n'
    httpd = _serve_plan(monkeypatch, plan)
    monkeypatch.setenv(macos_notify.HANDOFF_BROWSER_ENV, "Firefox")
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(port, _payload(url))
    finally:
        _stop(httpd)
    assert status == 204
    assert _url_opens(plan.commands, url) == [["open", "-a", "Firefox", url]]
    assert ["open", "-a", "Google Chrome", url] not in plan.commands
    assert ["open", "-b", "com.apple.safari", url] not in plan.commands
    assert not any(cmd and cmd[0] == "lsappinfo" for cmd in plan.commands)
    err = capfd.readouterr().err
    assert "com.apple.safari" not in err


def test_failed_open_without_a_next_candidate_stays_one_browser(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    plan = _Plan()
    plan.frontmost = b"Terminal\n"
    plan.url_open_codes = [1]
    httpd = _serve_plan(monkeypatch, plan)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    try:
        status = _post(port, _payload(url))
    finally:
        _stop(httpd)
    assert status == 204
    assert _url_opens(plan.commands, url) == [["open", "-b", "com.apple.safari", url]]
    assert ["open", "-a", "Google Chrome", url] not in plan.commands
    err = capfd.readouterr().err
    assert "open failed app=Safari exit=1" in err
    assert "open fallback" not in err


def test_post_returns_204_before_open_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    started = threading.Event()

    class _BlockOpen(_Calls):
        def run(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if args and args[0] == "open":
                started.set()
                assert release.wait(5)
            return super().run(args, **kwargs)

    calls = _BlockOpen()
    monkeypatch.delenv(macos_notify.HANDOFF_BROWSER_ENV, raising=False)
    monkeypatch.delenv("AGENT_TOOLS_NOTIFY_DIALOG", raising=False)
    monkeypatch.setenv("AGENT_TOOLS_NOTIFY_BROWSER", "com.apple.safari")
    monkeypatch.setattr(macos_notify.subprocess, "run", calls.run)
    httpd = macos_notify.make_server(0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        status = _post(port, _payload(), wait=False, timeout=2)
        assert status == 204
        assert started.wait(2)
        assert not release.is_set()
    finally:
        release.set()
        _stop(httpd)


_DENIED_SET_WORDS = {
    "kind",
    "name",
    "class",
    "id",
    "text",
    "file",
    "item",
    "window",
    "process",
    "result",
    "version",
    "path",
    "size",
    "date",
    "folder",
}
_SET_TARGET = re.compile(r"\bset\s+([A-Za-z_][A-Za-z0-9_]*)\s+to\b")


def _future_expiry(seconds: float) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return moment.isoformat()


def test_applescript_set_targets_avoid_reserved_words() -> None:
    scripts = (
        macos_notify._NOTIFY_SCRIPT,
        macos_notify._DIALOG_SCRIPT,
    )
    for script in scripts:
        assert "System Events" not in script
        for match in _SET_TARGET.finditer(script):
            word = match.group(1)
            assert word not in _DENIED_SET_WORDS, match.group(0)


def test_lsappinfo_text_names_a_browser_without_system_events() -> None:
    quoted = 'ASN:0x0-0x1234: "Google Chrome"'
    assert macos_notify._known_browser_in(quoted) == "Google Chrome"
    assert macos_notify._known_browser_in("Google Chrome\n") == "Google Chrome"
    assert macos_notify._known_browser_in("com.apple.safari") is None
    assert macos_notify._known_browser_in("loginwindow") is None
    assert macos_notify._known_browser_in('ASN:0x0-0x1: "Arc"') == "Arc"


def test_locked_then_unlocked_opens_default_once(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = _fast_clock(monkeypatch)
    plan = _Plan()
    plan.lock_states = [True, False]
    httpd = _serve_plan(monkeypatch, plan)
    port = httpd.server_address[1]
    url = "http://127.0.0.1:8765/h/token"
    payload = _payload(url)
    payload["expires_at"] = _future_expiry(3600)
    try:
        status = _post(port, payload)
    finally:
        _stop(httpd)
    assert status == 204
    assert _url_opens(plan.commands, url) == [["open", "-b", "com.apple.safari", url]]
    assert ["open", "-a", "Google Chrome", url] not in plan.commands
    assert all("System Events" not in " ".join(cmd) for cmd in plan.commands)
    err = capfd.readouterr().err
    assert "screen locked; waiting for unlock" in err
    assert clock.now >= 2


def test_locked_until_expiry_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = _fast_clock(monkeypatch)
    plan = _Plan()
    plan.lock_states = [True]
    httpd = _serve_plan(monkeypatch, plan)
    port = httpd.server_address[1]
    payload = _payload()
    payload["expires_at"] = _future_expiry(5)
    try:
        status = _post(port, payload)
    finally:
        _stop(httpd)
    assert status == 204
    assert any(cmd and cmd[0] == "osascript" for cmd in plan.commands)
    assert not any(cmd and cmd[0] == "open" for cmd in plan.commands)
    err = capfd.readouterr().err
    assert "screen locked; waiting for unlock" in err
    assert "screen locked; gave up" in err
    assert clock.now >= 2
