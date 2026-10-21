# Checkpoint

Objective:
Brief 7. Add a browser auth-handoff policy skill, a pasteable `## Browser tools` snippet, and a root README "Registering with agents" section. Commit on main. Do not push. Do not commit *.txt files. No secrets.

Frozen acceptance:
A1. `skills/browser-auth-handoff/SKILL.md` has frontmatter `name` and `description`. On 401/403, a login or SSO redirect, or a sign-in form for a service a human can sign into, the agent opens the web UI in the agent browser (Grok `browser` server tools; OMP `mcp__agent_browser_*`), calls handoff with a reason and `done_when` (URL regex or selector) when possible, waits, then continues. Timeout or cancelled is reported. OMP's built-in browser stays the default for quick unauthenticated browsing. "agent browser" in the prompt forces this runtime.
A2. `docs/agent-instructions/browser.md` is a short (~25 line) snippet with heading `## Browser tools` and the same rules, for `~/.grok/AGENTS.md` and `~/.omp/agent/AGENTS.md`.
A3. Root README section "Registering with agents": Grok `~/.grok/config.toml` `[mcp_servers.browser]` uses the launcher. OMP `~/.omp/agent/mcp.json` server `agent_browser`, type stdio, launcher command, env `PATH` plus `AGENT_TOOLS_CONTAINER_ENGINE=podman`, `timeout` 0, no pinned handoff or noVNC ports, notify port optional. Snippet install says replace an existing `## Browser tools` section or append.
A4. `scripts/check.py` validates skill frontmatter when `skills/` exists, and a test covers a failing skill.
A5. `uv run scripts/check.py` exits 0. Catalog pytest and runtime pytest pass.
A6. One commit (or a few) on main, local only. No `*.txt` and no secret values.

Current state:
- Acceptance rows A1–A5 are green. A6 is the local commit after this checkpoint rewrite.
- OMP server name in both READMEs is `agent_browser`. Grok stays `[mcp_servers.browser]`.

Decisions:
- Policy stays in the skill. The snippet is the required paste copy. The README tells how to register and how to install the snippet.
- `check.py` skips skill checks when `skills/` is absent, so catalog fixtures stay valid.
- OMP `timeout` 0 is the client timeout in milliseconds (disabled). The handoff tool's `timeout` input stays a positive number of seconds.
- Example `PATH` is `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` so `podman` resolves on Apple Silicon. No handoff, noVNC, or notify port is set in the example. Notify stays optional in prose.
- The runtime README OMP example uses the same `agent_browser` entry so it does not contradict the root README.

Files changed:
- skills/browser-auth-handoff/SKILL.md
- docs/agent-instructions/browser.md
- README.md
- runtimes/browser/README.md
- scripts/check.py
- tests/test_check.py
- research-progress.md

Commands that worked / failed:
- `uv run scripts/check.py` exit 0.
- Catalog pytest 53 passed in 6.69s.
- Runtime pytest first run: 24 passed, `test_browser_runtime[chromium]` failed because Playwright Chromium 1243 was not on disk. After `uv run --project runtimes/browser playwright install chromium`, 25 passed in 42.86s.
- Displays after the suite: X1, X2, X5, X13, X16. No test Xvfb left. Pre-existing Chrome and Xvfb were not killed. docker and podman are not on PATH, so no container was started.

Next action:
none

Unresolved (non-blocking):
- This machine has no docker or podman, so the image was not built here. The GitHub Actions smoke job is the build.
- The display list no longer includes X10 and now includes X5. This task did not start or stop those displays.
