"""check.py exits non-zero and names the breakage."""

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), str(root)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_valid_fixture():
    result = run(FIXTURES / "valid")
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_missing_field():
    result = run(FIXTURES / "missing_field")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "catalog/demo/ping.yaml: 'version' is a required property\n"


def test_duplicate_name():
    result = run(FIXTURES / "duplicate_name")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "tool mismatch in catalog/demo/pong.yaml: name 'demo.ping' does not match filename 'pong'\n"
        "duplicate tool name 'demo.ping' in catalog/demo/ping.yaml and catalog/demo/pong.yaml\n"
    )


def test_namespace_mismatch():
    result = run(FIXTURES / "namespace_mismatch")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "namespace mismatch in catalog/demo/ping.yaml: "
        "name 'widgets.ping' does not match folder 'demo'\n"
    )


def test_index_entry_without_folder():
    result = run(FIXTURES / "index_missing_folder")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "index entry 'ghost' has no folder at catalog/ghost\n"


def test_filename_mismatch():
    result = run(FIXTURES / "filename_mismatch")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "tool mismatch in catalog/demo/ping.yaml: name 'demo.pong' does not match filename 'ping'\n"
    )


def test_folder_without_index_entry():
    result = run(FIXTURES / "folder_without_index")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "namespace folder 'catalog/spare' has no index entry\n"


def test_duplicate_error_code():
    result = run(FIXTURES / "duplicate_error_code")
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "duplicate error code 'no_page' in catalog/demo/ping.yaml\n"


def test_repo_catalog():
    result = subprocess.run(
        [sys.executable, str(CHECK)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_browser_manifests():
    folder = REPO / "catalog" / "browser"
    files = sorted(path.name for path in folder.glob("*.yaml"))
    assert files == [
        "click.yaml",
        "handoff.yaml",
        "navigate.yaml",
        "read.yaml",
        "screenshot.yaml",
        "scroll.yaml",
        "select.yaml",
        "type.yaml",
        "wait_for.yaml",
    ]
    manifests = {
        name: yaml.safe_load((folder / name).read_text(encoding="utf-8")) for name in files
    }

    assert manifests["navigate.yaml"]["name"] == "browser.navigate"
    assert manifests["read.yaml"]["name"] == "browser.read"
    assert manifests["screenshot.yaml"]["name"] == "browser.screenshot"
    assert manifests["handoff.yaml"]["name"] == "browser.handoff"
    assert manifests["read.yaml"]["version"] == "2.0.0"
    assert manifests["navigate.yaml"]["version"] == "1.0.0"
    assert manifests["screenshot.yaml"]["version"] == "1.0.0"
    assert manifests["handoff.yaml"]["version"] == "1.1.0"
    for name in ("click.yaml", "type.yaml", "scroll.yaml", "select.yaml", "wait_for.yaml"):
        assert manifests[name]["version"] == "1.0.0"
        assert manifests[name]["auth_needed"] == ["browser_session"]
        assert [item["code"] for item in manifests[name]["errors"]] == [
            "not_found",
            "ambiguous",
            "not_interactable",
            "timeout",
            "invalid_target",
            "no_session",
        ]
        assert manifests[name]["outputs"]["required"] == [
            "url",
            "title",
            "scroll_y",
            "viewport",
            "image",
        ]
    assert manifests["wait_for.yaml"]["side_effects"].startswith("None.")
    assert "should" not in manifests["click.yaml"]["when_to_use"]
    read_inputs = manifests["read.yaml"]["inputs"]["properties"]["max_viewports"]
    assert read_inputs["type"] == "integer"
    assert read_inputs["minimum"] == 1
    assert read_inputs["maximum"] == 30
    assert read_inputs["default"] == 10
    assert "text" not in manifests["read.yaml"]["outputs"]["properties"]
    assert manifests["read.yaml"]["outputs"]["required"] == [
        "url",
        "page_height",
        "viewport_height",
        "truncated",
        "viewports",
    ]
    assert "truncated" in manifests["read.yaml"]["when_to_use"]
    assert "max_viewports" in manifests["read.yaml"]["when_to_use"]
    assert "restores" in manifests["read.yaml"]["side_effects"]

    assert {item["code"] for item in manifests["navigate.yaml"]["errors"]} == {
        "invalid_url",
        "navigation_failed",
    }
    assert [item["code"] for item in manifests["read.yaml"]["errors"]] == ["no_page"]
    assert [item["code"] for item in manifests["screenshot.yaml"]["errors"]] == ["no_page"]
    assert [item["code"] for item in manifests["handoff.yaml"]["errors"]] == [
        "share_failed",
        "notify_failed",
    ]
    assert manifests["handoff.yaml"]["outputs"]["properties"]["status"]["enum"] == [
        "done",
        "timeout",
        "cancelled",
    ]
    assert manifests["handoff.yaml"]["outputs"]["required"] == ["status", "completed_by"]
    assert manifests["handoff.yaml"]["outputs"]["properties"]["completed_by"]["enum"] == [
        "user",
        "auto",
        "timeout",
        "cancelled",
    ]
    assert manifests["handoff.yaml"]["outputs"]["properties"]["reason"]["const"] == "auto_detected"
    assert "done_when" in manifests["handoff.yaml"]["inputs"]["properties"]
    assert "done_when" not in manifests["handoff.yaml"]["inputs"]["required"]
    assert "should" not in manifests["handoff.yaml"]["when_to_use"]
    assert "should" not in manifests["handoff.yaml"]["side_effects"]
    assert "Sign in or finish the step, then click Done." in manifests["handoff.yaml"]["side_effects"]
    assert manifests["read.yaml"]["side_effects"].startswith("None.")
    assert manifests["screenshot.yaml"]["side_effects"].startswith("None.")


def test_teams_send_message_draft():
    folder = REPO / "catalog" / "teams"
    files = sorted(path.name for path in folder.glob("*.yaml"))
    assert files == ["send_message.yaml"]
    manifest = yaml.safe_load((folder / "send_message.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "teams.send_message"
    assert manifest["version"] == "0.1.0"
    assert set(manifest["inputs"]["properties"]) == {"team_id", "chat_id", "text"}
    assert manifest["inputs"]["required"] == ["text"]
    assert manifest["outputs"]["required"] == ["message_id"]
    assert manifest["outputs"]["properties"]["message_id"]["type"] == "string"
    assert manifest["side_effects"] == "Posts a message visible to other people."
    assert [item["code"] for item in manifest["errors"]] == [
        "not_found",
        "forbidden",
        "throttled",
    ]
    assert manifest["auth_needed"] == ["microsoft_graph_delegated"]
    assert "ChannelMessage.Send" in manifest["when_to_use"]
    assert "ChatMessage.Send" in manifest["when_to_use"]
    index = yaml.safe_load((REPO / "catalog" / "index.yaml").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in index["namespaces"]] == ["browser", "teams"]
