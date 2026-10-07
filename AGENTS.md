# AGENTS.md

This repo is a catalog of agent tools. It holds manifests, not implementations.

- A tool is one action the harness offers. It has typed inputs, typed outputs, and a stated side effect.
- Policy and judgment live in skills, not tools. If a manifest needs the word "should", that rule belongs in a skill.
- `catalog/index.yaml` lists namespaces with a one-line description each. Tool schemas live under `catalog/<namespace>/`.
- Every manifest lists name, when to use, inputs, outputs, side effects, and auth needed.
- Never commit secrets, tokens, or credentials. Auth fields name the credential a tool needs. They never hold its value.
- Keep the checkpoint in `research-progress.md` current before long tool loops and before you stop.
