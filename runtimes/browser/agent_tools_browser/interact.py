"""browser.click, browser.type, browser.scroll, browser.select, and browser.wait_for.

Targets are Playwright locators or a point in viewport CSS pixels. A point with
scroll_y scrolls the same scroller browser.read uses, then acts at (x, y).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import sys
import time

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from agent_tools_browser.validate import is_http_url

log = logging.getLogger("agent_tools_browser")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

ACTION_TIMEOUT_MS = 5000
WAIT_POLL_S = 0.05

# Same scroller rule as browser.read: the document when it overflows, otherwise
# the largest overflow box. Returns the scroll offset after moving there.
_SCROLL_PAGE_TO_JS = """
(y) => {
  const doc = document.scrollingElement || document.documentElement;
  const viewH = window.innerHeight || doc.clientHeight || 0;
  const docH = doc.scrollHeight || 0;
  if (docH > viewH + 1) {
    window.scrollTo(0, y);
    return Math.round(window.scrollY || doc.scrollTop || 0);
  }
  let best = null;
  let bestArea = 0;
  if (document.body) {
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
  }
  if (best) {
    best.scrollTop = y;
    return Math.round(best.scrollTop);
  }
  window.scrollTo(0, y);
  return Math.round(window.scrollY || doc.scrollTop || 0);
}
"""

_SCROLL_PAGE_JS = """
(spec) => {
  const doc = document.scrollingElement || document.documentElement;
  const viewH = window.innerHeight || doc.clientHeight || 0;
  const docH = doc.scrollHeight || 0;
  let target = null;
  if (docH <= viewH + 1 && document.body) {
    let bestArea = 0;
    for (const el of document.body.querySelectorAll("*")) {
      if (!(el instanceof HTMLElement)) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy !== "auto" && oy !== "scroll" && oy !== "overlay") continue;
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const area = el.clientWidth * el.clientHeight;
      if (area > bestArea) {
        target = el;
        bestArea = area;
      }
    }
  }
  const dx = spec.delta ? Number(spec.delta.dx) || 0 : 0;
  const dy = spec.delta ? Number(spec.delta.dy) || 0 : 0;
  if (target) {
    if (spec.to === "top") {
      target.scrollTop = 0;
      target.scrollLeft = 0;
    } else if (spec.to === "bottom") {
      target.scrollTop = target.scrollHeight;
      target.scrollLeft = target.scrollWidth;
    } else if (spec.delta) {
      target.scrollLeft += dx;
      target.scrollTop += dy;
    }
    return Math.round(target.scrollTop);
  }
  if (spec.to === "top") window.scrollTo(0, 0);
  else if (spec.to === "bottom") window.scrollTo(0, doc.scrollHeight);
  else if (spec.delta) window.scrollBy(dx, dy);
  return Math.round(window.scrollY || doc.scrollTop || 0);
}
"""

_SCROLL_ELEMENT_JS = """
(el, spec) => {
  const overflows = el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
  const dx = spec.delta ? Number(spec.delta.dx) || 0 : 0;
  const dy = spec.delta ? Number(spec.delta.dy) || 0 : 0;
  if (overflows) {
    if (spec.to === "top") {
      el.scrollTop = 0;
      el.scrollLeft = 0;
    } else if (spec.to === "bottom") {
      el.scrollTop = el.scrollHeight;
      el.scrollLeft = el.scrollWidth;
    } else if (spec.delta) {
      el.scrollLeft += dx;
      el.scrollTop += dy;
    }
    return Math.round(el.scrollTop);
  }
  const doc = document.scrollingElement || document.documentElement;
  if (spec.to === "top") window.scrollTo(0, 0);
  else if (spec.to === "bottom") window.scrollTo(0, doc.scrollHeight);
  else if (spec.delta) window.scrollBy(dx, dy);
  else el.scrollIntoView({block: "center", inline: "nearest"});
  return Math.round(window.scrollY || doc.scrollTop || 0);
}
"""

_OBSERVE_JS = """
() => {
  const doc = document.scrollingElement || document.documentElement;
  const viewH = window.innerHeight || doc.clientHeight || 0;
  const docH = doc.scrollHeight || 0;
  let scrollY = window.scrollY || doc.scrollTop || 0;
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
    if (best) scrollY = best.scrollTop;
  }
  return {
    url: location.href,
    title: document.title || "",
    scrollY: Math.round(scrollY),
    width: window.innerWidth || 0,
    height: window.innerHeight || 0,
  };
}
"""

_NEXT_FRAME_JS = """
() => new Promise((resolve) => {
  requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)));
})
"""

_EDITABLE_JS = """
(el) => {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    return !el.disabled && !el.readOnly;
  }
  return el.isContentEditable === true;
}
"""

_FOCUSED_EDITABLE_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    return !el.disabled && !el.readOnly;
  }
  return el.isContentEditable === true;
}
"""


def _is_point(target: object) -> bool:
    return isinstance(target, dict) and "x" in target and "y" in target


async def _resolve_bare(page: Page, text: str) -> tuple[object | None, str | None]:
    """placeholder, then label, then text, then CSS. One unique match wins."""

    found: list[tuple[object, int]] = []
    builders = (
        lambda: page.get_by_placeholder(text),
        lambda: page.get_by_label(text),
        lambda: page.get_by_text(text),
        lambda: page.locator(text),
    )
    for build in builders:
        try:
            locator = build()
            count = await locator.count()
        except Exception as exc:
            log.info("bare target strategy failed: %s", exc)
            continue
        if count > 0:
            found.append((locator, count))
    if not found:
        return None, "not_found"
    if len(found) > 1:
        return None, "ambiguous"
    locator, count = found[0]
    if count > 1:
        return None, "ambiguous"
    return locator, None


def _action_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "strict mode violation" in text:
        return "ambiguous"
    if "not a select" in text or "element is not a <select>" in text:
        return "invalid_target"
    if "did not find some options" in text or "options not found" in text or "no element" in text:
        return "not_found"
    if any(
        phrase in text
        for phrase in (
            "not enabled",
            "not visible",
            "intercepts pointer",
            "outside of the viewport",
            "not editable",
            "not stable",
        )
    ):
        return "not_interactable"
    if isinstance(exc, PlaywrightTimeout):
        return "timeout"
    return "not_interactable"


async def _finish(page: Page, scroll_y: int | None = None) -> dict | str:
    try:
        await page.evaluate(_NEXT_FRAME_JS)
        info = await page.evaluate(_OBSERVE_JS)
        png = await page.screenshot(type="png", animations="disabled")
    except Exception as exc:
        log.info("observation failed: %s", exc)
        return _action_error(exc)
    if not isinstance(png, bytes) or not png.startswith(PNG_MAGIC):
        return "not_interactable"
    if not isinstance(info, dict):
        return "not_interactable"
    url = str(info.get("url", ""))
    if not is_http_url(url):
        return "no_session"
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width < 1 or height < 1:
        return "not_interactable"
    measured = int(info.get("scrollY") or 0)
    reported = measured if scroll_y is None else max(0, int(scroll_y))
    return {
        "url": url,
        "title": str(info.get("title") or ""),
        "scroll_y": reported,
        "viewport": {"width": width, "height": height},
        "image": base64.b64encode(png).decode("ascii"),
    }


async def _resolve_locator(page: Page, target: object) -> tuple[object | None, str | None]:
    if isinstance(target, str):
        return await _resolve_bare(page, target)
    if not isinstance(target, dict):
        return None, "invalid_target"
    try:
        if "placeholder" in target:
            locator = page.get_by_placeholder(str(target["placeholder"]))
        elif "label" in target:
            locator = page.get_by_label(str(target["label"]))
        elif "role" in target:
            kwargs = {}
            if "name" in target:
                kwargs["name"] = target["name"]
            locator = page.get_by_role(target["role"], **kwargs)
        elif "text" in target:
            locator = page.get_by_text(target["text"], exact=bool(target.get("exact", False)))
        elif "css" in target:
            locator = page.locator(target["css"])
        else:
            return None, "invalid_target"
        count = await locator.count()
    except Exception as exc:
        log.info("locator failed: %s", exc)
        return None, "invalid_target"
    nth = target.get("nth")
    if count == 0:
        return None, "not_found"
    if count > 1 and nth is None:
        return None, "ambiguous"
    if nth is not None:
        if isinstance(nth, bool) or not isinstance(nth, int) or nth < 0 or nth >= count:
            return None, "not_found"
        locator = locator.nth(nth)
    return locator, None


async def _scroll_page_to(page: Page, scroll_y: int) -> str | None:
    try:
        await page.evaluate(_SCROLL_PAGE_TO_JS, int(scroll_y))
        await page.evaluate(_NEXT_FRAME_JS)
    except Exception as exc:
        log.info("scroll_y failed: %s", exc)
        return _action_error(exc)
    return None


async def _point_in_viewport(page: Page, target: dict) -> str | None:
    if "scroll_y" in target:
        failed = await _scroll_page_to(page, int(target["scroll_y"]))
        if failed:
            return failed
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    x = float(target["x"])
    y = float(target["y"])
    if x < 0 or y < 0 or x >= float(viewport["width"]) or y >= float(viewport["height"]):
        return "invalid_target"
    return None


def _modifier_keys(modifiers: list[str]) -> list[str]:
    keys = []
    for name in modifiers:
        if name == "ControlOrMeta":
            keys.append("Meta" if sys.platform == "darwin" else "Control")
        else:
            keys.append(name)
    return keys


async def _click_point(page: Page, target: dict, button: str, click_count: int, modifiers: list[str]) -> str | None:
    failed = await _point_in_viewport(page, target)
    if failed:
        return failed
    keys = _modifier_keys(modifiers)

    async def act() -> None:
        await page.mouse.click(
            float(target["x"]),
            float(target["y"]),
            button=button,
            click_count=click_count,
        )

    try:
        for key in keys:
            await page.keyboard.down(key)
        try:
            await act()
        finally:
            for key in reversed(keys):
                await page.keyboard.up(key)
    except Exception as exc:
        log.info("point click failed: %s", exc)
        return _action_error(exc)
    return None


async def click(page: Page, arguments: object) -> dict | str:
    if isinstance(arguments, str):
        arguments = {"target": arguments}
    if not isinstance(arguments, dict) or "target" not in arguments:
        return "invalid_target"
    target = arguments["target"]
    button = str(arguments.get("button") or "left")
    click_count = int(arguments.get("click_count") or 1)
    modifiers = list(arguments.get("modifiers") or [])
    try:
        if _is_point(target):
            failed = await _click_point(page, target, button, click_count, modifiers)
            if failed:
                return failed
            return await _finish(page)
        locator, code = await _resolve_locator(page, target)
        if code:
            return code
        assert locator is not None
        if not await locator.is_visible():
            return "not_interactable"
        if not await locator.is_enabled():
            return "not_interactable"
        await locator.click(
            button=button,
            click_count=click_count,
            modifiers=modifiers,
            timeout=ACTION_TIMEOUT_MS,
        )
    except Exception as exc:
        log.info("click failed: %s", exc)
        return _action_error(exc)
    return await _finish(page)


async def _type_into_focused(page: Page, text: str, clear: bool, press_enter: bool) -> str | None:
    try:
        focused = await page.evaluate(_FOCUSED_EDITABLE_JS)
    except Exception as exc:
        log.info("focus check failed: %s", exc)
        return _action_error(exc)
    if not focused:
        return "not_interactable"
    try:
        if clear:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        if text:
            await page.keyboard.type(text)
        if press_enter:
            await page.keyboard.press("Enter")
    except Exception as exc:
        log.info("typing failed: %s", exc)
        return _action_error(exc)
    return None


async def type_text(page: Page, arguments: dict) -> dict | str:
    text = str(arguments.get("text", ""))
    clear = bool(arguments.get("clear", False))
    press_enter = bool(arguments.get("press_enter", False))
    target = arguments.get("target")
    try:
        if target is None:
            failed = await _type_into_focused(page, text, clear, press_enter)
            if failed:
                return failed
            return await _finish(page)
        if _is_point(target):
            failed = await _click_point(page, target, "left", 1, [])
            if failed:
                return failed
            failed = await _type_into_focused(page, text, clear, press_enter)
            if failed:
                return failed
            return await _finish(page)
        locator, code = await _resolve_locator(page, target)
        if code:
            return code
        assert locator is not None
        editable = await locator.evaluate(_EDITABLE_JS)
        if not editable:
            return "not_interactable"
        if clear:
            await locator.fill(text, timeout=ACTION_TIMEOUT_MS)
        elif text:
            await locator.press_sequentially(text, timeout=ACTION_TIMEOUT_MS)
        if press_enter:
            await locator.press("Enter", timeout=ACTION_TIMEOUT_MS)
    except Exception as exc:
        log.info("type failed: %s", exc)
        return _action_error(exc)
    return await _finish(page)


def _scroll_spec(arguments: dict) -> dict:
    spec: dict = {}
    if "to" in arguments:
        spec["to"] = arguments["to"]
    if "delta" in arguments:
        spec["delta"] = arguments["delta"]
    return spec


def _normalize_scroll(arguments: object) -> dict | None:
    """Number, scroll_y, and y are absolute page offsets. A numeric delta is dy."""

    if isinstance(arguments, bool) or arguments is None:
        return None
    if isinstance(arguments, (int, float)):
        return {"_absolute": int(arguments)}
    if not isinstance(arguments, dict):
        return None
    args = dict(arguments)
    delta = args.get("delta")
    if isinstance(delta, bool):
        return None
    if isinstance(delta, (int, float)):
        args["delta"] = {"dx": 0, "dy": delta}
    has_move = "delta" in args or "to" in args or args.get("target") is not None
    if not has_move:
        if "scroll_y" in args:
            args["_absolute"] = int(args["scroll_y"])
        elif "y" in args:
            args["_absolute"] = int(args["y"])
        else:
            return None
    return args


async def scroll(page: Page, arguments: object) -> dict | str:
    normalized = _normalize_scroll(arguments)
    if normalized is None:
        return "invalid_target"
    if normalized.get("_absolute") is not None and normalized.get("target") is None:
        try:
            failed = await _scroll_page_to(page, int(normalized["_absolute"]))
        except Exception as exc:
            log.info("scroll failed: %s", exc)
            return _action_error(exc)
        if failed:
            return failed
        return await _finish(page)
    arguments = normalized
    target = arguments.get("target")
    spec = _scroll_spec(arguments)
    try:
        if target is None:
            if "to" not in spec and "delta" not in spec:
                return "invalid_target"
            scroll_y = int(await page.evaluate(_SCROLL_PAGE_JS, spec))
            return await _finish(page, scroll_y)
        if _is_point(target):
            failed = await _point_in_viewport(page, target)
            if failed:
                return failed
            handle = await page.evaluate_handle(
                "({x, y}) => document.elementFromPoint(x, y)",
                {"x": float(target["x"]), "y": float(target["y"])},
            )
            element = handle.as_element()
            if element is None:
                return "not_found"
            if "to" not in spec and "delta" not in spec:
                await element.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
                return await _finish(page)
            scroll_y = int(await element.evaluate(_SCROLL_ELEMENT_JS, spec))
            return await _finish(page, scroll_y)
        locator, code = await _resolve_locator(page, target)
        if code:
            return code
        assert locator is not None
        if "to" not in spec and "delta" not in spec:
            await locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            return await _finish(page)
        scroll_y = int(await locator.evaluate(_SCROLL_ELEMENT_JS, spec))
        return await _finish(page, scroll_y)
    except Exception as exc:
        log.info("scroll failed: %s", exc)
        return _action_error(exc)


async def _select_on(locator_or_element, arguments: dict) -> str | None:
    kwargs = {}
    if "value" in arguments:
        kwargs["value"] = arguments["value"]
    elif "label" in arguments:
        kwargs["label"] = arguments["label"]
    elif "index" in arguments:
        kwargs["index"] = int(arguments["index"])
    else:
        return "invalid_target"
    try:
        await locator_or_element.select_option(timeout=ACTION_TIMEOUT_MS, **kwargs)
    except Exception as exc:
        log.info("select failed: %s", exc)
        return _action_error(exc)
    return None


async def select(page: Page, arguments: dict) -> dict | str:
    target = arguments["target"]
    try:
        if _is_point(target):
            failed = await _point_in_viewport(page, target)
            if failed:
                return failed
            handle = await page.evaluate_handle(
                "({x, y}) => document.elementFromPoint(x, y)",
                {"x": float(target["x"]), "y": float(target["y"])},
            )
            element = handle.as_element()
            if element is None:
                return "not_found"
            tag = await element.evaluate("el => el.tagName")
            if tag != "SELECT":
                return "invalid_target"
            if not await element.is_enabled():
                return "not_interactable"
            failed = await _select_on(element, arguments)
            if failed:
                return failed
            return await _finish(page)
        locator, code = await _resolve_locator(page, target)
        if code:
            return code
        assert locator is not None
        tag = await locator.evaluate("el => el.tagName")
        if tag != "SELECT":
            return "invalid_target"
        if not await locator.is_enabled():
            return "not_interactable"
        failed = await _select_on(locator, arguments)
        if failed:
            return failed
    except Exception as exc:
        log.info("select failed: %s", exc)
        return _action_error(exc)
    return await _finish(page)


async def _wait_until(deadline: float, check) -> str | None:
    while True:
        outcome = await check()
        if outcome is True:
            return None
        if isinstance(outcome, str):
            return outcome
        if time.monotonic() >= deadline:
            return "timeout"
        await asyncio.sleep(min(WAIT_POLL_S, max(0.0, deadline - time.monotonic())))


async def wait_for(page: Page, arguments: dict) -> dict | str:
    timeout_ms = int(arguments.get("timeout_ms", 10000))
    deadline = time.monotonic() + (timeout_ms / 1000)

    if "text" in arguments:
        text = str(arguments["text"])

        async def text_present():
            try:
                count = await page.get_by_text(text).count()
            except Exception as exc:
                return _action_error(exc)
            return True if count > 0 else False

        failed = await _wait_until(deadline, text_present)
        if failed:
            return failed
        return await _finish(page)

    if "url_matches" in arguments:
        try:
            pattern = re.compile(str(arguments["url_matches"]))
        except re.error:
            return "invalid_target"

        async def url_ok():
            return pattern.search(page.url) is not None

        failed = await _wait_until(deadline, url_ok)
        if failed:
            return failed
        return await _finish(page)

    if "load_state" in arguments:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            await page.wait_for_load_state(arguments["load_state"], timeout=max(remaining, 1))
        except PlaywrightTimeout:
            return "timeout"
        except Exception as exc:
            log.info("load_state failed: %s", exc)
            return _action_error(exc)
        return await _finish(page)

    target = arguments.get("target")
    state = arguments.get("state")
    if not isinstance(target, (dict, str)) or state not in {"visible", "hidden"} or _is_point(target):
        return "invalid_target"

    async def locator_state():
        locator, code = await _resolve_locator(page, target)
        if code == "ambiguous" or code == "invalid_target":
            return code
        if state == "hidden":
            if code == "not_found" or locator is None:
                return True
            try:
                visible = await locator.is_visible()
            except Exception as exc:
                return _action_error(exc)
            return True if not visible else False
        if code == "not_found" or locator is None:
            return False
        try:
            visible = await locator.is_visible()
        except Exception as exc:
            return _action_error(exc)
        return True if visible else False

    failed = await _wait_until(deadline, locator_state)
    if failed:
        return failed
    return await _finish(page)
