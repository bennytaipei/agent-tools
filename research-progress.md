# Checkpoint

Objective:
Brief 17b: wheel scroll race. After mouse.wheel, wait until scrollLeft/scrollTop settle before measuring and before the scrollLeft fallback, so a late wheel does not double-scroll. One local commit on top of f85cc53 (do not amend). Do not push. Do not touch NABU or stop containers.

Current state:
- Done. HEAD is 7f856c73ee0eb38f55aa109bad2020713a3825b7 "Flatten browser inputs and report scroll, typing, and navigation failures."
- Parent remains f85cc53024a8afa799b1ce72e5ee8ee555235061 "Add failing regressions for brief 17." Not amended. Not pushed.
- _perform_scroll polls scrollLeft/scrollTop every 30ms after the wheel and after the fallback. It stops when two reads match and at least 120ms have passed, and it caps the wait at 800ms. The reported after value is that settled position.

Decisions:
- One commit for the staged brief-17 work plus this settle fix. Message: "Flatten browser inputs and report scroll, typing, and navigation failures."
- Settle poll is 30ms. Stop when two consecutive reads match and at least 120ms have passed since the wheel, cap 800ms. Same wait after the fallback.
- Reported after is the settled position. scroll_y stays the scroller's scroll_top.
- Horizontal proof is point dx=600 on #cell (600px wrapper, 1800px content): real scrollLeft stays 600 after 500ms and scrolled.scroll_left is [0, 600].
- Vertical proof uses dy=200 on #vscroll (max is about 520, so a doubled 400 is still visible). to:right ends at max and reports max. Two dx=300 calls land at 300 then 600.
- No lazy page auto-create. Published schemas stay one flat object.

Files changed:
- runtimes/browser/agent_tools_browser/interact.py (settle wait)
- runtimes/browser/tests/test_brief17.py (four race tests)
- The brief-17 staged manifests, tools.json, server, session, validate, schema, README, and test_check.py are in the same commit.

Commands that worked / failed:
- Pre-fix race tests: 3 failed, 1 passed in 4.47s. dx=600 settled at 1200, dy=200 at 400, first dx=300 at 600. to:right already ended at max.
- Post-fix race tests: 4 passed in 6.66s.
- Runtime pytest: 73 passed, 3 warnings, in 163.19s. EXIT 0.
- Repo pytest: 70 passed in 16.90s. EXIT 0.
- scripts/check.py: EXIT 0.
- Commit 7f856c73ee0eb38f55aa109bad2020713a3825b7: 19 files changed, 1953 insertions, 1790 deletions.

Next action:
None. Push and NABU deploy stay with the outer agent.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- Live frontmost-Chrome pick still unverified on unlocked NABU.
- Live JFrog page cannot be opened here. Fixes are proven on local fixtures.
- Do not stop live containers.
