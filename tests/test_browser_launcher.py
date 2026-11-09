"""Launcher engine choice, with docker and podman replaced by PATH stubs.

The child PATH is only the stub directory. A runner that has real docker or
podman in /usr/bin must not see those binaries.
"""

from __future__ import annotations

import ast
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / "runtimes" / "browser" / "bin" / "agent-tools-browser"

STUB = """#!/bin/sh
if [ "${STUB_LOG_PATH:-}" = "1" ]; then
  printf 'PATH %s\\n' "$PATH" >> "$STUB_LOG"
fi
printf '%s' "$(basename "$0")" >> "$STUB_LOG"
for arg in "$@"; do
  printf ' <%s>' "$arg" >> "$STUB_LOG"
done
printf '\\n' >> "$STUB_LOG"
if [ "${1:-}" = "info" ]; then
  base=$(basename "$0")
  if [ "$base" = "docker" ] && [ -n "${STUB_DOCKER_INFO_RC+x}" ]; then
    if [ "${STUB_INFO_PODMAN:-}" = "1" ]; then
      echo podman
    fi
    exit "$STUB_DOCKER_INFO_RC"
  fi
  if [ -n "${STUB_INFO_SUCCEED_AFTER:-}" ]; then
    count_file="${STUB_INFO_COUNT_FILE:-$HOME/stub-info-count}"
    n=0
    if [ -f "$count_file" ]; then
      IFS= read -r n < "$count_file" || n=0
    fi
    case $n in
      ""|*[!0-9]*) n=0 ;;
    esac
    n=$((n + 1))
    printf '%s\n' "$n" > "$count_file"
    if [ "$n" -ge "$STUB_INFO_SUCCEED_AFTER" ]; then
      exit 0
    fi
    exit 1
  fi
  if [ -n "${STUB_READY_FILE:-}" ] && [ -f "$STUB_READY_FILE" ]; then
    exit 0
  fi
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
if [ "${1:-}" = "machine" ] && [ "${2:-}" = "list" ]; then
  if [ -n "${STUB_MACHINE_LIST+x}" ]; then
    printf '%s\n' "$STUB_MACHINE_LIST"
  fi
  exit 0
fi
if [ "${1:-}" = "machine" ] && [ "${2:-}" = "start" ]; then
  if [ -n "${STUB_START_STDERR:-}" ]; then
    printf '%s\n' "$STUB_START_STDERR" >&2
  fi
  if [ -n "${STUB_READY_FILE:-}" ]; then
    if [ "${STUB_START_RC:-0}" = "0" ] || [ "${STUB_READY_EVEN_IF_START_FAILS:-}" = "1" ]; then
      printf '' > "$STUB_READY_FILE"
    fi
  fi
  exit "${STUB_START_RC:-0}"
fi
if [ "${1:-}" = "desktop" ] && [ "${2:-}" = "status" ]; then
  if [ "${STUB_DESKTOP_STOPPED:-}" = "1" ]; then
    echo "Docker Desktop is stopped"
  fi
  exit 0
fi
if [ "${1:-}" = "desktop" ] && [ "${2:-}" = "start" ]; then
  if [ -n "${STUB_START_STDERR:-}" ]; then
    printf '%s\n' "$STUB_START_STDERR" >&2
  fi
  if [ -n "${STUB_READY_FILE:-}" ]; then
    if [ "${STUB_START_RC:-0}" = "0" ] || [ "${STUB_READY_EVEN_IF_START_FAILS:-}" = "1" ]; then
      printf '' > "$STUB_READY_FILE"
    fi
  fi
  exit "${STUB_START_RC:-0}"
fi
if [ "${1:-}" = "image" ]; then
  if [ "${STUB_IMAGE_MISSING:-}" = "1" ]; then
    printf '%s\n' "No such image" >&2
    exit 1
  fi
  exit 0
fi
if [ "${1:-}" = "run" ]; then
  if [ -n "${STUB_RUN_STDERR:-}" ]; then
    printf '%s\n' "$STUB_RUN_STDERR" >&2
  fi
  if [ -n "${STUB_FAKE_MCP:-}" ]; then
    exec "$STUB_FAKE_MCP"
  fi
  if [ -n "${STUB_RUN_HOLD:-}" ]; then
    while [ ! -f "$STUB_RUN_HOLD" ]; do
      sleep 0.05
    done
  fi
fi
exit 0
"""

OSASCRIPT = """#!/bin/sh
printf '%s' osascript >> "$STUB_LOG"
for arg in "$@"; do
  printf ' <%s>' "$arg" >> "$STUB_LOG"
done
printf '\\n' >> "$STUB_LOG"
if [ "${STUB_DIALOG:-start}" = "cancel" ]; then
  exit 1
fi
exit 0
"""

UNAME = """#!/bin/sh
printf '%s\\n' "${STUB_UNAME:-Linux}"
exit 0
"""


def _bins(
    tmp_path: Path,
    *,
    docker: bool,
    podman: bool,
    osascript: bool = True,
    python3: bool = True,
) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    for tool in (
        "dirname",
        "basename",
        "sleep",
        "rm",
        "mkdir",
        "date",
        "tail",
        "tee",
        "mkfifo",
        "wc",
        "mv",
    ):
        source = shutil.which(tool)
        if source is None:
            raise RuntimeError(f"{tool} is required to run the launcher")
        os.symlink(source, bindir / tool)
    if python3:
        source = shutil.which("python3")
        if source is None:
            raise RuntimeError("python3 is required to run the launcher")
        os.symlink(source, bindir / "python3")
    if docker:
        path = bindir / "docker"
        path.write_text(STUB, encoding="utf-8")
        path.chmod(0o755)
    if podman:
        path = bindir / "podman"
        path.write_text(STUB, encoding="utf-8")
        path.chmod(0o755)
    uname = bindir / "uname"
    uname.write_text(UNAME, encoding="utf-8")
    uname.chmod(0o755)
    if osascript:
        path = bindir / "osascript"
        path.write_text(OSASCRIPT, encoding="utf-8")
        path.chmod(0o755)
    return bindir


def _run(
    tmp_path: Path,
    *,
    docker: bool = True,
    podman: bool = True,
    osascript: bool = True,
    python3: bool = True,
    args: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = _bins(
        tmp_path,
        docker=docker,
        podman=podman,
        osascript=osascript,
        python3=python3,
    )
    log = tmp_path / "log"
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(log),
        "HOME": str(tmp_path),
    }
    if extra:
        env.update(extra)
    result = subprocess.run(
        [str(LAUNCHER), *(args or [])],
        env=env,
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, text


def _run_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if " <run>" in line]


_NAME_RE = re.compile(r"<--name> <(agent-tools-browser-\d+-[0-9a-f]+)>")
_PORT_RE = re.compile(r"<127\.0\.0\.1:(\d+):(\d+)>")


def _name_and_ports(line: str) -> tuple[str, list[tuple[str, str]]]:
    name = _NAME_RE.search(line)
    assert name is not None, line
    ports = _PORT_RE.findall(line)
    assert len(ports) == 2, line
    return name.group(1), ports


def _assert_published(line: str, handoff_host: str | None, novnc_host: str | None) -> tuple[str, str, str]:
    name, ports = _name_and_ports(line)
    assert ports[0][1] == "8765", line
    assert ports[1][1] == "8766", line
    if handoff_host is not None:
        assert ports[0][0] == handoff_host
    if novnc_host is not None:
        assert ports[1][0] == novnc_host
    assert ports[0][0] != ports[1][0]
    assert ports[0][0] != "0" and ports[1][0] != "0"
    assert f"<AGENT_TOOLS_HANDOFF_PORT=8765>" in line
    assert f"<AGENT_TOOLS_NOVNC_PORT=8766>" in line
    assert f"<AGENT_TOOLS_PUBLIC_HANDOFF_PORT={ports[0][0]}>" in line
    assert f"<AGENT_TOOLS_PUBLIC_NOVNC_PORT={ports[1][0]}>" in line
    return name, ports[0][0], ports[1][0]


def test_launcher_is_posix_sh() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert 'exec "$ENGINE" build ' in text
    assert '\nexec "$ENGINE"' not in text
    assert LAUNCHER.stat().st_mode & 0o111
    result = subprocess.run(["sh", "-n", str(LAUNCHER)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    dash = shutil.which("dash")
    if dash:
        dashed = subprocess.run([dash, "-n", str(LAUNCHER)], check=False, capture_output=True, text=True)
        assert dashed.returncode == 0, dashed.stderr


def test_docker_on_linux_is_the_default(tmp_path: Path) -> None:
    result, text = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    runs = _run_lines(text)
    assert len(runs) == 1
    line = runs[0]
    assert line.startswith("docker <run> <-i> <--rm>")
    assert "<--add-host=host.docker.internal:host-gateway>" in line
    assert "<agent-tools-browser-profile:/var/lib/agent-tools/profile>" in line
    assert ":U>" not in line
    assert "<AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.docker.internal:8767/>" in line
    name, _handoff, _novnc = _assert_published(line, None, None)
    assert f"<stop> <-t> <10> <{name}>" in text
    assert f"<rm> <-f> <{name}>" in text
    assert line.endswith(" <agent-tools-browser>")
    assert "host.containers.internal" not in line
    assert "osascript" not in text
    assert "<machine>" not in text
    assert "<desktop>" not in text


def test_podman_when_docker_info_fails(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"STUB_DOCKER_INFO_RC": "1"})
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert line.startswith("podman <run> <-i> <--rm>")
    assert "<agent-tools-browser-profile:/var/lib/agent-tools/profile:U>" in line
    assert "<AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:8767/>" in line
    assert "--add-host" not in line
    assert "host.docker.internal" not in line
    assert "<start>" not in text


def test_podman_when_docker_is_missing(tmp_path: Path) -> None:
    result, text = _run(tmp_path, docker=False)
    assert result.returncode == 0, result.stderr
    assert _run_lines(text)[0].startswith("podman <run>")
    assert "docker " not in text


def test_explicit_engine_skips_docker_probe(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"AGENT_TOOLS_CONTAINER_ENGINE": "podman"})
    assert result.returncode == 0, result.stderr
    assert _run_lines(text)[0].startswith("podman <run>")
    assert "docker " not in text
    assert ":U>" in _run_lines(text)[0]


def test_explicit_docker(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra={"AGENT_TOOLS_CONTAINER_ENGINE": "docker"},
    )
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert line.startswith("docker <run>")
    assert "<--add-host=host.docker.internal:host-gateway>" in line
    assert ":U>" not in line


def test_podman_shim_prefers_podman_binary(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"STUB_VERSION_PODMAN": "1"})
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert line.startswith("podman <run>")
    assert "<agent-tools-browser-profile:/var/lib/agent-tools/profile:U>" in line
    assert "host.containers.internal" in line
    assert "--add-host" not in line


def test_podman_shim_without_podman_binary_keeps_docker_command(tmp_path: Path) -> None:
    result, text = _run(tmp_path, podman=False, extra={"STUB_INFO_PODMAN": "1"})
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert line.startswith("docker <run>")
    assert "<agent-tools-browser-profile:/var/lib/agent-tools/profile:U>" in line
    assert "host.containers.internal" in line
    assert "--add-host" not in line
    assert "host.docker.internal" not in line


def test_docker_on_darwin_omits_add_host(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"STUB_UNAME": "Darwin"})
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert line.startswith("docker <run>")
    assert "--add-host" not in line
    assert "host.docker.internal" in line
    assert ":U>" not in line


def test_build_flag_only_builds(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        args=["--build"],
        extra={
            "AGENT_TOOLS_CONTAINER_ENGINE": "podman",
            "AGENT_TOOLS_IMAGE": "custom-browser",
            "AGENT_TOOLS_NOTIFY_PORT": "9999",
            "AGENT_TOOLS_HANDOFF_PORT": "9000",
            "AGENT_TOOLS_NOVNC_PORT": "9001",
            "AGENT_TOOLS_PROFILE_VOLUME": "profile-smoke",
        },
    )
    assert result.returncode == 0, result.stderr
    root = str(REPO)
    assert text.splitlines() == [
        "podman <info>",
        (
            f"podman <build> <-f> <{root}/runtimes/browser/Containerfile> "
            f"<-t> <custom-browser> <{root}>"
        ),
    ]


def test_run_overrides(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra={
            "AGENT_TOOLS_CONTAINER_ENGINE": "podman",
            "AGENT_TOOLS_IMAGE": "custom-browser",
            "AGENT_TOOLS_NOTIFY_PORT": "9999",
            "AGENT_TOOLS_HANDOFF_PORT": "9000",
            "AGENT_TOOLS_NOVNC_PORT": "9001",
            "AGENT_TOOLS_PROFILE_VOLUME": "profile-smoke",
        },
    )
    assert result.returncode == 0, result.stderr
    run = _run_lines(text)[0]
    _assert_published(run, "9000", "9001")
    assert "<AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.containers.internal:9999/>" in run
    assert "<profile-smoke:/var/lib/agent-tools/profile:U>" in run
    assert run.endswith(" <custom-browser>")
    assert "<build>" not in run


def test_notify_webhook_overrides_port(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra={
            "AGENT_TOOLS_CONTAINER_ENGINE": "podman",
            "AGENT_TOOLS_NOTIFY_WEBHOOK": "http://example.test/hook",
            "AGENT_TOOLS_NOTIFY_PORT": "9999",
        },
    )
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    assert "AGENT_TOOLS_NOTIFY_PORT" not in line
    webhook_bits = [part for part in line.split(" <") if "NOTIFY_WEBHOOK=" in part]
    assert webhook_bits == ["AGENT_TOOLS_NOTIFY_WEBHOOK=http://example.test/hook>"]


def test_stubs_hide_engines_outside_the_stub_dir(tmp_path: Path, monkeypatch) -> None:
    poison = tmp_path / "poison"
    poison.mkdir()
    script = "#!/bin/sh\nprintf 'POISON %s\\n' \"$0\" >> \"${STUB_LOG:-/dev/null}\"\nexit 99\n"
    for name in ("docker", "podman"):
        path = poison / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{poison}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}")
    result, text = _run(tmp_path, docker=False, extra={"STUB_LOG_PATH": "1"})
    assert result.returncode == 0, result.stderr
    assert "POISON" not in text
    assert _run_lines(text)[0].startswith("podman <run>")
    logged = [line.split(" ", 1)[1] for line in text.splitlines() if line.startswith("PATH ")]
    assert logged
    assert set(logged) == {str(tmp_path / "bin")}


def test_unknown_argument(tmp_path: Path) -> None:
    result, text = _run(tmp_path, args=["--nope"])
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert text == ""


def _stopped_machine(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "AGENT_TOOLS_CONTAINER_ENGINE": "podman",
        "STUB_INFO_RC": "1",
        "STUB_READY_FILE": str(tmp_path / "ready"),
        "STUB_MACHINE_LIST": "podman-machine-default* false false true",
    }
    env.update(extra)
    return env


def _assert_not_started(text: str) -> None:
    assert "<machine> <start>" not in text
    assert "<desktop> <start>" not in text
    assert _run_lines(text) == []


def test_auto_start_rejects_unknown_mode(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"AGENT_TOOLS_AUTO_START_ENGINE": "sometimes"})
    assert result.returncode == 2
    assert "always, ask, or never" in result.stderr
    assert text == ""


def test_start_timeout_rejects_zero(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"AGENT_TOOLS_ENGINE_START_TIMEOUT": "0"})
    assert result.returncode == 2
    assert "positive integer" in result.stderr
    assert text == ""


def test_podman_machine_never_does_not_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="never"),
    )
    assert result.returncode == 1
    assert "AGENT_TOOLS_AUTO_START_ENGINE=never" in result.stderr
    _assert_not_started(text)


def test_podman_machine_ask_without_tty_does_not_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="ask"),
    )
    assert result.returncode == 1
    assert "No way to ask for permission" in result.stderr
    assert "AGENT_TOOLS_AUTO_START_ENGINE=always" in result.stderr
    _assert_not_started(text)
    assert "osascript" not in text


def test_podman_machine_always_starts_then_runs(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="always"),
    )
    assert result.returncode == 0, result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert _run_lines(text)[0].startswith("podman <run>")
    assert "osascript" not in text


def test_stopped_default_machine_is_the_one_started(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            AGENT_TOOLS_AUTO_START_ENGINE="always",
            STUB_MACHINE_LIST="other false false false\npodman-machine-default* false false true",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert "<start> <other>" not in text


def test_running_machine_is_not_started(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            AGENT_TOOLS_AUTO_START_ENGINE="always",
            STUB_MACHINE_LIST="podman-machine-default* true false true\nother false false false",
            STUB_INFO_SUCCEED_AFTER="2",
            AGENT_TOOLS_ENGINE_START_TIMEOUT="5",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "<machine> <start>" not in text
    assert "<desktop> <start>" not in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_darwin_dialog_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(tmp_path, STUB_UNAME="Darwin", AGENT_TOOLS_AUTO_START_ENGINE="ask"),
    )
    assert result.returncode == 0, result.stderr
    dialog = next(line for line in text.splitlines() if line.startswith("osascript "))
    assert 'buttons {"Start", "Cancel"}' in dialog
    assert 'cancel button "Cancel"' in dialog
    assert "default button \"Start\"" in dialog
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_darwin_dialog_cancel(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            STUB_UNAME="Darwin",
            STUB_DIALOG="cancel",
            AGENT_TOOLS_AUTO_START_ENGINE="ask",
        ),
    )
    assert result.returncode == 1
    assert "Start was cancelled" in result.stderr
    _assert_not_started(text)


def test_darwin_without_osascript_does_not_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        osascript=False,
        extra=_stopped_machine(tmp_path, STUB_UNAME="Darwin", AGENT_TOOLS_AUTO_START_ENGINE="ask"),
    )
    assert result.returncode == 1
    assert "No way to ask for permission" in result.stderr
    _assert_not_started(text)


def test_podman_machine_start_failure(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            AGENT_TOOLS_AUTO_START_ENGINE="always",
            AGENT_TOOLS_ENGINE_START_TIMEOUT="1",
            STUB_START_RC="1",
        ),
    )
    assert result.returncode == 1
    assert "Podman machine failed to start" in result.stderr
    assert "Timed out after 1s waiting for podman" in result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert "<podman-machine-default*>" not in text
    assert _run_lines(text) == []


def test_engine_start_timeout(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            AGENT_TOOLS_AUTO_START_ENGINE="always",
            AGENT_TOOLS_ENGINE_START_TIMEOUT="1",
            STUB_READY_FILE="",
        ),
    )
    assert result.returncode == 1
    assert "Timed out after 1s waiting for podman" in result.stderr
    assert _run_lines(text) == []


def test_docker_desktop_always_starts_then_runs(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra={
            "STUB_INFO_RC": "1",
            "STUB_DESKTOP_STOPPED": "1",
            "STUB_READY_FILE": str(tmp_path / "ready"),
            "AGENT_TOOLS_AUTO_START_ENGINE": "always",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "docker <desktop> <start>" in text
    assert _run_lines(text)[0].startswith("docker <run>")
    assert "podman <run>" not in text


def test_build_starts_stopped_machine_and_does_not_run(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        args=["--build"],
        extra=_stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="always"),
    )
    assert result.returncode == 0, result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert "<build>" in text
    assert _run_lines(text) == []


def _run_tty(tmp_path: Path, answer: bytes, extra: dict[str, str]) -> tuple[int, str, str]:
    bindir = _bins(tmp_path, docker=True, podman=True)
    log = tmp_path / "log"
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(log),
        "HOME": str(tmp_path),
        **extra,
    }
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(REPO)
        os.execve(str(LAUNCHER), [str(LAUNCHER)], env)
        os._exit(127)
    os.set_blocking(fd, False)
    buf = b""
    sent = False
    status: int | None = None
    deadline = time.time() + 5
    try:
        while time.time() < deadline:
            if fd < 0:
                chunk = b""
            else:
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    chunk = b""
                except OSError:
                    chunk = b""
            if chunk:
                buf += chunk
                if not sent and b"[y/N]" in buf:
                    os.write(fd, answer)
                    sent = True
                if sent and fd >= 0 and b"Start was cancelled" in buf:
                    os.close(fd)
                    fd = -1
            wait_pid, wait_status = os.waitpid(pid, os.WNOHANG)
            if wait_pid == pid:
                status = wait_status
                break
            if not chunk:
                time.sleep(0.02)
        if status is None:
            os.kill(pid, 15)
            os.waitpid(pid, 0)
            raise AssertionError(f"launcher did not exit, tty={buf!r}")
    finally:
        if fd >= 0:
            os.close(fd)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
    else:
        code = 1
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return code, buf.decode(errors="replace"), text


def test_linux_tty_yes_starts_podman(tmp_path: Path) -> None:
    code, _tty, text = _run_tty(
        tmp_path,
        b"y\n",
        _stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="ask"),
    )
    assert code == 0, text
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_linux_tty_no_does_not_start(tmp_path: Path) -> None:
    code, tty, text = _run_tty(
        tmp_path,
        b"n\n",
        _stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="ask"),
    )
    assert code == 1
    assert "Start was cancelled" in tty
    _assert_not_started(text)


def test_unset_empty_zero_and_auto_pick_free_host_ports(tmp_path: Path) -> None:
    cases = (
        {},
        {"AGENT_TOOLS_HANDOFF_PORT": "", "AGENT_TOOLS_NOVNC_PORT": ""},
        {"AGENT_TOOLS_HANDOFF_PORT": "0", "AGENT_TOOLS_NOVNC_PORT": "0"},
        {"AGENT_TOOLS_HANDOFF_PORT": "auto", "AGENT_TOOLS_NOVNC_PORT": "auto"},
        {"AGENT_TOOLS_HANDOFF_PORT": "0", "AGENT_TOOLS_NOVNC_PORT": "auto"},
    )
    for index, extra in enumerate(cases):
        result, text = _run(tmp_path / f"case-{index}", extra=extra or None)
        assert result.returncode == 0, result.stderr
        line = _run_lines(text)[0]
        _assert_published(line, None, None)
        assert "<127.0.0.1:0:" not in line


def test_explicit_ports_map_onto_container_listeners(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra={"AGENT_TOOLS_HANDOFF_PORT": "9000"},
    )
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    _name, handoff, novnc = _assert_published(line, "9000", None)
    assert novnc != "9000"
    assert f"<127.0.0.1:{novnc}:8766>" in line


def test_unique_container_names(tmp_path: Path) -> None:
    _result_a, text_a = _run(tmp_path / "a")
    _result_b, text_b = _run(tmp_path / "b")
    name_a, _ports_a = _name_and_ports(_run_lines(text_a)[0])
    name_b, _ports_b = _name_and_ports(_run_lines(text_b)[0])
    assert name_a != name_b


def test_port_scan_without_python3(tmp_path: Path) -> None:
    result, text = _run(tmp_path, python3=False)
    assert result.returncode == 0, result.stderr
    line = _run_lines(text)[0]
    _assert_published(line, None, None)


def _popen(
    tmp_path: Path,
    *,
    extra: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = _bins(tmp_path, docker=True, podman=True)
    log = tmp_path / "log"
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(log),
        "HOME": str(tmp_path),
    }
    if extra:
        env.update(extra)
    return subprocess.Popen(
        [str(LAUNCHER)],
        env=env,
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _wait_log(path: Path, needle: str, timeout: float = 8.0) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        if path.exists():
            last = path.read_text(encoding="utf-8")
            if needle in last:
                return last
        time.sleep(0.05)
    raise AssertionError(f"missing {needle!r} in {last!r}")


def _stop_procs(procs: list[subprocess.Popen[str]]) -> None:
    for proc in procs:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)


def test_trap_cleanup_on_term(tmp_path: Path) -> None:
    hold = tmp_path / "hold"
    work = tmp_path / "run"
    proc = _popen(work, extra={"STUB_RUN_HOLD": str(hold)})
    try:
        text = _wait_log(work / "log", " <run>")
        name, _ports = _name_and_ports(_run_lines(text)[0])
        os.kill(proc.pid, signal.SIGTERM)
        code = proc.wait(timeout=8)
        log = (work / "log").read_text(encoding="utf-8")
        assert f"<stop> <-t> <10> <{name}>" in log
        assert f"<rm> <-f> <{name}>" in log
        assert code == 143, log
    finally:
        hold.write_text("go\n", encoding="utf-8")
        _stop_procs([proc])


def test_concurrent_launches_get_distinct_ports(tmp_path: Path) -> None:
    hold = tmp_path / "hold"
    procs: list[subprocess.Popen[str]] = []
    try:
        procs = [
            _popen(tmp_path / "a", extra={"STUB_RUN_HOLD": str(hold)}),
            _popen(tmp_path / "b", extra={"STUB_RUN_HOLD": str(hold)}),
        ]
        text_a = _wait_log(tmp_path / "a" / "log", " <run>")
        text_b = _wait_log(tmp_path / "b" / "log", " <run>")
        name_a, ports_a = _name_and_ports(_run_lines(text_a)[0])
        name_b, ports_b = _name_and_ports(_run_lines(text_b)[0])
        assert name_a != name_b
        hosts_a = {ports_a[0][0], ports_a[1][0]}
        hosts_b = {ports_b[0][0], ports_b[1][0]}
        assert hosts_a.isdisjoint(hosts_b)
        assert ports_a[0][1] == "8765" and ports_a[1][1] == "8766"
        assert ports_b[0][1] == "8765" and ports_b[1][1] == "8766"
        hold.write_text("go\n", encoding="utf-8")
        for proc in procs:
            code = proc.wait(timeout=8)
            assert code == 0
        final_a = (tmp_path / "a" / "log").read_text(encoding="utf-8")
        final_b = (tmp_path / "b" / "log").read_text(encoding="utf-8")
        assert f"<stop> <-t> <10> <{name_a}>" in final_a
        assert f"<rm> <-f> <{name_a}>" in final_a
        assert f"<stop> <-t> <10> <{name_b}>" in final_b
        assert f"<rm> <-f> <{name_b}>" in final_b
    finally:
        hold.write_text("go\n", encoding="utf-8")
        _stop_procs(procs)


DEGRADED = REPO / "runtimes" / "browser" / "bin" / "agent-tools-browser-degraded"
TOOLS_SNAPSHOT = REPO / "runtimes" / "browser" / "tools.json"
CATALOG_TOOL_ORDER = (
    "navigate",
    "read",
    "screenshot",
    "handoff",
    "click",
    "type",
    "scroll",
    "select",
    "wait_for",
)

FAKE_MCP = """#!/usr/bin/env python3
import json
import sys

def reply(msg_id, result):
    sys.stdout.buffer.write(
        (json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\\n").encode()
    )
    sys.stdout.buffer.flush()

while True:
    line = sys.stdin.buffer.readline()
    if not line:
        break
    try:
        msg = json.loads(line.decode())
    except Exception:
        continue
    if not isinstance(msg, dict) or msg.get("id") is None:
        continue
    if msg.get("method") == "tools/call":
        reply(msg["id"], {"content": [{"type": "text", "text": "proxied-ok"}], "isError": False})
    else:
        reply(msg["id"], {})
"""


def _catalog_tools() -> list[dict]:
    tools = []
    folder = REPO / "catalog" / "browser"
    for name in CATALOG_TOOL_ORDER:
        data = yaml.safe_load((folder / f"{name}.yaml").read_text(encoding="utf-8"))
        tools.append(
            {
                "name": name,
                "description": data["when_to_use"],
                "inputSchema": data["inputs"],
            }
        )
    return tools


def test_tools_snapshot_matches_catalog() -> None:
    snapshot = json.loads(TOOLS_SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == _catalog_tools()


def test_degraded_server_py39_syntax() -> None:
    compiled = subprocess.run(
        [sys.executable, "-m", "py_compile", str(DEGRADED)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    source = DEGRADED.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert type(node).__name__ != "Match"
        annotation = getattr(node, "annotation", None)
        if isinstance(annotation, ast.BinOp):
            assert not isinstance(annotation.op, ast.BitOr)
        returns = getattr(node, "returns", None)
        if isinstance(returns, ast.BinOp):
            assert not isinstance(returns.op, ast.BitOr)
    assert "\nwith (" not in source


def test_stopped_machine_autostarts_and_strips_star(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra=_stopped_machine(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert "<podman-machine-default*>" not in text
    assert "osascript" not in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_starting_machine_waits_without_calling_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            STUB_MACHINE_LIST="podman-machine-default* false true true",
            STUB_INFO_SUCCEED_AFTER="2",
            AGENT_TOOLS_ENGINE_START_TIMEOUT="5",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "<machine> <start>" not in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_machine_start_error_still_waits_for_info(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(
            tmp_path,
            STUB_START_RC="1",
            STUB_READY_EVEN_IF_START_FAILS="1",
            STUB_START_STDERR="Error: podman-machine-default*: VM does not exist",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert "<podman-machine-default*>" not in text
    assert "Podman machine failed to start" in result.stderr
    assert _run_lines(text)[0].startswith("podman <run>")
    body = (tmp_path / ".local" / "state" / "agent-tools" / "browser.log").read_text(encoding="utf-8")
    assert "VM does not exist" in body


def test_never_without_python3_exits(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        python3=False,
        extra=_stopped_machine(tmp_path, AGENT_TOOLS_AUTO_START_ENGINE="never"),
    )
    assert result.returncode == 1
    assert "AGENT_TOOLS_AUTO_START_ENGINE=never" in result.stderr
    _assert_not_started(text)
    body = (tmp_path / ".local" / "state" / "agent-tools" / "browser.log").read_text(encoding="utf-8")
    assert "python3 missing" in body


def test_log_records_decisions_and_override(tmp_path: Path) -> None:
    log = tmp_path / "custom.log"
    result, _text = _run(
        tmp_path,
        extra={
            "AGENT_TOOLS_LOG_FILE": str(log),
            "STUB_RUN_STDERR": "container-stderr-marker",
        },
    )
    assert result.returncode == 0, result.stderr
    body = log.read_text(encoding="utf-8")
    assert "engine chosen:" in body
    assert "state ready:" in body
    assert "container exit" in body
    assert "container-stderr-marker" in body
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \d+ ", body)


def test_default_and_darwin_log_paths(tmp_path: Path) -> None:
    result, _text = _run(tmp_path / "linux")
    assert result.returncode == 0, result.stderr
    linux = tmp_path / "linux" / ".local" / "state" / "agent-tools" / "browser.log"
    assert "state ready:" in linux.read_text(encoding="utf-8")
    result, _text = _run(tmp_path / "mac", extra={"STUB_UNAME": "Darwin"})
    assert result.returncode == 0, result.stderr
    mac = tmp_path / "mac" / "Library" / "Logs" / "agent-tools-browser.log"
    assert "state ready:" in mac.read_text(encoding="utf-8")


def test_xdg_state_home_log_path(tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    result, _text = _run(tmp_path / "run", extra={"XDG_STATE_HOME": str(xdg)})
    assert result.returncode == 0, result.stderr
    assert "state ready:" in (xdg / "agent-tools" / "browser.log").read_text(encoding="utf-8")


def test_log_keeps_last_megabyte_when_over_cap(tmp_path: Path) -> None:
    log = tmp_path / "big.log"
    log.write_bytes(b"A" * (5 * 1024 * 1024 + 80))
    result, _text = _run(tmp_path / "run", extra={"AGENT_TOOLS_LOG_FILE": str(log)})
    assert result.returncode == 0, result.stderr
    data = log.read_bytes()
    assert len(data) < 2 * 1024 * 1024
    assert b"state ready:" in data


def _start_mcp(tmp_path: Path, extra: dict[str, str]) -> subprocess.Popen[bytes]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = _bins(tmp_path, docker=True, podman=True)
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(tmp_path / "log"),
        "HOME": str(tmp_path),
        **extra,
    }
    proc = subprocess.Popen(
        [str(LAUNCHER)],
        env=env,
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    def _drain() -> None:
        assert proc.stderr is not None
        proc.stderr.read()

    threading.Thread(target=_drain, daemon=True).start()
    return proc


def _write_mcp(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    assert proc.stdin is not None
    proc.stdin.write(payload)
    flusher = getattr(proc.stdin, "flush", None)
    if flusher is not None:
        flusher()


def _read_mcp(proc: subprocess.Popen[bytes], timeout: float) -> dict:
    assert proc.stdout is not None
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if not ready:
            if proc.poll() is not None:
                break
            continue
        piece = proc.stdout.read(1)
        if not piece:
            break
        buf += piece
        if piece == b"\n":
            return json.loads(buf.decode())
    raise AssertionError(f"no MCP response in {buf!r} rc={proc.poll()}")


def _stop_mcp(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_engine_never_up_answers_mcp_until_stdin_closes(tmp_path: Path) -> None:
    log = tmp_path / "browser.log"
    proc = _start_mcp(
        tmp_path,
        _stopped_machine(
            tmp_path,
            AGENT_TOOLS_ENGINE_START_TIMEOUT="1",
            AGENT_TOOLS_LOG_FILE=str(log),
            STUB_START_RC="1",
        ),
    )
    try:
        _write_mcp(
            proc,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                }
            ).encode()
            + b"\n",
        )
        init = _read_mcp(proc, 8)
        assert init["id"] == 1
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["capabilities"] == {"tools": {}}
        assert init["result"]["serverInfo"]["name"] == "agent-tools-browser"
        assert "not up yet" in init["result"]["instructions"]
        _write_mcp(
            proc,
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n",
        )
        _write_mcp(proc, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode() + b"\n")
        listed = _read_mcp(proc, 8)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == [tool["name"] for tool in _catalog_tools()]
        assert listed["result"]["tools"] == _catalog_tools()
        _write_mcp(
            proc,
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "navigate", "arguments": {}}}).encode()
            + b"\n",
        )
        called = _read_mcp(proc, 8)
        assert called["result"]["isError"] is True
        text = called["result"]["content"][0]["text"]
        assert "agent-tools-browser is not running:" in text
        assert "Timed out after 1s waiting for podman" in text
        assert str(log) in text
        assert proc.poll() is None
        _write_mcp(proc, json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}).encode() + b"\n")
        ping = _read_mcp(proc, 8)
        assert ping["result"] == {}
        _write_mcp(proc, json.dumps({"jsonrpc": "2.0", "id": 5, "method": "nope"}).encode() + b"\n")
        missing = _read_mcp(proc, 8)
        assert missing["error"]["code"] == -32601
        assert proc.poll() is None
        assert proc.stdin is not None
        proc.stdin.close()
        code = proc.wait(timeout=5)
        assert code == 1
        stub = (tmp_path / "log").read_text(encoding="utf-8")
        assert "podman <machine> <start> <podman-machine-default>" in stub
        assert "<podman-machine-default*>" not in stub
    finally:
        _stop_mcp(proc)


def test_engine_recovers_and_proxies_the_same_session(tmp_path: Path) -> None:
    fake = tmp_path / "fake-mcp"
    fake.write_text(FAKE_MCP, encoding="utf-8")
    fake.chmod(0o755)
    ready = tmp_path / "ready"
    log = tmp_path / "browser.log"
    proc = _start_mcp(
        tmp_path,
        _stopped_machine(
            tmp_path,
            AGENT_TOOLS_ENGINE_START_TIMEOUT="1",
            AGENT_TOOLS_LOG_FILE=str(log),
            STUB_START_RC="1",
            STUB_READY_FILE=str(ready),
            STUB_FAKE_MCP=str(fake),
        ),
    )
    try:
        _write_mcp(
            proc,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                }
            ).encode()
            + b"\n",
        )
        init = _read_mcp(proc, 8)
        assert init["result"]["serverInfo"]["name"] == "agent-tools-browser"
        _write_mcp(proc, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode() + b"\n")
        listed = _read_mcp(proc, 8)
        assert [tool["name"] for tool in listed["result"]["tools"]] == list(CATALOG_TOOL_ORDER)
        ready.write_text("", encoding="utf-8")
        _write_mcp(
            proc,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "navigate", "arguments": {"url": "https://example.test/"}},
                }
            ).encode()
            + b"\n",
        )
        called = _read_mcp(proc, 20)
        assert called["result"]["isError"] is False
        assert called["result"]["content"][0]["text"] == "proxied-ok"
        assert proc.poll() is None
        _write_mcp(proc, json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}).encode() + b"\n")
        ping = _read_mcp(proc, 8)
        assert ping["result"] == {}
        assert proc.poll() is None
        assert proc.stdin is not None
        proc.stdin.close()
        code = proc.wait(timeout=8)
        assert code == 0
    finally:
        _stop_mcp(proc)


def test_image_missing_degraded_names_build(tmp_path: Path) -> None:
    log = tmp_path / "browser.log"
    proc = _start_mcp(
        tmp_path,
        {
            "AGENT_TOOLS_CONTAINER_ENGINE": "podman",
            "STUB_IMAGE_MISSING": "1",
            "AGENT_TOOLS_LOG_FILE": str(log),
        },
    )
    try:
        _write_mcp(
            proc,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
                }
            ).encode()
            + b"\n",
        )
        init = _read_mcp(proc, 8)
        assert init["result"]["protocolVersion"] == "2025-06-18"
        assert "not up yet" in init["result"]["instructions"]
        _write_mcp(
            proc,
            json.dumps(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "navigate", "arguments": {}}}
            ).encode()
            + b"\n",
        )
        called = _read_mcp(proc, 8)
        text = called["result"]["content"][0]["text"]
        assert called["result"]["isError"] is True
        assert "agent-tools-browser --build" in text
        assert str(log) in text
        assert proc.poll() is None
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(timeout=5) == 1
    finally:
        _stop_mcp(proc)
