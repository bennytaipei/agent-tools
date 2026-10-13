"""smoke_container.py engine selection. No image build."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SMOKE_PATH = REPO / "runtimes" / "browser" / "contrib" / "smoke_container.py"
LAUNCHER = REPO / "runtimes" / "browser" / "bin" / "agent-tools-browser"

_SPEC = importlib.util.spec_from_file_location("smoke_container", SMOKE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
smoke = importlib.util.module_from_spec(_SPEC)
sys.modules["smoke_container"] = smoke
_SPEC.loader.exec_module(smoke)

STUB = """#!/bin/sh
if [ "${1:-}" = "info" ]; then
  if [ "${STUB_INFO_PODMAN:-}" = "1" ]; then
    echo podman
  fi
  exit "${STUB_INFO_RC:-0}"
fi
if [ "${1:-}" = "--version" ]; then
  if [ "${STUB_VERSION_PODMAN:-}" = "1" ]; then
    echo "podman version 5.0.0"
  else
    echo "Docker version 27.0.0"
  fi
  exit 0
fi
exit 0
"""


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, docker: bool, podman: bool) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, enabled in (("docker", docker), ("podman", podman)):
        if not enabled:
            continue
        path = bindir / name
        path.write_text(STUB, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))


def test_smoke_uses_the_launcher() -> None:
    assert smoke.LAUNCHER == LAUNCHER
    assert LAUNCHER.is_file()
    source = SMOKE_PATH.read_text(encoding="utf-8")
    assert '[str(LAUNCHER), "--build"]' in source
    assert "args=[]" in source
    assert "agent-tools-browser" in source


def test_help_lists_engine(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--help"])
    assert exc.value.code == 0
    assert "--engine" in capsys.readouterr().out


def test_missing_engine_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _install(tmp_path, monkeypatch, docker=False, podman=False)
    assert smoke.main([]) == 2
    assert "no container engine available" in capsys.readouterr().err
    assert smoke.main(["--engine", "docker"]) == 2


def test_explicit_engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, docker=True, podman=True)
    docker = smoke.engine_plan("docker")
    assert docker is not None
    assert (docker.command, docker.alias) == ("docker", "host.docker.internal")
    podman = smoke.engine_plan("podman")
    assert podman is not None
    assert (podman.command, podman.alias) == ("podman", "host.containers.internal")


def test_auto_docker_when_info_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, docker=True, podman=True)
    plan = smoke.engine_plan(None)
    assert plan is not None
    assert plan.command == "docker"
    assert plan.style == "docker"


def test_auto_podman_when_docker_info_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, docker=True, podman=True)
    monkeypatch.setenv("STUB_INFO_RC", "1")
    plan = smoke.engine_plan(None)
    assert plan is not None
    assert plan.command == "podman"
    assert plan.alias == "host.containers.internal"


def test_auto_shim_prefers_podman(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, docker=True, podman=True)
    monkeypatch.setenv("STUB_VERSION_PODMAN", "1")
    plan = smoke.engine_plan(None)
    assert plan is not None
    assert plan.command == "podman"
    assert plan.style == "podman"


def test_auto_shim_without_podman_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, docker=True, podman=False)
    monkeypatch.setenv("STUB_INFO_PODMAN", "1")
    plan = smoke.engine_plan(None)
    assert plan is not None
    assert plan.command == "docker"
    assert plan.style == "podman"
    assert plan.alias == "host.containers.internal"
