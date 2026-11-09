# Checkpoint

Objective:
Brief 12. The handoff bar is fine, but the remote screen is blank because view.html's noVNC module graph 404s (`vendor/pako`). Link every tree that graph needs. One local commit. Do not push. Do not stop the NABU VM (OMP pid 30858).

Frozen acceptance (brief 12):
P1. websockify's web root links everything view.html imports. At least `vendor/`. Idempotent, same as the `core` link.
P2. Confirmed core does not import `app/` or `include/` on this host's noVNC 1.6.0 (`/usr/share/novnc`) or on noVNC 1.3.0 (Debian bookworm image package, same `../vendor/pako` paths).
P3. Bar design unchanged (one 40px bar, escaped reason, Done/Cancel posts).
P4. Runtime test opens a real handoff page in Playwright Chromium, waits, and asserts no 404s for the iframe's resources and a canvas with nonzero size scaled to the iframe (width <= iframe width, and width or height within 2px of the iframe). That test fails on 15e5f9e and passes after the fix.
P5. `uv run scripts/check.py`, repo pytest, and runtime pytest pass. One local commit. Print BRIEF12 DONE.

Current state:
- Local main is 15e5f9e. Brief 10 is 599e8f3. Poll-error fix is 9f07010. Auth store is 793d890. Slim page is 15e5f9e.
- Host noVNC is 1.6.0. core/inflator.js and core/deflator.js import `../vendor/pako/lib/zlib/{inflate,deflate,zstream}.js`. Pako then imports siblings under `vendor/pako` only.
- Bookworm novnc 1:1.3.0-1 (container image) installs the same `/usr/share/novnc/vendor/pako/lib/zlib/` files. Upstream v1.3.0 core imports only that vendor tree from outside core.
- `_novnc_web_root` now walks core/rfb.js and symlinks each top-level tree it reaches. On this host that is `vendor` plus `core`.
- The new viewer test failed on the unfixed tree: 404s for inflate.js, deflate.js, and zstream.js. After the link it passed in 4.70s.
- check.py exited 0. Repo pytest 66 passed. Runtime pytest 41 passed in 115s.

Decisions:
- Walk view.html's module graph from `core/rfb.js` and symlink each top-level directory it reaches under `/usr/share/novnc`, plus `vendor` when that directory exists. Do not link `app/` or `include/`.
- Do not bump handoff past 1.1.1. Do not change the bar CSS or button markup.
- Do not push. NABU deploy stays with the outer agent.

Files changed:
- runtimes/browser/agent_tools_browser/handoff.py
- runtimes/browser/tests/test_runtime.py
- runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- Host scan: only `../vendor/pako` leaves core.
- Fetched noVNC v1.3.0 tarball. Same vendor imports. No app/include imports from core.
- Bookworm file list includes `/usr/share/novnc/vendor/pako/lib/zlib/inflate.js` and the matching deflate and zstream files.
- Viewer test on 15e5f9e failed: 404 inflate.js, deflate.js, zstream.js.
- Viewer test after the link passed in 4.70s.
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 41 passed in 115s.

Next action:
Commit brief 12 locally. Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Push and NABU verify stay with the outer agent.
- Linux cannot run macOS open or osascript.
