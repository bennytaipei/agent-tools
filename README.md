# agent-tools

Catalog of agent tools. This repo holds manifests, not implementations.

A tool is one action the harness offers. It has typed inputs, typed outputs, and a stated side effect. Policy and judgment live in skills, not tools.

## Layout

`catalog/index.yaml` is level one. It lists namespaces. Each namespace has a one-line description and the path to its folder.

Level two is one manifest per tool: `catalog/<namespace>/<tool>.yaml`.

An agent lists namespaces from `catalog/index.yaml` first. It loads a namespace's tool schemas only when it needs them. It does not load every manifest up front.

Namespaces today are `browser` (`catalog/browser/`): `browser.navigate`, `browser.read`, `browser.screenshot`, `browser.handoff`, `browser.click`, `browser.type`, `browser.scroll`, `browser.select`, `browser.wait_for`; and draft `teams` (`catalog/teams/`): `teams.send_message` at version `0.1.0`. Benny will replace that draft with the real Teams tools.

`browser.handoff` matches `url_matches` against the page URL without the query string or fragment (`scheme://host[:port]/path`). `selector_present` and `text_present` count only visible elements.

## Contract

`schema/manifest.schema.json` (JSON Schema draft 2020-12) is the contract for every manifest. `additionalProperties` is false. These fields are required:

- `name` is the fully qualified `<namespace>.<tool>`. Each segment is lowercase snake_case, for example `browser.handoff`. The namespace matches the folder name. The tool matches the filename.
- `version` is semver `MAJOR.MINOR.PATCH`.
- `when_to_use` says when an agent should call the tool.
- `inputs` is a JSON Schema whose `type` is `object`.
- `outputs` is a JSON Schema whose `type` is `object`.
- `side_effects` is a non-empty string that says what the call changes. Read-only tools say `None.` explicitly.
- `errors` is a list of `{code, when}`. `code` is lowercase snake_case and unique within the manifest. `when` says when that code is returned. An empty list is allowed and is written `[]`. Normal results stay in `outputs`. For `browser.handoff`, `done`, `timeout`, and `cancelled` are output statuses.
- `auth_needed` is a list of credential or session names. The list holds names only, never a secret or token value. An empty list is allowed and is written `[]`.

`schema/index.schema.json` validates `catalog/index.yaml`. `namespaces` is a list of `{name, description, path}`. `description` is one line. `path` is `catalog/<name>`. Index entries and namespace folders match one to one.

## Versioning

Bump `MAJOR` when inputs or outputs change in a breaking way. A breaking change removes or renames a field, changes a type, makes a new input required, or removes an output callers already rely on.

Bump `MINOR` when the contract grows and existing callers keep working. A new optional input, a new output field, or a new error code is `MINOR`.

Bump `PATCH` when wording changes and inputs, outputs, and error codes stay the same. Updates to `when_to_use`, descriptions, and `side_effects` text are `PATCH`.

## Add a namespace

1. Add one entry under `namespaces` in `catalog/index.yaml` with `name`, a one-line `description`, and `path` set to `catalog/<name>`.
2. Create the folder `catalog/<name>/` and add one `<tool>.yaml` manifest per tool. Set each manifest `name` to `<name>.<tool>`.
3. Run `uv run scripts/check.py`.

The check exits non-zero and prints a message when a manifest breaks the schema, a name disagrees with its folder or filename, a tool name is duplicated, an error code repeats inside one manifest, or the index and the namespace folders disagree. `scripts/check.py` declares `pyyaml` and `jsonschema` inline (PEP 723). `uv run` installs them for that run.

Tests for the checker live in `tests/` and run with:

```
uv run --with pytest --with pyyaml --with jsonschema pytest -q
```

## Browser runtime

The reference server and the container launcher live in `runtimes/browser/`. The launcher to register is `runtimes/browser/bin/agent-tools-browser`.

The launcher starts a stopped Podman machine or Docker Desktop on its own (`AGENT_TOOLS_AUTO_START_ENGINE` defaults to `always`). It waits until the engine answers `info` and the image is present, then runs the container. If the engine or the image is still unusable, the process stays connected and answers MCP with the reason and a fix. When the browser tools fail, `tail ~/Library/Logs/agent-tools-browser.log`.

## Registering with agents

Replace `__REPO_ROOT__` with the absolute path of this repo. The examples below do not set `AGENT_TOOLS_HANDOFF_PORT` or `AGENT_TOOLS_NOVNC_PORT`. Each session then publishes a free host port for the handoff page and another for noVNC. An empty value, `0`, and `auto` also pick a free port. The container still listens on `8765` and `8766`. `AGENT_TOOLS_NOTIFY_PORT` is optional. Leave it unset to keep `8767`.

### Grok

In `~/.grok/config.toml`, the server name is `browser` and the command is the launcher:

```toml
[mcp_servers.browser]
command = "__REPO_ROOT__/runtimes/browser/bin/agent-tools-browser"
startup_timeout_sec = 120
```

`startup_timeout_sec` is how long startup may take. It is not the handoff wait. Tool names on this server are `navigate`, `read`, `screenshot`, `handoff`, `click`, `type`, `scroll`, `select`, and `wait_for`.

### OMP

In `~/.omp/agent/mcp.json`, the server name is `agent_browser`. `type` is `stdio`. `timeout` is milliseconds, and `0` disables the client-side MCP timeout so a sign-in handoff can block until the person finishes. `env.PATH` is the path the launcher uses to find `podman`. Set `AGENT_TOOLS_CONTAINER_ENGINE` to `podman`. Do not set `AGENT_TOOLS_HANDOFF_PORT` or `AGENT_TOOLS_NOVNC_PORT`. A project file `.omp/mcp.json` uses the same shape.

```json
{
  "mcpServers": {
    "agent_browser": {
      "type": "stdio",
      "command": "__REPO_ROOT__/runtimes/browser/bin/agent-tools-browser",
      "env": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "AGENT_TOOLS_CONTAINER_ENGINE": "podman"
      },
      "timeout": 0
    }
  }
}
```

OMP tool names are `mcp__agent_browser_navigate`, `mcp__agent_browser_handoff`, and the other `mcp__agent_browser_*` tools.

### Browser instructions

`docs/agent-instructions/browser.md` is the `## Browser tools` snippet. Paste it into `~/.grok/AGENTS.md` and `~/.omp/agent/AGENTS.md`. If a `## Browser tools` section is already in the file, replace that section. If it is not, append the snippet. The rule text is `skills/browser-auth-handoff/SKILL.md`.
