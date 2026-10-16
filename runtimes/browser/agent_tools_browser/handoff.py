"""HTTP noVNC handoff for one live X display.

Starts x11vnc and websockify, serves a page with the noVNC view and Done and
Cancel buttons, and posts {reason, url, expires_at} to the notify webhook.
Every path stops x11vnc and websockify.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agent_tools_browser.processes import (
    Tracked,
    port_is_open,
    spawn,
    stop_tracked,
    tail_file,
    wait_until_listening,
)

log = logging.getLogger("agent_tools_browser")

NOVNC_WEB = "/usr/share/novnc"
WEBHOOK_ENV = "AGENT_TOOLS_NOTIFY_WEBHOOK"
HANDOFF_PORT_ENV = "AGENT_TOOLS_HANDOFF_PORT"
NOVNC_PORT_ENV = "AGENT_TOOLS_NOVNC_PORT"
PUBLIC_HANDOFF_PORT_ENV = "AGENT_TOOLS_PUBLIC_HANDOFF_PORT"
PUBLIC_NOVNC_PORT_ENV = "AGENT_TOOLS_PUBLIC_NOVNC_PORT"
BIND_ENV = "AGENT_TOOLS_BIND_ADDRESS"
PUBLIC_HOST_ENV = "AGENT_TOOLS_PUBLIC_HOST"
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


class ShareError(RuntimeError):
    pass


class NotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ListenSettings:
    """Where the handoff page and noVNC listen, and which host Benny's browser uses.

    bind_host is the address inside this process (0.0.0.0 in a container).
    public_host is the host in the URL posted to the webhook and in the page's
    noVNC iframe. Benny opens that URL on the Mac, so it is the published host,
    which defaults to 127.0.0.1. Unset bind ports stay ephemeral.
    public_page_port and public_novnc_port are the published host ports when
    they differ from the ports this process binds. Unset means the URL and the
    iframe use the bound ports.
    """

    bind_host: str
    public_host: str
    page_port: int | None
    novnc_port: int | None
    public_page_port: int | None
    public_novnc_port: int | None


def listen_settings() -> ListenSettings:
    return ListenSettings(
        bind_host=_host_value(BIND_ENV, "127.0.0.1"),
        public_host=_host_value(PUBLIC_HOST_ENV, "127.0.0.1"),
        page_port=_optional_port(HANDOFF_PORT_ENV),
        novnc_port=_optional_port(NOVNC_PORT_ENV),
        public_page_port=_optional_port(PUBLIC_HANDOFF_PORT_ENV),
        public_novnc_port=_optional_port(PUBLIC_NOVNC_PORT_ENV),
    )


def probe_host(bind_host: str) -> str:
    """Address this process can connect to in order to see its own listener."""

    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


class _ReuseHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class HandoffShare:
    def __init__(self, display: str, xauthority: Path, state_dir: Path, markers: list[str]) -> None:
        self._display = display
        self._xauthority = xauthority
        self._state_dir = state_dir
        self._markers = markers
        self._token = secrets.token_urlsafe(32)
        self._x11vnc: Tracked | None = None
        self._websockify: Tracked | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._status = "timeout"
        self.url = ""
        self.novnc_port = 0
        self.page_port = 0
        self.vnc_port = 0
        self._bind_host = "127.0.0.1"
        self._public_host = "127.0.0.1"
        self._page_port_pref: int | None = None
        self._public_page_port: int | None = None
        self._public_novnc_port: int | None = None

    def start(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        try:
            settings = listen_settings()
            self._bind_host = settings.bind_host
            self._public_host = settings.public_host
            self._page_port_pref = settings.page_port
            self._public_page_port = settings.public_page_port
            self._public_novnc_port = settings.public_novnc_port
            self.vnc_port = _free_port()
            self.novnc_port = settings.novnc_port if settings.novnc_port is not None else _free_port()
            self._start_x11vnc()
            self._start_websockify()
            self._start_page()
        except Exception as exc:
            detail = self._stderr_tail()
            self.stop()
            raise ShareError(f"{exc}; {detail}".strip()) from exc

    def notify(self, reason: str, timeout: int, webhook: str) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=timeout)).isoformat()
        payload = {"reason": reason, "url": self.url, "expires_at": expires_at}
        try:
            _post_json(webhook, payload)
        except NotifyError:
            self.stop()
            raise

    def user_result(self) -> str | None:
        if not self._done.is_set():
            return None
        if self._status not in {"done", "cancelled"}:
            return None
        return self._status

    def wait_user(self, seconds: float) -> bool:
        if seconds < 0:
            seconds = 0.0
        return self._done.wait(seconds)

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None and thread is not None and thread.is_alive():
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
        elif httpd is not None:
            httpd.server_close()
        stop_tracked(self._websockify, self._markers)
        self._websockify = None
        stop_tracked(self._x11vnc, self._markers)
        self._x11vnc = None

    def _start_x11vnc(self) -> None:
        log_path = self._state_dir / "x11vnc.log"
        env = _child_env(self._display, self._xauthority)
        tracked = spawn(
            [
                "x11vnc",
                "-display",
                self._display,
                "-auth",
                str(self._xauthority),
                "-rfbport",
                str(self.vnc_port),
                "-localhost",
                "-nopw",
                "-forever",
                "-shared",
                "-noxdamage",
                "-noxfixes",
                "-noxrecord",
                "-quiet",
            ],
            env=env,
            label="x11vnc",
            stderr_path=log_path,
        )
        self._x11vnc = tracked
        wait_until_listening(self.vnc_port, tracked, host="127.0.0.1")

    def _start_websockify(self) -> None:
        token_path = self._state_dir / "novnc.token"
        token_path.write_text(f"{self._token}: 127.0.0.1:{self.vnc_port}\n", encoding="utf-8")
        token_path.chmod(0o600)
        log_path = self._state_dir / "websockify.log"
        tracked = spawn(
            [
                "websockify",
                "--web",
                NOVNC_WEB,
                "--token-plugin",
                "TokenFile",
                "--token-source",
                str(token_path),
                f"{self._bind_host}:{self.novnc_port}",
            ],
            env=_child_env(self._display, self._xauthority),
            label="websockify",
            stderr_path=log_path,
        )
        self._websockify = tracked
        wait_until_listening(self.novnc_port, tracked, host=probe_host(self._bind_host))

    def _start_page(self) -> None:
        iframe_port = self.novnc_port if self._public_novnc_port is None else self._public_novnc_port
        html = _page(self._token, iframe_port, self.novnc_port, self._public_host)
        handler = _handler_class(self._token, html, self._accept)
        page_port = 0 if self._page_port_pref is None else self._page_port_pref
        httpd = _ReuseHTTPServer((self._bind_host, page_port), handler)
        httpd.daemon_threads = True
        self.page_port = httpd.server_address[1]
        public_page = self.page_port if self._public_page_port is None else self._public_page_port
        self.url = f"http://{self._public_host}:{public_page}/h/{self._token}"
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="handoff-http", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if port_is_open(self.page_port):
                return
            time.sleep(0.02)
        raise ShareError("handoff page did not listen")

    def _accept(self, status: str) -> None:
        if status not in {"done", "cancelled"}:
            return
        if not self._done.is_set():
            self._status = status
            self._done.set()

    def _stderr_tail(self) -> str:
        parts = []
        for name in ("x11vnc.log", "websockify.log"):
            text = tail_file(self._state_dir / name).strip()
            if text:
                parts.append(f"{name}: {text}")
        return " | ".join(parts)


def os_environ_webhook() -> str:
    import os

    return os.environ.get(WEBHOOK_ENV, "").strip()


def _post_json(url: str, payload: dict[str, str]) -> None:
    if not url:
        raise NotifyError("webhook is not set")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise NotifyError("webhook URL is not absolute http(s)")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
            if response.status < 200 or response.status >= 300:
                raise NotifyError(f"webhook status {response.status}")
    except NotifyError:
        raise
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"webhook status {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NotifyError(str(exc)) from exc


def _free_port() -> int:
    with socket_bind() as sock:
        return sock.getsockname()[1]


def socket_bind():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def _child_env(display: str, xauthority: Path) -> dict[str, str]:
    import os

    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = str(xauthority)
    env.pop("WAYLAND_DISPLAY", None)
    return env


def _host_value(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if not _HOST_RE.fullmatch(raw) or raw.startswith(".") or raw.endswith(".") or ".." in raw:
        raise ShareError(f"{name} is not a host name")
    return raw


def prepare_done_when(done_when: dict | None) -> None:
    """Reject a done_when URL pattern that is not a regular expression."""

    if not done_when:
        return
    pattern = done_when.get("url_matches")
    if not isinstance(pattern, str):
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ShareError("done_when url_matches is not a regular expression") from exc


def _optional_port(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ShareError(f"{name} is not a TCP port")
    port = int(raw)
    if port < 1 or port > 65535:
        raise ShareError(f"{name} is not a TCP port")
    return port


def _page(token: str, iframe_port: int, bound_novnc_port: int, public_host: str) -> bytes:
    iframe = (
        f"http://{public_host}:{iframe_port}/vnc.html?autoconnect=1&resize=scale"
        f"&path=websockify%3Ftoken%3D{token}"
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Browser handoff</title>
  <style>
    body {{ margin: 0; font-family: sans-serif; }}
    .banner {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 20;
      margin: 0;
      padding: 12px 16px;
      background: #111;
      color: #fff;
      font-size: 20px;
    }}
    .bar {{
      position: fixed;
      top: 52px;
      left: 0;
      right: 0;
      z-index: 21;
      display: flex;
      gap: 12px;
      padding: 8px 16px;
      background: #222;
    }}
    button {{ font-size: 28px; padding: 14px 28px; }}
    iframe {{
      position: fixed;
      top: 140px;
      left: 0;
      width: 100vw;
      height: calc(100vh - 140px);
      border: 0;
    }}
  </style>
</head>
<body>
  <p class="banner">Sign in or finish the step, then click Done.</p>
  <div class="bar" data-novnc-port="{bound_novnc_port}">
    <form method="post" action="/h/{token}/done"><button type="submit">Done</button></form>
    <form method="post" action="/h/{token}/cancel"><button type="submit">Cancel</button></form>
  </div>
  <iframe src="{iframe}" title="noVNC"></iframe>
</body>
</html>
"""
    return html.encode("utf-8")


def _handler_class(token: str, html: bytes, accept):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path != f"/h/{token}":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
            path = urlsplit(self.path).path
            if path == f"/h/{token}/done":
                status = "done"
            elif path == f"/h/{token}/cancel":
                status = "cancelled"
            else:
                self.send_error(404)
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            accept(status)

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler
