# Checkpoint

Objective:
Brief 9. An elegant handoff page. Local commit only. Do not push. Do not stop the NABU VM (OMP pid 30858).

Frozen acceptance (brief 9):
P1. One bar, about 40px, background #0f0f10, hairline only. No second bar, no white banner, no class="banner".
P2. Reason is shown, HTML-escaped. Fallback text is "Sign in or finish the step, then click Done."
P3. Done and Cancel stay POST forms to /h/{token}/done and /h/{token}/cancel. Button text stays >Done< and >Cancel<.
P4. Iframe top equals the bar height. Page background is dark. No keyboard shortcut that steals plain keys.
P5. noVNC is a generated view.html (core/rfb.js, scaleViewport, dark background), not stock vnc.html. Iframe query still contains token%3D.
P6. No manifest change. handoff stays 1.1.1. side_effects still contains the fallback sentence.
P7. render_handoff_page.py writes the same renderer with a stand-in iframe.
P8. check.py, repo pytest, runtime pytest pass. Local commit. Print BRIEF9 DONE.

Current state:
- Brief 10 is 599e8f3. Poll-error fix is 9f07010. Auth store is 793d890.
- Brief 9 page is implemented. check.py exited 0, repo pytest 66 passed, runtime pytest 40 passed in 99s.
- Headless Chrome screenshot of the render-script page shows one dark bar, the reason, a white Done pill, and a subtle Cancel. The iframe in that shot is about:blank, not a live desktop.

Decisions:
- Do not bump handoff past 1.1.1. The page is not a contract change.
- Pass reason into HandoffShare.start. The page is built inside start, before notify.
- websockify --web serves {state}/novnc-web: view.html plus a symlink to /usr/share/novnc/core. core/*.js does not import outside core/.
- Iframe src uses view.html and keeps path=websockify%3Ftoken%3D so the token split in tests still works.
- Do not push. NABU deploy stays with the outer agent.

Files changed:
- runtimes/browser/agent_tools_browser/handoff.py
- runtimes/browser/agent_tools_browser/session.py
- runtimes/browser/tests/test_runtime.py
- runtimes/browser/README.md
- runtimes/browser/contrib/render_handoff_page.py
- research-progress.md

Commands that worked / failed:
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 40 passed in 99s.
- Headless Chrome wrote /tmp/handoff-preview.png from the render script.

Next action:
Commit brief 9 locally. Nothing further in this queue.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU has a live agent-tools container (OMP session pid 30858). Do not stop the VM.
- Push and NABU verify stay with the outer agent.
- Linux cannot run macOS open or osascript.
- The live noVNC desktop was not screenshotted. The shot is the page chrome with about:blank in the frame.
