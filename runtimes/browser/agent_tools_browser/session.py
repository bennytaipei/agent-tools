"""Headed Chrome on a private Xvfb display, plus browser.handoff."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import time
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from agent_tools_browser.handoff import HandoffShare, NotifyError, ShareError, os_environ_webhook
from agent_tools_browser.processes import (
    ProcessError,
    Tracked,
    displays_in_use,
    reap,
    spawn,
    stop_tracked,
    tail_file,
)
from agent_tools_browser.validate import is_http_url

log = logging.getLogger("agent_tools_browser")

CHROME_PATH = "/usr/bin/google-chrome"
PROFILE_ENV = "AGENT_TOOLS_BROWSER_PROFILE"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CHROME_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--ozone-platform=x11",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-extensions",
    "--hide-crash-restore-bubble",
    "--window-size=1280,800",
    "--window-position=0,0",
]


class BrowserSession:
    def __init__(self) -> None:
        self._profile = _profile_dir()
        self._state = _state_dir(self._profile) if self._profile is not None else None
        self._markers = _markers(self._profile, self._state)
        self._display = ""
        self._auth: Path | None = None
        self._xvfb: Tracked | None = None
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._has_page = False
        self._share: HandoffShare | None = None
        self._start_lock = asyncio.Lock()
        self._closed = False

    async def navigate(self, url: str) -> dict | str:
        if not is_http_url(url):
            return "invalid_url"
        try:
            await self.ensure_started()
        except Exception:
            log.exception("browser failed to start")
            return "navigation_failed"
        # A refused connection keeps navigating to an error page on that tab.
        # The next call uses a new tab so the error navigation cannot interrupt it.
        page = await self._replace_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            log.error("navigation failed: %s", exc)
            return "navigation_failed"
        final = page.url
        if not is_http_url(final) or final.startswith("chrome-error"):
            return "navigation_failed"
        self._has_page = True
        return {"url": final}

    async def _replace_page(self) -> Page:
        assert self._context is not None
        old = self._page
        page = await self._context.new_page()
        self._page = page
        page.set_default_navigation_timeout(15000)
        if old is not None:
            try:
                await old.close()
            except Exception:
                log.info("could not close the previous page")
        return page

    async def read(self) -> dict | str:
        if not self._has_page or self._page is None:
            return "no_page"
        try:
            text = await self._page.inner_text("body")
            url = self._page.url
        except Exception:
            log.exception("read failed")
            return "no_page"
        if not is_http_url(url):
            return "no_page"
        return {"url": url, "text": text}

    async def screenshot(self) -> dict | str:
        if not self._has_page or self._page is None:
            return "no_page"
        try:
            png = await self._page.screenshot(type="png")
        except Exception:
            log.exception("screenshot failed")
            return "no_page"
        if not isinstance(png, bytes) or not png.startswith(PNG_MAGIC):
            return "no_page"
        return {"image": base64.b64encode(png).decode("ascii")}

    async def handoff(self, reason: str, timeout: int) -> dict | str:
        webhook = os_environ_webhook()
        if not webhook:
            return "notify_failed"
        try:
            await self.ensure_started()
        except Exception:
            log.exception("browser failed to start for handoff")
            return "share_failed"
        assert self._display and self._auth is not None and self._state is not None
        share = HandoffShare(self._display, self._auth, self._state, self._markers)
        self._share = share
        try:
            await asyncio.to_thread(share.start)
            status = await asyncio.to_thread(share.notify_and_wait, reason, timeout, webhook)
        except NotifyError:
            log.exception("handoff notify failed")
            return "notify_failed"
        except (ShareError, ProcessError, OSError):
            log.exception("handoff share failed")
            return "share_failed"
        finally:
            await asyncio.to_thread(share.stop)
            if self._share is share:
                self._share = None
        if status not in {"done", "cancelled", "timeout"}:
            return "share_failed"
        return {"status": status}

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._closed:
                raise ProcessError("browser session is closed")
            if self._context is not None:
                return
            if self._profile is None or self._state is None:
                raise ProcessError(f"{PROFILE_ENV} is not set")
            if not Path(CHROME_PATH).is_file():
                raise ProcessError(f"system Chrome is not at {CHROME_PATH}")
            self._profile.mkdir(parents=True, exist_ok=True)
            self._state.mkdir(parents=True, exist_ok=True)
            os.chmod(self._profile, 0o700)
            os.chmod(self._state, 0o700)
            os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
            self._display, self._auth, self._xvfb = await asyncio.to_thread(
                _start_display, self._state, self._markers
            )
            try:
                self._playwright = await async_playwright().start()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(self._profile),
                    channel="chrome",
                    executable_path=CHROME_PATH,
                    headless=False,
                    viewport={"width": 1280, "height": 800},
                    args=CHROME_ARGS,
                    env=_chrome_env(self._display, self._auth),
                    handle_sigint=False,
                    handle_sigterm=False,
                    handle_sighup=False,
                    chromium_sandbox=False,
                    timeout=30000,
                )
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = await self._context.new_page()
                self._page.set_default_navigation_timeout(15000)
            except Exception:
                await self._stop_browser()
                raise

    async def aclose(self) -> None:
        self._closed = True
        share = self._share
        self._share = None
        if share is not None:
            await asyncio.to_thread(share.stop)
        await self._stop_browser()
        await asyncio.to_thread(reap, self._markers)

    async def _stop_browser(self) -> None:
        context = self._context
        playwright = self._playwright
        xvfb = self._xvfb
        self._context = None
        self._playwright = None
        self._page = None
        self._xvfb = None
        self._has_page = False
        # Kill the display and Chrome before asking Playwright to disconnect.
        # A graceful close can outlast the MCP client's shutdown grace period.
        await asyncio.to_thread(reap, self._markers)
        if xvfb is not None:
            await asyncio.to_thread(stop_tracked, xvfb, self._markers)
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), timeout=0.5)
            except Exception:
                log.info("browser context close did not finish cleanly")
        if playwright is not None:
            try:
                await asyncio.wait_for(playwright.stop(), timeout=0.5)
            except Exception:
                log.info("playwright stop did not finish cleanly")
        await asyncio.to_thread(reap, self._markers)


def _profile_dir() -> Path | None:
    raw = os.environ.get(PROFILE_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def _state_dir(profile: Path | None) -> Path | None:
    if profile is None:
        return None
    return profile.parent / f"{profile.name}.runtime"


def _markers(profile: Path | None, state: Path | None) -> list[str]:
    markers: list[str] = []
    for path in (profile, state):
        if path is not None:
            markers.append(str(path))
    return markers


def _chrome_env(display: str, auth: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = str(auth)
    env.pop("WAYLAND_DISPLAY", None)
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    return env


def _start_display(state: Path, markers: list[str]) -> tuple[str, Path, Tracked]:
    used = displays_in_use()
    last_error = "no free display"
    for number in range(200, 500):
        if number in used:
            continue
        display = f":{number}"
        auth = state / "Xauthority"
        _write_auth(auth, display)
        log_path = state / "xvfb.log"
        env = os.environ.copy()
        env["XAUTHORITY"] = str(auth)
        env.pop("DISPLAY", None)
        tracked = spawn(
            [
                "Xvfb",
                display,
                "-screen",
                "0",
                "1280x800x24",
                "-nolisten",
                "tcp",
                "-auth",
                str(auth),
            ],
            env=env,
            label="Xvfb",
            stderr_path=log_path,
        )
        if _display_ready(display, auth, tracked):
            log.info("using display %s", display)
            return display, auth, tracked
        last_error = tail_file(log_path).strip() or f"Xvfb {display} did not become ready"
        stop_tracked(tracked, markers)
        used.add(number)
    raise ProcessError(last_error)


def _write_auth(path: Path, display: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    cookie = os.urandom(16).hex()
    result = subprocess.run(
        ["xauth", "-f", str(path), "add", display, ".", cookie],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ProcessError(result.stderr.strip() or "xauth failed")
    path.chmod(0o600)


def _display_ready(display: str, auth: Path, tracked: Tracked) -> bool:
    env = os.environ.copy()
    env["DISPLAY"] = display
    env["XAUTHORITY"] = str(auth)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if tracked.proc.poll() is not None:
            return False
        probe = subprocess.run(
            ["xdpyinfo"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return True
        time.sleep(0.05)
    return False
