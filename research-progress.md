# Checkpoint

Objective:
Brief 8. The browser MCP launcher heals itself when the Podman VM / engine is down, with no human triage (Benny, Nov 9: "make this fix permanent and automatic").

Root cause (measured on NABU, Nov 9 2026):
- OMP log omp.2026-11-09.30858.log: agent_browser failed 09:37:33.979 CST, reconnects failed 09:49:31, 09:50:07, 09:50:10, 09:50:13, final 09:50:19, all "MCP subprocess closed stdout before responding".
- macOS unified log: an osascript dialog (the launcher's `ask` prompt) opened before each failure and a button was clicked ~1-2s later (AppKit sendAction), e.g. dialog 09:37:31.82, click 09:37:33.95, osascript exit 09:37:34.04. No podman machine VM came up. Benny then ran `podman machine start` by hand (zsh history); guest booted 09:50:36.
- `podman machine list --format '{{.Name}} {{.Running}} {{.Default}}'` on podman 5.8.2 prints `podman-machine-default* true true`: the default machine's Name carries a trailing `*`.
- The launcher passes that name to `podman machine start`. `podman machine start 'podman-machine-default*'` -> "Error: podman-machine-default*: VM does not exist", rc 125, in ~0.1s. The launcher then exits before any MCP handshake, so OMP shows only "not connected".
- Falsify row run: inspect/start with the starred name fails (measured); the test stub never emits `*`, which is why tests passed.

Frozen acceptance (brief 8):
B1. Machine name from `machine list` has the trailing `*` stripped; test stub emits `*` like real podman 5.
B2. AGENT_TOOLS_AUTO_START_ENGINE default `always`; ask/never still work.
B3. After start (or when the machine is Running/Starting but info fails), wait until `<engine> info` succeeds, bounded by AGENT_TOOLS_ENGINE_START_TIMEOUT. A failed `machine start` (e.g. already starting by another launcher) still waits for info before failing.
B4. Launcher never closes stdout before handshake when engine/image is not usable: a stdlib-only (python3.9-compatible) degraded MCP server answers initialize + tools/list (from a checked-in tools JSON snapshot kept in sync with catalog/ by a test) and returns tools/call as isError with the real reason and the fix. Each tools/call retries engine bring-up; on success it starts the container, replays initialize, and proxies the rest of the session.
B5. Log file: ~/Library/Logs/agent-tools-browser.log (Darwin) or ${XDG_STATE_HOME:-~/.local/state}/agent-tools/browser.log; override AGENT_TOOLS_LOG_FILE. Timestamped decisions, engine command stderr, container stderr. Size-capped.
B6. Tests: engine down -> auto-started; never up -> handshake ok and tools/call reason; recovers -> next tools/call proxied ok. Existing 53 repo + 25 runtime tests stay green; check.py green.
B7. README docs updated. Pushed to main, CI green. Deployed to NABU and verified with VM really stopped (only if no live agent-tools container) or a fake-engine shim.

Current state:
- Brief 8 implemented on main, local commit only (do not push). check.py, repo pytest (66), and runtime pytest (25) are green. Launcher tests cover the starred machine name, default always, readiness wait, degraded MCP, recovery proxy, missing image, and the log file.
- NABU has a live agent-tools container (OMP session pid 30858), so do not stop the VM while it runs.

Decisions:
- Happy path unchanged: run the container directly when info succeeds and the image exists. Degraded server only on failed.
- python3 is present on NABU (/usr/bin/python3 works, Homebrew python3 on PATH) and macstudio (/usr/bin/python3 3.9.6). Without python3, the launcher logs and exits as before.
- Default AUTO_START is always. ask and never stay. --ensure-engine is the probe. AGENT_TOOLS_NO_DEGRADED=1 skips the degraded handoff.
- Machine list format adds {{.Starting}}. A trailing * is stripped. Starting or Running means wait, do not start.
- A non-zero machine start still waits for info. Unknown info failure (no stopped machine, not live) fails immediately rather than running the container.
- Degraded MCP is newline-delimited JSON-RPC, tools from runtimes/browser/tools.json, Python 3.9 stdlib only.

Next action:
Outer agent pushes main and, when the NABU container is not live, deploys and verifies with the Podman VM stopped. Do not push from this session.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
