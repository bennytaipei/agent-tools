# Checkpoint

Objective:
Brief 17b: wheel scroll race. After mouse.wheel, wait until scrollLeft/scrollTop settle before measuring and before the scrollLeft fallback, so a late wheel does not double-scroll. One local commit on top of f85cc53 (do not amend). Do not push. Do not touch NABU or stop containers.

Current state:
- HEAD is f85cc53 "Add failing regressions for brief 17." Staged brief-17 fixes stay staged.
- Settle wait is in _perform_scroll. Pre-fix race tests: dx landed at 1200, dy at 400, first dx=300 at 600. to:right already ended at max. After the wait, those four tests passed in 6.66s.
- Next: full runtime pytest, repo pytest, and scripts/check.py, then one local commit.

Decisions:
- One commit for the staged brief-17 work plus this fix. Message: "Flatten browser inputs and report scroll, typing, and navigation failures."
- Settle poll is ~30ms. Stop when two consecutive reads match and at least ~120ms have passed since the wheel, cap ~800ms. Same wait after the fallback.
- Reported after is the settled position. scroll_y stays the scroller's scroll_top.
- Horizontal proof is point dx=600 on #cell (600px wrapper, 1800px content): real scrollLeft stays 600 after 500ms and scrolled.scroll_left is [0, 600].
- Vertical proof uses dy=200 on #vscroll (max is ~520, so a doubled 400 is still visible). to:right ends at max and reports max. Two dx=300 calls land at 300 then 600.
- No lazy page auto-create. Published schemas stay one flat object.

Files changed:
- runtimes/browser/tests/test_brief17.py (new race tests, on top of staged brief-17 tests)
- runtimes/browser/agent_tools_browser/interact.py (_perform_scroll settle)
- staged brief-17 files listed below stay in the same commit

Commands that worked / failed:
- Pre-fix race tests: 3 failed, 1 passed in 4.47s. dx=600 settled at 1200, dy=200 at 400, first dx=300 at 600. to:right already ended at max.
- Post-fix race tests: 4 passed in 6.66s.
- Runtime pytest: 73 passed, 3 warnings, in 163.19s. EXIT 0.
- Repo pytest: 70 passed in 16.90s. EXIT 0.
- scripts/check.py: EXIT 0.

Next action:
Create the one local commit (staged brief-17 work plus this settle fix). Do not amend f85cc53. Then write the commit hash here.
Unresolved (non-blocking):
- macstudio still on 2167ef1.
- Live frontmost-Chrome pick still unverified on unlocked NABU.
- Live JFrog page cannot be opened here. Fixes are proven on local fixtures.
- Push and NABU deploy stay with the outer agent. Do not stop live containers.
