"""Published schemas for brief 17. These fail until the manifests are flat."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "runtimes" / "browser" / "tools.json"
CATALOG = REPO / "catalog" / "browser"
COMBINATORS = {"oneOf", "anyOf", "allOf"}


def _walk(node: object, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key in COMBINATORS:
                yield here
            yield from _walk(value, here)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")


def _manifest(name: str) -> dict:
    return yaml.safe_load((CATALOG / name).read_text(encoding="utf-8"))


def test_tools_json_has_no_combinators() -> None:
    tools = json.loads(TOOLS.read_text(encoding="utf-8"))
    found = list(_walk(tools))
    assert found == []


def test_scroll_schema_accepts_flat_dx_target() -> None:
    manifest = _manifest("scroll.yaml")
    assert manifest["version"] == "1.2.0"
    schema = manifest["inputs"]
    assert schema["type"] == "object"
    assert "oneOf" not in schema and "anyOf" not in schema and "allOf" not in schema
    props = schema["properties"]
    assert "dx" in props and "dy" in props
    assert "x" not in props
    assert set(props["to"]["enum"]) == {"top", "bottom", "left", "right"}
    target = props["target"]
    assert target["type"] == "object"
    assert "oneOf" not in target
    for key in ("placeholder", "label", "role", "name", "text", "exact", "css", "nth", "x", "y", "scroll_y"):
        assert key in target["properties"]
    example = {"dx": 600, "dy": 0, "target": {"x": 756, "y": 425}}
    Draft202012Validator(schema).validate(example)
    when = manifest["when_to_use"]
    assert '{"dx":600,"target":{"x":756,"y":425}}' in when
    outputs = manifest["outputs"]["properties"]
    assert "scrolled" in outputs
    assert "moved" in outputs["scrolled"]["properties"]


def test_changed_manifests_are_flat_minor_bumps() -> None:
    expected = {
        "click.yaml": "1.2.0",
        "type.yaml": "1.2.0",
        "select.yaml": "1.2.0",
        "wait_for.yaml": "1.2.0",
        "navigate.yaml": "1.1.0",
        "handoff.yaml": "1.2.0",
        "read.yaml": "2.0.0",
        "screenshot.yaml": "1.0.0",
    }
    for name, version in expected.items():
        manifest = _manifest(name)
        assert manifest["version"] == version
        found = list(_walk(manifest["inputs"]))
        assert found == [], (name, found)
    click = _manifest("click.yaml")
    assert "should" not in click["when_to_use"]
    assert '{"target":{"placeholder":"Search Admin Resources"}}' in click["when_to_use"]
    assert '{"target":{"role":"button","name":"Sign in"}}' in click["when_to_use"]
    type_manifest = _manifest("type.yaml")
    assert "typed" in type_manifest["outputs"]["properties"]
    navigate = _manifest("navigate.yaml")
    assert "status" in navigate["outputs"]["properties"]
    assert "status" not in navigate["outputs"]["required"]
    handoff = _manifest("handoff.yaml")
    done = handoff["inputs"]["properties"]["done_when"]
    assert done["type"] == "object"
    assert "url_matches" in done["properties"]
    assert "should" not in handoff["when_to_use"]
    assert "should" not in handoff["side_effects"]
    read = _manifest("read.yaml")
    assert "truncated" in read["when_to_use"]
    assert "max_viewports" in read["when_to_use"]


def test_flat_dx_schema_rejects_nothing_about_numbers() -> None:
    """A bare number is not a published root branch. The object form still validates."""

    schema = _manifest("scroll.yaml")["inputs"]
    validator = Draft202012Validator(schema)
    try:
        validator.validate(2200)
    except ValidationError:
        rejected_number = True
    else:
        rejected_number = False
    assert rejected_number
    validator.validate({"scroll_y": 2200})
    validator.validate({"delta": {"dx": 0, "dy": 200}})
