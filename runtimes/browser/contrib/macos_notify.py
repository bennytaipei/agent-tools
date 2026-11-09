#!/usr/bin/env python3
"""macOS listener for browser handoff notifications.

Binds 127.0.0.1. A POST of JSON {reason, url, expires_at} shows a notification
and opens url in the default http browser, then returns 204. Stdlib only.
The LaunchAgent sends stderr to /tmp/agent-tools-browser-notify.log.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PORT_ENV = "AGENT_TOOLS_MACOS_NOTIFY_PORT"
BROWSER_ENV = "AGENT_TOOLS_NOTIFY_BROWSER"
DIALOG_ENV = "AGENT_TOOLS_NOTIFY_DIALOG"
DEFAULT_PORT = 8767
SAFARI_BUNDLE = "com.apple.safari"
_MAX_BODY = 65536
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
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


def dialog_command(url: str) -> list[str]:
    return ["osascript", "-e", _DIALOG_SCRIPT, url]


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


def open_in_browser(url: str) -> None:
    if not _http_url(url):
        raise NotifyRequestError("url must be an absolute http or https URL")
    bundle = default_http_browser()
    _check(_run(open_command(url, bundle)))
    # `open -b` again asks LaunchServices to bring that app forward.
    # `tell application id ... to activate` can prompt for Automation, which a
    # background LaunchAgent cannot answer. A display dialog is opt-in.
    focused = _run(activate_command(bundle))
    if focused.returncode != 0 and _dialog_enabled():
        _run(dialog_command(url))


def handle_payload(payload: object) -> None:
    reason, url, _expires_at = _required_fields(payload)
    _log_line(f"request reason={reason!r} url={url}")
    show_notification(reason)
    open_in_browser(url)


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
            handle_payload(payload)
        except NotifyRequestError:
            _send(self, 400)
            return
        except (subprocess.CalledProcessError, OSError):
            _send(self, 500)
            return
        _send(self, 204)

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


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True)
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
