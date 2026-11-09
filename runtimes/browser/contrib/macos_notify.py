#!/usr/bin/env python3
"""macOS listener for browser handoff notifications.

Binds 127.0.0.1. A POST of JSON {reason, url, expires_at} shows a notification
and returns 204, then opens url and checks that the browser is actually in
front with a window. Stdlib only. The LaunchAgent sends stderr to
/tmp/agent-tools-browser-notify.log.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PORT_ENV = "AGENT_TOOLS_MACOS_NOTIFY_PORT"
BROWSER_ENV = "AGENT_TOOLS_NOTIFY_BROWSER"
HANDOFF_BROWSER_ENV = "AGENT_TOOLS_HANDOFF_BROWSER"
DIALOG_ENV = "AGENT_TOOLS_NOTIFY_DIALOG"
DEFAULT_PORT = 8767
SAFARI_BUNDLE = "com.apple.safari"
VERIFY_BUDGET_S = 4.0
VERIFY_POLL_S = 0.25
LOCK_POLL_S = 2.0
_MAX_BODY = 65536
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
# Process names System Events reports. Safari is a frontmost candidate only.
_KNOWN_BROWSERS = (
    "Google Chrome",
    "Arc",
    "Microsoft Edge",
    "Brave Browser",
    "Firefox",
    "Safari",
)
_FALLBACK_APPS = (
    "Google Chrome",
    "Arc",
    "Microsoft Edge",
    "Brave Browser",
    "Firefox",
)
_BUNDLE_APP = {
    "com.apple.safari": "Safari",
    "com.google.chrome": "Google Chrome",
    "company.thebrowser.browser": "Arc",
    "com.microsoft.edgemac": "Microsoft Edge",
    "com.brave.browser": "Brave Browser",
    "org.mozilla.firefox": "Firefox",
}
_NOTIFY_SCRIPT = (
    'on run argv\n'
    'display notification (item 1 of argv) with title "Browser handoff"\n'
    "end run"
)
_DIALOG_SCRIPT = (
    "on run argv\n"
    'display dialog "Browser handoff" buttons {"Open"} default button "Open"\n'
    "end run"
)
# System Events only. App names and bundle ids arrive as argv, not as source.
# Prefixed names: `set kind to` is error -10006 because `kind` is a property.
_VERIFY_SCRIPT = (
    "on run argv\n"
    "set qKind to item 1 of argv\n"
    "set qTarget to item 2 of argv\n"
    "with timeout of 3 seconds\n"
    'tell application "System Events"\n'
    'if qKind is "bundle" then\n'
    "ignoring case\n"
    "set procList to (every application process whose bundle identifier is qTarget)\n"
    "end ignoring\n"
    "else\n"
    "set procList to (every application process whose name is qTarget)\n"
    "end if\n"
    "if (count of procList) is 0 then\n"
    'return "missing"\n'
    "end if\n"
    "set qProc to item 1 of procList\n"
    "set procName to name of qProc\n"
    "set isFront to frontmost of qProc\n"
    "set winCount to count of windows of qProc\n"
    "if isFront then\n"
    'set frontText to "true"\n'
    "else\n"
    'set frontText to "false"\n'
    "end if\n"
    "return procName & \"|\" & frontText & \"|\" & (winCount as text)\n"
    "end tell\n"
    "end timeout\n"
    "end run"
)
_FRONTMOST_SCRIPT = (
    "on run argv\n"
    "with timeout of 3 seconds\n"
    'tell application "System Events"\n'
    "set frontProc to first application process whose frontmost is true\n"
    "return name of frontProc\n"
    "end tell\n"
    "end timeout\n"
    "end run"
)


class _Gate:
    """Lets tests wait until the open/verify work that follows 204 has finished."""

    def __init__(self) -> None:
        self._pending = 0
        self._cv = threading.Condition()

    def enter(self) -> None:
        with self._cv:
            self._pending += 1

    def leave(self) -> None:
        with self._cv:
            self._pending -= 1
            if self._pending == 0:
                self._cv.notify_all()

    def wait(self, timeout: float = 10) -> bool:
        with self._cv:
            if self._pending == 0:
                return True
            return self._cv.wait_for(lambda: self._pending == 0, timeout)


_GATE = _Gate()


class _OpenState:
    def __init__(self) -> None:
        self.dialog = False
        self.timeouts = 0
        self.stop = False


class NotifyRequestError(ValueError):
    pass


def notification_command(reason: str) -> list[str]:
    return [
        "osascript",
        "-e",
        _NOTIFY_SCRIPT,
        reason,
    ]


def open_command(url: str, bundle: str) -> list[str]:
    return ["open", "-b", bundle, url]


def activate_command(bundle: str) -> list[str]:
    return ["open", "-b", bundle]


def open_app_command(url: str, name: str) -> list[str]:
    return ["open", "-a", name, url]


def activate_app_command(name: str) -> list[str]:
    return ["open", "-a", name]


def verify_command(kind: str, target: str) -> list[str]:
    return ["osascript", "-e", _VERIFY_SCRIPT, kind, target]


def frontmost_command() -> list[str]:
    return ["osascript", "-e", _FRONTMOST_SCRIPT]


def dialog_command(url: str) -> list[str]:
    return ["osascript", "-e", _DIALOG_SCRIPT, url]


def wait_for_opens(timeout: float = 10) -> bool:
    """Block until open/verify work started after a 204 has finished."""

    return _GATE.wait(timeout)


def _launch_services_plist() -> Path:
    return (
        Path.home()
        / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    )


def default_http_browser(plist_path: Path | None = None) -> str:
    """Bundle id for the default http handler, or Safari when none is recorded."""

    override = os.environ.get(BROWSER_ENV, "").strip()
    if override:
        return override if _BUNDLE_RE.fullmatch(override) else SAFARI_BUNDLE
    path = _launch_services_plist() if plist_path is None else plist_path
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return SAFARI_BUNDLE
    return _bundle_from_handlers(data)


def show_notification(reason: str) -> None:
    # The reason is an argv item, not interpolated into the AppleScript source.
    _check(_run(notification_command(reason)))


def open_in_browser(url: str, expires_at: str) -> None:
    """Open url and stop at the first browser that is frontmost with a window.

    A locked screen is waited out until expires_at. No browser is opened while
    the screen stays locked. `open` exiting 0 does not mean a window appeared.
    Safari can swallow the URL while Google Chrome stays in front. Activate
    with `open -b` or `open -a` again. `tell application ... to activate` can
    prompt for Automation, which a background LaunchAgent cannot answer. Two
    System Events verify timeouts in a row stop further fallbacks.
    """

    if not _http_url(url):
        raise NotifyRequestError("url must be an absolute http or https URL")
    if not _wait_for_unlock(expires_at):
        return
    tried: set[str] = set()
    state = _OpenState()
    for kind, target in _primary_targets():
        if _attempt(url, kind, target, tried, state, require_installed=False):
            return
        if state.stop:
            return
    front = _frontmost_browser()
    if front is not None:
        if _attempt(url, "app", front, tried, state, require_installed=False):
            return
        if state.stop:
            return
    for name in _FALLBACK_APPS:
        if state.stop:
            return
        if _attempt(url, "app", name, tried, state, require_installed=True):
            return


def _attempt(
    url: str,
    kind: str,
    target: str,
    tried: set[str],
    state: _OpenState,
    require_installed: bool,
) -> bool:
    if state.stop:
        return False
    keys = _identity_keys(kind, target)
    if tried.intersection(keys):
        return False
    label = _label(kind, target)
    if require_installed and not _app_installed(label):
        tried.update(keys)
        _log_line(f"skip app={label} not installed")
        return False
    tried.update(keys)
    if kind == "bundle":
        _run(open_command(url, target))
        focused = _run(activate_command(target))
    else:
        _run(open_app_command(url, target))
        focused = _run(activate_app_command(target))
    if focused.returncode != 0 and _dialog_enabled() and not state.dialog:
        _run(dialog_command(url))
        state.dialog = True
    ok, timed_out = _poll_verified(kind, target, label)
    if timed_out:
        state.timeouts += 1
        if state.timeouts >= 2:
            _log_line(f"verify unavailable; stopping after {label}")
            state.stop = True
    else:
        state.timeouts = 0
    return ok


def handle_payload(payload: object) -> None:
    """Notify, then open. HTTP callers send 204 before calling the open half."""

    reason, url, expires_at = _required_fields(payload)
    _log_line(f"request reason={reason!r} url={url}")
    show_notification(reason)
    open_in_browser(url, expires_at)


def make_server(port: int) -> ThreadingHTTPServer:
    if port < 0 or port > 65535:
        raise NotifyRequestError(f"{PORT_ENV} is not a TCP port")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    httpd.daemon_threads = True
    return httpd


def resolve_port(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Listen for browser handoff notifications.")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.port is not None:
        port = args.port
    else:
        raw = os.environ.get(PORT_ENV, "").strip()
        if not raw:
            port = DEFAULT_PORT
        elif raw.isdigit():
            port = int(raw)
        else:
            raise SystemExit(f"{PORT_ENV} is not a TCP port")
    if port < 1 or port > 65535:
        raise SystemExit(f"{PORT_ENV} is not a TCP port")
    return port


def main(argv: list[str] | None = None) -> None:
    port = resolve_port(argv)
    httpd = make_server(port)
    bound = httpd.server_address
    print(f"listening on {bound[0]}:{bound[1]}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        _log_line(f"request POST path={self.path}")
        try:
            payload = _read_json(self)
            reason, url, expires_at = _required_fields(payload)
            _log_line(f"request reason={reason!r} url={url}")
            show_notification(reason)
        except NotifyRequestError:
            _send(self, 400)
            return
        except (subprocess.CalledProcessError, OSError):
            _send(self, 500)
            return
        # The runtime is waiting on this POST. Verify polls for seconds, so
        # flush 204 before that work. The request thread keeps running it.
        _GATE.enter()
        try:
            _send(self, 204)
            try:
                self.wfile.flush()
            except OSError:
                pass
            try:
                open_in_browser(url, expires_at)
            except Exception as exc:
                _log_line(f"open failed error={exc}")
        finally:
            _GATE.leave()

    def do_GET(self) -> None:  # noqa: N802
        _log_line(f"request GET path={self.path}")
        _send(self, 405)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _log_line(message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        sys.stderr.write(f"{stamp} {message}\n")
        sys.stderr.flush()
    except Exception:
        return


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        err = ""
        if exc.stderr:
            err = exc.stderr.decode("utf-8", "replace").strip()
        _log_line(f"command argv={args!r} exit=timeout stderr={err}")
        raise
    except OSError as exc:
        _log_line(f"command argv={args!r} exit=error stderr={exc}")
        raise
    stderr = ""
    if result.stderr:
        stderr = result.stderr.decode("utf-8", "replace").strip()
    _log_line(f"command argv={args!r} exit={result.returncode} stderr={stderr}")
    return result


def _check(result: subprocess.CompletedProcess) -> None:
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )


def _dialog_enabled() -> bool:
    return os.environ.get(DIALOG_ENV, "").strip() == "1"


def _bundle_from_handlers(data: object) -> str:
    if not isinstance(data, dict):
        return SAFARI_BUNDLE
    handlers = data.get("LSHandlers")
    if not isinstance(handlers, list):
        return SAFARI_BUNDLE
    for scheme in ("http", "https"):
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            if handler.get("LSHandlerURLScheme") != scheme:
                continue
            for key in ("LSHandlerRoleAll", "LSHandlerRoleViewer"):
                role = handler.get(key)
                if isinstance(role, str) and _BUNDLE_RE.fullmatch(role.strip()):
                    return role.strip()
    return SAFARI_BUNDLE


def _read_json(handler: BaseHTTPRequestHandler) -> object:
    raw_length = handler.headers.get("Content-Length", "0")
    if not raw_length.isdigit():
        raise NotifyRequestError("content length")
    length = int(raw_length)
    if length < 1 or length > _MAX_BODY:
        raise NotifyRequestError("body size")
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NotifyRequestError("json") from exc


def _required_fields(payload: object) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        raise NotifyRequestError("object")
    reason = payload.get("reason")
    url = payload.get("url")
    expires_at = payload.get("expires_at")
    if not isinstance(reason, str) or not reason.strip():
        raise NotifyRequestError("reason")
    if not isinstance(url, str) or not _http_url(url):
        raise NotifyRequestError("url")
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise NotifyRequestError("expires_at")
    if "\x00" in reason:
        raise NotifyRequestError("reason")
    return reason, url, expires_at


def _primary_targets() -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    override = os.environ.get(HANDOFF_BROWSER_ENV, "").strip()
    if override:
        if _BUNDLE_RE.fullmatch(override) and "." in override:
            targets.append(("bundle", override))
        else:
            targets.append(("app", override))
    targets.append(("bundle", default_http_browser()))
    return targets


def _label(kind: str, target: str) -> str:
    if kind == "app":
        return target
    return _BUNDLE_APP.get(target.lower(), target)


def _identity_keys(kind: str, target: str) -> set[str]:
    keys = set()
    if kind == "bundle":
        keys.add("bundle:" + target.lower())
        app = _BUNDLE_APP.get(target.lower())
        if app:
            keys.add("app:" + app.lower())
    else:
        keys.add("app:" + target.lower())
        for bundle, app in _BUNDLE_APP.items():
            if app.lower() == target.lower():
                keys.add("bundle:" + bundle)
    return keys


def _verify_query(kind: str, target: str) -> tuple[str, str]:
    if kind == "app":
        return "app", target
    app = _BUNDLE_APP.get(target.lower())
    if app:
        return "app", app
    return "bundle", target


def _poll_verified(kind: str, target: str, label: str) -> tuple[bool, bool]:
    """Return (verified, timed_out). A timeout ends the attempt immediately."""

    query_kind, query_target = _verify_query(kind, target)
    deadline = time.monotonic() + VERIFY_BUDGET_S
    last_front = "False"
    last_windows = "0"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log_line(
                f"verify app={label} frontmost={last_front} windows={last_windows} -> fallback"
            )
            return False, False
        try:
            result = _run(
                verify_command(query_kind, query_target),
                timeout=min(VERIFY_BUDGET_S, remaining),
            )
        except subprocess.TimeoutExpired:
            _log_line(f"verify app={label} frontmost=timeout windows=timeout -> fallback")
            return False, True
        if _result_timed_out(result):
            _log_line(f"verify app={label} frontmost=timeout windows=timeout -> fallback")
            return False, True
        parsed = _parse_verify(result.stdout)
        if parsed is not None:
            name, front, windows = parsed
            if name:
                label = name
            last_front = "True" if front else "False"
            last_windows = str(windows)
            if front and windows >= 1:
                _log_line(f"verify app={label} frontmost=True windows={windows} -> ok")
                return True, False
        time.sleep(min(VERIFY_POLL_S, remaining))


def _parse_verify(stdout: bytes) -> tuple[str, bool, int] | None:
    text = stdout.decode("utf-8", "replace").strip()
    if not text or text == "missing":
        return None
    parts = text.split("|")
    if len(parts) != 3:
        return None
    try:
        windows = int(parts[2])
    except ValueError:
        return None
    return parts[0], parts[1].lower() == "true", windows


def _result_timed_out(result: subprocess.CompletedProcess) -> bool:
    if result.returncode == 0:
        return False
    err = ""
    if result.stderr:
        err = result.stderr.decode("utf-8", "replace").lower()
    return "timeout" in err or "-1712" in err


def _find_locked(node: object) -> bool | None:
    if isinstance(node, dict):
        if "CGSSessionScreenIsLocked" in node:
            value = node["CGSSessionScreenIsLocked"]
            if isinstance(value, bool):
                return value
            return None
        for child in node.values():
            found = _find_locked(child)
            if found is not None:
                return found
        return None
    if isinstance(node, list):
        for child in node:
            found = _find_locked(child)
            if found is not None:
                return found
    return None


def _lock_from_ioreg() -> bool | None:
    try:
        result = _run(["ioreg", "-n", "Root", "-d1", "-a"], timeout=VERIFY_BUDGET_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        data = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError, OverflowError):
        return None
    return _find_locked(data)


def _lock_from_lsappinfo() -> bool:
    try:
        result = _run(["lsappinfo", "front"], timeout=VERIFY_BUDGET_S)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0 or not result.stdout:
        return False
    text = result.stdout.decode("utf-8", "replace")
    return "loginwindow" in text.lower()


def _screen_locked() -> bool:
    locked = _lock_from_ioreg()
    if locked is not None:
        return locked
    return _lock_from_lsappinfo()


def _seconds_until(expires_at: str) -> float | None:
    text = expires_at.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed - datetime.now(timezone.utc)).total_seconds()


def _wait_for_unlock(expires_at: str) -> bool:
    """True when the screen is unlocked in time to open a browser."""

    if not _screen_locked():
        return True
    _log_line("screen locked; waiting for unlock")
    seconds = _seconds_until(expires_at)
    if seconds is None or seconds <= 0:
        _log_line("screen locked; gave up")
        return False
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(LOCK_POLL_S, remaining))
        if time.monotonic() >= deadline:
            break
        if not _screen_locked():
            return True
    _log_line("screen locked; gave up")
    return False


def _frontmost_browser() -> str | None:
    try:
        result = _run(frontmost_command(), timeout=VERIFY_BUDGET_S)
    except (OSError, subprocess.TimeoutExpired):
        _log_line("verify frontmost=timeout windows=timeout -> fallback")
        return None
    if result.returncode != 0:
        if _result_timed_out(result):
            _log_line("verify frontmost=timeout windows=timeout -> fallback")
        return None
    name = result.stdout.decode("utf-8", "replace").strip()
    if name in _KNOWN_BROWSERS:
        return name
    return None


def _app_installed(name: str) -> bool:
    query = f"kMDItemDisplayName == '{name}'c && kMDItemKind == 'Application'"
    try:
        result = _run(["mdfind", query], timeout=VERIFY_BUDGET_S)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout and result.stdout.strip())


def _http_url(url: str) -> bool:
    if any(ch.isspace() for ch in url):
        return False
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _send(handler: BaseHTTPRequestHandler, status: int) -> None:
    handler.send_response(status)
    if status == 405:
        handler.send_header("Allow", "POST")
    handler.send_header("Content-Length", "0")
    handler.end_headers()


if __name__ == "__main__":
    main()
