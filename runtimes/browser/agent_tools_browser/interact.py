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

from agent_tools_browser.validate import ToolFailure, is_http_url

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

# Count short timers and network calls so a debounced render is still in flight.
_ARM_SETTLE_JS = """
() => {
  if (window.__agentSettle) return true;
  window.__agentSettle = true;
  window.__agentInflight = 0;
  const origTimeout = window.setTimeout;
  window.setTimeout = function (fn, ms, ...rest) {
    const delay = Number(ms) || 0;
    if (delay > 0 && delay <= 1500) {
      window.__agentInflight++;
      return origTimeout(function () {
        window.__agentInflight = Math.max(0, window.__agentInflight - 1);
        if (typeof fn === "function") return fn.apply(this, rest);
      }, delay);
    }
    return origTimeout(fn, ms, ...rest);
  };
  if (typeof window.fetch === "function") {
    const origFetch = window.fetch;
    window.fetch = function (...args) {
      window.__agentInflight++;
      return origFetch.apply(this, args).finally(() => {
        window.__agentInflight = Math.max(0, window.__agentInflight - 1);
      });
    };
  }
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    window.__agentInflight++;
    this.addEventListener("loadend", () => {
      window.__agentInflight = Math.max(0, window.__agentInflight - 1);
    });
    return send.apply(this, args);
  };
  return true;
}
"""

_SETTLE_JS = """
() => new Promise((resolve) => {
  const start = performance.now();
  let last = performance.now();
  const root = document.documentElement || document.body;
  const observer = new MutationObserver(() => { last = performance.now(); });
  if (root) {
    observer.observe(root, {subtree: true, childList: true, attributes: true, characterData: true});
  }
  const timer = setInterval(() => {
    const now = performance.now();
    let net = false;
    try {
      net = performance.getEntriesByType("resource").some((entry) => entry.responseEnd === 0);
    } catch (err) {
      net = false;
    }
    const busy = (window.__agentInflight || 0) > 0 || net;
    if (!busy && now - last >= 250) {
      cleanup();
      resolve(true);
    } else if (now - start >= 1500) {
      cleanup();
      resolve(true);
    }
  }, 40);
  function cleanup() {
    clearInterval(timer);
    observer.disconnect();
  }
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


_BARE_STRATEGIES = ("placeholder", "label", "text", "css")
_LOCATOR_KEYS = ("placeholder", "label", "role", "text", "css")


def _frame_label(page: Page, frame) -> str:
    if frame == page.main_frame:
        return "main"
    url = getattr(frame, "url", "") or ""
    return url or "frame"


def _similar_classes(names: list[str], selector: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][\w-]*", selector)
    picked: list[str] = []
    for name in names:
        for token in tokens:
            if len(token) < 3 or name == token:
                continue
            stem = token.split("__", 1)[0]
            if stem and stem in name:
                picked.append(name)
                break
            if len(token) >= 4 and token[:4] in name:
                picked.append(name)
                break
    if "el-" in selector:
        for name in names:
            if name.startswith("el-") and name not in picked:
                picked.append(name)
    unique: list[str] = []
    for name in picked:
        if name not in unique:
            unique.append(name)
    return unique[:5]


async def _class_names(page: Page) -> list[str]:
    names: list[str] = []
    for frame in page.frames:
        try:
            found = await frame.evaluate(
                """() => {
                  const names = [];
                  for (const el of document.querySelectorAll("*")) {
                    for (const cls of el.classList) names.push(cls);
                  }
                  return names;
                }"""
            )
        except Exception:
            continue
        if isinstance(found, list):
            names.extend(str(item) for item in found)
    return names


def _not_found(page: Page, kind: str, value: str, similar: list[str]) -> ToolFailure:
    frames = len(page.frames)
    if kind == "css" and similar:
        extra = " similar classes: " + ", ".join(similar)
    else:
        extra = " use read to look at the page, then use a point target {x,y} on the container"
    return _failure(
        "not_found",
        f"not found: searched {frames} frames for {kind} {value}.{extra}",
    )


def _ambiguous(parts: list[str]) -> ToolFailure:
    listed = ", ".join(parts)
    return _failure(
        "ambiguous",
        f"Matches {listed}. add \"nth\": 0..N-1 or a narrower target",
    )


async def _resolve_bare(page: Page, text: str) -> tuple[object | None, ToolFailure | None]:
    """placeholder, then label, then text, then CSS, across every frame."""

    found: list[tuple[object, str, int]] = []
    frames_hit: set[str] = set()
    for frame in page.frames:
        label = _frame_label(page, frame)
        builders = {
            "placeholder": lambda frame=frame: frame.get_by_placeholder(text),
            "label": lambda frame=frame: frame.get_by_label(text),
            "text": lambda frame=frame: frame.get_by_text(text),
            "css": lambda frame=frame: frame.locator(text),
        }
        for strategy in _BARE_STRATEGIES:
            try:
                locator = builders[strategy]()
                count = await locator.count()
            except Exception as exc:
                log.info("bare target strategy failed: %s", exc)
                continue
            if count > 0:
                found.append((locator, strategy, count))
                frames_hit.add(label)
    if not found:
        return None, _not_found(page, "string", text, [])
    if len(frames_hit) > 1 or len(found) > 1 or found[0][2] > 1:
        parts = [f"{strategy}={count}" for _locator, strategy, count in found]
        return None, _ambiguous(parts)
    return found[0][0], None


def _clip(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _failure(code: str, detail: str) -> ToolFailure:
    return ToolFailure(code, _clip(detail))


def _code_name(value: object) -> str | None:
    if isinstance(value, ToolFailure):
        return value.code
    if isinstance(value, str):
        return value
    return None


def _action_error(exc: Exception) -> ToolFailure:
    raw = str(exc)
    first = raw.split("Call log:")[0].strip().splitlines()
    line = first[0].strip() if first else "interaction failed"
    text = raw.lower()
    if "strict mode violation" in text:
        return _failure("ambiguous", f'{line} Add "nth": 0..N-1 or a narrower target.')
    if "not a select" in text or "element is not a <select>" in text:
        return _failure("invalid_target", f"{line} Target a select element.")
    if "did not find some options" in text or "options not found" in text or "no element" in text:
        return _failure("not_found", line)
    if "intercepts pointer" in text:
        return _failure(
            "not_interactable",
            f"Covered by another element ({line}). Close the overlay, scroll the container, or use a point target {{x,y}}.",
        )
    if "not visible" in text:
        return _failure(
            "not_interactable",
            "Element is not visible. Scroll the container or use a point target {x,y}.",
        )
    if "not enabled" in text or "disabled" in text:
        return _failure("not_interactable", "Element is disabled. Choose an enabled control.")
    if "not editable" in text:
        return _failure("not_interactable", "Element is not editable. Target an input, textarea, or contenteditable.")
    if "outside of the viewport" in text:
        return _failure(
            "not_interactable",
            "Element is outside the viewport. Scroll the container, then retry, or use a point target {x,y}.",
        )
    if isinstance(exc, PlaywrightTimeout) or "timeout" in text:
        return _failure("timeout", f"Timed out. {line} Raise timeout_ms or check the target.")
    return _failure("not_interactable", line)


async def _arm_settle(page: Page) -> None:
    for frame in page.frames:
        try:
            await frame.evaluate(_ARM_SETTLE_JS)
        except Exception:
            log.info("settle hook skipped on a frame")


async def _settle(page: Page) -> None:
    try:
        await page.evaluate(_SETTLE_JS)
    except Exception:
        log.info("settle wait skipped")


async def _finish(page: Page, scroll_y: int | None = None) -> dict | ToolFailure:
    try:
        await _settle(page)
        info = await page.evaluate(_OBSERVE_JS)
        png = await page.screenshot(type="png", animations="disabled")
    except Exception as exc:
        log.info("observation failed: %s", exc)
        return _action_error(exc)
    if not isinstance(png, bytes) or not png.startswith(PNG_MAGIC):
        return _failure("not_interactable", "The page did not produce a screenshot.")
    if not isinstance(info, dict):
        return _failure("not_interactable", "The page did not report its viewport.")
    url = str(info.get("url", ""))
    if not is_http_url(url):
        return _failure("no_session", "page is not on an http(s) URL; navigate first")
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


def _locator_kind(target: dict) -> tuple[str, str] | None:
    """Precedence: placeholder, label, role, text, css, then a point."""

    for key in _LOCATOR_KEYS:
        if key in target and target[key] not in (None, ""):
            return key, str(target[key])
    if "x" in target and "y" in target:
        return "point", ""
    return None


def _build_locator(frame, target: dict, kind: str):
    if kind == "placeholder":
        return frame.get_by_placeholder(str(target["placeholder"]))
    if kind == "label":
        return frame.get_by_label(str(target["label"]))
    if kind == "role":
        kwargs = {}
        if target.get("name") not in (None, ""):
            kwargs["name"] = target["name"]
        return frame.get_by_role(str(target["role"]), **kwargs)
    if kind == "text":
        return frame.get_by_text(str(target["text"]), exact=bool(target.get("exact", False)))
    return frame.locator(str(target["css"]))


async def _resolve_locator(page: Page, target: object) -> tuple[object | None, ToolFailure | None]:
    if isinstance(target, str):
        return await _resolve_bare(page, target)
    if not isinstance(target, dict):
        return None, _failure(
            "invalid_target",
            "Target needs one of placeholder, label, role, text, css, or x and y. "
            'Example: {"target":{"placeholder":"Search Admin Resources"}}.',
        )
    kind = _locator_kind(target)
    if kind is None or kind[0] == "point":
        return None, _failure(
            "invalid_target",
            "Target needs one of placeholder, label, role, text, css, or x and y. "
            'Example: {"target":{"placeholder":"Search Admin Resources"}}.',
        )
    name, value = kind
    matches: list[tuple[object, str, int]] = []
    for frame in page.frames:
        try:
            locator = _build_locator(frame, target, name)
            count = await locator.count()
        except Exception as exc:
            log.info("locator failed: %s", exc)
            return None, _failure("invalid_target", f"{name} {value} is not a locator this page can use.")
        if count > 0:
            matches.append((locator, _frame_label(page, frame), count))
    if not matches:
        similar = _similar_classes(await _class_names(page), value) if name == "css" else []
        return None, _not_found(page, name, value, similar)
    if len(matches) > 1:
        parts = [f"{label}={count}" for _locator, label, count in matches]
        return None, _ambiguous(parts)
    locator, _label, count = matches[0]
    nth = target.get("nth")
    if count > 1 and nth is None:
        return None, _ambiguous([f"{name}={count}"])
    if nth is not None:
        if isinstance(nth, bool) or not isinstance(nth, int) or nth < 0 or nth >= count:
            return None, _not_found(page, name, value, [])
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


async def click(page: Page, arguments: object) -> dict | str | ToolFailure:
    if isinstance(arguments, str):
        arguments = {"target": arguments}
    if not isinstance(arguments, dict) or "target" not in arguments:
        return _failure(
            "invalid_target",
            "Click needs a target. Example: {\"target\":{\"placeholder\":\"Search Admin Resources\"}}.",
        )
    target = arguments["target"]
    button = str(arguments.get("button") or "left")
    click_count = int(arguments.get("click_count") or 1)
    modifiers = list(arguments.get("modifiers") or [])
    await _arm_settle(page)
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
            return _failure(
                "not_interactable",
                "Element is not visible. Scroll the container, use scroll with target, or use a point target {x,y}.",
            )
        if not await locator.is_enabled():
            return _failure("not_interactable", "Element is disabled. Choose an enabled control.")
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


_FIELD_VALUE_JS = """
(el) => {
  if (!el) return "";
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return el.value || "";
  return (el.innerText || el.textContent || "");
}
"""

_WATCH_EVENTS_JS = """
(el) => {
  el.__agentEvents = [];
  const note = (ev) => { el.__agentEvents.push(ev.type); };
  el.addEventListener("input", note);
  el.addEventListener("change", note);
  el.addEventListener("keydown", note);
  return true;
}
"""


def _typed_payload(value: str, text: str, clear: bool, events: list[str]) -> tuple[dict, str | None]:
    shown = value[:200]
    matched = value == text if clear else value.endswith(text)
    note = None
    if value != text:
        note = f"Field now contains '{shown}'; send clear:true to replace instead of append"
    return {"value": shown, "matched_text": matched, "events": events}, note


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


async def _read_events(locator) -> list[str]:
    try:
        events = await locator.evaluate("el => el.__agentEvents || []")
    except Exception:
        return []
    if not isinstance(events, list):
        return []
    return [str(item) for item in events]


async def _type_on_locator(page: Page, locator, text: str, clear: bool, press_enter: bool) -> tuple[dict, str | None] | ToolFailure:
    editable = await locator.evaluate(_EDITABLE_JS)
    if not editable:
        return _failure(
            "not_interactable",
            "Element is not editable. Target an input, textarea, or contenteditable.",
        )
    try:
        await locator.evaluate(_WATCH_EVENTS_JS)
        await locator.click(timeout=ACTION_TIMEOUT_MS)
        if clear:
            await locator.press("Control+A", timeout=ACTION_TIMEOUT_MS)
            if text:
                await page.keyboard.insert_text(text)
            else:
                await locator.press("Backspace", timeout=ACTION_TIMEOUT_MS)
        elif text:
            await locator.press_sequentially(text, timeout=ACTION_TIMEOUT_MS)
        await page.evaluate(_NEXT_FRAME_JS)
        value = str(await locator.evaluate(_FIELD_VALUE_JS))
        if clear and text and value != text:
            await locator.fill(text, timeout=ACTION_TIMEOUT_MS)
            await page.evaluate(_NEXT_FRAME_JS)
            value = str(await locator.evaluate(_FIELD_VALUE_JS))
        if press_enter:
            await locator.press("Enter", timeout=ACTION_TIMEOUT_MS)
            value = str(await locator.evaluate(_FIELD_VALUE_JS))
    except Exception as exc:
        log.info("type failed: %s", exc)
        return _action_error(exc)
    events = await _read_events(locator)
    return _typed_payload(value, text, clear, events)


async def type_text(page: Page, arguments: dict) -> dict | str | ToolFailure:
    text = str(arguments.get("text", ""))
    clear = bool(arguments.get("clear", False))
    press_enter = bool(arguments.get("press_enter", False))
    target = arguments.get("target")
    await _arm_settle(page)
    typed: dict | None = None
    note: str | None = None
    try:
        if target is None or _is_point(target):
            if _is_point(target):
                failed = await _click_point(page, target, "left", 1, [])
                if failed:
                    return failed
            failed = await _type_into_focused(page, text, clear, press_enter)
            if failed:
                return failed
        else:
            locator, code = await _resolve_locator(page, target)
            if code:
                return code
            assert locator is not None
            outcome = await _type_on_locator(page, locator, text, clear, press_enter)
            if isinstance(outcome, ToolFailure):
                return outcome
            typed, note = outcome
    except Exception as exc:
        log.info("type failed: %s", exc)
        return _action_error(exc)
    result = await _finish(page)
    if isinstance(result, dict) and typed is not None:
        result["typed"] = typed
        if note:
            result["note"] = note
    return result


def _scroll_spec(arguments: dict) -> dict:
    spec: dict = {}
    if "to" in arguments:
        spec["to"] = arguments["to"]
    if "delta" in arguments:
        spec["delta"] = arguments["delta"]
    return spec


_METRICS_JS = """
(el) => {
  const maxLeft = Math.max(0, el.scrollWidth - el.clientWidth);
  const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
  return {
    left: Math.round(el.scrollLeft || 0),
    top: Math.round(el.scrollTop || 0),
    maxLeft: Math.round(maxLeft),
    maxTop: Math.round(maxTop),
  };
}
"""

_APPLY_SCROLL_JS = """
(el, spec) => {
  if (spec.to === "left") el.scrollLeft = 0;
  else if (spec.to === "right") el.scrollLeft = el.scrollWidth;
  else if (spec.to === "top") el.scrollTop = 0;
  else if (spec.to === "bottom") el.scrollTop = el.scrollHeight;
  else {
    el.scrollLeft += Number(spec.dx) || 0;
    el.scrollTop += Number(spec.dy) || 0;
  }
  const maxLeft = Math.max(0, el.scrollWidth - el.clientWidth);
  const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
  return {
    left: Math.round(el.scrollLeft || 0),
    top: Math.round(el.scrollTop || 0),
    maxLeft: Math.round(maxLeft),
    maxTop: Math.round(maxTop),
  };
}
"""

_SUMMARY_JS = """
(el) => {
  if (!el || !el.tagName) return "none";
  const id = el.id ? "#" + el.id : "";
  const cls = [...el.classList].slice(0, 3).map((name) => "." + name).join("");
  return el.tagName.toLowerCase() + id + cls;
}
"""


def _normalize_scroll(arguments: object) -> dict | None:
    """Number, scroll_y, and y are absolute page offsets. A numeric delta is dy.

    Top-level dx and dy are a horizontal or vertical delta. y stays the absolute
    page offset and is not a point coordinate.
    """

    if isinstance(arguments, bool) or arguments is None:
        return None
    if isinstance(arguments, (int, float)):
        return {"_absolute": int(arguments)}
    if not isinstance(arguments, dict):
        return None
    args = dict(arguments)
    for key in ("dx", "dy"):
        if isinstance(args.get(key), bool):
            return None
    delta = args.get("delta")
    if isinstance(delta, bool):
        return None
    if isinstance(delta, (int, float)):
        args["delta"] = {"dx": 0, "dy": delta}
    elif "delta" not in args and ("dx" in args or "dy" in args):
        args["delta"] = {"dx": args.get("dx", 0) or 0, "dy": args.get("dy", 0) or 0}
    to = args.get("to")
    if to is not None and to not in {"top", "bottom", "left", "right"}:
        return None
    has_move = "delta" in args or "to" in args or args.get("target") is not None
    if not has_move:
        if "scroll_y" in args:
            args["_absolute"] = int(args["scroll_y"])
        elif "y" in args and "x" not in args:
            args["_absolute"] = int(args["y"])
        else:
            return None
    return args


def _delta_of(arguments: dict) -> tuple[float, float]:
    delta = arguments.get("delta")
    if not isinstance(delta, dict):
        return 0.0, 0.0
    return float(delta.get("dx") or 0), float(delta.get("dy") or 0)


def _scrollable(metrics: dict) -> bool:
    return metrics.get("maxLeft", 0) > 1 or metrics.get("maxTop", 0) > 1


def _can_move(metrics: dict, dx: float, dy: float, to: str | None) -> bool:
    left = metrics.get("left", 0)
    top = metrics.get("top", 0)
    max_left = metrics.get("maxLeft", 0)
    max_top = metrics.get("maxTop", 0)
    if to == "right":
        return max_left > 1 and left < max_left - 1
    if to == "left":
        return max_left > 1 and left > 1
    if to == "bottom":
        return max_top > 1 and top < max_top - 1
    if to == "top":
        return max_top > 1 and top > 1
    ok = False
    if dx > 0 and max_left > 1 and left < max_left - 1:
        ok = True
    if dx < 0 and max_left > 1 and left > 1:
        ok = True
    if dy > 0 and max_top > 1 and top < max_top - 1:
        ok = True
    if dy < 0 and max_top > 1 and top > 1:
        ok = True
    return ok


def _moved(before: dict, after: dict) -> bool:
    return abs(after.get("left", 0) - before.get("left", 0)) >= 1 or abs(
        after.get("top", 0) - before.get("top", 0)
    ) >= 1


def _edge_note(summary: str, metrics: dict, dx: float, dy: float, to: str | None) -> str:
    if to in {"right", "left"} or (dx and not dy):
        edge = "right" if to == "right" or dx > 0 else "left"
        axis = "scroll_left"
        pos = metrics.get("left", 0)
        maximum = metrics.get("maxLeft", 0)
    else:
        edge = "bottom" if to == "bottom" or dy > 0 else "top"
        axis = "scroll_top"
        pos = metrics.get("top", 0)
        maximum = metrics.get("maxTop", 0)
    return (
        f"Nothing scrolled: nearest scrollable ancestor {summary} is already at its {edge} edge "
        f"({axis} {pos} of {maximum})"
    )


def _scrolled_body(summary: str, frame: str, before: dict, after: dict, note: str | None) -> dict:
    body = {
        "element": summary,
        "frame": frame,
        "scroll_left": [before.get("left", 0), after.get("left", 0)],
        "scroll_top": [before.get("top", 0), after.get("top", 0)],
        "moved": _moved(before, after),
        "max_scroll_left": after.get("maxLeft", before.get("maxLeft", 0)),
        "max_scroll_top": after.get("maxTop", before.get("maxTop", 0)),
    }
    if note:
        body["note"] = note
    return body


async def _element_chain(element, frame, page: Page):
    chain = []
    seen: set[int] = set()
    current = element
    current_frame = frame
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append((current, current_frame))
        parent_handle = await current.evaluate_handle(
            """el => {
              if (el.parentElement) return el.parentElement;
              const root = el.getRootNode && el.getRootNode();
              if (root && root.host) return root.host;
              return null;
            }"""
        )
        parent = parent_handle.as_element()
        if parent is not None:
            current = parent
            continue
        parent_frame = current_frame.parent_frame
        if parent_frame is None:
            break
        try:
            current = await current_frame.frame_element()
        except Exception:
            break
        current_frame = parent_frame
    return chain


async def _pick_scroller(chain, dx: float, dy: float, to: str | None):
    ranked = []
    for element, frame in chain:
        try:
            metrics = await element.evaluate(_METRICS_JS)
        except Exception:
            continue
        if not isinstance(metrics, dict) or not _scrollable(metrics):
            continue
        ranked.append((element, frame, metrics, _can_move(metrics, dx, dy, to)))
    for element, frame, metrics, movable in ranked:
        if movable:
            return element, frame, metrics
    if ranked:
        element, frame, metrics, _movable = ranked[0]
        return element, frame, metrics
    return None, None, None


async def _element_at_point(page: Page, x: float, y: float):
    handle = await page.evaluate_handle(
        "({x, y}) => document.elementFromPoint(x, y)",
        {"x": x, "y": y},
    )
    element = handle.as_element()
    frame = page.main_frame
    guard = 0
    while element is not None and guard < 6:
        guard += 1
        tag = await element.evaluate("el => el.tagName")
        if tag not in {"IFRAME", "FRAME"}:
            return element, frame
        child = await element.content_frame()
        if child is None:
            return element, frame
        box = await element.bounding_box()
        if not box:
            return element, frame
        insets = await element.evaluate(
            """el => {
              const style = getComputedStyle(el);
              return {
                x: parseFloat(style.borderLeftWidth) || 0,
                y: parseFloat(style.borderTopWidth) || 0,
              };
            }"""
        )
        local_x = x - box["x"] - float(insets.get("x") or 0)
        local_y = y - box["y"] - float(insets.get("y") or 0)
        inner = await child.evaluate_handle(
            "({x, y}) => document.elementFromPoint(x, y)",
            {"x": max(0, local_x), "y": max(0, local_y)},
        )
        nxt = inner.as_element()
        if nxt is None:
            return element, frame
        element = nxt
        frame = child
        x, y = local_x, local_y
    return element, frame


# Chrome applies mouse.wheel on a later task. Poll until the offset stops
# changing, and not before ~120ms, or an immediate read looks like "no move".
_WHEEL_SETTLE_POLL_S = 0.03
_WHEEL_SETTLE_MIN_S = 0.12
_WHEEL_SETTLE_MAX_S = 0.8


def _offset_pair(metrics: dict) -> tuple[int, int]:
    return int(metrics.get("left") or 0), int(metrics.get("top") or 0)


async def _read_metrics(element) -> dict:
    try:
        metrics = await element.evaluate(_METRICS_JS)
    except Exception as exc:
        log.info("scroll metrics failed: %s", exc)
        return {}
    return metrics if isinstance(metrics, dict) else {}


async def _await_scroll_settle(element) -> dict:
    """Return scroll metrics once scrollLeft and scrollTop have stopped changing."""

    started = time.monotonic()
    previous = await _read_metrics(element)
    while True:
        await asyncio.sleep(_WHEEL_SETTLE_POLL_S)
        current = await _read_metrics(element)
        elapsed = time.monotonic() - started
        settled = _offset_pair(current) == _offset_pair(previous) and elapsed >= _WHEEL_SETTLE_MIN_S
        if settled or elapsed >= _WHEEL_SETTLE_MAX_S:
            return current or previous
        previous = current


async def _perform_scroll(page: Page, element, frame, dx: float, dy: float, to: str | None, point: tuple[float, float] | None) -> dict:
    before = await element.evaluate(_METRICS_JS)
    summary = str(await element.evaluate(_SUMMARY_JS))
    label = _frame_label(page, frame)
    wheel_dx = dx
    wheel_dy = dy
    if to == "right":
        wheel_dx = max(dx, 4000)
    elif to == "left":
        wheel_dx = min(dx, -4000)
    elif to == "bottom":
        wheel_dy = max(dy, 4000)
    elif to == "top":
        wheel_dy = min(dy, -4000)
    wheeled = False
    try:
        if point is not None:
            await page.mouse.move(point[0], point[1])
        else:
            box = await element.bounding_box()
            if box:
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(20, box["height"] / 2))
        if wheel_dx or wheel_dy:
            await page.mouse.wheel(wheel_dx, wheel_dy)
            wheeled = True
    except Exception as exc:
        log.info("wheel scroll failed: %s", exc)
    if wheeled:
        after = await _await_scroll_settle(element)
    else:
        after = await _read_metrics(element)
    incomplete = False
    if to == "right" and after.get("left", 0) < after.get("maxLeft", 0) - 1:
        incomplete = True
    elif to == "left" and after.get("left", 0) > 1:
        incomplete = True
    elif to == "bottom" and after.get("top", 0) < after.get("maxTop", 0) - 1:
        incomplete = True
    elif to == "top" and after.get("top", 0) > 1:
        incomplete = True
    if not _moved(before, after) or incomplete:
        after = await element.evaluate(_APPLY_SCROLL_JS, {"dx": dx, "dy": dy, "to": to or ""})
        if not isinstance(after, dict):
            after = await _read_metrics(element)
        after = await _await_scroll_settle(element)
    note = None
    if not _moved(before, after):
        if _scrollable(before):
            note = _edge_note(summary, before, dx, dy, to)
        else:
            where = f"({point[0]:.0f},{point[1]:.0f})" if point else "the target"
            note = f"No scrollable ancestor found at {where}; try a css target for the container"
    return _scrolled_body(summary, label, before, after, note)


async def scroll(page: Page, arguments: object) -> dict | str | ToolFailure:
    normalized = _normalize_scroll(arguments)
    if normalized is None:
        return _failure(
            "invalid_target",
            'Scroll needs dx, dy, delta, to, scroll_y, y, or a target. Example: {"dx":600,"target":{"x":756,"y":425}} or 2200.',
        )
    await _arm_settle(page)
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
    dx, dy = _delta_of(arguments)
    to = arguments.get("to") if isinstance(arguments.get("to"), str) else None
    try:
        if target is None:
            if "to" not in spec and "delta" not in spec:
                return _failure(
                    "invalid_target",
                    'Scroll needs dx, dy, delta, to, scroll_y, or y. Example: {"dx":600,"target":{"x":756,"y":425}}.',
                )
            scroll_y = int(await page.evaluate(_SCROLL_PAGE_JS, spec))
            return await _finish(page, scroll_y)
        if _is_point(target):
            failed = await _point_in_viewport(page, target)
            if failed:
                return _failure("invalid_target", "Point target is outside the viewport. Use x and y from read.")
            element, frame = await _element_at_point(page, float(target["x"]), float(target["y"]))
            if element is None:
                return _not_found(page, "point", f"{target['x']},{target['y']}", [])
            if "to" not in spec and "delta" not in spec:
                await element.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
                return await _finish(page)
            chain = await _element_chain(element, frame, page)
            scroller, scroller_frame, _metrics = await _pick_scroller(chain, dx, dy, to)
            point = (float(target["x"]), float(target["y"]))
            if scroller is None:
                result = await _finish(page)
                if isinstance(result, dict):
                    result["scrolled"] = _scrolled_body(
                        "none",
                        _frame_label(page, frame),
                        {"left": 0, "top": 0, "maxLeft": 0, "maxTop": 0},
                        {"left": 0, "top": 0, "maxLeft": 0, "maxTop": 0},
                        f"No scrollable ancestor found at ({point[0]:.0f},{point[1]:.0f}); try a css target for the container",
                    )
                    result["scrolled"]["moved"] = False
                return result
            scrolled = await _perform_scroll(page, scroller, scroller_frame, dx, dy, to, point)
            result = await _finish(page, int(scrolled["scroll_top"][1]))
            if isinstance(result, dict):
                result["scrolled"] = scrolled
            return result
        locator, code = await _resolve_locator(page, target)
        if code:
            return code
        assert locator is not None
        if "to" not in spec and "delta" not in spec:
            await locator.scroll_into_view_if_needed(timeout=ACTION_TIMEOUT_MS)
            return await _finish(page)
        element = await locator.element_handle()
        if element is None:
            return _not_found(page, "target", "", [])
        frame = await element.owner_frame() or page.main_frame
        chain = await _element_chain(element, frame, page)
        scroller, scroller_frame, _metrics = await _pick_scroller(chain, dx, dy, to)
        if scroller is None:
            result = await _finish(page)
            if isinstance(result, dict):
                result["scrolled"] = _scrolled_body(
                    "none",
                    _frame_label(page, frame),
                    {"left": 0, "top": 0},
                    {"left": 0, "top": 0},
                    "No scrollable ancestor found at the target; try a css target for the container",
                )
                result["scrolled"]["moved"] = False
            return result
        scrolled = await _perform_scroll(page, scroller, scroller_frame, dx, dy, to, None)
        result = await _finish(page, int(scrolled["scroll_top"][1]))
        if isinstance(result, dict):
            result["scrolled"] = scrolled
        return result
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
        if isinstance(outcome, (str, ToolFailure)):
            return outcome
        if time.monotonic() >= deadline:
            return _failure("timeout", "The condition was not met before timeout_ms. Raise timeout_ms or narrow the target.")
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
            return _failure("invalid_target", "url_matches is not a regular expression.")

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
            return _failure("timeout", "The load state was not reached before timeout_ms. Raise timeout_ms.")
        except Exception as exc:
            log.info("load_state failed: %s", exc)
            return _action_error(exc)
        return await _finish(page)

    target = arguments.get("target")
    state = arguments.get("state")
    if not isinstance(target, (dict, str)) or state not in {"visible", "hidden"} or _is_point(target):
        return _failure(
            "invalid_target",
            "wait_for needs target and state, or text, or url_matches, or load_state. A point is not a wait target.",
        )

    async def locator_state():
        locator, code = await _resolve_locator(page, target)
        code_name = _code_name(code)
        if code_name in {"ambiguous", "invalid_target"}:
            return code
        if state == "hidden":
            if code_name == "not_found" or locator is None:
                return True
            try:
                visible = await locator.is_visible()
            except Exception as exc:
                return _action_error(exc)
            return True if not visible else False
        if code_name == "not_found" or locator is None:
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
