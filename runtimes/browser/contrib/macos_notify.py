#!/usr/bin/env python3
"""macOS listener for browser handoff notifications.

Binds 127.0.0.1. A POST of JSON {reason, url, expires_at} shows a notification
and opens url in the default browser, then returns 204. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT_ENV = "AGENT_TOOLS_MACOS_NOTIFY_PORT"
DEFAULT_PORT = 8767
_MAX_BODY = 65536
_NOTIFY_SCRIPT = (
    'on run argv\n'
    'display notification (item 1 of argv) with title "Browser handoff"\n'
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


def open_command(url: str) -> list[str]:
    return ["open", url]


def show_notification(reason: str) -> None:
    # The reason is an argv item, not interpolated into the AppleScript source.
    subprocess.run(notification_command(reason), check=True)


def open_in_browser(url: str) -> None:
    if not _http_url(url):
        raise NotifyRequestError("url must be an absolute http or https URL")
    subprocess.run(open_command(url), check=True)


def handle_payload(payload: object) -> None:
    reason, url, _expires_at = _required_fields(payload)
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
        _send(self, 405)

    def log_message(self, fmt: str, *args: object) -> None:
        return


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
