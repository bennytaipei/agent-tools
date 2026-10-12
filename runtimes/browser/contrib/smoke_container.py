#!/usr/bin/env python3
"""Smoke-test the browser runtime container over MCP stdio.

Uses runtimes/browser/bin/agent-tools-browser --build. --engine docker or
--engine podman selects the engine. With no --engine, the choice matches the
launcher: docker when `docker info` succeeds, otherwise podman. A docker
binary that reports podman is the Podman compat shim.

Exit 0 when the smoke test passes. Exit 2 when the engine is not available.
Exit 1 when the build or the protocol check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

REPO = Path(__file__).resolve().parents[3]
LAUNCHER = REPO / "runtimes" / "browser" / "bin" / "agent-tools-browser"
IMAGE = "agent-tools-browser:smoke"
TOOLS = (
    "navigate",
    "read",
    "screenshot",
    "handoff",
)


@dataclass(frozen=True)
class EnginePlan:
    """How the launcher will run, so the smoke URL uses the same host alias."""

    command: str
    style: str
    alias: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the browser container.")
    parser.add_argument("--engine", choices=("docker", "podman"))
    args = parser.parse_args(argv)
    plan = engine_plan(args.engine)
    if plan is None:
        if args.engine:
            print(f"{args.engine} is not on PATH; smoke test not run", file=sys.stderr)
        else:
            print("no container engine available; smoke test not run", file=sys.stderr)
        return 2
    volume = f"agent-tools-browser-smoke-{os.getpid()}"
    handoff, novnc = _two_free_ports()
    site = _start_site()
    try:
        url = f"http://{plan.alias}:{site.server_address[1]}/landed"
        env = os.environ.copy()
        if args.engine:
            env["AGENT_TOOLS_CONTAINER_ENGINE"] = plan.command
        env["AGENT_TOOLS_IMAGE"] = IMAGE
        env["AGENT_TOOLS_PROFILE_VOLUME"] = volume
        env["AGENT_TOOLS_HANDOFF_PORT"] = str(handoff)
        env["AGENT_TOOLS_NOVNC_PORT"] = str(novnc)
        env["AGENT_TOOLS_NOTIFY_WEBHOOK"] = "http://127.0.0.1:9/unused"
        asyncio.run(_exercise(env, url))
    finally:
        site.shutdown()
        site.server_close()
        _cleanup(plan.command, volume)
    print("smoke test passed")
    return 0


def engine_plan(requested: str | None) -> EnginePlan | None:
    """Match runtimes/browser/bin/agent-tools-browser engine choice."""

    if requested == "podman":
        if shutil.which("podman") is None:
            return None
        return EnginePlan("podman", "podman", "host.containers.internal")
    if requested == "docker":
        if shutil.which("docker") is None:
            return None
        return EnginePlan("docker", "docker", "host.docker.internal")
    if shutil.which("docker") and _docker_info_ok():
        if _docker_is_podman_shim():
            if shutil.which("podman"):
                return EnginePlan("podman", "podman", "host.containers.internal")
            return EnginePlan("docker", "podman", "host.containers.internal")
        return EnginePlan("docker", "docker", "host.docker.internal")
    if shutil.which("podman"):
        return EnginePlan("podman", "podman", "host.containers.internal")
    return None


def _docker_info_ok() -> bool:
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _docker_is_podman_shim() -> bool:
    version = subprocess.run(
        ["docker", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if "podman" in (version.stdout or "").lower():
        return True
    info = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return "podman" in (info.stdout or "").lower()


def _two_free_ports() -> tuple[int, int]:
    sockets = []
    ports: list[int] = []
    for _ in range(2):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        ports.append(sock.getsockname()[1])
        sockets.append(sock)
    for sock in sockets:
        sock.close()
    return ports[0], ports[1]


def _start_site() -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"<!DOCTYPE html><html><body><p>SMOKE</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _cleanup(engine: str, volume: str) -> None:
    listed = subprocess.run(
        [engine, "ps", "-aq", "--filter", f"ancestor={IMAGE}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    ids = [line for line in listed.stdout.split() if line]
    if ids:
        subprocess.run(
            [engine, "rm", "-f", *ids],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    subprocess.run(
        [engine, "volume", "rm", "-f", volume],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


async def _exercise(env: dict[str, str], url: str) -> None:
    server = StdioServerParameters(
        command=str(LAUNCHER),
        args=["--build"],
        env=env,
        cwd=str(REPO),
    )
    async with Client(server, read_timeout_seconds=300) as client:
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        print("tools:", ", ".join(names))
        if names != list(TOOLS):
            raise SystemExit(f"expected {list(TOOLS)}, got {names}")
        result = await client.call_tool("navigate", {"url": url})
        if result.is_error:
            raise SystemExit(f"navigate failed: {result.content[0].text}")
        body = json.loads(result.content[0].text)
        print("url:", body.get("url"))
        if body.get("url") != url:
            raise SystemExit(f"expected {url}, got {body!r}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"command failed: {exc.cmd}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
