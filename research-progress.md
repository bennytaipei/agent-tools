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

`visible in vision read?` is from a second run of the same fixture through `browser.read` 2.0.0. That call returned 4 viewports (`page_height` 2793, `viewport_height` 800, `truncated` false, `scroll_y` 0, 800, 1600, 1993). The PNGs were inspected: viewport 0 shows the top of the page; viewports 2 and 3 show `BELOW_THE_FOLD`; viewport 1 is the blank spacer.

| case | expected human-visible? | returned by read 1.0 (`inner_text`)? | visible in vision read? | notes |
| --- | --- | --- | --- | --- |
| below the fold (`BELOW_THE_FOLD`) | no | yes | yes | `below_in_viewport=false`, `below_top=2336`. `inner_text` returned the paragraph. Vision read shows it at `scroll_y` 1600 and 1993. |
| overflow box, scrolled out (`OVERFLOW_SCROLLED_AWAY`) | no | yes | no | `overflow_away_in_box=false`, `overflow_scroll_top=296`, client height 80, scroll height 376. `inner_text` returned it. The viewport PNG shows the box scrolled to `OVERFLOW_BOTTOM_VISIBLE` only. Page scroll does not move that box. |
| overflow box, in view (`OVERFLOW_BOTTOM_VISIBLE`) | yes | yes | yes | `overflow_bottom_in_box=true`. Painted inside the box in viewport 0. |
| `display: none` (`DISPLAY_NONE_TEXT`) | no | no | no | `display=none`. Marker absent from the read text and from every viewport PNG. |
| `visibility: hidden` (`VISIBILITY_HIDDEN_TEXT`) | no | no | no | `visibility=hidden`. Marker absent from the read text and from every viewport PNG. |
| open shadow root (`OPEN_SHADOW_TEXT`) | yes | no | yes | `shadow_in_viewport=true`, `shadow_mode=open`. `inner_text` missed it. Viewport 0 shows the text. |
| same-origin iframe (`IFRAME_SAME_ORIGIN`) | yes | no | yes | `iframe_in_viewport=true`, `iframe_src=frame.html` (HTTP 200). `inner_text` missed it. Viewport 0 shows the iframe text. |
| canvas (`CANVAS_ONLY_TEXT`) | yes | no | yes | `canvas_in_viewport=true`, `canvas_pixels=filled`. `inner_text` missed it. Viewport 0 shows the canvas glyphs. |
| on-screen control (`VISIBLE_ON_SCREEN`) | yes | yes | yes | `visible_in_viewport=true`. Viewport 0 shows it. |

Current state:
- Part A is e4d1442. Part B is 1672c70. Part C is 224f4b3. The follow-up fixes are local and not pushed.
- `browser.read` is 2.0.0. Screenshot stays 1.0.0. The vision column in the table above is filled.
- The launcher is `runtimes/browser/bin/agent-tools-browser`. `--build` builds the image and exits. Smoke builds with `--build`, then starts the launcher with no arguments. CI job `smoke` runs that script with `--engine docker`.
- Launcher unit tests put only the stub directory on PATH. Check job 38424819685 failed `test_podman_when_docker_is_missing` and `test_podman_shim_without_podman_binary_keeps_docker_command` because PATH also listed `/usr/bin` and `/bin`, so the runner's docker and podman ran.
- Displays before this task: X1, X2, X10, X13. Runs used Xvfb :200 and that display is gone. Those four displays remain. No Chrome from this task remains.
- No container engine is on PATH (`docker` and `podman` are absent). The image was not built on this machine. The CI job is the build.

Decisions:
- Measure with the existing MCP `read` tool against a local fixture before editing `session.read`.
- `browser.read` goes to 2.0.0 because the outputs change. `browser.screenshot` stays 1.0.0 unless its manifest text has to change. An image block on the screenshot MCP result is not a manifest change.
- Avoid the word "should" in manifests.
- Human-visible means painted in the initial viewport. Below-fold text and overflow-clipped text are not human-visible on that screen even though a person could scroll to them.
- When the document scrolls, vision read scrolls the document. The small overflow box is not the main scroller on this fixture, so `OVERFLOW_SCROLLED_AWAY` is expected to stay out of the screenshots. A separate page whose only scroller is an element tests the non-document path.
- MCP read content is the JSON text block, then for each viewport a short text label and an image block. Screenshot content is the JSON text block plus one image block.
- Launcher treats a `docker` binary whose `--version` or `info` output contains `podman` as Podman. Explicit `AGENT_TOOLS_CONTAINER_ENGINE` still selects that engine's flag style.
- Smoke uses a separate profile volume name (`AGENT_TOOLS_PROFILE_VOLUME`) so a smoke run does not write the real `agent-tools-browser-profile` volume.
- `--build` only builds. Starting the server is a second invocation with no arguments. Smoke does those two steps.
- Launcher tests do not put `/usr/bin` or the parent PATH on the child PATH. `dirname` and `basename` are symlinked into the stub directory.

Files changed:
- Part A: `tests/fixtures/browser_read/read_visibility.html`, `frame.html`, `research-progress.md`.
- Part B: `catalog/browser/read.yaml`, `session.py`, `server.py`, `test_runtime.py`, `tests/test_check.py`.
- Part C: `runtimes/browser/bin/agent-tools-browser`, `contrib/smoke_container.py`, `tests/test_browser_launcher.py`, `runtimes/browser/tests/test_smoke_engine.py`, `.github/workflows/check.yml`, `runtimes/browser/README.md`.

Commands that worked / failed:
- Worked: `uv run --project runtimes/browser python /tmp/measure_read.py`. Navigate returned the fixture URL. Read returned `inner_text` JSON. Marker membership: VISIBLE_ON_SCREEN yes, OVERFLOW_SCROLLED_AWAY yes, OVERFLOW_BOTTOM_VISIBLE yes, DISPLAY_NONE_TEXT no, VISIBILITY_HIDDEN_TEXT no, OPEN_SHADOW_TEXT no, IFRAME_SAME_ORIGIN no, CANVAS_ONLY_TEXT no, BELOW_THE_FOLD yes.
- Read text: `VISIBLE_ON_SCREEN`, blank, `OVERFLOW_SCROLLED_AWAY`, blank, `OVERFLOW_BOTTOM_VISIBLE`, blank, a whitespace line, blank, `BELOW_THE_FOLD`, then the `LAYOUT` lines listed in the table notes. `frame.html` was served 200. Favicon 404 is unused.
- Worked, before the Part A commit: `uv run scripts/check.py` (exit 0). `uv run --with pytest --with pyyaml --with jsonschema pytest -q` (11 passed). Runtime pytest failed once on `test_browser_runtime[chromium]` because Playwright Chromium was not cached. `uv run --project runtimes/browser playwright install chromium` installed Chrome for Testing 153.0.8010.12. Re-run: 12 passed in 22.51s.
- Worked, after read 2.0.0: `uv run scripts/check.py` (exit 0). Catalog pytest 11 passed. `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` (14 passed in 27.63s). The new tests are `test_viewport_offsets` and `test_read_viewports_scroll_and_restore`.
- Worked: `uv run --project runtimes/browser python /tmp/measure_vision.py`. Read returned `page_height` 2793, `viewport_height` 800, `truncated` false, four viewports at `scroll_y` 0, 800, 1600, 1993. Content block types were text, then text/image pairs. Labels were `viewport N of 4, scroll_y ...`. Image mime type `image/png`. The four PNGs were inspected for the vision column. Viewport 0 shows `VISIBLE_ON_SCREEN`, `OVERFLOW_BOTTOM_VISIBLE`, `OPEN_SHADOW_TEXT`, `IFRAME_SAME_ORIGIN`, and `CANVAS_ONLY_TEXT`. It does not show `OVERFLOW_SCROLLED_AWAY`, `DISPLAY_NONE_TEXT`, or `VISIBILITY_HIDDEN_TEXT`. Viewports at `scroll_y` 1600 and 1993 show `BELOW_THE_FOLD`.
- Worked, launcher stubs: `uv run --with pytest --with pyyaml --with jsonschema pytest -q tests/test_browser_launcher.py tests/test_check.py` (23 passed).
- Worked, after the launcher and again before the Part C commit: `uv run scripts/check.py` (exit 0). `uv run --with pytest --with pyyaml --with jsonschema pytest -q` (23 passed). `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` (22 passed in 26.44s). That includes `test_smoke_engine.py`.
- Not run: a real image build. `docker` and `podman` are not on PATH. `smoke_container.py` would exit 2 here. The workflow job is the build.
- Failed on GitHub Actions check job 38424819685: `test_podman_when_docker_is_missing` ran real `docker run` (pull access denied). `test_podman_shim_without_podman_binary_keeps_docker_command` ran real podman (`/etc/containers/registries.conf`). Cause: test PATH was `stubdir:/usr/bin:/bin`.
- Worked after the PATH isolation: `uv run --with pytest --with pyyaml --with jsonschema pytest -q tests/test_browser_launcher.py` (13 passed).
- Worked after `--build` exits without starting the server: `uv run scripts/check.py` (exit 0). Catalog pytest 25 passed in 1.44s. Runtime pytest 22 passed in 29.67s.

Checked:
- A. Fixture `tests/fixtures/browser_read/read_visibility.html` plus `frame.html`. Table above. Evidence is the `inner_text` run quoted in Commands.
- 1. `catalog/browser/read.yaml` version `2.0.0`. `session.read` scrolls from the top, one viewport at a time, and restores. `test_read_viewports_scroll_and_restore` passed: a page taller than 10 viewports returns 10 with `truncated` true, `max_viewports` 30 returns every viewport with `truncated` false, and both the document and an inner overflow scroller restore a green mid-page pixel after the capture (first PNG red, last PNG blue).
- 2. The same test asserts each image block is `type` image, `mime_type` `image/png`, preceded by `viewport N of M, scroll_y ...`, and `structuredContent` matches the manifest. The fixture re-run printed those labels.
- 3. `catalog/browser/screenshot.yaml` is still `1.0.0` (manifest text unchanged). `test_browser_runtime` asserts the screenshot result has one image block whose bytes are the structured PNG.
- 4. `when_to_use` on `browser.read` says to look at the screenshots the way a person looks at the screen, and to call again with a higher `max_viewports` when `truncated` is true.
- 5. `test_viewport_offsets` and `test_read_viewports_scroll_and_restore` passed. Literal scroll offsets, the cap of 10, PNG bytes, and restored colors are asserted.
- 6. `runtimes/browser/bin/agent-tools-browser` starts with `#!/bin/sh`. `sh -n` passed inside `test_launcher_is_posix_sh`. Stub tests cover explicit `docker` and `podman`, `docker info` failure, missing `docker`, and a Podman shim with and without a `podman` binary.
- 7. Stub runs assert Podman `:U` and `host.containers.internal`, Docker without `:U`, `host.docker.internal`, and `--add-host=host.docker.internal:host-gateway` on Linux only. `AGENT_TOOLS_NOTIFY_WEBHOOK` overrides the URL built from `AGENT_TOOLS_NOTIFY_PORT`. Ports and `AGENT_TOOLS_IMAGE` are asserted.
- 8. `test_build_flag_only_builds` asserts one `podman build -f <repo>/runtimes/browser/Containerfile -t <image> <repo>` line and no `run`.
- 9. `smoke_container.py` runs the launcher with `--build`, then starts it with `args=[]`. It accepts `--engine`. `test_smoke_engine.py` covers that split, exit 2, and the same engine choice.
- 10. `.github/workflows/check.yml` job `smoke` on `ubuntu-latest` runs `smoke_container.py --engine docker`. The `check` job is unchanged aside from a comment. This machine did not run that job.
- 11. `runtimes/browser/README.md` registers `__REPO_ROOT__/runtimes/browser/bin/agent-tools-browser` for Grok Build, Claude Code, and OMP. Env vars are named. Launcher stub tests passed.
- 12. `uv run scripts/check.py` exit 0. Catalog pytest 25 passed in 1.44s. Runtime pytest 22 passed in 29.67s. `browser.read` is `2.0.0`. The other browser manifests stay `1.0.0`.
- 13. Displays X1, X2, X10, and X13 remain. X16 was already up from another session and was left alone. No Chrome, Xvfb :200, or container from this task remains.

Next action:
- None. The PATH isolation and the `--build` exit are committed locally. Not pushed.

Unresolved (non-blocking):
- This machine has no docker or podman, so the image was not built here. The `smoke` GitHub Actions job is the build. It runs when the branch is pushed.
