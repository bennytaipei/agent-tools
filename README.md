# agent-tools

Catalog of agent tools. This repo holds manifests, not implementations.

A tool is one action the harness offers. It has typed inputs, typed outputs, and a stated side effect. Policy and judgment live in skills, not tools.

## Layout

`catalog/index.yaml` is level one. It lists namespaces. Each namespace has a one-line description and the path to its folder.

Level two is the tool manifests in that folder: `catalog/<namespace>/<tool>.yaml`. Each manifest has `name`, `when_to_use`, `inputs`, `outputs`, `side_effects`, and `auth_needed`. Inputs and outputs use JSON Schema types. A property listed in `required` is required; any other property is optional. `auth_needed` names the credential or session a tool relies on and never holds a value.

The only namespace today is `browser` (`catalog/browser/`): `navigate`, `read`, `screenshot`, `handoff`.

## Loading

An agent lists namespaces from `catalog/index.yaml` first. It loads a namespace's tool schemas only when it needs them. It does not load every manifest up front.
