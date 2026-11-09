# Checkpoint

Objective:
Brief 13. On NABU, Safari is the default http handler and `open -b com.apple.safari` exits 0, but Safari stays in the background with 0 windows while Google Chrome is frontmost. The handoff URL never appears. Fix macos_notify.py only, plus tests and the runtime README. One local commit on main. Do not push. Do not stop the NABU VM (OMP pid 30858).

Frozen acceptance (brief 13):
P1. After each open, verify with System Events only (no app-specific AppleScript), `with timeout of 3 seconds`, whole check capped at about 4s. Poll about 250ms. Verified means that browser's process is frontmost and has at least one window.
P2. If verification fails, fall back, skipping apps already tried and apps not installed: frontmost app when it is Google Chrome, Arc, Microsoft Edge, Brave Browser, Firefox, or Safari; then Google Chrome, Arc, Microsoft Edge, Brave Browser, Firefox. Each fallback uses `open -a <App> <url>` then activates the same way. Stop at the first verified app.
P3. AGENT_TOOLS_HANDOFF_BROWSER (bundle id or app name) is tried first when set.
P4. Log every attempt and its verify result, including `verify app=Safari frontmost=False windows=0 -> fallback`.
P5. Notification and HTTP 204 stay. Send 204 before the slow open/verify work.
P6. Tests: verified default does not fall back; wedged default (0 windows) uses Chrome; env override is first; System Events timeout is not verified; nothing verified still returns 204 and logs the last attempt.
P7. Document in runtimes/browser/README.md.
P8. check.py, repo pytest, and runtime pytest pass. One local commit. Print BRIEF13 DONE.

Current state:
- Local main is 240196c (brief 12 vendor link). The listener now returns 204 after the notification, then verifies with System Events and falls back.
- macos notify tests: 15 passed in 5.23s, including the wedged-Safari, env-override, timeout, none-verified, and 204-before-open cases.

Decisions:
- Verify known bundles by process name (Safari, Google Chrome, Arc, Microsoft Edge, Brave Browser, Firefox). Unknown bundles use System Events' bundle identifier, ignoring case.
- Activate with `open -b` / `open -a`, not `tell application ... to activate` (that prompts for Automation).
- A System Events timeout (-1712 or subprocess timeout) ends that attempt as not verified.
- Installation probe for the static fallback list is `mdfind`. The frontmost app is already running, so it is not probed.
- This Linux host cannot run osascript. Tests use a fake command runner. NABU deploy stays with the outer agent.

Files changed:
- runtimes/browser/contrib/macos_notify.py
- runtimes/browser/tests/test_macos_notify.py
- runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests/test_macos_notify.py -q` 15 passed in 5.23s.
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 47 passed in 101s.

Next action:
Commit brief 13 locally. Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Push and NABU verify stay with the outer agent.
- Linux cannot run macOS open or osascript. Live Safari behavior is not executed here.
