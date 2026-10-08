# Browser runtime

Reference MCP server for the four browser tools. The manifests in `catalog/browser/` are the contract. This package loads each tool's name, description, and schemas from those files at startup.

The server speaks MCP over stdio. A remote agent drives one headed Chrome session. On a login wall it calls `browser.handoff`. Benny opens the returned URL, uses the same live browser through noVNC, and presses Done or Cancel.

## Run

From the repo root, with nothing installed globally:

```
uv run --project runtimes/browser python -m agent_tools_browser
```

Chrome is the system binary `/usr/bin/google-chrome` (Playwright channel `chrome`). The process starts Xvfb on a free display and does not attach to a display that is already in use. `x11vnc` and `websockify` start only during `browser.handoff`, use a free port, and stop when the call returns. websockify serves the bundled noVNC files at `/usr/share/novnc`.

## Environment

Set these in the server process. The repo never stores their values.

- `AGENT_TOOLS_BROWSER_PROFILE`: directory for the persistent Chrome profile. The runtime also writes an X authority file and handoff token files under a sibling directory named `<profile-name>.runtime`.
- `AGENT_TOOLS_NOTIFY_WEBHOOK`: URL that receives one JSON POST when a handoff starts. The body is `reason`, `url`, and `expires_at`. No variable, or a failed POST, returns `notify_failed` and tears the share down.

`AGENT_TOOLS_ROOT` is optional. It points at the repo root when the process cannot find `catalog/browser/` from its own files or the working directory.

## Register with a harness

Point the MCP client at the stdio command above. Put `AGENT_TOOLS_BROWSER_PROFILE` and `AGENT_TOOLS_NOTIFY_WEBHOOK` in the server environment. A typical client entry:

```json
{
  "mcpServers": {
    "browser": {
      "command": "uv",
      "args": ["run", "--project", "runtimes/browser", "python", "-m", "agent_tools_browser"]
    }
  }
}
```

Add `AGENT_TOOLS_BROWSER_PROFILE` and `AGENT_TOOLS_NOTIFY_WEBHOOK` to that server's environment in the host that launches the harness. Do not commit the values.

The server exposes exactly `browser.navigate`, `browser.read`, `browser.screenshot`, and `browser.handoff`. Inputs that fail the manifest schema, and results that fail it, come back as that tool's declared error codes.

## Tests

```
uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q
```

The tests use a local HTTP server and a local webhook. They do not use the internet. GitHub-hosted `ubuntu-latest` does not have system Chrome, Xvfb, x11vnc, websockify, and `/usr/share/novnc`, so CI runs the catalog check only.
