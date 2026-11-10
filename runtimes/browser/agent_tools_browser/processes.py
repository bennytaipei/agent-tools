"""Start and stop only the processes this runtime created.

The machine is shared. Cleanup matches a pid's start time or a unique path
this runtime put on the child's command line. It never signals a process
that fails both checks.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agent_tools_browser")

_OURS = ("Xvfb", "x11vnc", "websockify", "chrome", "chromium", "google-chrome", "playwright")


class ProcessError(RuntimeError):
    pass


@dataclass
class Tracked:
    proc: subprocess.Popen[bytes]
    start_time: str | None
    label: str


def displays_in_use() -> set[int]:
    used: set[int] = set()
    socket_dir = Path("/tmp/.X11-unix")
    if socket_dir.is_dir():
        for entry in socket_dir.iterdir():
            number = entry.name[1:]
            if entry.name.startswith("X") and number.isdigit():
                used.add(int(number))
    for entry in Path("/tmp").glob(".X*-lock"):
        number = entry.name[2:-5]
        if number.isdigit():
            used.add(int(number))
    return used


def process_start_time(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    tail = text.rsplit(")", 1)[-1].split()
    # Field 22 of /proc/pid/stat is starttime. Field 3 is the first token after comm.
    if len(tail) <= 19:
        return None
    return tail[19]


def cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")


def spawn(args: list[str], *, env: dict[str, str], label: str, stderr_path: Path) -> Tracked:
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_handle = stderr_path.open("ab")
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            env=env,
            start_new_session=False,
        )
    finally:
        stderr_handle.close()
    start = process_start_time(proc.pid)
    log.info("started %s pid %s", label, proc.pid)
    return Tracked(proc=proc, start_time=start, label=label)


def stop_tracked(tracked: Tracked | None, markers: list[str]) -> None:
    if tracked is None:
        return
    _signal(tracked.proc.pid, signal.SIGTERM, tracked.start_time, markers)
    try:
        tracked.proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        _signal(tracked.proc.pid, signal.SIGKILL, tracked.start_time, markers)
        try:
            tracked.proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            log.warning("%s pid %s did not exit", tracked.label, tracked.proc.pid)


def _signal(pid: int, sig: int, start_time: str | None, markers: list[str]) -> None:
    if not _may_signal(pid, start_time, markers):
        return
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("not permitted to signal pid %s", pid)


def command_has_path(command: str, path: str) -> bool:
    """True when path is its own argument, not a prefix of a longer path."""

    if len(path) < 12:
        return False
    start = 0
    while True:
        index = command.find(path, start)
        if index < 0:
            return False
        end = index + len(path)
        if end >= len(command) or command[end] in " /\"'":
            return True
        start = index + 1


def _may_signal(pid: int, start_time: str | None, markers: list[str]) -> bool:
    if pid <= 1 or pid == os.getpid():
        return False
    command = cmdline(pid)
    if not command:
        return False
    if any(command_has_path(command, marker) for marker in markers):
        return True
    if start_time is None or process_start_time(pid) != start_time:
        return False
    return any(token in command for token in _OURS)


def pids_matching(markers: list[str]) -> list[int]:
    """Pids whose command line contains one of the unique paths we created."""

    usable = [marker for marker in markers if len(marker) >= 12]
    if not usable:
        return []
    found: list[int] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        command = cmdline(pid)
        if command and any(command_has_path(command, marker) for marker in usable):
            found.append(pid)
    return found


def reap(markers: list[str]) -> list[int]:
    """Stop processes this runtime started. Returns pids that were still alive."""

    alive = pids_matching(markers)
    for pid in alive:
        _signal(pid, signal.SIGTERM, None, markers)
    if alive:
        time.sleep(0.3)
    still = [pid for pid in alive if _pid_alive(pid)]
    for pid in still:
        _signal(pid, signal.SIGKILL, None, markers)
    if still:
        time.sleep(0.2)
    return [pid for pid in still if _pid_alive(pid)]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until_listening(
    port: int,
    tracked: Tracked,
    timeout: float = 5.0,
    host: str = "127.0.0.1",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tracked.proc.poll() is not None:
            raise ProcessError(f"{tracked.label} exited {tracked.proc.returncode}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
            except OSError:
                time.sleep(0.05)
                continue
            return
    raise ProcessError(f"{tracked.label} did not listen on {host}:{port}")


def port_is_open(port: int) -> bool:
    if port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


def describe_port_holder(port: int) -> str:
    """Process command lines that still LISTEN on this TCP port. Empty if none."""

    if port <= 0:
        return ""
    inodes: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = Path(f"/proc/net/{name}").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[3] != "0A":
                continue
            hex_port = parts[1].rsplit(":", 1)[-1]
            try:
                found = int(hex_port, 16) == port
            except ValueError:
                continue
            if found:
                inodes.add(parts[9])
    if not inodes:
        return ""
    holders: list[str] = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if not (target.startswith("socket:[") and target.endswith("]")):
                continue
            if target[8:-1] not in inodes:
                continue
            command = cmdline(int(entry.name))[:160]
            holders.append(f"pid {entry.name} {command}".strip())
            break
    return "; ".join(holders)


def tail_file(path: Path, limit: int = 2000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-limit:]
