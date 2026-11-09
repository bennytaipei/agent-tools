# Checkpoint

Objective:
Brief 11. Logins must survive container exit, restarts, and concurrent sessions. Local commits only. Then brief 9 (elegant handoff page) as its own commit. Do not push. Do not stop the NABU VM (OMP pid 30858).

Root cause (measured on NABU, Nov 9, do not re-investigate):
- One named volume `agent-tools-browser-profile` is the Chromium user-data-dir. Containers are `--rm`.
- `_stop_browser` reaps Chrome before `context.close()` (0.5 s). Chromium flushes cookies on a ~30 s timer, so a session ended after ~3 s loses a Max-Age cookie; a session left up 40 s keeps it.
- The launcher runs `podman stop -t 1` and the server has no SIGTERM handler, so aclose often never runs.
- Session cookies (no Expires) never survive a fresh container.
- A second container on the same profile takes SingletonLock. Two Chromiums then write one Cookies DB.

Frozen acceptance (brief 11):
S1. Auth store at `{profile}/agent-tools-auth.json` (Playwright storage_state). Atomic tmp+rename under fcntl on `{profile}/agent-tools-auth.lock`. Merge: cookies keyed by (name, domain, path); this context's values win; keys it loaded or saw and no longer has are deleted; other keys stay. Drop expired. Same for localStorage per origin. IndexedDB if `storage_state(indexed_db=True)` works.
S2. Save after each tools/call (at most once per 2 s), immediately when handoff ends, every 15 s while the browser is up, and on shutdown before the browser is killed.
S3. Restore cookies (including session cookies) and localStorage right after the context starts.
S4. Non-blocking fcntl on `{profile}/.agent-tools-primary.lock`. Primary clears stale SingletonLock/SingletonCookie/SingletonSocket and uses the profile dir. Secondary uses a private temp profile, seeded only from the auth store. Log the role.
S5. SIGTERM/SIGINT/SIGHUP run aclose. Order: save, context.close (5 s), playwright.stop, then reap. Launcher closes container stdin and waits, then `stop -t 10`.
S6. No tool-contract change. Document in runtimes/browser/README.md.
S7. Failing runtime tests first: persistent cookie after immediate close; session cookie after restart; SIGTERM writes the store; two sessions, one primary, secondary cookie survives, logout deletes, unrelated cookie kept; stale SingletonLock does not block primary. Launcher tests expect the stop grace.
S8. check.py, repo pytest, runtime pytest pass. Local commit. Print BRIEF11 DONE.
S9. Own commit, separate from the auth store: a done_when evaluation error resets consecutive_true and does not set seen_false. Regression: true, then an evaluation error, then true again, must not auto-complete.

Current state:
- Brief 10 is local commit 599e8f3. Do not amend it.
- S9 is local commit 9f07010. A done_when poll error no longer sets seen_false.
- Auth store is implemented. check.py exited 0, repo pytest 66 passed, runtime pytest 39 passed in 91s.
- Stale SingletonLock symlinked to otherhost-12345 did block Chromium until the primary unlinks SingletonLock, SingletonCookie, and SingletonSocket.
- SIGTERM: cancelling MCP leaves the stdio task group blocked on stdin. aclose runs, then os._exit, so the process does not wait on the client.
- Red run: `pytest runtimes/browser/tests/test_auth_persist.py` 5 failed in 78.87s. Persistent and session cookies were absent on the next session. SIGTERM left no agent-tools-auth.json. The second session's navigate failed (two Chromiums, one profile). A SingletonLock symlink to otherhost-12345 blocked launch (process_singleton_posix.cc: the profile appears to be in use on another computer). Launcher tests still see `stop -t 1`.
- The concurrent test as written expects a logout in the secondary to stick while the primary still holds that cookie. That contradicts the merge rule. Rewrite it so the overlapping primary does not delete the secondary's cookie, and a later solo session performs the logout.
- Stale foreign-host SingletonLock does block Chromium. Primary must unlink SingletonLock, SingletonCookie, and SingletonSocket before launch.

Decisions:
- The auth file lives on the profile directory the launcher already mounts, not a second volume.
- Secondary markers must not include the shared profile path, or reap would kill the primary Chrome. Secondary gets its own state dir under the temp profile.
- After each save, the delete baseline is this context's current keys, not the merged file.
- localStorage origins missing from the current storage_state are not observed, so they are not cleared.
- Display dialog and handoff matching rules from brief 10 stay as they are, except S9 (poll errors are unknown, not false).
- Launcher grace is about 4s, only while RUN_PID is set, so happy-path tests do not sleep. `stop -t 10`.
- Do not push. NABU deploy stays with the outer agent.

Files changed:
- research-progress.md
- tests/test_browser_launcher.py (stop -t 10)
- runtimes/browser/tests/test_auth_persist.py
- runtimes/browser/tests/test_runtime.py (S9, already in 9f07010)
- runtimes/browser/agent_tools_browser/auth_store.py
- runtimes/browser/agent_tools_browser/session.py
- runtimes/browser/agent_tools_browser/server.py
- runtimes/browser/bin/agent-tools-browser
- runtimes/browser/README.md

Commands that worked / failed:
- Red, before the fix: auth persist 5 failed in 78.87s (cookies gone, no auth file after SIGTERM, second session navigation_failed, SingletonLock timeout). Launcher stop tests 3 failed (`stop -t 1`).
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 39 passed in 91s.

Next action:
Commit the auth store locally. Then read /workspace/agent-tools-brief-9.md and implement the handoff page as its own commit.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Brief 9 (elegant handoff page) waits until brief 11 is committed.
- Push and NABU verify stay with the outer agent.
