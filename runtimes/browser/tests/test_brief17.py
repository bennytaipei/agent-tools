"""Regressions for brief 17. They fail on the brief 16 runtime."""

from __future__ import annotations

import asyncio
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from playwright.async_api import async_playwright

from agent_tools_browser import interact
from agent_tools_browser.session import CHANNEL_ENV, PROFILE_ENV, BrowserSession
from agent_tools_browser.validate import ToolFailure

REPO = Path(__file__).resolve().parents[3]
FIXTURE = (REPO / "tests" / "fixtures" / "browser_interact" / "brief17.html").read_text(encoding="utf-8")
CATALOG = REPO / "catalog" / "browser"


def _detail(result: object) -> tuple[str, str]:
    if isinstance(result, ToolFailure):
        return result.code, result.detail
    if isinstance(result, str):
        return result, ""
    raise AssertionError(f"expected an error, got {type(result).__name__}")


async def _open(fn) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        await page.route(
            "http://fixture.test/**",
            lambda route: route.fulfill(body=FIXTURE, content_type="text/html"),
        )
        await page.goto("http://fixture.test/")
        try:
            await fn(page)
        finally:
            await browser.close()


def _run(fn) -> None:
    asyncio.run(_open(fn))


def test_no_session_detail_says_navigate() -> None:
    result = BrowserSession()._require_page()
    code, detail = _detail(result)
    assert code == "no_session"
    assert "navigate" in detail.lower()
    assert "http" in detail.lower()


def test_blank_page_no_session_detail() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=["--no-sandbox"])
            page = await browser.new_page()
            try:
                result = await interact.click(page, {"target": {"css": "body"}})
            finally:
                await browser.close()
        code, detail = _detail(result)
        assert code == "no_session"
        assert "http" in detail.lower()

    asyncio.run(check())


def test_point_dx_scrolls_overflow_ancestor() -> None:
    async def check(page) -> None:
        box = await page.locator("#cell").bounding_box()
        assert box is not None
        result = await interact.scroll(
            page,
            {"delta": {"dx": 600, "dy": 0}, "target": {"x": box["x"] + 8, "y": box["y"] + 8}},
        )
        assert isinstance(result, dict), result
        scrolled = result["scrolled"]
        assert scrolled["moved"] is True
        assert scrolled["scroll_left"][1] > scrolled["scroll_left"][0]
        assert scrolled["frame"] == "main"
        left = await page.evaluate("document.querySelector('.my-wrap').scrollLeft")
        assert left >= 100

    _run(check)


def test_css_and_top_level_dx_scroll_the_container() -> None:
    async def check(page) -> None:
        css = await interact.scroll(page, {"delta": {"dx": 300, "dy": 0}, "target": {"css": ".my-wrap"}})
        assert isinstance(css, dict), css
        assert css["scrolled"]["moved"] is True
        assert css["scrolled"]["scroll_left"][1] >= 100
        top = await interact.scroll(page, {"dx": 200, "dy": 0, "target": {"css": ".my-wrap"}})
        assert isinstance(top, dict), top
        assert top["scrolled"]["scroll_left"][1] > css["scrolled"]["scroll_left"][1]

    _run(check)


def test_vertical_container_dy() -> None:
    async def check(page) -> None:
        result = await interact.scroll(page, {"dy": 200, "target": {"css": "#vscroll"}})
        assert isinstance(result, dict), result
        scrolled = result["scrolled"]
        assert scrolled["moved"] is True
        assert scrolled["scroll_top"][1] >= scrolled["scroll_top"][0] + 100
        top = await page.evaluate("document.querySelector('#vscroll').scrollTop")
        assert top >= 100

    _run(check)


def test_already_at_edge_reports_note() -> None:
    async def check(page) -> None:
        await page.evaluate("document.querySelector('.my-wrap').scrollLeft = 99999")
        result = await interact.scroll(page, {"dx": 80, "target": {"css": ".my-wrap"}})
        assert isinstance(result, dict), result
        scrolled = result["scrolled"]
        assert scrolled["moved"] is False
        note = scrolled.get("note") or result.get("note") or ""
        assert "Nothing scrolled" in note
        assert "900" in note or "edge" in note

    _run(check)


async def _box_point(page, selector: str) -> dict[str, float]:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    return {"x": box["x"] + 8, "y": box["y"] + 8}


async def _round_prop(page, selector: str, prop: str) -> int:
    value = await page.evaluate(
        """([selector, prop]) => Math.round(document.querySelector(selector)[prop])""",
        [selector, prop],
    )
    return int(value)


def test_point_dx_settles_at_requested_offset() -> None:
    """Wheel scrolling is async. A fallback that runs before it lands doubles the move."""

    async def check(page) -> None:
        point = await _box_point(page, "#cell")
        result = await interact.scroll(page, {"dx": 600, "target": point})
        assert isinstance(result, dict), result
        assert result["scrolled"]["scroll_left"] == [0, 600]
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == 600
        await asyncio.sleep(0.5)
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == 600

    _run(check)


def test_point_dy_settles_at_requested_offset() -> None:
    # #vscroll is 80px tall with 600px of content (max about 520). dy=200 stays
    # under that max even if a late wheel adds the delta a second time.
    async def check(page) -> None:
        point = await _box_point(page, "#vscroll")
        result = await interact.scroll(page, {"dy": 200, "target": point})
        assert isinstance(result, dict), result
        assert result["scrolled"]["scroll_top"] == [0, 200]
        assert await _round_prop(page, "#vscroll", "scrollTop") == 200
        await asyncio.sleep(0.5)
        assert await _round_prop(page, "#vscroll", "scrollTop") == 200

    _run(check)


def test_to_right_reports_the_settled_max() -> None:
    async def check(page) -> None:
        point = await _box_point(page, "#cell")
        result = await interact.scroll(page, {"to": "right", "target": point})
        assert isinstance(result, dict), result
        scrolled = result["scrolled"]
        maximum = scrolled["max_scroll_left"]
        assert maximum > 100
        assert scrolled["scroll_left"] == [0, maximum]
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == maximum
        await asyncio.sleep(0.5)
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == maximum

    _run(check)


def test_sequential_dx_lands_on_each_step() -> None:
    async def check(page) -> None:
        point = await _box_point(page, "#cell")
        first = await interact.scroll(page, {"dx": 300, "target": point})
        assert isinstance(first, dict), first
        assert first["scrolled"]["scroll_left"] == [0, 300]
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == 300
        second = await interact.scroll(page, {"dx": 300, "target": point})
        assert isinstance(second, dict), second
        assert second["scrolled"]["scroll_left"] == [300, 600]
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == 600
        await asyncio.sleep(0.5)
        assert await _round_prop(page, ".my-wrap", "scrollLeft") == 600

    _run(check)


def test_to_right_scrolls_to_the_end() -> None:
    async def check(page) -> None:
        result = await interact.scroll(page, {"to": "right", "target": {"css": ".my-wrap"}})
        assert isinstance(result, dict), result
        assert result["scrolled"]["moved"] is True
        left, maximum = await page.evaluate(
            """() => {
              const el = document.querySelector('.my-wrap');
              return [el.scrollLeft, el.scrollWidth - el.clientWidth];
            }"""
        )
        assert left >= maximum - 2

    _run(check)


def test_iframe_scroll_click_and_type() -> None:
    async def check(page) -> None:
        scrolled = await interact.scroll(page, {"dx": 300, "target": {"css": ".inner"}})
        assert isinstance(scrolled, dict), scrolled
        assert scrolled["scrolled"]["moved"] is True
        assert scrolled["scrolled"]["frame"] != "main"
        left = await page.frame_locator("#fr").locator(".inner").evaluate("el => el.scrollLeft")
        assert left >= 100

        # #go sits at the left of the scrolled content, so its box leaves the
        # viewport after the css scroll. Point at the iframe scroller instead.
        frame_box = await page.locator("#fr").bounding_box()
        assert frame_box is not None
        pointed = await interact.scroll(
            page,
            {
                "dx": 200,
                "target": {"x": frame_box["x"] + 40, "y": frame_box["y"] + 24},
            },
        )
        assert isinstance(pointed, dict), pointed
        assert pointed["scrolled"]["frame"] != "main"

        click = await interact.click(page, {"target": {"css": "#go"}})
        assert isinstance(click, dict), click
        typed = await interact.type_text(page, {"target": {"placeholder": "inframe"}, "text": "abc"})
        assert isinstance(typed, dict), typed
        assert typed["typed"]["value"] == "abc"
        text = await interact.click(page, {"target": {"text": "Hello frame"}})
        assert isinstance(text, dict), text
        role = await interact.click(page, {"target": {"role": "button", "name": "Frame action"}})
        assert isinstance(role, dict), role

    _run(check)


def test_not_found_detail_lists_frames_and_similar_class() -> None:
    async def check(page) -> None:
        result = await interact.click(page, {"target": {"css": ".el-table__body-wrapper"}})
        code, detail = _detail(result)
        assert code == "not_found"
        assert "frame" in detail.lower()
        assert "2" in detail
        assert "el-table__body-wrapper" in detail
        assert "el-table__header-wrapper" in detail or "el-" in detail
        assert len(detail) <= 400

    _run(check)


def test_ambiguous_detail_names_strategies() -> None:
    async def check(page) -> None:
        result = await interact.click(page, {"target": "Hello twin"})
        code, detail = _detail(result)
        assert code == "ambiguous"
        assert "nth" in detail
        assert "placeholder" in detail
        assert "text" in detail

    _run(check)


def test_not_interactable_detail_says_why() -> None:
    async def check(page) -> None:
        covered = await interact.click(page, {"target": {"css": "#under"}})
        code, detail = _detail(covered)
        assert code == "not_interactable"
        assert "cover" in detail.lower() or "intercept" in detail.lower()
        disabled = await interact.click(page, {"target": {"css": "#off"}})
        code, detail = _detail(disabled)
        assert code == "not_interactable"
        assert "disabled" in detail.lower()

    _run(check)


def test_type_reports_value_and_waits_for_debounce() -> None:
    async def check(page) -> None:
        started = await page.evaluate("Date.now()")
        first = await interact.type_text(
            page,
            {"target": {"css": "[placeholder='Filter By Policy Name']"}, "text": "abc"},
        )
        assert isinstance(first, dict), first
        assert first["typed"]["value"] == "abc"
        assert first["typed"]["matched_text"] is True
        assert "input" in first["typed"].get("events", [])
        rendered = await page.evaluate("window.renderedAt || 0")
        assert rendered >= started
        shown = await page.evaluate("document.getElementById('o').textContent")
        assert shown == "filtered:abc"

        second = await interact.type_text(
            page,
            {"target": {"css": "#f"}, "text": "abc"},
        )
        assert isinstance(second, dict), second
        assert second["typed"]["value"] == "abcabc"
        note = second.get("note") or ""
        assert "abcabc" in note
        assert "clear" in note

        cleared = await interact.type_text(page, {"target": {"css": "#f"}, "text": "z", "clear": True})
        assert isinstance(cleared, dict), cleared
        assert cleared["typed"]["value"] == "z"
        value = await page.evaluate("document.getElementById('f').value")
        assert value == "z"

        entered = await interact.type_text(
            page,
            {"target": {"css": "#f"}, "text": "", "press_enter": True, "clear": False},
        )
        assert isinstance(entered, dict), entered
        assert await page.evaluate("window.entered === true")

        editable = await interact.type_text(
            page,
            {"target": {"css": "#edit"}, "text": "hi", "clear": True},
        )
        assert isinstance(editable, dict), editable
        assert "hi" in editable["typed"]["value"]

    _run(check)


def test_navigate_details_for_download_status_and_dns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv(CHANNEL_ENV, "chromium")
    monkeypatch.setenv(PROFILE_ENV, str(profile))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/json":
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/file":
                body = b"data"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=a.bin")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/denied":
                body = b"<html><body>denied</body></html>"
                self.send_response(401)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/loop":
                self.send_response(302)
                host, port = self.server.server_address[:2]
                self.send_header("Location", f"http://{host}:{port}/loop")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def check() -> None:
        session = BrowserSession()
        try:
            bad = await session.navigate("notaurl")
            code, detail = _detail(bad)
            assert code == "invalid_url"
            assert "http" in detail.lower()

            base = f"http://127.0.0.1:{port}"
            json_page = await session.navigate(f"{base}/json")
            assert isinstance(json_page, dict), json_page
            assert json_page.get("status") == 200

            denied = await session.navigate(f"{base}/denied")
            assert isinstance(denied, dict), denied
            assert denied.get("status") == 401

            download = await session.navigate(f"{base}/file")
            code, detail = _detail(download)
            assert code == "navigation_failed"
            assert "download" in detail.lower()

            loop = await session.navigate(f"{base}/loop")
            code, detail = _detail(loop)
            assert code == "navigation_failed"
            assert detail

            missing = await session.navigate("http://nonexistent.invalid/")
            code, detail = _detail(missing)
            assert code == "navigation_failed"
            assert "nonexistent.invalid" in detail
            # This sandbox resolves that host and returns an HTTP failure.
            # A normal resolver reports NAME_NOT_RESOLVED. Either line is the
            # Playwright error the agent should see.
            assert (
                "NAME_NOT_RESOLVED" in detail
                or "ERR_NAME_NOT_RESOLVED" in detail
                or "net::ERR_" in detail
            )
        finally:
            await session.aclose()

    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()


def test_every_declared_error_has_a_detail() -> None:
    from agent_tools_browser.validate import error_detail

    snippets = {
        "invalid_url": "http",
        "navigation_failed": "url",
        "no_page": "navigate",
        "share_failed": "share",
        "notify_failed": "notify",
        "not_found": "frame",
        "ambiguous": "nth",
        "not_interactable": "scroll",
        "timeout": "timeout",
        "invalid_target": "placeholder",
        "no_session": "navigate",
        "not_found_teams": "not",
    }
    for path in sorted(CATALOG.glob("*.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        for item in manifest["errors"]:
            detail = error_detail(manifest["name"], item["code"])
            assert isinstance(detail, str) and detail.strip()
            needle = snippets[item["code"]]
            assert needle in detail.lower()
    assert "not" in error_detail("teams.send_message", "not_found").lower()
    assert error_detail("teams.send_message", "forbidden").strip()
    assert error_detail("teams.send_message", "throttled").strip()
