# Checkpoint

Objective:
Brief 14. On NABU with b3fbd57, every verify script failed because `kind` is a reserved AppleScript term (`Can't set kind to "app". (-10006)`). The Mac screen was locked, System Events timed out, and the listener opened Safari, then Chrome, then Firefox. Fix the script, wait out a locked screen, and stop after two verify timeouts. Local commit only. Do not push. Do not stop the NABU VM (OMP pid 30858).

Frozen acceptance (brief 14):
P1. AppleScript `set` targets in the listener scripts are not reserved terms. `kind` is renamed. Audit name, class, id, text, file, item, window, process, result, version, path, size, date, folder. `target` is not a language keyword; still prefix it.
P2. A test parses _VERIFY_SCRIPT, _FRONTMOST_SCRIPT, and the other listener scripts and rejects `set <denied-word> to`. If osascript exists, run the verify script and assert stderr has no -10006 or -2741. Skip that run when osascript is absent.
P3. Before any open, detect a locked screen with `ioreg -n Root -d1 -a` (CGSSessionScreenIsLocked via plistlib) or `lsappinfo front` containing loginwindow. If locked, log `screen locked; waiting for unlock`, poll every 2s until unlock or expires_at, and do not open while locked. Log on give-up. Notification stays immediate. 204 stays first.
P4. Two consecutive verify timeouts stop fallbacks. Log `verify unavailable; stopping after <app>`. At most two browsers are opened in that case.
P5. Fake-runner tests: locked then unlocked opens only the default and verifies; locked until expiry opens nothing and logs give-up; System Events timeouts open at most two browsers.
P6. README updated.
P7. check.py, repo pytest, and runtime pytest pass. One local commit. Print BRIEF14 FINISHED.

Current state:
- Local main is b3fbd57. The listener prefixes AppleScript variables, waits out a locked screen, and stops after two System Events verify timeouts.
- macOS notify tests: 19 passed, 1 skipped (osascript is not installed) in 7.21s.

Decisions:
- Prefix script variables (`qKind`, `qTarget`, `procList`, `qProc`). Denylist matches the brief. `target` is not reserved; prefix it anyway.
- Lock wait uses monotonic time based on seconds until expires_at so tests can fast-forward sleep.
- A missing or failed lock probe means unlocked, so this Linux host does not block.
- A verify timeout is TimeoutExpired or stderr timeout/-1712. A non-timeout miss resets the consecutive counter.
- Do not push. NABU deploy stays with the outer agent.

Files changed:
- runtimes/browser/contrib/macos_notify.py
- runtimes/browser/tests/test_macos_notify.py
- runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests/test_macos_notify.py -q` 19 passed, 1 skipped in 7.21s.
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 51 passed, 1 skipped in 101.49s.

Next action:
Commit brief 14 locally. Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Push and NABU verify stay with the outer agent.
- Linux cannot run osascript. The compile/run guard skips here if osascript is missing.
