# Checkpoint

Objective:
Brief 6. Add browser interaction tools (click, type, scroll, select, wait_for), per-session host ports with container cleanup, and handoff 1.1.0 (banner, Done/Cancel, done_when). Commit on main. Do not push. Do not commit *.txt files.

Frozen acceptance:
A1. `uv run scripts/check.py` exit 0.
A2. Catalog pytest all pass (`uv run --with pytest --with pyyaml --with jsonschema pytest -q`).
A3. Runtime pytest all pass (`uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q`), including new interaction and handoff tests.
A4. Launcher tests stubbed (no real docker/podman), including concurrent launch; `sh -n` passes.
A5. Five new manifests at 1.0.0; handoff at 1.1.0; navigate, screenshot, and read versions unchanged.
A6. Root README and runtimes/browser/README.md: Grok (`~/.grok/config.toml`) and OMP (`~/.omp/agent/mcp.json`) examples do not pin ports. Optional pinning is explained.
A7. CI workflow still includes the docker smoke job. Smoke script works with auto ports.
A8. No leftover Chrome, Xvfb, or containers this task started. Displays before this task: X1, X2, X10, X13, X16.

Current state:
- Interaction tools committed as d3a051f. Full catalog and runtime suites passed before that commit (check.py 0, catalog 41, runtime 23).
- Per-session ports are implemented and stub-tested: launcher picks free host ports, unique names, trap stop/rm, public port URL. `tests/test_browser_launcher.py` 36 passed. `sh -n` passed. `test_fixed_ports_and_public_host` passed. Handoff UX 1.1.0 is not started.

Decisions:
- Interaction tools append after the existing four in catalog order.
- Point targets scroll with the same scroller rule as browser.read 2.0.0, then act in viewport CSS pixels.
- Successful actions return url, title, scroll_y, viewport {width, height}, and a base64 PNG, plus an MCP image block labeled `after <tool>, scroll_y N`.
- Error results stay `{code}` only.
- Launcher treats unset, empty, 0, and auto as a free host port. Explicit ports are the published host ports. The container keeps listening on 8765 and 8766. `AGENT_TOOLS_PUBLIC_HANDOFF_PORT` and `AGENT_TOOLS_PUBLIC_NOVNC_PORT` are what the posted URL and noVNC frame use. `data-novnc-port` stays the bound listening port.
- Container name is `agent-tools-browser-<pid>-<random>`, with `--rm` and a trap that stop/rm on INT, TERM, HUP, and EXIT. The engine run is backgrounded so the trap can fire. `--build` still execs.
- handoff 1.1.0 adds optional `done_when` and required output `completed_by`. Auto match sets `reason` to `auto_detected`. `completed_by` is user, auto, timeout, or cancelled.
- Avoid the word "should" in manifests.

Files changed:
- Committed in d3a051f: five manifests, interact.py, session/server/catalog/validate, fixture, tests, catalog test list.
- Ports slice: launcher, handoff public ports, launcher tests, both READMEs, smoke auto ports.

Commands that worked / failed:
- `uv run scripts/check.py` exit 0 (before the ports edit).
- Catalog pytest 41 passed. Runtime pytest 23 passed (includes test_page_interactions), before ports.
- `sh -n runtimes/browser/bin/agent-tools-browser` passed inside launcher tests. Launcher pytest 36 passed, including concurrent launch and SIGTERM cleanup.
- `test_listen_settings_*` and `test_fixed_ports_and_public_host` passed. Smoke engine assertion passed.

Next action:
- Implement handoff 1.1.0 (banner, Done/Cancel, done_when, completed_by). Run full acceptance. Commit locally. Do not push.

Unresolved (non-blocking):
- This machine has no docker or podman, so the image will not be built here. The GitHub Actions smoke job is the build.
