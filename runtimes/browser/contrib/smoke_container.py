#!/usr/bin/env python3
"""Smoke-test the browser runtime container over MCP stdio.

Uses podman when it is on PATH, otherwise docker. Builds the image, runs it
with -i, sends MCP initialize and tools/list, and calls navigate
against a page served on the host.

Exit 0 when the smoke test passes. Exit 2 when neither engine is on PATH.
Exit 1 when the build or the protocol check fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

REPO = Path(__file__).resolve().parents[3]
IMAGE = "agent-tools-browser:smoke"
TOOLS = (
    "navigate",
    "read",
    "screenshot",
    "handoff",
)


def engine_name() -> str | None:
    for name in ("podman", "docker"):
        if shutil.which(name):
            return name
    return None


def main() -> int:
    engine = engine_name()
    if engine is None:
        print("no container engine available; smoke test not run", file=sys.stderr)
        return 2
    name = f"agent-tools-browser-smoke-{os.getpid()}"
    profile_dir = tempfile.TemporaryDirectory(prefix="agent-tools-browser-profile-")
    site = _start_site()
    try:
        _build(engine)
        host_port = site.server_address[1]
        alias = "host.containers.internal" if engine == "podman" else "host.docker.internal"
        url = f"http://{alias}:{host_port}/landed"
        args = _run_args(engine, name, Path(profile_dir.name), alias)
        asyncio.run(_exercise(engine, args, url))
    finally:
        site.shutdown()
        site.server_close()
        subprocess.run(
            [engine, "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        profile_dir.cleanup()
    print("smoke test passed")
    return 0


def _build(engine: str) -> None:
    subprocess.run(
        [engine, "build", "-f", "runtimes/browser/Containerfile", "-t", IMAGE, "."],
        cwd=REPO,
        check=True,
    )


def _run_args(engine: str, name: str, profile: Path, alias: str) -> list[str]:
    profile.mkdir(parents=True, exist_ok=True)
    os.chmod(profile, 0o777)
    volume = f"{profile}:/var/lib/agent-tools/profile"
    if engine == "podman":
        volume += ":U"
    args = [
        "run",
        "-i",
        "--rm",
        "--name",
        name,
        "--add-host",
        f"{alias}:host-gateway",
        "-v",
        volume,
        "-e",
        "AGENT_TOOLS_BROWSER_PROFILE=/var/lib/agent-tools/profile",
        IMAGE,
    ]
    if engine == "docker":
        # The image user is uid 1000. Match the host owner of the bind mount
        # so the profile directory stays writable without a podman :U chown.
        uid = str(os.getuid())
        gid = str(os.getgid())
        args[1:1] = ["--user", f"{uid}:{gid}", "-e", "HOME=/tmp"]
    return args


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


async def _exercise(engine: str, args: list[str], url: str) -> None:
    server = StdioServerParameters(command=engine, args=args, cwd=str(REPO))
    async with Client(server, read_timeout_seconds=180) as client:
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
