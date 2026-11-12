# Checkpoint

Objective:
Brief 17: container scroll, flat schemas, frames, type feedback, useful errors. Two local commits. Do not push. Do not touch NABU or stop containers.

Current state:
- Tip is brief 16 commit 64214e7. Working tree was clean at start.
- Reading the repro harness and interact/session/schema code before writing failing tests.

Decisions:
- First commit is tests only and must fail on 64214e7. Second commit is the fix.
- No lazy page auto-create. no_session gets a detail that says to call navigate.
- Scroll and click published schemas become one flat object. Bare number and bare string stay server-side only.
- Horizontal scroll walks scrollable ancestors and reports scrolled.moved plus a note when nothing moves.
- Locators search every frame. Type reports the field value and waits for settle before the screenshot.

Files changed:
- None yet.

Commands that worked / failed:
- None yet.

Next action:
Run the new brief 17 tests, record the failing names, and commit them as "Add failing regressions for brief 17". Then implement the fix.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- Live frontmost-Chrome pick still unverified on unlocked NABU.
- Live JFrog page cannot be opened here. Fixes are proven on local fixtures.
- Push and NABU deploy stay with the outer agent. Do not stop live containers.
