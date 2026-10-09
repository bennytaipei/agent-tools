# Checkpoint

Objective:
Make the browser runtime deployable as a container on Benny's Mac (Apple Silicon, macOS, Podman with a linux/arm64 VM). The MCP harness launches it with `podman run -i` over stdio. The handoff page and noVNC are reachable from the Mac's browser at 127.0.0.1. Handoff notifies Benny with a macOS notification that opens the handoff page. Commit on main. Do not push.

Frozen acceptance (one row per criterion):
1. Browser choice is configurable by env var. The default stays system Chrome (channel chrome), so current box behavior is unchanged. A documented env var selects Playwright's bundled Chromium, which works on linux/arm64 where Google Chrome does not exist. The behavior tests pass with both settings on this machine.
2. Ports and URL host are configurable by env var: a fixed handoff page port, a fixed noVNC/websockify port, a bind address (0.0.0.0 inside a container), and the public host used in the URL posted to the webhook (default 127.0.0.1). When unset, the current free-port behavior is unchanged. A test covers fixed ports, and asserts that the posted url uses the configured public host and port.
3. runtimes/browser/Containerfile builds a multi-arch-safe image (it must build on linux/arm64 and linux/amd64). It contains Xvfb, x11vnc, websockify, noVNC, uv, the runtime, and Playwright Chromium with its OS deps. It uses the Chromium setting from row 1. It runs as a non-root user. The entrypoint is the MCP stdio server. The profile directory is a volume path. No secrets are baked in.
4. If podman or docker is available on this machine, build the image here (amd64). Prove it with a scripted smoke test that runs it with `-i`, sends MCP initialize plus tools/list over stdio, and gets the four browser tools back, then calls browser.navigate on a page served from the host and gets the URL back. If no container engine is available here, say so in research-progress.md and keep the script for the Mac.
5. runtimes/browser/contrib/macos_notify.py is a stdlib-only HTTP listener bound to 127.0.0.1 on a configurable port. On POST of {reason, url, expires_at}, it shows a macOS notification (osascript) with the reason and opens url in the default browser (`open`), then returns 204. Unit-test the request handling with the osascript and open calls stubbed. No macOS needed for the test.
6. runtimes/browser/contrib/ has a launchd plist template for that listener, plus a doc section in runtimes/browser/README.md titled "Run on a Mac with Podman". It covers building the image, the exact `podman run -i --rm` command for MCP registration (published 127.0.0.1 ports, the named profile volume, and the webhook pointing at host.containers.internal), installing the listener, and registering with Grok Build, Claude Code, and OMP. Env vars appear by name. No secret values.
7. All existing checks and tests still pass: `uv run scripts/check.py`, the catalog pytest, and the runtime behavior tests. Catalog manifests change only if a real contract gap appears, with a version bump per the README.
8. This machine is shared and heavily loaded. Never kill processes you did not start. Clean up every process and container your tests start.

Current state:
- All eight rows are done on main. Not pushed.
- Row 1: `AGENT_TOOLS_BROWSER_CHANNEL` unset or `chrome` keeps system Chrome. `chromium` uses Playwright's bundled Chromium. The behavior suite passed for both in one pytest run.
- Row 2: `AGENT_TOOLS_HANDOFF_PORT`, `AGENT_TOOLS_NOVNC_PORT`, `AGENT_TOOLS_BIND_ADDRESS`, and `AGENT_TOOLS_PUBLIC_HOST`. Unset keeps free ports on 127.0.0.1. `test_fixed_ports_and_public_host` passed with bind `0.0.0.0`, public host `browser.example`, and fixed ports. The posted URL used that host and the handoff port.
- Row 3: `runtimes/browser/Containerfile` is in the tree. It was not built on this machine (see row 4).
- Row 4: podman and docker are not on PATH. `runtimes/browser/contrib/smoke_container.py` is kept for the Mac. Running it here printed `no container engine available; smoke test not run` and exited 2. The image was not built here.
- Row 5: listener and `test_macos_notify.py` passed with osascript and open stubbed.
- Row 6: plist `runtimes/browser/contrib/com.agent-tools.browser-notify.plist` and README section "Run on a Mac with Podman".
- Row 7: catalog manifests unchanged. check.py exit 0. Catalog pytest 11 passed. Runtime pytest 11 passed.
- Row 8: displays after the tests are still X1, X10, X13, X16, and X3. No Xvfb, x11vnc, websockify, or Chrome process from this runtime remains. No container was started.
- Commits on main, not pushed: 8bd9924, a9a6eb7, 0267b86, and this checkpoint.

Decisions:
- `AGENT_TOOLS_BROWSER_CHANNEL`: unset or `chrome` keeps system Chrome (`channel=chrome`, executable `/usr/bin/google-chrome`). `chromium` launches Playwright's bundled Chromium with no channel and no executable path. The image sets `chromium`.
- `AGENT_TOOLS_HANDOFF_PORT` and `AGENT_TOOLS_NOVNC_PORT`: unset keeps a free port. A set value is the listen port. x11vnc stays on a free localhost port.
- `AGENT_TOOLS_BIND_ADDRESS`: unset is `127.0.0.1`. The image sets `0.0.0.0`. `AGENT_TOOLS_PUBLIC_HOST`: unset is `127.0.0.1`. The posted URL and the page's noVNC iframe use the public host, because Benny's browser on the Mac loads them.
- Image defaults: handoff port 8765, noVNC port 8766, profile `/var/lib/agent-tools/profile`, user `agent` (uid 1000). No webhook is baked in. The entrypoint is the venv Python MCP server. uv 0.12.21 is copied from the multi-arch uv image.
- The Mac listener binds `127.0.0.1` and reads `AGENT_TOOLS_MACOS_NOTIFY_PORT` (default 8767). The container webhook in the runbook is `http://host.containers.internal:8767/`.
- OMP registration is `~/.omp/agent/mcp.json` (project file `.omp/mcp.json` uses the same shape). Grok Build is `~/.grok/config.toml`. Claude Code is a user-scoped stdio server.
- Catalog manifests stay at their current versions. No contract gap turned up.

Done earlier:
- Browser reference runtime on main at 63ce837. Manifests at 1.0.0. Headed system Chrome on a private Xvfb display, handoff via x11vnc, websockify, and `/usr/share/novnc`, webhook `AGENT_TOOLS_NOTIFY_WEBHOOK`. Behavior tests cover navigate, read, screenshot, handoff done/cancelled/timeout, and notify_failed. CI runs the catalog check only, because ubuntu-latest lacks the display stack.
- Manifest contract locked and four browser tools migrated. Draft teams.send_message at 0.1.0. Those commits are on main through ebf7959.

Files changed:
- .dockerignore
- .github/workflows/check.yml
- research-progress.md
- runtimes/browser/Containerfile
- runtimes/browser/README.md
- runtimes/browser/agent_tools_browser/handoff.py
- runtimes/browser/agent_tools_browser/processes.py
- runtimes/browser/agent_tools_browser/session.py
- runtimes/browser/contrib/com.agent-tools.browser-notify.plist
- runtimes/browser/contrib/macos_notify.py
- runtimes/browser/contrib/smoke_container.py
- runtimes/browser/tests/test_macos_notify.py
- runtimes/browser/tests/test_runtime.py

Commands that worked / failed:
- Worked: `uv run --project runtimes/browser playwright install chromium` (Chrome for Testing 153.0.8010.12 at ~/.cache/ms-playwright/chromium-1243).
- Worked: `uv run scripts/check.py` (exit 0).
- Worked: `uv run --with pytest --with pyyaml --with jsonschema pytest -q` (11 passed in 1.26s).
- Worked: `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` (11 passed in 23.46s). That run covered default Chrome, bundled Chromium, fixed ports with public host `browser.example`, and the macOS listener stubs.
- Recorded: `uv run --project runtimes/browser python runtimes/browser/contrib/smoke_container.py` printed `no container engine available; smoke test not run` and exited 2. `command -v podman` and `command -v docker` printed nothing, so the image was not built here.

Next action:
- None. Committed on main. Not pushed. The outer agent verifies and pushes.
- Human gate: build the image on the Mac with Podman and run the smoke script, then a live handoff where Benny logs in through the published noVNC page.

Unresolved (non-blocking):
- The Containerfile has not been built on this machine. The Mac smoke script is the build proof.
- Podman on macOS reaches the Mac via host.containers.internal. The listener binds 127.0.0.1, as specified.
