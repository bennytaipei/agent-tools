#!/usr/bin/env python3
"""macOS listener for browser handoff notifications.

Binds 127.0.0.1. A POST of JSON {reason, url, expires_at} shows a notification
and returns 204, then opens url in one browser. A second browser is opened
only when `open` itself fails. Stdlib only. The LaunchAgent sends stderr to
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
LOCK_POLL_S = 2.0
_MAX_BODY = 65536
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
# Display names lsappinfo reports. Longest first so "Brave Browser" wins.
_KNOWN_BROWSERS = (
    "Microsoft Edge",
    "Google Chrome",
    "Brave Browser",
    "Firefox",
    "Safari",
    "Arc",
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


class _Gate:
    """Lets tests wait until the open work that follows 204 has finished."""

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


def dialog_command(url: str) -> list[str]:
    return ["osascript", "-e", _DIALOG_SCRIPT, url]


def wait_for_opens(timeout: float = 10) -> bool:
    """Block until open work started after a 204 has finished."""

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
    """Open url in one browser.

    A locked screen is waited out until expires_at. No browser is opened while
    the screen stays locked. The choice is AGENT_TOOLS_HANDOFF_BROWSER, else
    the frontmost known browser from lsappinfo, else the default http browser.
    A second browser is tried only when the URL `open` returns non-zero.
    Activate with `open -b` or `open -a` again. `tell application ... to
    activate` can prompt for Automation, which a background LaunchAgent cannot
    answer.
    """

    if not _http_url(url):
        raise NotifyRequestError("url must be an absolute http or https URL")
    if not _wait_for_unlock(expires_at):
        return
    primary = _primary_target()
    state = _OpenState()
    if _open_target(url, primary, state):
        return
    fallback = _fallback_target(primary)
    if fallback is None:
        return
    _log_line(f"open fallback app={_label(*fallback)}")
    _open_target(url, fallback, state)


def _open_target(url: str, target: tuple[str, str], state: _OpenState) -> bool:
    """Open url in target. True means do not try another browser."""

    kind, name = target
    label = _label(kind, name)
    if kind == "bundle":
        argv = open_command(url, name)
        activate = activate_command(name)
    else:
        argv = open_app_command(url, name)
        activate = activate_app_command(name)
    try:
        opened = _run(argv)
    except (OSError, subprocess.TimeoutExpired):
        _log_line(f"open app={label} exit=unavailable")
        return True
    if opened.returncode != 0:
        _log_line(f"open failed app={label} exit={opened.returncode}")
        return False
    try:
        focused = _run(activate)
    except (OSError, subprocess.TimeoutExpired):
        _log_line(f"open app={label} activate=unavailable")
        return True
    if focused.returncode != 0 and _dialog_enabled() and not state.dialog:
        try:
            _run(dialog_command(url))
        except (OSError, subprocess.TimeoutExpired):
            pass
        state.dialog = True
    return True


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
        # The runtime is waiting on this POST. Unlock waits poll for seconds,
        # so flush 204 before that work. The request thread keeps running it.
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


def _handoff_target() -> tuple[str, str] | None:
    override = os.environ.get(HANDOFF_BROWSER_ENV, "").strip()
    if not override:
        return None
    if _BUNDLE_RE.fullmatch(override) and "." in override:
        return ("bundle", override)
    return ("app", override)


def _default_target() -> tuple[str, str]:
    return ("bundle", default_http_browser())


def _primary_target() -> tuple[str, str]:
    chosen = _handoff_target()
    if chosen is not None:
        return chosen
    front = _frontmost_browser_name()
    if front:
        return ("app", front)
    return _default_target()


def _fallback_target(primary: tuple[str, str]) -> tuple[str, str] | None:
    """The one later candidate, after the URL open returned non-zero."""

    front = _frontmost_browser_name()
    candidates: list[tuple[str, str]] = []
    if front:
        candidates.append(("app", front))
    candidates.append(_default_target())
    for candidate in candidates:
        if not _same_target(candidate, primary):
            return candidate
    return None


def _same_target(left: tuple[str, str], right: tuple[str, str]) -> bool:
    return bool(_identity_keys(*left).intersection(_identity_keys(*right)))


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


def _lsappinfo_text(mode: str) -> str | None:
    try:
        result = _run(["lsappinfo", mode], timeout=VERIFY_BUDGET_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.decode("utf-8", "replace")


def _canonical_browser(value: str) -> str | None:
    folded = value.strip().casefold()
    for name in _KNOWN_BROWSERS:
        if name.casefold() == folded:
            return name
    return None


def _known_browser_in(text: str) -> str | None:
    """Pull a known browser display name out of lsappinfo text.

    Quoted names win. A bundle id such as com.apple.safari is not a display name.
    """

    for quoted in re.findall(r'"([^"]*)"', text):
        found = _canonical_browser(quoted)
        if found:
            return found
    found = _canonical_browser(text)
    if found:
        return found
    for name in _KNOWN_BROWSERS:
        pattern = r"(?<![\w.])" + re.escape(name) + r"(?![\w.])"
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def _frontmost_browser_name() -> str | None:
    """Frontmost known browser via lsappinfo. No System Events, no TCC prompt."""

    front = _lsappinfo_text("front")
    if front:
        found = _known_browser_in(front)
        if found:
            return found
    info = _lsappinfo_text("info")
    if not info:
        return None
    return _known_browser_in(info)


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
