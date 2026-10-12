# Checkpoint

Objective:
Make `browser.read` vision-first, and make the browser container launcher work with both Docker and Podman. Commit on main. Do not push. The outer agent verifies, then pushes.

Part A measures the current `browser.read` (`page.inner_text("body")`) on a fixture page before any read change. Part B replaces that output with viewport screenshots (`browser.read` 2.0.0). Part C adds a POSIX sh launcher the harness runs instead of a raw podman command.

Frozen acceptance (one row per criterion):
A. Before changing read, a fixture page and a real run record what current `browser.read` returns for text below the fold, text in an `overflow: auto` container scrolled out of view, `display: none` text, `visibility: hidden` text, text in an open shadow root, text in a same-origin iframe, and text drawn only on a canvas. The table lists the case, whether it was returned, and the evidence.
1. `browser.read` is 2.0.0. It scrolls from the top one viewport at a time and captures a PNG of each viewport, up to `max_viewports` (optional integer, default 10, min 1, max 30). Outputs are `url`, `page_height`, `viewport_height`, `truncated` (true when the page is longer than the captured viewports), and `viewports` (`index`, `scroll_y`, base64 PNG `image`). No DOM text. A page whose scrolling element is not the document scrolls the main scrollable element. The original scroll position is restored. `side_effects` says the call scrolls the page temporarily and restores it.
2. The MCP result for read returns each viewport as an image content block (`type` image, `mimeType` `image/png`), in order. Each image is preceded by a short text block such as `viewport 2 of 5, scroll_y 1600`. `structuredContent` matches the outputs schema.
3. `browser.screenshot` keeps its 1.0.0 contract. Its MCP result also includes an image content block. The manifest version becomes 1.1.0 only if the manifest text changes.
4. `when_to_use` for read tells the agent this is how to read a page: look at the images like a human, and ask for more viewports if `truncated` is true.
5. Local behavior tests: a long page yields several viewports with increasing `scroll_y`, `truncated` is true when capped, every image decodes to PNG, the scroll position is restored, and the MCP result has image blocks in order. Literal values are asserted where possible.
6. `runtimes/browser/bin/agent-tools-browser` is a POSIX sh launcher. It picks `AGENT_TOOLS_CONTAINER_ENGINE` when set (`docker` or `podman`), else `docker` when `docker` is on PATH and `docker info` works, else `podman`. A Mac with only Podman (docker missing, or docker as Podman's compat shim) works.
7. Podman uses the profile volume option `:U` and the host alias `host.containers.internal`. Docker omits `:U`, uses `host.docker.internal`, and on Linux adds `--add-host=host.docker.internal:host-gateway`. The webhook URL is built from `AGENT_TOOLS_NOTIFY_PORT` (default 8767) and the engine host alias, unless `AGENT_TOOLS_NOTIFY_WEBHOOK` is set. Ports come from `AGENT_TOOLS_HANDOFF_PORT` and `AGENT_TOOLS_NOVNC_PORT` (defaults 8765 and 8766). The image comes from `AGENT_TOOLS_IMAGE` (default `agent-tools-browser`). The launcher execs the engine with `run -i --rm`.
8. `--build` builds the image with the chosen engine from the repo root.
9. `smoke_container.py` uses the launcher and accepts `--engine docker|podman`.
10. CI adds an ubuntu-latest job that builds the image with docker and runs the smoke test with `--engine docker`. The existing catalog job stays.
11. `runtimes/browser/README.md` registers the launcher by absolute path for Grok Build, Claude Code, and OMP. Env vars appear by name only. Launcher engine choice and argument building are unit-tested with stubbed `docker` and `podman` on PATH.
12. `uv run scripts/check.py`, the catalog pytest, and the runtime tests pass. Manifest versions follow the README rules.
13. This machine is shared and loaded. Processes this task did not start are left alone. Everything this task starts is cleaned up.

Human-visible in the Part A table means visible in the initial 800x1280 viewport without scrolling the page or the overflow box. The fixture records that itself (`LAYOUT` lines in the read text). Viewport height was 800. `below_top` was 2336.

| case | expected human-visible? | returned by read? | notes |
| --- | --- | --- | --- |
| below the fold (`BELOW_THE_FOLD`) | no | yes | `below_in_viewport=false`, `below_top=2336`. `inner_text` still returned the paragraph. |
| overflow box, scrolled out (`OVERFLOW_SCROLLED_AWAY`) | no | yes | `overflow_away_in_box=false`, `overflow_scroll_top=296`, client height 80, scroll height 376. The string was in the read text. |
| overflow box, in view (`OVERFLOW_BOTTOM_VISIBLE`) | yes | yes | `overflow_bottom_in_box=true`. Control for the box. |
| `display: none` (`DISPLAY_NONE_TEXT`) | no | no | `display=none`. Marker absent from the read text. |
| `visibility: hidden` (`VISIBILITY_HIDDEN_TEXT`) | no | no | `visibility=hidden`. Marker absent from the read text. |
| open shadow root (`OPEN_SHADOW_TEXT`) | yes | no | `shadow_in_viewport=true`, `shadow_mode=open`. Marker absent. `inner_text` does not enter the shadow tree. |
| same-origin iframe (`IFRAME_SAME_ORIGIN`) | yes | no | `iframe_in_viewport=true`, `iframe_src=frame.html`. The child document was fetched (HTTP 200). Marker absent. |
| canvas (`CANVAS_ONLY_TEXT`) | yes | no | `canvas_in_viewport=true`, `canvas_pixels=filled`. Marker absent. The glyphs are pixels. |
| on-screen control (`VISIBLE_ON_SCREEN`) | yes | yes | `visible_in_viewport=true`. |

Current state:
- On main at 2167ef1. Not pushed by this session.
- Row A measurement is done. Read implementation is still `inner_text("body")`. Fixture: `tests/fixtures/browser_read/read_visibility.html` and `frame.html`. Evidence: one MCP `navigate` + `read` on that page. The read JSON is quoted in Commands below.
- Displays before the measurement: X1, X2, X10, X13. The measurement used Xvfb :200 and that display is gone. Those four displays remain. No Chrome from this task remains.
- No container engine is on PATH (`docker` and `podman` are absent). The image will not be built on this machine. CI row 10 is the docker build.
- Vision-read column is not in the table yet. Part B adds it after the new read lands.

Decisions:
- Measure with the existing MCP `read` tool against a local fixture before editing `session.read`.
- `browser.read` goes to 2.0.0 because the outputs change. `browser.screenshot` stays 1.0.0 unless its manifest text has to change. An image block on the screenshot MCP result is not a manifest change.
- Avoid the word "should" in manifests.
- Human-visible means painted in the initial viewport. Below-fold text and overflow-clipped text are not human-visible on that screen even though a person could scroll to them.
- When the document scrolls, vision read scrolls the document. The small overflow box is not the main scroller on this fixture, so `OVERFLOW_SCROLLED_AWAY` is expected to stay out of the screenshots. A separate page whose only scroller is an element tests the non-document path.
- MCP read content is the JSON text block, then for each viewport a short text label and an image block. Screenshot content is the JSON text block plus one image block.
- Launcher treats a `docker` binary whose `--version` or `info` output contains `podman` as Podman. Explicit `AGENT_TOOLS_CONTAINER_ENGINE` still selects that engine's flag style.

Files changed:
- `tests/fixtures/browser_read/read_visibility.html`
- `tests/fixtures/browser_read/frame.html`
- `research-progress.md`

Commands that worked / failed:
- Worked: `uv run --project runtimes/browser python /tmp/measure_read.py`. Navigate returned the fixture URL. Read returned `inner_text` JSON. Marker membership: VISIBLE_ON_SCREEN yes, OVERFLOW_SCROLLED_AWAY yes, OVERFLOW_BOTTOM_VISIBLE yes, DISPLAY_NONE_TEXT no, VISIBILITY_HIDDEN_TEXT no, OPEN_SHADOW_TEXT no, IFRAME_SAME_ORIGIN no, CANVAS_ONLY_TEXT no, BELOW_THE_FOLD yes.
- Read text: `VISIBLE_ON_SCREEN`, blank, `OVERFLOW_SCROLLED_AWAY`, blank, `OVERFLOW_BOTTOM_VISIBLE`, blank, a whitespace line, blank, `BELOW_THE_FOLD`, then the `LAYOUT` lines listed in the table notes. `frame.html` was served 200. Favicon 404 is unused.
- Worked: `uv run scripts/check.py` (exit 0).
- Worked: `uv run --with pytest --with pyyaml --with jsonschema pytest -q` (11 passed).
- Failed once, then worked: runtime pytest. The first run failed `test_browser_runtime[chromium]` because Playwright Chromium was not in `~/.cache/ms-playwright`. `uv run --project runtimes/browser playwright install chromium` installed Chrome for Testing 153.0.8010.12. Re-run: `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` (12 passed in 22.51s).

Next action:
- Commit Part A, then implement `browser.read` 2.0.0.

Unresolved (non-blocking):
- This machine has no docker or podman, so the image build is proven by the CI job, not a local build.
