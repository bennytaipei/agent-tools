"""Headed Chrome on a private Xvfb display, plus browser.handoff."""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from agent_tools_browser.auth_store import (
    baseline_from_state,
    clear_stale_singleton,
    cookie_for_browser,
    fresh_cookies,
    load_state,
    local_storage_script,
    next_save_seq,
    release_primary,
    save_merged,
    try_primary,
)
from agent_tools_browser.handoff import (
    HandoffShare,
    NotifyError,
    ShareError,
    os_environ_webhook,
    prepare_done_when,
)
from agent_tools_browser.interact import click as interact_click
from agent_tools_browser.interact import scroll as interact_scroll
from agent_tools_browser.interact import select as interact_select
from agent_tools_browser.interact import type_text as interact_type
from agent_tools_browser.interact import wait_for as interact_wait_for
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
# unset or chrome: system Chrome. chromium: Playwright's bundled browser.
CHANNEL_ENV = "AGENT_TOOLS_BROWSER_CHANNEL"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
READ_MAX_VIEWPORTS_DEFAULT = 10
SAVE_DEBOUNCE_S = 2.0
SAVE_INTERVAL_S = 15.0


def _url_for_match(url: str) -> str:
    """Page URL without the query string or fragment."""

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _user_handoff(status: str | None) -> dict | None:
    if status == "done":
        return {"status": "done", "completed_by": "user"}
    if status == "cancelled":
        return {"status": "cancelled", "completed_by": "cancelled"}
    return None
READ_MAX_VIEWPORTS_LIMIT = 30
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
    "--disable-popup-blocking",
    "--window-size=1280,800",
    "--window-position=0,0",
]

# The document is the page scroller when it overflows. Otherwise the largest
# overflow box is the page, which is how a full-height app shell scrolls.
_MEASURE_SCROLLER_JS = """
() => {
  const doc = document.scrollingElement || document.documentElement;
  const viewH = window.innerHeight || doc.clientHeight || 0;
  const docH = doc.scrollHeight || 0;
  let target = doc;
  let kind = "document";
  if (docH <= viewH + 1 && document.body) {
    let best = null;
    let bestArea = 0;
    for (const el of document.body.querySelectorAll("*")) {
      if (!(el instanceof HTMLElement)) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy !== "auto" && oy !== "scroll" && oy !== "overlay") continue;
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const area = el.clientWidth * el.clientHeight;
      if (area > bestArea) {
        best = el;
        bestArea = area;
      }
    }
    if (best) {
      target = best;
      kind = "element";
    }
  }
  window.__agentToolsReadScroller = target;
  const pageHeight = kind === "document" ? docH : target.scrollHeight;
  const viewportHeight = kind === "document" ? viewH : target.clientHeight;
  const scrollY = kind === "document" ? window.scrollY : target.scrollTop;
  const scrollX = kind === "document" ? window.scrollX : target.scrollLeft;
  return {
    kind,
    pageHeight: Math.round(pageHeight),
    viewportHeight: Math.round(viewportHeight),
    scrollY: Math.round(scrollY),
    scrollX: Math.round(scrollX),
  };
}
"""

_SCROLL_JS = """
(y) => {
  const el = window.__agentToolsReadScroller;
  const doc = document.scrollingElement || document.documentElement;
  if (!el || el === doc || el === document.documentElement || el === document.body) {
    window.scrollTo(0, y);
    return Math.round(window.scrollY);
  }
  el.scrollTop = y;
  return Math.round(el.scrollTop);
}
"""

_RESTORE_JS = """
({x, y}) => {
  const el = window.__agentToolsReadScroller;
  const doc = document.scrollingElement || document.documentElement;
  if (!el || el === doc || el === document.documentElement || el === document.body) {
    window.scrollTo(x, y);
  } else {
    el.scrollLeft = x;
    el.scrollTop = y;
  }
  delete window.__agentToolsReadScroller;
}
"""

_NEXT_FRAME_JS = """
() => new Promise((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)));
})
"""


def normalize_max_viewports(value: object) -> int | None:
    """Return a legal viewport cap, or None when the value is outside 1..30."""

    if value is None:
        return READ_MAX_VIEWPORTS_DEFAULT
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > READ_MAX_VIEWPORTS_LIMIT:
        return None
    return value


def viewport_offsets(page_height: int, viewport_height: int, max_viewports: int) -> tuple[list[int], bool]:
    """Scroll offsets from the top, and whether the cap cut the page short."""

    if viewport_height < 1 or max_viewports < 1:
        return [], True
    if page_height <= viewport_height:
        needed = 1
    else:
        needed = math.ceil(page_height / viewport_height)
    count = min(needed, max_viewports)
    return [index * viewport_height for index in range(count)], needed > max_viewports


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
        self._handoff_origin_ids: set[int] = set()
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._home_state = self._state
        self._home_markers = list(self._markers)
        self._user_data: Path | None = None
        self._private_profile: Path | None = None
        self._primary_fd: int | None = None
        self._role = ""
        self._cookie_baseline: set[tuple[str, str, str]] = set()
        self._storage_baseline: dict[str, set[str]] = {}
        self._auth_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task[None] | None = None
        self._periodic_task: asyncio.Task[None] | None = None
        self._last_save_mono = 0.0
        self._activity_gen = 0

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

    async def read(self, max_viewports: int | None = None) -> dict | str:
        if not self._has_page or self._page is None:
            return "no_page"
        limit = normalize_max_viewports(max_viewports)
        if limit is None:
            return "no_page"
        page = self._page
        origin: dict | None = None
        try:
            try:
                await page.wait_for_load_state("load", timeout=3000)
            except Exception:
                log.info("read continuing before the load event")
            origin = await page.evaluate(_MEASURE_SCROLLER_JS)
            page_height = int(origin["pageHeight"])
            viewport_height = int(origin["viewportHeight"])
            offsets, truncated = viewport_offsets(page_height, viewport_height, limit)
            if viewport_height < 1 or not offsets:
                return "no_page"
            viewports = []
            for index, offset in enumerate(offsets):
                actual = int(await page.evaluate(_SCROLL_JS, offset))
                await page.evaluate(_NEXT_FRAME_JS)
                png = await page.screenshot(type="png", animations="disabled")
                if not isinstance(png, bytes) or not png.startswith(PNG_MAGIC):
                    return "no_page"
                viewports.append(
                    {
                        "index": index,
                        "scroll_y": actual,
                        "image": base64.b64encode(png).decode("ascii"),
                    }
                )
            url = page.url
        except Exception:
            log.exception("read failed")
            return "no_page"
        finally:
            if origin is not None:
                try:
                    await page.evaluate(
                        _RESTORE_JS,
                        {"x": int(origin["scrollX"]), "y": int(origin["scrollY"])},
                    )
                except Exception:
                    log.info("could not restore scroll after read")
        if not is_http_url(url):
            return "no_page"
        return {
            "url": url,
            "page_height": page_height,
            "viewport_height": viewport_height,
            "truncated": truncated,
            "viewports": viewports,
        }

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

    def _require_page(self) -> Page | str:
        if not self._has_page or self._page is None:
            return "no_session"
        return self._page

    async def click(self, arguments: dict) -> dict | str:
        page = self._require_page()
        if isinstance(page, str):
            return page
        return await interact_click(page, arguments)

    async def type_text(self, arguments: dict) -> dict | str:
        page = self._require_page()
        if isinstance(page, str):
            return page
        return await interact_type(page, arguments)

    async def scroll(self, arguments: dict) -> dict | str:
        page = self._require_page()
        if isinstance(page, str):
            return page
        return await interact_scroll(page, arguments)

    async def select(self, arguments: dict) -> dict | str:
        page = self._require_page()
        if isinstance(page, str):
            return page
        return await interact_select(page, arguments)

    async def wait_for(self, arguments: dict) -> dict | str:
        page = self._require_page()
        if isinstance(page, str):
            return page
        return await interact_wait_for(page, arguments)

    async def handoff(
        self,
        reason: str,
        timeout: int,
        done_when: dict | None = None,
    ) -> dict | str:
        webhook = os_environ_webhook()
        if not webhook:
            return "notify_failed"
        try:
            prepare_done_when(done_when)
        except ShareError:
            log.exception("handoff done_when rejected")
            return "share_failed"
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
            await asyncio.to_thread(share.notify, reason, timeout, webhook)
            outcome = await self._await_handoff(share, timeout, done_when)
        except NotifyError:
            log.exception("handoff notify failed")
            return "notify_failed"
        except (ShareError, ProcessError, OSError):
            log.exception("handoff share failed")
            return "share_failed"
        finally:
            try:
                await asyncio.to_thread(share.stop)
            finally:
                if self._share is share:
                    self._share = None
                try:
                    await self.save_auth()
                except Exception:
                    log.exception("auth save after handoff failed")
        return outcome

    async def _await_handoff(
        self,
        share: HandoffShare,
        timeout: int,
        done_when: dict | None,
    ) -> dict | str:
        deadline = time.monotonic() + timeout
        # Pages already open belong to the session. A page created after this
        # point is an SSO popup, and that is the page Benny is looking at.
        context = self._context
        self._handoff_origin_ids = (
            {id(page) for page in context.pages} if context is not None else set()
        )
        seen_false = False
        consecutive_true = 0
        warned_already_true = False
        while True:
            user = _user_handoff(share.user_result())
            if user is not None:
                return user
            if done_when:
                try:
                    matched = await self._done_when_matches(done_when)
                except Exception:
                    # Unknown, not a miss. Do not arm the false-to-true guard.
                    log.warning("done_when poll failed", exc_info=True)
                    matched = False
                    consecutive_true = 0
                else:
                    if matched and seen_false:
                        consecutive_true += 1
                    elif matched:
                        consecutive_true = 0
                        if not warned_already_true:
                            log.warning(
                                "done_when already true on the first observation; "
                                "waiting for a false-to-true transition"
                            )
                            warned_already_true = True
                    else:
                        seen_false = True
                        consecutive_true = 0
                if matched and seen_false and consecutive_true >= 2:
                    user = _user_handoff(share.user_result())
                    if user is not None:
                        return user
                    return {
                        "status": "done",
                        "completed_by": "auto",
                        "reason": "auto_detected",
                    }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "timeout", "completed_by": "timeout"}
            await asyncio.to_thread(share.wait_user, min(1.0, remaining))

    def _page_for_done_when(self) -> Page | None:
        context = self._context
        origin = self._handoff_origin_ids
        if context is not None:
            opened = [
                page
                for page in context.pages
                if not page.is_closed() and id(page) not in origin
            ]
            if opened:
                return opened[-1]
        page = self._page
        if page is not None and not page.is_closed():
            return page
        if context is not None:
            for page in reversed(list(context.pages)):
                if not page.is_closed():
                    return page
        return None

    async def _done_when_matches(self, done_when: dict) -> bool:
        page = self._page_for_done_when()
        if page is None:
            return False
        if "url_matches" in done_when:
            return re.search(str(done_when["url_matches"]), _url_for_match(page.url)) is not None
        if "selector_present" in done_when:
            locator = page.locator(str(done_when["selector_present"])).locator("visible=true")
            return await locator.count() > 0
        if "text_present" in done_when:
            locator = page.get_by_text(str(done_when["text_present"])).locator("visible=true")
            return await locator.count() > 0
        return False

    def note_activity(self) -> None:
        """Schedule an auth save. Call this on the event loop, after a tool call."""

        if self._closed or self._context is None:
            return
        self._activity_gen += 1
        task = self._debounce_task
        if task is not None and not task.done():
            return
        now = time.monotonic()
        if self._last_save_mono == 0.0 or now - self._last_save_mono >= SAVE_DEBOUNCE_S:
            delay = 0.0
        else:
            delay = SAVE_DEBOUNCE_S - (now - self._last_save_mono)
        try:
            self._debounce_task = asyncio.create_task(self._debounced_save(delay))
        except RuntimeError:
            log.info("auth save not scheduled; the event loop is closed")

    async def _debounced_save(self, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._closed or self._context is None:
            return
        seen = self._activity_gen
        try:
            await self.save_auth()
        except Exception:
            log.exception("debounced auth save failed")
            return
        if self._closed or self._activity_gen == seen:
            return
        try:
            self._debounce_task = asyncio.create_task(self._debounced_save(SAVE_DEBOUNCE_S))
        except RuntimeError:
            return

    async def _periodic_save(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(SAVE_INTERVAL_S)
            except asyncio.CancelledError:
                return
            if self._closed or self._context is None:
                return
            try:
                await self.save_auth()
            except Exception:
                log.exception("periodic auth save failed")

    def _start_periodic(self) -> None:
        if self._periodic_task is not None and not self._periodic_task.done():
            return
        self._periodic_task = asyncio.create_task(self._periodic_save())

    async def _cancel_background_saves(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for attr in ("_debounce_task", "_periodic_task"):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def save_auth(self) -> None:
        """Write this context into the shared store. Safe when shutdown already started."""

        async with self._auth_lock:
            context = self._context
            profile = self._profile
            if context is None or profile is None:
                return
            try:
                try:
                    state = await context.storage_state(indexed_db=True)
                except TypeError:
                    state = await context.storage_state()
            except Exception:
                log.exception("storage_state failed")
                return
            if not isinstance(state, dict):
                return
            seq = next_save_seq()
            baseline_cookies = set(self._cookie_baseline)
            baseline_storage = {
                origin: set(names) for origin, names in self._storage_baseline.items()
            }
            try:
                wrote = await asyncio.to_thread(
                    save_merged,
                    profile,
                    baseline_cookies,
                    baseline_storage,
                    state,
                    seq,
                )
            except Exception:
                log.exception("auth store write failed")
                return
            if not wrote:
                return
            self._cookie_baseline, self._storage_baseline = baseline_from_state(state)
            self._last_save_mono = time.monotonic()

    async def _restore_auth(self) -> None:
        assert self._context is not None and self._profile is not None
        state = await asyncio.to_thread(load_state, self._profile)
        now = time.time()
        cookies = fresh_cookies(list(state.get("cookies") or []), now)
        shaped = [cookie for cookie in (cookie_for_browser(item) for item in cookies) if cookie]
        if shaped:
            try:
                await self._context.add_cookies(shaped)
            except Exception:
                log.exception("add_cookies failed; retrying one by one")
                for cookie in shaped:
                    try:
                        await self._context.add_cookies([cookie])
                    except Exception:
                        log.warning("skipped cookie %s", cookie.get("name"))
        origins: dict[str, dict[str, str]] = {}
        for origin in state.get("origins") or []:
            if not isinstance(origin, dict):
                continue
            key = origin.get("origin")
            if not isinstance(key, str) or not key:
                continue
            items: dict[str, str] = {}
            for item in origin.get("localStorage") or []:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("value"), str)
                ):
                    items[item["name"]] = item["value"]
            if items:
                origins[key] = items
        if origins:
            await self._context.add_init_script(local_storage_script(origins))
        self._cookie_baseline, self._storage_baseline = baseline_from_state(
            {"cookies": cookies, "origins": state.get("origins") or []}
        )

    async def _claim_profile(self) -> None:
        assert self._profile is not None and self._home_state is not None
        self._profile.mkdir(parents=True, exist_ok=True)
        os.chmod(self._profile, 0o700)
        self._primary_fd = await asyncio.to_thread(try_primary, self._profile)
        if self._primary_fd is not None:
            self._role = "primary"
            self._user_data = self._profile
            self._state = self._home_state
            self._markers = list(self._home_markers)
            self._private_profile = None
        else:
            self._role = "secondary"
            private = await asyncio.to_thread(_private_profile_dir)
            self._private_profile = private
            self._user_data = private
            self._state = private / "runtime"
            # The shared profile path must not be a marker. Reap would kill the primary.
            self._markers = _markers(private, self._state)
        log.info("browser profile role %s", self._role)

    def _release_profile_claim(self) -> None:
        release_primary(self._primary_fd)
        self._primary_fd = None
        private = self._private_profile
        self._private_profile = None
        self._user_data = None
        self._role = ""
        self._state = self._home_state
        self._markers = list(self._home_markers)
        if private is not None:
            shutil.rmtree(private, ignore_errors=True)

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._closed:
                raise ProcessError("browser session is closed")
            if self._context is not None:
                return
            if self._profile is None or self._home_state is None:
                raise ProcessError(f"{PROFILE_ENV} is not set")
            channel = browser_channel()
            if channel == "chrome" and not Path(CHROME_PATH).is_file():
                raise ProcessError(f"system Chrome is not at {CHROME_PATH}")
            os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
            try:
                await self._claim_profile()
                assert self._state is not None and self._user_data is not None
                self._state.mkdir(parents=True, exist_ok=True)
                os.chmod(self._state, 0o700)
                os.chmod(self._user_data, 0o700)
                self._display, self._auth, self._xvfb = await asyncio.to_thread(
                    _start_display, self._state, self._markers
                )
                self._playwright = await async_playwright().start()
                if self._role == "primary":
                    await asyncio.to_thread(clear_stale_singleton, self._profile)
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(self._user_data),
                    **_launch_options(channel, self._display, self._auth),
                )
                await self._restore_auth()
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = await self._context.new_page()
                self._page.set_default_navigation_timeout(15000)
                self._start_periodic()
            except Exception:
                await self._stop_browser()
                self._release_profile_claim()
                raise

    async def aclose(self) -> None:
        self._closed = True
        await self._cancel_background_saves()
        share = self._share
        self._share = None
        if share is not None:
            await asyncio.to_thread(share.stop)
        try:
            await self.save_auth()
        except Exception:
            log.exception("auth save on shutdown failed")
        await self._stop_browser()
        await asyncio.to_thread(reap, self._markers)
        self._release_profile_claim()

    async def _stop_browser(self) -> None:
        context = self._context
        playwright = self._playwright
        xvfb = self._xvfb
        self._context = None
        self._playwright = None
        self._page = None
        self._xvfb = None
        self._has_page = False
        # Close Chrome first so the profile can flush. Reap only leftovers.
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), timeout=5)
            except Exception:
                log.info("browser context close did not finish cleanly")
        if playwright is not None:
            try:
                await asyncio.wait_for(playwright.stop(), timeout=5)
            except Exception:
                log.info("playwright stop did not finish cleanly")
        if xvfb is not None:
            await asyncio.to_thread(stop_tracked, xvfb, self._markers)
        await asyncio.to_thread(reap, self._markers)


def browser_channel() -> str:
    """Which browser binary this process launches.

    The default is system Chrome so a machine that already has
    /usr/bin/google-chrome keeps the current behavior. chromium selects
    Playwright's bundled Chromium, which is what the container image ships
    on linux/arm64.
    """

    raw = os.environ.get(CHANNEL_ENV, "").strip().lower()
    if raw in {"", "chrome"}:
        return "chrome"
    if raw == "chromium":
        return "chromium"
    raise ProcessError(f"{CHANNEL_ENV} must be chrome or chromium")


def _launch_options(channel: str, display: str, auth: Path) -> dict:
    options: dict = {
        "headless": False,
        "viewport": {"width": 1280, "height": 800},
        "args": CHROME_ARGS,
        "env": _chrome_env(display, auth),
        "handle_sigint": False,
        "handle_sigterm": False,
        "handle_sighup": False,
        "chromium_sandbox": False,
        "timeout": 60000,
    }
    if channel == "chrome":
        # channel chrome plus the system executable. Bundled Chromium omits both.
        options["channel"] = "chrome"
        options["executable_path"] = CHROME_PATH
    return options


def _private_profile_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="agent-tools-profile-"))
    os.chmod(path, 0o700)
    return path


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
