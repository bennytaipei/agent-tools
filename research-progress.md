# Checkpoint

Objective:
Brief 15 + 15b complete. One browser per handoff. Frontmost pick via lsappinfo ASN. Pushed to origin/main. Deployed to NABU by file copy.

Current state:
- origin/main at 4c55144 (89bff3e then 4c55144).
- NABU listener restarted on the copied 4c55144 file, listening 127.0.0.1:8767.
- Dry-run ASN path measured OK while screen showed loginwindow.

Decisions:
- System Events removed from open path. Fallback only when URL open returns non-zero.
- Frontmost: lsappinfo front → ASN → lsappinfo info <asn>; also accept known bundle ids.
- NABU deploy by CopyFromBox; git pull unavailable there for this private repo.

Files changed:
- runtimes/browser/contrib/macos_notify.py
- runtimes/browser/tests/test_macos_notify.py
- runtimes/browser/README.md
- research-progress.md

Commands that worked / failed:
- notify pytest 20 passed; check.py 0; repo pytest 66; runtime pytest 52 (Grok report) / re-run notify+check+repo on box.
- NABU: launchctl kickstart ok; lsof 127.0.0.1:8767 LISTEN; dry-run DRY_RUN_OK_ASN.
- NABU git fetch: Repository not found (expected without bennytaipei creds).

Next action:
None for this bug. Optional later: unlocked live POST when Benny wants a window check; set AGENT_TOOLS_HANDOFF_BROWSER=Google Chrome if default Safari is wrong for him.

Unresolved (non-blocking):
- macstudio still on 2167ef1.
- NABU working tree dirty vs its stale origin tracking (ahead 20 + local file copies).
- Screen was loginwindow during dry-run; live frontmost-Chrome pick not exercised on unlocked NABU.
