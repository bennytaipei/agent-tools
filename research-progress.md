# Checkpoint

Objective:
Build a reference runtime for the four browser tools so a remote headless agent can call them. The manifests in catalog/browser/ are the contract and the single source of truth. A remote agent drives Chrome on an authenticated site. On a login wall it calls browser.handoff, Benny takes the same live browser over HTTP noVNC, logs in, clicks Done, and the agent continues with that session. Do not change manifests unless the implementation proves a contract gap. Work on branch browser-runtime. Do not push.

Frozen acceptance (one row per criterion):
1. The runtime lives under runtimes/browser/ as a Python package. Run it with `uv`, using pyproject dependencies or PEP 723 inline dependencies. Nothing gets installed globally.
2. It is exposed as an MCP server over stdio with exactly four tools: browser.navigate, browser.read, browser.screenshot, and browser.handoff. Each tool's name, description, and input schema are loaded from the catalog manifest at startup, not copied by hand.
3. The runtime validates every tool's inputs against the manifest's inputs schema and every result against its outputs schema. Failures return the manifest's declared error codes only. It never invents a code.
4. Chrome comes from Playwright using the installed system Chrome (/usr/bin/google-chrome, channel chrome), with a persistent profile directory from an env var. It runs headed on its own Xvfb display, so handoff shows the exact same browser. It must not reuse or disturb displays or ports already in use on this machine. Pick free ones at runtime.
5. browser.handoff(reason, timeout) starts x11vnc on the runtime's display and websockify with the bundled noVNC (/usr/share/novnc) on a free port, behind a random one-time token in the URL. It serves a small handoff page with the noVNC view plus Done and Cancel buttons. It notifies Benny by POSTing JSON {reason, url, expires_at} to the webhook in an env var (for example AGENT_TOOLS_NOTIFY_WEBHOOK). It blocks until Done, Cancel, or the timeout, then returns done, cancelled, or timeout. It stops x11vnc and websockify in every case, including errors. No env var or a failed POST means notify_failed, and the share is torn down.
6. No secrets in the repo. Credentials and webhook URLs come only from env vars and appear in docs by name only.
7. Behavior tests drive the real runtime against a local test HTTP server and a local webhook receiver, with no internet. navigate returns the final URL after a redirect. read returns text containing a literal string from the test page. screenshot returns base64 that decodes to PNG bytes. invalid_url and navigation_failed come back for bad input. Handoff returns done when the test POSTs Done, cancelled on Cancel, and timeout on a short timeout. In each handoff case, the noVNC port is closed afterwards and the webhook received {reason, url, expires_at}. notify_failed comes back when the webhook env var is unset. Every result validates against its manifest. Assert literal values.
8. runtimes/browser/README.md explains how to run the server, the env vars by name, and how a harness registers it as an MCP server.
9. scripts/check.py and the existing tests still pass. The new tests pass with one documented `uv run` command. CI runs the new tests too if they can run on ubuntu-latest. If they can't, say why in the workflow and keep the catalog check job green.
10. This machine is heavily loaded and shared with other agents' desktops. Never kill processes you did not start. Clean up every Xvfb, x11vnc, websockify, and Chrome process your tests start.

Current state:
- All ten acceptance rows pass. Catalog manifests stay at 1.0.0. No contract gap turned up, so no version bump.
- Branch browser-runtime is local. Do not push.
- After the behavior tests, displays in /tmp/.X11-unix are still X1, X10, X13, X16, and X3. No Xvfb, x11vnc, websockify, or Chrome process from this runtime remains.

Decisions:
- Branch is browser-runtime, cut from main tip ebf7959. Do not push.
- Manifests stay untouched. The implementation maps onto the existing error codes.
- MCP tool description is the manifest when_to_use string. input_schema and output_schema are the manifest inputs and outputs objects, loaded at startup.
- A schema rejection uses a code that manifest already declares: invalid_url for navigate, share_failed for handoff, no_page for read and screenshot. Unexpected operational failures use navigation_failed, share_failed, no_page, or notify_failed. Error results are MCP is_error with text {"code":"<declared>"}. Success results carry structured content that matches the outputs schema.
- format: uri is enforced with a checker this runtime adds, because the stock Draft 2020-12 checker does not implement uri. Navigate accepts only absolute http and https URLs.
- Chrome runs headed (channel chrome, executable /usr/bin/google-chrome) on a private Xvfb display chosen at runtime from 200–499. The server process DISPLAY is not changed, and the MCP client does not pass the host DISPLAY. Each run has a private X authority file. Cleanup signals only processes whose command line contains this run's profile or state path, or a pid this run recorded with a matching start time.
- Each navigate opens a new tab and closes the previous one. A refused connection leaves Chrome navigating to an error page, and that navigation was interrupting the next goto.
- Handoff posts {reason, url, expires_at} to AGENT_TOOLS_NOTIFY_WEBHOOK. The url field is the handoff page. websockify listens on its own free port, serves /usr/share/novnc, and accepts the VNC websocket only with the one-time token. x11vnc and websockify stop before the tool returns, including notify_failed and share_failed. An unset webhook returns notify_failed before Chrome starts. A failed POST starts the share, then tears x11vnc and websockify down.
- Browser behavior tests are not part of the catalog pytest run (root pytest.ini testpaths = tests). ubuntu-latest has no system Chrome, Xvfb, x11vnc, websockify, or /usr/share/novnc, so CI only runs the catalog check. The workflow comment records that and the local command.
- Env vars appear in docs by name only: AGENT_TOOLS_BROWSER_PROFILE, AGENT_TOOLS_NOTIFY_WEBHOOK, and optional AGENT_TOOLS_ROOT. The harness example has no env values.

Done earlier:
- Manifest contract locked (schema, checker, tests, CI) and four browser tools migrated to it. Draft teams.send_message at 0.1.0 proves a write tool with external auth. Manifests only. Those commits are local on main and were not pushed.

Files changed:
- .gitignore
- pytest.ini
- .github/workflows/check.yml
- research-progress.md
- runtimes/browser/README.md
- runtimes/browser/pyproject.toml
- runtimes/browser/uv.lock
- runtimes/browser/agent_tools_browser/ (catalog, handoff, processes, server, session, validate, package entry)
- runtimes/browser/tests/test_runtime.py

Commands that worked / failed:
- Failed, then fixed: the first behavior run returned navigation interrupted by chrome-error://chromewebdata/ after a refused connection. A follow-up that settled the tab on about:blank was then interrupted by about:blank. Each navigate now uses a fresh tab. The suite after that fix passed.
- Worked: `uv run scripts/check.py` (exit 0).
- Worked: `uv run --with pytest --with pyyaml --with jsonschema pytest -q` (11 passed in 1.22s).
- Worked: `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` (3 passed in 11.26s).

Next action:
- None. Leave the branch local. Do not push.

Unresolved (non-blocking):
- None.
