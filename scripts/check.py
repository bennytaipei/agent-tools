# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml",
#   "jsonschema",
# ]
# ///
"""Validate catalog manifests, the namespace index, and their layout.

Run from the repo root:

    uv run scripts/check.py

Pass a directory that contains catalog/ to validate a fixture instead of this repo.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

REPO = Path(__file__).resolve().parent.parent
DRAFT = "https://json-schema.org/draft/2020-12/schema"
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
QUALIFIED = re.compile(
    r"^[a-z][a-z0-9]*(_[a-z0-9]+)*\.[a-z][a-z0-9]*(_[a-z0-9]+)*$"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 2:
        print("usage: check.py [root]", file=sys.stderr)
        return 2
    root = REPO if len(args) == 1 else Path(args[1]).resolve()
    if not root.is_dir():
        print(f"{root}: not a directory", file=sys.stderr)
        return 1
    messages = validate_catalog(root)
    if messages:
        print("\n".join(messages), file=sys.stderr)
        return 1
    return 0


def validate_catalog(root: Path) -> list[str]:
    messages: list[str] = []
    manifest_validator = load_validator(REPO / "schema" / "manifest.schema.json", messages)
    index_validator = load_validator(REPO / "schema" / "index.schema.json", messages)
    if manifest_validator is None or index_validator is None:
        return messages

    catalog = root / "catalog"
    if not catalog.is_dir():
        messages.append("catalog: directory not found")
        return messages

    index_path = catalog / "index.yaml"
    namespaces: list[dict] = []
    if not index_path.is_file():
        messages.append("catalog/index.yaml: file not found")
    else:
        index_data = load_yaml(index_path, "catalog/index.yaml", messages)
        if index_data is not None:
            messages.extend(schema_messages(index_validator, index_data, "catalog/index.yaml"))
            namespaces = namespace_entries(index_data)

    indexed: set[str] = set()
    for entry in namespaces:
        name = entry.get("name")
        path_value = entry.get("path")
        if not isinstance(name, str) or SNAKE.fullmatch(name) is None:
            continue
        if name in indexed:
            messages.append(f"duplicate namespace name '{name}' in catalog/index.yaml")
            continue
        indexed.add(name)
        expected = f"catalog/{name}"
        if not (catalog / name).is_dir():
            messages.append(f"index entry '{name}' has no folder at {expected}")
        if isinstance(path_value, str) and path_value != expected:
            messages.append(
                f"index entry '{name}' path '{path_value}' does not match folder '{expected}'"
            )

    for child in sorted(catalog.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        if child.name == "index.yaml":
            continue
        rel = f"catalog/{child.name}"
        if child.is_dir():
            if child.name not in indexed:
                messages.append(f"namespace folder '{rel}' has no index entry")
        else:
            messages.append(f"{rel}: unexpected file in catalog root")

    seen_names: dict[str, str] = {}
    folders = sorted(
        (path for path in catalog.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name,
    )
    for folder in folders:
        for stray in sorted(folder.glob("*.yml")):
            if stray.is_file():
                rel = f"catalog/{folder.name}/{stray.name}"
                messages.append(f"{rel}: manifests use the .yaml suffix")
        manifests = sorted(path for path in folder.glob("*.yaml") if path.is_file())
        for manifest in manifests:
            rel = f"catalog/{folder.name}/{manifest.name}"
            data = load_yaml(manifest, rel, messages)
            if not isinstance(data, dict):
                if data is not None:
                    messages.append(f"{rel}: manifest must be a mapping")
                continue
            messages.extend(schema_messages(manifest_validator, data, rel))
            check_manifest_shape(data, folder.name, manifest.stem, rel, messages)
            record_name(data, rel, seen_names, messages)
    return messages


def load_validator(path: Path, messages: list[str]) -> Draft202012Validator | None:
    label = path.relative_to(REPO).as_posix()
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        messages.append(f"{label}: {exc}")
        return None
    if not isinstance(schema, dict) or schema.get("$schema") != DRAFT:
        messages.append(f"{label}: $schema must be {DRAFT}")
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        messages.append(f"{label}: invalid schema: {exc.message}")
        return None
    return Draft202012Validator(schema)


def load_yaml(path: Path, label: str, messages: list[str]):
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        messages.append(f"{label}: invalid YAML: {exc}")
        return None
    except OSError as exc:
        messages.append(f"{label}: {exc}")
        return None
    if data is None:
        messages.append(f"{label}: document is empty")
        return None
    return data


def namespace_entries(data: object) -> list[dict]:
    if not isinstance(data, dict):
        return []
    raw = data.get("namespaces")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def schema_messages(validator: Draft202012Validator, instance: object, label: str) -> list[str]:
    errors = sorted(validator.iter_errors(instance), key=error_sort_key)
    return [f"{label}: {format_error(err)}" for err in errors]


def format_error(err: ValidationError) -> str:
    if not err.absolute_path:
        return err.message
    return f"{err.message} at {json_path(err.absolute_path)}"


def json_path(parts) -> str:
    bits: list[str] = []
    for part in parts:
        if isinstance(part, int):
            bits.append(f"[{part}]")
        elif bits:
            bits.append(f".{part}")
        else:
            bits.append(str(part))
    return "".join(bits)


def error_sort_key(err: ValidationError) -> list[str]:
    return [str(part) for part in err.absolute_path] + [err.message]


def check_manifest_shape(
    data: dict,
    folder: str,
    stem: str,
    label: str,
    messages: list[str],
) -> None:
    name = data.get("name")
    if isinstance(name, str) and QUALIFIED.fullmatch(name):
        namespace, tool = name.split(".", 1)
        if namespace != folder:
            messages.append(
                f"namespace mismatch in {label}: name '{name}' does not match folder '{folder}'"
            )
        if tool != stem:
            messages.append(
                f"tool mismatch in {label}: name '{name}' does not match filename '{stem}'"
            )
    check_error_codes(data, label, messages)
    check_nested_schemas(data, label, messages)


def check_error_codes(data: dict, label: str, messages: list[str]) -> None:
    errors = data.get("errors")
    if not isinstance(errors, list):
        return
    seen: set[str] = set()
    reported: set[str] = set()
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str):
            continue
        if code in seen and code not in reported:
            messages.append(f"duplicate error code '{code}' in {label}")
            reported.add(code)
        seen.add(code)


def check_nested_schemas(data: dict, label: str, messages: list[str]) -> None:
    for field in ("inputs", "outputs"):
        schema = data.get(field)
        if not isinstance(schema, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            messages.append(f"{label}: {field} is not a JSON Schema: {exc.message}")


def record_name(data: dict, label: str, seen: dict[str, str], messages: list[str]) -> None:
    name = data.get("name")
    if not isinstance(name, str):
        return
    previous = seen.get(name)
    if previous is None:
        seen[name] = label
        return
    messages.append(f"duplicate tool name '{name}' in {previous} and {label}")


if __name__ == "__main__":
    raise SystemExit(main())
