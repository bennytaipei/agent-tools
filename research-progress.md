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
- Interaction tools are d3a051f. Per-session ports are edbcfc1. Handoff 1.1.0 is in the working tree, ready to commit.
- Acceptance rows below are green.

Decisions:
- Interaction tools append after the existing four in catalog order.
- Point targets scroll with the same scroller rule as browser.read 2.0.0, then act in viewport CSS pixels.
- Successful actions return url, title, scroll_y, viewport {width, height}, and a base64 PNG, plus an MCP image block labeled `after <tool>, scroll_y N`.
- Error results stay `{code}` only.
- Launcher treats unset, empty, 0, and auto as a free host port. Explicit ports are the published host ports. The container keeps listening on 8765 and 8766. `AGENT_TOOLS_PUBLIC_HANDOFF_PORT` and `AGENT_TOOLS_PUBLIC_NOVNC_PORT` are what the posted URL and noVNC frame use. `data-novnc-port` stays the bound listening port.
- The launcher backgrounds the engine and redirects stdin from a duplicated fd so MCP stdio still reaches the container and the trap can run.
- Container name is `agent-tools-browser-<pid>-<random>`, with `--rm` and a trap that stop/rm on INT, TERM, HUP, and EXIT. `--build` still execs.
- handoff 1.1.0 adds optional `done_when` and required output `completed_by`. Auto match sets `reason` to `auto_detected`. `completed_by` is user, auto, timeout, or cancelled. The page banner is exactly "Sign in or finish the step, then click Done." Done and Cancel use position fixed and a 28px font.
- Avoid the word "should" in manifests.

Files changed:
- d3a051f: five interaction manifests, interact.py, session/server/catalog/validate, fixture, tests.
- edbcfc1: launcher, public handoff ports, launcher tests, both READMEs, smoke auto ports.
- Handoff 1.1.0: catalog/browser/handoff.yaml, handoff.py, session.py, server.py, runtime tests, test_check.py, runtime README.

Commands that worked / failed:
- A1. `uv run scripts/check.py` exit 0 after handoff.yaml 1.1.0.
- A2. Catalog pytest 47 passed.
- A3. Runtime pytest 25 passed in 37.11s, including test_page_interactions, test_handoff_auto_detects_without_a_click, test_handoff_rejects_invalid_done_when_regex, manual Done, Cancel, and timeout.
- A4. `uv run --with pytest pytest tests/test_browser_launcher.py -q` 36 passed in 4.85s, including test_concurrent_launches_get_distinct_ports and test_trap_cleanup_on_term. `sh -n` passes inside test_launcher_is_posix_sh.
- A5. tests/test_check.py asserts click, type, scroll, select, and wait_for at 1.0.0, handoff at 1.1.0, navigate and screenshot at 1.0.0, read at 2.0.0. That test passed in the catalog run.
- A6. Root README and runtimes/browser/README.md Grok and OMP examples set only the launcher command. Both explain optional `AGENT_TOOLS_HANDOFF_PORT` and `AGENT_TOOLS_NOVNC_PORT` pinning, and that empty, 0, and auto pick a free port.
- A7. `.github/workflows/check.yml` smoke job still runs `smoke_container.py --engine docker`. The smoke script pops handoff and noVNC port env vars. `test_smoke_uses_the_launcher` passed.
- A8. After the runtime suite, displays are X1, X2, X10, X13, X16. No Chrome process from this task. docker and podman are not on PATH, so no container was started.

Next action:
none

Unresolved (non-blocking):
- This machine has no docker or podman, so the image was not built here. The GitHub Actions smoke job is the build.
