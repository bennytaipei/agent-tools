# Checkpoint

Objective:
Lock the tool contract format so many namespaces can be added later, and move the four browser tools onto that contract. Manifests only. No tool implementations. No secrets.

Frozen acceptance (one row per criterion):
1. schema/manifest.schema.json (JSON Schema draft 2020-12) defines one tool contract. Required fields: name, version, when_to_use, inputs, outputs, side_effects, errors, auth_needed. additionalProperties false.
2. name is fully qualified as <namespace>.<tool>, lowercase snake_case segments, for example browser.handoff.
3. version is semver (MAJOR.MINOR.PATCH). The README says when to bump each part. Breaking input or output change means MAJOR.
4. inputs and outputs are JSON Schema objects (type object).
5. side_effects is a non-empty string. Read-only tools say "None." explicitly.
6. errors is a list of {code, when}. code is lowercase snake_case and unique within a manifest. An empty list is allowed but must be explicit.
7. auth_needed is a list of credential or session names. It never holds a value.
8. schema/index.schema.json validates catalog/index.yaml: namespaces list of {name, description, path}. description is one line.
9. scripts/check.py validates the whole catalog and exits non-zero with a clear message on any failure. It checks every manifest against the schema, the index against its schema, that each manifest's namespace matches its folder and its tool part matches its filename, that names are unique across the catalog, and that index entries and namespace folders match one to one. It runs with `uv run scripts/check.py` using PEP 723 inline dependencies (pyyaml, jsonschema). Nothing gets installed globally.
10. tests/ prove check.py catches real breakage. Use small fixture catalogs: one valid, and one each for a missing field, a duplicate name, a namespace or folder mismatch, and an index entry with no folder. Assert on exit code and on the message text. Run with `uv run --with pytest --with pyyaml --with jsonschema pytest -q`.
11. The four browser manifests (navigate, read, screenshot, handoff) are migrated to the schema. Qualified names, version 1.0.0, and realistic errors, for example navigation_failed or invalid_url for navigate, no_page for read and screenshot, and share_failed or notify_failed for handoff. handoff's done, timeout, and cancelled remain outputs, not errors. Still exactly four browser verbs.
12. .github/workflows/check.yml runs check.py and the tests on push and pull_request.
13. README.md documents the contract fields, versioning, and how to add a namespace: one index entry, one folder, then run the check.
14. `uv run scripts/check.py` passes and the test command passes on the final tree.

Current state:
- All fourteen acceptance rows pass on this tree. Manifests only. No tool implementations. No secrets.
- schema/manifest.schema.json and schema/index.schema.json are JSON Schema draft 2020-12. The manifest contract requires name, version, when_to_use, inputs, outputs, side_effects, errors, and auth_needed, and sets additionalProperties false.
- Names are <namespace>.<tool> in lowercase snake_case. version is MAJOR.MINOR.PATCH. inputs and outputs are JSON Schema objects. side_effects is a non-empty string. Read-only browser.read and browser.screenshot say "None." explicitly.
- errors is a list of {code, when}. codes are lowercase snake_case. The schema requires the list. scripts/check.py rejects a repeated code. An empty list is valid.
- auth_needed is a list of credential or session names and holds no values.
- The four browser manifests are browser.navigate, browser.read, browser.screenshot, and browser.handoff at version 1.0.0. navigate reports invalid_url and navigation_failed. read and screenshot report no_page. handoff reports share_failed and notify_failed. handoff outputs stay done, timeout, and cancelled.
- scripts/check.py checks manifests, the index, folder and filename alignment, unique tool names, and a one-to-one index/folder match. tests/ covers a valid catalog and the breakage cases, including filename mismatch, a folder with no index entry, and a duplicate error code.
- .github/workflows/check.yml runs the check and the tests on push and pull_request. README.md documents the fields, versioning, and how to add a namespace.

Decisions:
- Qualified names are exactly two lowercase snake_case segments, because the checker ties the namespace to the folder and the tool to the filename.
- Draft 2020-12 cannot say "error codes are unique" for objects in an array. The schema states the rule. scripts/check.py rejects a repeated code.
- check.py takes an optional catalog root so tests can point at fixture catalogs. The default root is the repo.
- Two files cannot both be path-aligned and share one qualified name. The duplicate-name fixture reuses demo.ping in pong.yaml, and the test asserts the duplicate tool name message.
- AGENTS.md's manifest field list includes version and errors so it matches the locked contract.

Done earlier:
- Private repo scaffold: README, catalog/index.yaml, and four browser manifests (navigate, read, screenshot, handoff). Manifests only, no implementations, no secrets. One commit on main.

Files changed:
- AGENTS.md
- README.md
- research-progress.md
- schema/manifest.schema.json
- schema/index.schema.json
- scripts/check.py
- catalog/browser/navigate.yaml
- catalog/browser/read.yaml
- catalog/browser/screenshot.yaml
- catalog/browser/handoff.yaml
- tests/test_check.py
- tests/fixtures/valid/
- tests/fixtures/missing_field/
- tests/fixtures/duplicate_name/
- tests/fixtures/namespace_mismatch/
- tests/fixtures/index_missing_folder/
- tests/fixtures/filename_mismatch/
- tests/fixtures/folder_without_index/
- tests/fixtures/duplicate_error_code/
- .github/workflows/check.yml

Commands that worked / failed:
- worked: `uv run scripts/check.py` exited 0 on the final tree.
- worked: `uv run --with pytest --with pyyaml --with jsonschema pytest -q` — 10 passed on the final tree.

Next action:
- None. All fourteen acceptance rows pass. Do not push.

Unresolved (non-blocking):
- None.
