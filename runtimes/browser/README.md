# Browser runtime

Reference MCP server for the four browser tools. The manifests in `catalog/browser/` are the contract. This package loads each tool's name, description, and schemas from those files at startup.

The server speaks MCP over stdio. A remote agent drives one headed Chrome session. On a login wall it calls `browser.handoff`. Benny opens the returned URL, uses the same live browser through noVNC, and presses Done or Cancel.

## Run

From the repo root, with nothing installed globally:

```
uv run --project runtimes/browser python -m agent_tools_browser
```

The default browser is the system binary `/usr/bin/google-chrome` (Playwright channel `chrome`). The process starts Xvfb on a free display and does not attach to a display that is already in use. `x11vnc` and `websockify` start only during `browser.handoff`, use a free port, and stop when the call returns. websockify serves the bundled noVNC files at `/usr/share/novnc`.

## Environment

Set these in the server process. The repo never stores their values.

- `AGENT_TOOLS_BROWSER_PROFILE`: directory for the persistent Chrome profile. The runtime also writes an X authority file and handoff token files under a sibling directory named `<profile-name>.runtime`.
- `AGENT_TOOLS_NOTIFY_WEBHOOK`: URL that receives one JSON POST when a handoff starts. The body is `reason`, `url`, and `expires_at`. No variable, or a failed POST, returns `notify_failed` and tears the share down.
- `AGENT_TOOLS_BROWSER_CHANNEL`: `chrome` (the default when unset) uses system Chrome. `chromium` uses Playwright's bundled Chromium. Install that browser once with `uv run --project runtimes/browser playwright install chromium`. The container image sets `chromium` because linux/arm64 has no Google Chrome package.
- `AGENT_TOOLS_HANDOFF_PORT`: TCP port for the handoff page. Unset chooses a free port.
- `AGENT_TOOLS_NOVNC_PORT`: TCP port for websockify and noVNC. Unset chooses a free port. x11vnc stays on its own free localhost port.
- `AGENT_TOOLS_BIND_ADDRESS`: address the handoff page and websockify bind. Unset is `127.0.0.1`. A container sets `0.0.0.0` so published ports reach the listeners.
- `AGENT_TOOLS_PUBLIC_HOST`: host in the URL posted to the webhook and in the page's noVNC frame. Unset is `127.0.0.1`. Benny's browser loads this host, so it is the address published on the machine in front of the container.

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

## Run on a Mac with Podman

Benny's Mac is Apple Silicon. Podman runs this image in a linux/arm64 virtual machine. The harness starts one container with `podman run -i` and speaks MCP on that stdin and stdout. The handoff page and noVNC are published on `127.0.0.1`, so the Mac's own browser can open them. A listener on the Mac receives the handoff POST, posts a notification, and opens the page.

### Build the image

From the repo root, on the Mac:

```
podman build -f runtimes/browser/Containerfile -t agent-tools-browser .
```

`runtimes/browser/Containerfile` builds on linux/arm64 and linux/amd64. The image contains Xvfb, x11vnc, websockify, noVNC, uv, the runtime, and Playwright Chromium with its OS packages. It sets `AGENT_TOOLS_BROWSER_CHANNEL` to `chromium`. The entrypoint is the MCP stdio server. The process user is `agent` (uid 1000). The profile directory is the volume path `/var/lib/agent-tools/profile`. The image contains no webhook URL and no other secret.

The same image build on linux/amd64 is what `runtimes/browser/contrib/smoke_container.py` runs when podman or docker is on PATH.

### Podman command

This is the stdio command to register. `-i` keeps stdin open for MCP. Ports are published only on `127.0.0.1`. `8765` is `AGENT_TOOLS_HANDOFF_PORT`. `8766` is `AGENT_TOOLS_NOVNC_PORT`. The named volume keeps the profile across runs, and `:U` makes it writable by the container user. The webhook is the listener below, reached from the container as `host.containers.internal`.

```
podman run -i --rm \
  -p 127.0.0.1:8765:8765 \
  -p 127.0.0.1:8766:8766 \
  -v agent-tools-browser-profile:/var/lib/agent-tools/profile:U \
  -e AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/ \
  -e AGENT_TOOLS_BROWSER_CHANNEL=chromium \
  -e AGENT_TOOLS_BIND_ADDRESS=0.0.0.0 \
  -e AGENT_TOOLS_PUBLIC_HOST=127.0.0.1 \
  -e AGENT_TOOLS_HANDOFF_PORT=8765 \
  -e AGENT_TOOLS_NOVNC_PORT=8766 \
  -e AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile \
  agent-tools-browser
```

Change a published port only together with the matching `AGENT_TOOLS_HANDOFF_PORT` or `AGENT_TOOLS_NOVNC_PORT` value. `AGENT_TOOLS_PUBLIC_HOST` stays `127.0.0.1` so the page and the noVNC frame load in the Mac's browser.

### Install the listener

`runtimes/browser/contrib/macos_notify.py` is a stdlib program. It binds `127.0.0.1`. The port comes from `AGENT_TOOLS_MACOS_NOTIFY_PORT`, or from `--port`. The default is `8767`. A POST whose JSON has `reason`, `url`, and `expires_at` runs `osascript` to show `reason`, then runs `open` on `url`, then returns 204.

The launchd template is `runtimes/browser/contrib/com.agent-tools.browser-notify.plist`. Copy it and replace the `__REPO_ROOT__` path with the absolute path of this repo:

```
cp runtimes/browser/contrib/com.agent-tools.browser-notify.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agent-tools.browser-notify.plist
```

`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.agent-tools.browser-notify.plist` stops it. The plist sets `AGENT_TOOLS_MACOS_NOTIFY_PORT` to `8767`, which is the port in the webhook URL above.

### Register with Grok Build

In `~/.grok/config.toml`:

```toml
[mcp_servers.browser]
command = "podman"
args = [
  "run", "-i", "--rm",
  "-p", "127.0.0.1:8765:8765",
  "-p", "127.0.0.1:8766:8766",
  "-v", "agent-tools-browser-profile:/var/lib/agent-tools/profile:U",
  "-e", "AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/",
  "-e", "AGENT_TOOLS_BROWSER_CHANNEL=chromium",
  "-e", "AGENT_TOOLS_BIND_ADDRESS=0.0.0.0",
  "-e", "AGENT_TOOLS_PUBLIC_HOST=127.0.0.1",
  "-e", "AGENT_TOOLS_HANDOFF_PORT=8765",
  "-e", "AGENT_TOOLS_NOVNC_PORT=8766",
  "-e", "AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile",
  "agent-tools-browser",
]
startup_timeout_sec = 120
```

`startup_timeout_sec` gives Xvfb and Chromium time to come up on a cold start. The same command works with `grok mcp add browser -- podman run -i --rm ...` using the arguments from the shell block above. Put no secret values in this file. `AGENT_TOOLS_NOTIFY_WEBHOOK` here is the local listener URL.

### Register with Claude Code

User scope makes the browser available in every project on the Mac:

```
claude mcp add --scope user browser -- podman run -i --rm -p 127.0.0.1:8765:8765 -p 127.0.0.1:8766:8766 -v agent-tools-browser-profile:/var/lib/agent-tools/profile:U -e AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/ -e AGENT_TOOLS_BROWSER_CHANNEL=chromium -e AGENT_TOOLS_BIND_ADDRESS=0.0.0.0 -e AGENT_TOOLS_PUBLIC_HOST=127.0.0.1 -e AGENT_TOOLS_HANDOFF_PORT=8765 -e AGENT_TOOLS_NOVNC_PORT=8766 -e AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile agent-tools-browser
```

The same entry in `~/.claude.json`:

```json
{
  "mcpServers": {
    "browser": {
      "type": "stdio",
      "command": "podman",
      "args": [
        "run", "-i", "--rm",
        "-p", "127.0.0.1:8765:8765",
        "-p", "127.0.0.1:8766:8766",
        "-v", "agent-tools-browser-profile:/var/lib/agent-tools/profile:U",
        "-e", "AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/",
        "-e", "AGENT_TOOLS_BROWSER_CHANNEL=chromium",
        "-e", "AGENT_TOOLS_BIND_ADDRESS=0.0.0.0",
        "-e", "AGENT_TOOLS_PUBLIC_HOST=127.0.0.1",
        "-e", "AGENT_TOOLS_HANDOFF_PORT=8765",
        "-e", "AGENT_TOOLS_NOVNC_PORT=8766",
        "-e", "AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile",
        "agent-tools-browser"
      ]
    }
  }
}
```

If the handshake times out, set `MCP_TIMEOUT` (milliseconds) in the environment of the Claude Code process.

### Register with OMP

The user file is `~/.omp/agent/mcp.json`. A project file `.omp/mcp.json` uses the same shape:

```json
{
  "mcpServers": {
    "browser": {
      "type": "stdio",
      "command": "podman",
      "args": [
        "run", "-i", "--rm",
        "-p", "127.0.0.1:8765:8765",
        "-p", "127.0.0.1:8766:8766",
        "-v", "agent-tools-browser-profile:/var/lib/agent-tools/profile:U",
        "-e", "AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/",
        "-e", "AGENT_TOOLS_BROWSER_CHANNEL=chromium",
        "-e", "AGENT_TOOLS_BIND_ADDRESS=0.0.0.0",
        "-e", "AGENT_TOOLS_PUBLIC_HOST=127.0.0.1",
        "-e", "AGENT_TOOLS_HANDOFF_PORT=8765",
        "-e", "AGENT_TOOLS_NOVNC_PORT=8766",
        "-e", "AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile",
        "agent-tools-browser"
      ]
    }
  }
}
```

### Smoke test

On a machine with podman or docker:

```
uv run --project runtimes/browser python runtimes/browser/contrib/smoke_container.py
```

The script builds the image, runs it with `-i`, checks MCP initialize and tools/list for the four browser tools, and calls `browser.navigate` on a page served from the host. It exits 2 when neither podman nor docker is on PATH. It removes only the container it started.

## Tests

```
uv run --project runtimes/browser --with pytest pytest runtimes/browser/tests -q
```

The behavior tests use a local HTTP server and a local webhook. They do not use the internet. They run once with the default system Chrome channel and once with `AGENT_TOOLS_BROWSER_CHANNEL=chromium`. Install bundled Chromium before that second run. A separate test sets fixed handoff and noVNC ports, binds `0.0.0.0`, and checks that the posted URL uses `AGENT_TOOLS_PUBLIC_HOST` and `AGENT_TOOLS_HANDOFF_PORT`.

The macOS listener test stubs `osascript` and `open`. It does not need macOS.

GitHub-hosted `ubuntu-latest` does not have the display stack, so CI runs the catalog check only.
