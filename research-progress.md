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
- Tree was clean at 348be3e. No docker or podman on PATH.
- Interaction manifests, runtime, and fixture test are written. `test_page_interactions` passed. Full suite not re-run yet. Ports and handoff 1.1.0 are not started.

Decisions:
- Interaction tools append after the existing four in catalog order.
- Point targets scroll with the same scroller rule as browser.read 2.0.0, then act in viewport CSS pixels.
- Successful actions return url, title, scroll_y, viewport {width, height}, and a base64 PNG, plus an MCP image block labeled `after <tool>, scroll_y N`.
- Error results stay `{code}` only.
- Launcher treats unset, empty, 0, and auto as a free host port. Explicit ports are the published host ports. The container keeps listening on 8765 and 8766. `AGENT_TOOLS_PUBLIC_HANDOFF_PORT` and `AGENT_TOOLS_PUBLIC_NOVNC_PORT` are what the posted URL and noVNC frame use.
- Container name is `agent-tools-browser-<pid>-<random>`, with `--rm` and a trap that stop/rm on INT, TERM, HUP, and EXIT. The engine is not exec'd, so the trap runs when stdin closes.
- handoff 1.1.0 adds optional `done_when` and output `completed_by`. Auto match sets `reason` to `auto_detected`. `completed_by` is user, auto, timeout, or cancelled.
- Avoid the word "should" in manifests.

Files changed:
- None yet.

Commands that worked / failed:
- None yet for this brief.

Next action:
- Add the five interaction manifests, runtime, and fixture tests. Then per-session ports. Then handoff 1.1.0. Run acceptance and commit locally in small commits.

Unresolved (non-blocking):
- This machine has no docker or podman, so the image will not be built here. The GitHub Actions smoke job is the build.
