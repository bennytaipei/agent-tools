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
    assert files == ["handoff.yaml", "navigate.yaml", "read.yaml", "screenshot.yaml"]
    manifests = {
        name: yaml.safe_load((folder / name).read_text(encoding="utf-8")) for name in files
    }

    assert manifests["navigate.yaml"]["name"] == "browser.navigate"
    assert manifests["read.yaml"]["name"] == "browser.read"
    assert manifests["screenshot.yaml"]["name"] == "browser.screenshot"
    assert manifests["handoff.yaml"]["name"] == "browser.handoff"
    assert {body["version"] for body in manifests.values()} == {"1.0.0"}

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
    assert manifests["read.yaml"]["side_effects"].startswith("None.")
    assert manifests["screenshot.yaml"]["side_effects"].startswith("None.")
