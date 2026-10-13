"""Launcher engine choice, with docker and podman replaced by PATH stubs.

The child PATH is only the stub directory. A runner that has real docker or
podman in /usr/bin must not see those binaries.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
import time
from pathlib import Path

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
  if [ -n "${STUB_READY_FILE:-}" ]; then
    printf '' > "$STUB_READY_FILE"
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
  if [ -n "${STUB_READY_FILE:-}" ]; then
    printf '' > "$STUB_READY_FILE"
  fi
  exit "${STUB_START_RC:-0}"
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


def _bins(tmp_path: Path, *, docker: bool, podman: bool, osascript: bool = True) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("dirname", "basename", "sleep"):
        source = shutil.which(tool)
        if source is None:
            raise RuntimeError(f"{tool} is required to run the launcher")
        os.symlink(source, bindir / tool)
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
    args: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    bindir = _bins(tmp_path, docker=docker, podman=podman, osascript=osascript)
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
        capture_output=True,
        text=True,
        check=False,
    )
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, text


def _run_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if " <run>" in line]


def test_launcher_is_posix_sh() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "\nexec " in text
    assert LAUNCHER.stat().st_mode & 0o111
    result = subprocess.run(["sh", "-n", str(LAUNCHER)], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_docker_on_linux_is_the_default(tmp_path: Path) -> None:
    result, text = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    runs = _run_lines(text)
    assert len(runs) == 1
    line = runs[0]
    assert line.startswith("docker <run> <-i> <--rm>")
    assert "<--add-host=host.docker.internal:host-gateway>" in line
    assert "<agent-tools-browser-profile:/var/lib/agent-tools/profile>" in line
    assert ":U>" not in line
    assert "<AGENT_TOOLS_NOTIFY_WEBHOOK=http://host.docker.internal:8767/>" in line
    assert "<-p> <127.0.0.1:8765:8765>" in line
    assert "<-p> <127.0.0.1:8766:8766>" in line
    assert line.endswith(" <agent-tools-browser>")
    assert "host.containers.internal" not in line
    assert "osascript" not in text
    assert "<machine>" not in text
    assert "<desktop>" not in text


def test_podman_when_docker_info_fails(tmp_path: Path) -> None:
    result, text = _run(tmp_path, extra={"STUB_INFO_RC": "1"})
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
        extra={"AGENT_TOOLS_CONTAINER_ENGINE": "docker", "STUB_INFO_RC": "1"},
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
    assert "<127.0.0.1:9000:9000>" in run
    assert "<127.0.0.1:9001:9001>" in run
    assert "<AGENT_TOOLS_HANDOFF_PORT=9000>" in run
    assert "<AGENT_TOOLS_NOVNC_PORT=9001>" in run
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
    assert "<AGENT_TOOLS_NOTIFY_WEBHOOK=http://example.test/hook>" in line
    assert "9999" not in line


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
        "STUB_MACHINE_LIST": "podman-machine-default false true",
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
    result, text = _run(tmp_path, extra=_stopped_machine(tmp_path))
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
            STUB_MACHINE_LIST="other false false\npodman-machine-default false true",
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
            STUB_MACHINE_LIST="podman-machine-default true true\nother false",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "<machine> <start>" not in text
    assert "<desktop> <start>" not in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_darwin_dialog_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        extra=_stopped_machine(tmp_path, STUB_UNAME="Darwin"),
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
        extra=_stopped_machine(tmp_path, STUB_UNAME="Darwin", STUB_DIALOG="cancel"),
    )
    assert result.returncode == 1
    assert "Start was cancelled" in result.stderr
    _assert_not_started(text)


def test_darwin_without_osascript_does_not_start(tmp_path: Path) -> None:
    result, text = _run(
        tmp_path,
        osascript=False,
        extra=_stopped_machine(tmp_path, STUB_UNAME="Darwin"),
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
            STUB_START_RC="1",
        ),
    )
    assert result.returncode == 1
    assert "Podman machine failed to start" in result.stderr
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
        os.close(fd)
    if os.WIFEXITED(status):
        code = os.WEXITSTATUS(status)
    else:
        code = 1
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return code, buf.decode(errors="replace"), text


def test_linux_tty_yes_starts_podman(tmp_path: Path) -> None:
    code, _tty, text = _run_tty(tmp_path, b"y\n", _stopped_machine(tmp_path))
    assert code == 0, text
    assert "podman <machine> <start> <podman-machine-default>" in text
    assert _run_lines(text)[0].startswith("podman <run>")


def test_linux_tty_no_does_not_start(tmp_path: Path) -> None:
    code, tty, text = _run_tty(tmp_path, b"n\n", _stopped_machine(tmp_path))
    assert code == 1
    assert "Start was cancelled" in tty
    _assert_not_started(text)
