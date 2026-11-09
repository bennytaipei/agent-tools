# Checkpoint

Objective:
Brief 15b. After 89bff3e, NABU dry-run showed `lsappinfo front` returns only an ASN and bare `lsappinfo info` is empty, so frontmost-browser pick never fires and the default Safari is always chosen. Resolve ASN via `lsappinfo info <asn>`. Local commit only. Do not push.

Frozen acceptance (brief 15b):
P1. `_frontmost_browser_name` calls `lsappinfo front`, then `lsappinfo info <asn>` (and/or -only name/bundleid). Never bare `lsappinfo info`.
P2. Known display name OR known bundle id from `_BUNDLE_APP` selects that browser.
P3. Tests stub ASN + info-<asn>; assert bare info is unused; Chrome frontmost opens Chrome; loginwindow falls to default; HANDOFF skips lsappinfo.
P4. One-browser / open-failure-only fallback from 89bff3e unchanged.
P5. check.py, repo pytest, runtime pytest pass. One local commit. Print BRIEF15B FINISHED.

Current state:
- Local main is 89bff3e. Implementing ASN resolution for the frontmost pick. One-browser fallback stays.

Decisions:
- `lsappinfo front` is ASN-only on NABU. Bare `lsappinfo info` is empty and must not be called.
- Resolve with `lsappinfo info <asn>`. If that stdout is empty, try `info -only name <asn>` then `info -only bundleid <asn>`.
- A known display name or a `_BUNDLE_APP` bundle id selects the browser. loginwindow selects none.
- Lock detection stays on ioreg, then `lsappinfo front` containing loginwindow.

Files changed:
- (pending) runtimes/browser/contrib/macos_notify.py
- (pending) runtimes/browser/tests/test_macos_notify.py
- (pending) runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests/test_macos_notify.py -q` 20 passed in 6.61s.
- `uv run scripts/check.py` exited 0.
- `uv run --with pytest --with pyyaml --with jsonschema pytest -q` 66 passed.
- `uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q` 52 passed in 106.31s.

Next action:
Commit brief 15b locally. Nothing further in this queue.

Unresolved:
- NABU cannot git pull (private repo, no bennytaipei creds). Deploy by file copy.
