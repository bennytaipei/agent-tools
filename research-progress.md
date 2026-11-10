# Checkpoint

Objective:
Brief 16: handoff bind reuse, bare-number scroll, secondary auth store, lenient targets and validation detail. Local commit only. Do not push. Do not stop NABU containers.

Current state:
- Implemented and tested. Local commit follows this rewrite.
- check.py passed. Repo pytest: 66 passed. Runtime pytest: 55 passed.
- EADDRINUSE repro (before the fix, ports 36773/49019, display :200): four sequential start/stop cycles on fixed ports succeeded. Starting the next share about 10ms after stop() began failed with errno 98 Address already in use while both the page port and the noVNC port were still LISTEN. TIME_WAIT was not the failure. SO_REUSEADDR does not allow two simultaneous LISTEN sockets. A later inspect that sees only TIME_WAIT matches this race: the listener is gone once stop finishes. After the owner lock, test_fixed_ports_survive_done_cancel_timeout_and_overlap passed.
- Fresh stdio tools/list descriptions:

click: Click an element on the current page. target is placeholder, label, text, css, role and optional name, point {x, y, scroll_y}, or a string resolved as placeholder, then label, then text, then CSS. button is left, right, or middle. click_count is how many times to click. modifiers are Alt, Control, Meta, Shift, or ControlOrMeta. A string as the whole arguments value is the target. Examples: {"target":{"placeholder":"Search Admin Resources"}} and {"target":{"role":"button","name":"Sign in"}}.

scroll: Scroll the page or a container. A number, scroll_y, or y is an absolute page offset in CSS pixels, the same value browser.read reports as scroll_y. delta is {dx, dy} or a number of pixels down (dx 0). to is top or bottom. target scrolls that element into view, or scrolls that container when delta or to is set. target is placeholder, label, text, css, role and optional name, point {x, y, scroll_y}, or a string. Examples: 2200 and {"scroll_y":2200}.

Decisions:
- One share owns the fixed ports. start waits until the previous owner has closed them. stop does not return while those ports still accept connections. Not a long-lived server.
- share_failed keeps its code and adds detail (port, errno, holder).
- Scroll number / scroll_y / y is absolute page scroll_y. Numeric delta is dy. A bare number is rewritten to {"scroll_y": N} before the MCP object check. A non-number non-object becomes {} and returns invalid_target. A number is never rewritten to {}.
- click and scroll inputs are type object plus oneOf so tools/list can serialize them. A bare click string is rewritten to {"target": text}.
- Interaction manifests are 1.1.0. navigate, read, screenshot, and handoff stay at their current versions; only when_to_use text changed. Handoff stays 1.1.1.
- Secondary keeps a private user-data-dir and reads and writes the shared auth store.

Files changed:
- catalog/browser/*.yaml, schema/manifest.schema.json, runtimes/browser/tools.json
- handoff.py, processes.py, server.py, interact.py, session.py, validate.py
- tests, runtimes/browser/tests/test_handoff_rebind.py, README, page fixture

Commands that worked / failed:
- Repro overlap before the fix: ShareError [Errno 98] Address already in use; page and novnc still open.
- Sequential rebind before the fix: succeeded.
- uv run scripts/check.py: passed.
- Repo pytest: 66 passed.
- Runtime pytest: 55 passed.
- test_page_interactions, handoff rebind, and secondary auth: passed.

Next action:
Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- Live frontmost-Chrome pick still unverified on unlocked NABU.
- Push and NABU deploy stay with the outer agent. Do not stop live containers.
