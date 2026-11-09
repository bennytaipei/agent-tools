# Checkpoint

Objective:
Brief 15. On NABU with abdae45, every handoff opens Safari then Chrome because System Events verify times out (-1712) and a failed verify still triggers fallback. Open exactly one browser per handoff. Fall back only when `open` itself fails. Prefer lsappinfo for frontmost pick. Local commit only. Do not push. Do not stop the NABU VM.

Frozen acceptance (brief 15):
P1. Exactly one browser open per successful handoff. A verify timeout/permission error never opens a second browser.
P2. Pick order: AGENT_TOOLS_HANDOFF_BROWSER, else frontmost known browser via lsappinfo, else default http browser.
P3. Fallback to another browser only when `open` returns non-zero.
P4. System Events removed from the open-path decision (or log-only with no behavioral effect). Locked-screen wait and notification stay.
P5. Tests: single open when verify unavailable; fallback only on open failure; frontmost-browser pick. Old verify-fallback tests rewritten or removed.
P6. README updated.
P7. check.py, repo pytest, and runtime pytest pass. One local commit. Print BRIEF15 FINISHED.

Current state:
- Local main is abdae45. Listener opens one browser. Fallback only when the URL open returns non-zero. Frontmost pick uses lsappinfo. System Events verify loop is gone. Notify tests: 18 passed.

Decisions:
- Root cause measured on NABU log: open Safari exit 0, System Events -1712, then open Chrome. Inferred cause: LaunchAgent lacks Automation for System Events.
- Brief 14's two-timeout cap is not enough; successful open must end the attempt.
- Delete the System Events verify loop. A second `open` of the same app still brings it forward. Fall back once, and only after the URL `open` returns non-zero. An `open` timeout does not fall back.
- Pick order: HANDOFF env, else lsappinfo front then info when the name is a known browser, else the default http bundle.

Files changed:
- runtimes/browser/contrib/macos_notify.py
- runtimes/browser/tests/test_macos_notify.py
- runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests/test_macos_notify.py -q` 18 passed in 6.51s.
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 50 passed in 105.70s.

Next action:
Commit brief 15 locally. Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- Stuck osascript on NABU (pid 18320) to clean up at deploy.
- NABU ahead 20 of origin with untracked .build/.machine/.smoke logs; deploy via pull or copy file.
