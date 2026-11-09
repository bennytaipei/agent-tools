# Checkpoint

Objective:
Brief 10. Stop browser.handoff from auto-completing on the login page, and make the macOS notify listener bring the default browser forward. Local commit only. Then brief 9 as a separate commit. Do not push. Do not stop the NABU VM (OMP pid 30858).

Root cause (measured, do not re-investigate):
- Bug A. Invicti SSO handoff auto-completed on the login form. url_matches ran re.search on the full page URL, so a returnUrl or redirect_uri query that contains the post-login path matched. selector_present matched the login form on the first poll. _await_handoff returned auto_detected on the first true poll, and any poll exception returned share_failed.
- Bug B. LaunchAgent com.agent-tools.browser-notify runs contrib/macos_notify.py, which ran `open <url>`. The default http handler is Safari. `open` events landed in Safari and nothing came to the front. The listener log only said "listening" because stderr is /tmp/agent-tools-browser-notify.log and requests were not logged.

Frozen acceptance (brief 10):
A1. url_matches is tested against scheme://host[:port]/path only. Query and fragment are ignored. Documented in catalog/browser/handoff.yaml and the READMEs.
A2. selector_present and text_present count only visible elements. text_present keeps substring semantics.
A3. Auto-complete only after the condition was false at least once in this handoff, then true on two consecutive polls about 1s apart. Already true at the first poll logs a warning and does not auto-complete. No new result field (schema additionalProperties false).
A4. Poll evaluation errors are no-match and reset the consecutive-true count. They do not end the handoff. share_failed stays for share failures and for a bad regex or selector rejected up front by prepare_done_when.
A5. If the context gained pages during the handoff, evaluate the most recently opened page that is still open, else self._page.
A6. handoff.yaml version 1.1.1. done_when describes the page after sign-in, not anything on the login page.
A7. skills/browser-auth-handoff/SKILL.md and docs/agent-instructions/browser.md: prefer url_matches on a post-login path; never a selector or text that exists on the login page.
A8. Regression tests (real Playwright, local pages) failed before the fix: query-string URL, SSO query URL, login selector, and hidden text auto-completed; selector `:::` returned share_failed; the SSO popup timed out. The login-to-/app/scans case already auto-completed on the first true poll.
B1. Log every request and every command (exit code and stderr, timestamped) to stderr. Launchd writes that to /tmp/agent-tools-browser-notify.log.
B2. Resolve the default http bundle from the LaunchServices secure plist, fallback com.apple.safari. Run `open -b <bundle> <url>` then `open -b <bundle>`. No osascript activate. Display dialog stays off unless AGENT_TOOLS_NOTIFY_DIALOG=1 and activation fails.
B3. AGENT_TOOLS_NOTIFY_BROWSER overrides the bundle.
B4. Tests in runtimes/browser/tests/test_macos_notify.py for bundle resolution, command list, logging, and override. No real `open`. Listener stays stdlib and 3.9-compatible.
B5. check.py, repo pytest, and runtime pytest pass. Local commit on main.

Current state:
- Brief 10 is ready to commit on main. New tests failed on the pre-fix code (recorded above), then passed after the fix.
- check.py exit 0. Runtime pytest 33 passed. Repo pytest 66 passed.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.

Decisions:
- Activation is a second `open -b <bundle>`, not `tell application id ... to activate`. Activate often prompts for Automation, which a background LaunchAgent cannot answer. This Linux host cannot run `open` or osascript; tests stub subprocess.run.
- Display dialog default off. A modal dialog from a LaunchAgent is noisy and often not visible on the graphical session. Opt-in with AGENT_TOOLS_NOTIFY_DIALOG=1 only after the second `open -b` returns non-zero. The notification (title "Browser handoff", body = reason) stays.
- Existing _handoff_auto_detects expected an already-true page to auto-complete immediately. That contradicts A3. It now navigates to /arm, which redirects to /landed, so each condition is false and then true.
- Chrome is launched with --disable-popup-blocking so an SSO window.open is visible to the handoff poll.
- tools.json was regenerated from catalog inputs. wait_for.yaml is unchanged.
- Do not push. NABU deploy stays with the outer agent.

Files changed:
- catalog/browser/handoff.yaml, runtimes/browser/tools.json, runtimes/browser/agent_tools_browser/session.py, runtimes/browser/agent_tools_browser/handoff.py
- runtimes/browser/contrib/macos_notify.py, runtimes/browser/tests/test_macos_notify.py, runtimes/browser/tests/test_runtime.py
- README.md, runtimes/browser/README.md, docs/agent-instructions/browser.md, skills/browser-auth-handoff/SKILL.md, tests/test_check.py

Commands that worked / failed:
- Pre-fix: the new handoff and notify tests failed (8 failed, 17 deselected).
- Post-fix: those tests passed (15 passed). Full runtime pytest 33 passed. check.py exit 0.
- Repo pytest failed once on selector_present missing from browser.md. Re-run after the wording fix.

Next action:
Commit brief 10 on main, then read /workspace/agent-tools-brief-9.md and do it as its own commit.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Push and NABU verify stay with the outer agent. This Linux host cannot execute macOS `open` or osascript.
