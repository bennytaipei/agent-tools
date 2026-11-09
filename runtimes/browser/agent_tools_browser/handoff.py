"""HTTP noVNC handoff for one live X display.

Starts x11vnc and websockify, serves a page with the noVNC view and Done and
Cancel buttons, and posts {reason, url, expires_at} to the notify webhook.
Every path stops x11vnc and websockify.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import shutil
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
        self._reason = ""

    def start(self, reason: str = "") -> None:
        self._reason = reason
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
        web_root = _novnc_web_root(self._state_dir)
        log_path = self._state_dir / "websockify.log"
        tracked = spawn(
            [
                "websockify",
                "--web",
                str(web_root),
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
        page = render_handoff_page(
            self._token,
            iframe_port,
            self.novnc_port,
            self._public_host,
            self._reason,
        ).encode("utf-8")
        handler = _handler_class(self._token, page, self._accept)
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


def _selector_syntax_ok(selector: str) -> bool:
    """Cheap bracket and quote check. Runtime selector errors stay in the poll."""

    if "\x00" in selector or not selector.strip():
        return False
    pairs = {")": "(", "]": "[", "}": "{"}
    opening = set(pairs.values())
    stack: list[str] = []
    quote = ""
    escaped = False
    for char in selector:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in opening:
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
    return not stack and not quote


def prepare_done_when(done_when: dict | None) -> None:
    """Reject a bad url_matches regex or an unbalanced selector up front."""

    if not done_when:
        return
    pattern = done_when.get("url_matches")
    if isinstance(pattern, str):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ShareError("done_when url_matches is not a regular expression") from exc
    selector = done_when.get("selector_present")
    if isinstance(selector, str) and not _selector_syntax_ok(selector):
        raise ShareError("done_when selector_present is not a valid selector")


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


_HANDOFF_FALLBACK = "Sign in or finish the step, then click Done."

_VIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>noVNC</title>
  <style>
    html, body, #screen {
      margin: 0;
      width: 100%;
      height: 100%;
      background: #0f0f10;
      overflow: hidden;
    }
  </style>
  <script type="module">
    import RFB from './core/rfb.js';

    function query(name, fallback) {
      const match = document.location.href.match(new RegExp('.*[?&]' + name + '=([^&#]*)'));
      return match ? decodeURIComponent(match[1]) : fallback;
    }

    const path = query('path', 'websockify');
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = scheme + '://' + location.host + '/' + path;
    const rfb = new RFB(document.getElementById('screen'), url);
    rfb.scaleViewport = true;
    rfb.background = '#0f0f10';
  </script>
</head>
<body>
  <div id="screen"></div>
</body>
</html>
"""


# Static import specifiers. view.html loads core/rfb.js, which pulls these in.
_REL_IMPORT = re.compile(
    r"""(?:import|from)\s+['"](\.[^'"]+)['"]|import\s*\(\s*['"](\.[^'"]+)['"]"""
)


def _novnc_import_roots(novnc_web: Path) -> list[str]:
    """Top-level directories the viewer graph reaches outside core/.

    noVNC 1.6 (this host) and 1.3 (bookworm image) leave core only through
    vendor/pako. app/ and include/ are the stock UI and are not on this graph.
    """

    root = novnc_web.resolve()
    start = novnc_web / "core" / "rfb.js"
    if not start.is_file():
        return []
    queue = [start]
    seen: set[Path] = set()
    names: list[str] = []
    seen_names: set[str] = set()
    while queue:
        path = queue.pop()
        try:
            key = path.resolve()
        except OSError:
            continue
        if key in seen or not key.is_file():
            continue
        seen.add(key)
        try:
            top = key.relative_to(root).parts[0]
        except ValueError:
            continue
        if top != "core" and top not in seen_names and (novnc_web / top).is_dir():
            seen_names.add(top)
            names.append(top)
        try:
            text = key.read_text(encoding="utf-8")
        except OSError:
            continue
        base = key.parent
        for match in _REL_IMPORT.finditer(text):
            spec = (match.group(1) or match.group(2)).split("?", 1)[0]
            if spec.startswith("."):
                queue.append(base / spec)
    return names


def _ensure_dir_link(link: Path, target: Path) -> None:
    desired = str(target)
    if link.is_symlink():
        if os.readlink(link) != desired:
            link.unlink()
            link.symlink_to(target, target_is_directory=True)
        return
    if link.exists():
        if link.is_dir():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target, target_is_directory=True)


def _novnc_web_root(state_dir: Path) -> Path:
    """Directory websockify serves: view.html plus the noVNC trees it imports."""

    root = state_dir / "novnc-web"
    root.mkdir(parents=True, exist_ok=True)
    novnc = Path(NOVNC_WEB)
    names = ["core"]
    for name in _novnc_import_roots(novnc):
        if name not in names:
            names.append(name)
    if (novnc / "vendor").is_dir() and "vendor" not in names:
        names.append("vendor")
    for name in names:
        _ensure_dir_link(root / name, novnc / name)
    (root / "view.html").write_text(_VIEW_HTML, encoding="utf-8")
    return root


def render_handoff_page(
    token: str,
    iframe_port: int,
    bound_novnc_port: int,
    public_host: str,
    reason: str = "",
    iframe_src: str | None = None,
) -> str:
    """Handoff page. iframe_src replaces the live noVNC frame for a screenshot."""

    text = reason.strip() if isinstance(reason, str) and reason.strip() else _HANDOFF_FALLBACK
    if iframe_src is None:
        iframe_src = (
            f"http://{public_host}:{iframe_port}/view.html?autoconnect=1&resize=scale"
            f"&path=websockify%3Ftoken%3D{token}"
        )
    safe_reason = html.escape(text, quote=True)
    safe_src = html.escape(iframe_src, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Browser handoff</title>
  <style>
    :root {{ --handoff-bar: 40px; }}
    html, body {{ margin: 0; background: #0f0f10; height: 100%; }}
    .bar {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 20;
      box-sizing: border-box;
      height: var(--handoff-bar);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 0 12px;
      background: #0f0f10;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-family: system-ui, sans-serif;
    }}
    .instruction {{
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      flex: 1;
      color: #a1a1aa;
      font-size: 13px;
    }}
    .dot {{
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: #a1a1aa;
      flex: none;
    }}
    .text {{
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .actions {{ display: flex; gap: 8px; flex: none; }}
    form {{ margin: 0; }}
    button {{
      height: 28px;
      padding: 0 12px;
      border-radius: 999px;
      font: 13px system-ui, sans-serif;
      cursor: pointer;
    }}
    button.done {{ background: #fff; color: #0f0f10; border: 0; }}
    button.cancel {{
      background: transparent;
      color: #a1a1aa;
      border: 1px solid rgba(255,255,255,0.16);
    }}
    button.done:hover, button.done:focus-visible {{ background: #e4e4e7; }}
    button.cancel:hover, button.cancel:focus-visible {{
      color: #fff;
      border-color: rgba(255,255,255,0.4);
    }}
    iframe {{
      position: fixed;
      top: var(--handoff-bar);
      left: 0;
      width: 100vw;
      height: calc(100vh - var(--handoff-bar));
      border: 0;
      background: #0f0f10;
    }}
  </style>
</head>
<body>
  <div class="bar" data-novnc-port="{bound_novnc_port}">
    <div class="instruction"><span class="dot"></span><span class="text">{safe_reason}</span></div>
    <div class="actions">
      <form method="post" action="/h/{token}/done"><button class="done" type="submit">Done</button></form>
      <form method="post" action="/h/{token}/cancel"><button class="cancel" type="submit">Cancel</button></form>
    </div>
  </div>
  <iframe src="{safe_src}" title="noVNC"></iframe>
</body>
</html>
"""


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
