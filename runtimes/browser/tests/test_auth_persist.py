"""Auth store survival across sessions. Real Chromium, local cookies, no network."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agent_tools_browser.processes import cmdline, command_has_path, reap

from test_runtime import _call, _client, _payload

AUTH_NAME = "agent-tools-auth.json"


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


def _start_cookie_site() -> ThreadingHTTPServer:
    class Handler(_Quiet):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/set":
                name = query["name"][0]
                value = query["value"][0]
                cookie = f"{name}={value}; Path=/"
                max_age = query.get("max_age", [""])[0]
                if max_age:
                    cookie += f"; Max-Age={max_age}"
                body = b"set"
                self.send_response(200)
                self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/clear":
                name = query["name"][0]
                body = b"clear"
                self.send_response(200)
                self.send_header("Set-Cookie", f"{name}=; Path=/; Max-Age=0")
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/check":
                name = query["name"][0]
                value = query["value"][0]
                header = self.headers.get("Cookie") or ""
                found = any(part.strip() == f"{name}={value}" for part in header.split(";"))
                self.send_response(302)
                self.send_header("Location", "/yes" if found else "/no")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path in {"/yes", "/no"}:
                body = parsed.path.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread_target = httpd.serve_forever
    import threading

    threading.Thread(target=thread_target, daemon=True).start()
    return httpd


def _markers(profile: Path) -> list[str]:
    state = profile.parent / f"{profile.name}.runtime"
    return [str(profile.resolve()), str(state.resolve())]


async def _set_cookie(client: object, origin: str, name: str, value: str, max_age: str | None) -> None:
    query = {"name": name, "value": value}
    if max_age is not None:
        query["max_age"] = max_age
    url = origin + "/set?" + urllib.parse.urlencode(query)
    result = _payload(await _call(client, "navigate", {"url": url}))
    assert result["url"].startswith(origin + "/set")


async def _has_cookie(client: object, origin: str, name: str, value: str) -> bool:
    url = origin + "/check?" + urllib.parse.urlencode({"name": name, "value": value})
    result = _payload(await _call(client, "navigate", {"url": url}))
    return result["url"].endswith("/yes")


def _cookies(profile: Path) -> list[dict]:
    path = profile / AUTH_NAME
    assert path.is_file(), f"missing {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = data.get("cookies")
    assert isinstance(cookies, list)
    return cookies


def _server_pid(profile: Path) -> int:
    needle = str(profile.resolve()).encode()
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            env = (entry / "environ").read_bytes()
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
        except OSError:
            continue
        if needle in env and "agent_tools_browser" in cmd:
            found.append(int(entry.name))
    assert found, "server process not found"
    return found[0]


def test_persistent_cookie_survives_immediate_close(tmp_path: Path) -> None:
    asyncio.run(_persistent_cookie(tmp_path))


def test_session_cookie_survives_restart(tmp_path: Path) -> None:
    asyncio.run(_session_cookie(tmp_path))


def test_sigterm_writes_the_auth_store(tmp_path: Path) -> None:
    asyncio.run(_sigterm_saves(tmp_path))


def test_concurrent_sessions_share_one_profile_dir(tmp_path: Path) -> None:
    asyncio.run(_concurrent(tmp_path))


def test_secondary_loads_auth_store_while_primary_is_running(tmp_path: Path) -> None:
    asyncio.run(_secondary_sees_alpha(tmp_path))


def test_stale_singleton_lock_does_not_block_primary(tmp_path: Path) -> None:
    asyncio.run(_stale_lock(tmp_path))


async def _persistent_cookie(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        async with _client(profile, None, channel="chromium") as client:
            await _set_cookie(client, origin, "persist", "yes", "86400")
        async with _client(profile, None, channel="chromium") as client:
            assert await _has_cookie(client, origin, "persist", "yes")
        names = {(item.get("name"), item.get("value")) for item in _cookies(profile)}
        assert ("persist", "yes") in names
    finally:
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


async def _session_cookie(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        async with _client(profile, None, channel="chromium") as client:
            await _set_cookie(client, origin, "sess", "1", None)
        async with _client(profile, None, channel="chromium") as client:
            assert await _has_cookie(client, origin, "sess", "1")
    finally:
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


async def _sigterm_saves(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    client = _client(profile, None, channel="chromium")
    entered = False
    try:
        await client.__aenter__()
        entered = True
        await _set_cookie(client, origin, "persist", "sig", "86400")
        pid = _server_pid(profile)
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and _pid_alive(pid):
            await asyncio.sleep(0.1)
        assert not _pid_alive(pid)
        names = {(item.get("name"), item.get("value")) for item in _cookies(profile)}
        assert ("persist", "sig") in names
    finally:
        if entered:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


async def _concurrent(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        async with _client(profile, None, channel="chromium") as primary:
            await _set_cookie(primary, origin, "keep", "1", "86400")
            await asyncio.sleep(2.5)
            async with _client(profile, None, channel="chromium") as secondary:
                await _set_cookie(secondary, origin, "beta", "1", "86400")
                shared, private = _chrome_roles(profile)
                assert shared, "primary chrome is not on the shared profile"
                assert private, "secondary chrome is not on a private profile"
            # Primary never held beta, so its exit must not delete beta.
            assert await _has_cookie(primary, origin, "keep", "1")
        async with _client(profile, None, channel="chromium") as third:
            assert await _has_cookie(third, origin, "beta", "1")
            assert await _has_cookie(third, origin, "keep", "1")
            clear = _payload(
                await _call(
                    third,
                    "navigate",
                    {"url": origin + "/clear?" + urllib.parse.urlencode({"name": "beta"})},
                )
            )
            assert clear["url"].startswith(origin + "/clear")
        async with _client(profile, None, channel="chromium") as fourth:
            assert await _has_cookie(fourth, origin, "keep", "1")
            assert not await _has_cookie(fourth, origin, "beta", "1")
    finally:
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


async def _wait_store_contains(profile: Path, needle: str) -> None:
    path = profile / AUTH_NAME
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.is_file() and needle in path.read_text(encoding="utf-8"):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{path} never contained {needle}")


async def _secondary_sees_alpha(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        async with _client(profile, None, channel="chromium") as primary:
            await _set_cookie(primary, origin, "alpha", "1", "86400")
            await _wait_store_contains(profile, "alpha")
            async with _client(profile, None, channel="chromium") as secondary:
                assert await _has_cookie(secondary, origin, "alpha", "1")
                shared, private = _chrome_roles(profile)
                assert shared, "primary chrome is not on the shared profile"
                assert private, "secondary chrome is not on a private profile"
                await _set_cookie(secondary, origin, "beta", "1", "86400")
                await _wait_store_contains(profile, "beta")
        async with _client(profile, None, channel="chromium") as fresh:
            assert await _has_cookie(fresh, origin, "alpha", "1")
            assert await _has_cookie(fresh, origin, "beta", "1")
    finally:
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


async def _stale_lock(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("otherhost-12345")
    site = _start_cookie_site()
    origin = f"http://127.0.0.1:{site.server_address[1]}"
    try:
        async with _client(profile, None, channel="chromium") as client:
            await _set_cookie(client, origin, "persist", "lock", "86400")
            assert await _has_cookie(client, origin, "persist", "lock")
    finally:
        site.shutdown()
        site.server_close()
        reap(_markers(profile))


def _chrome_roles(profile: Path) -> tuple[list[str], list[str]]:
    shared: list[str] = []
    private: list[str] = []
    root = str(profile.resolve())
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        command = cmdline(int(entry.name))
        lowered = command.lower()
        if "chrome" not in lowered and "chromium" not in lowered:
            continue
        if command_has_path(command, root):
            shared.append(command)
        elif "agent-tools-profile-" in command:
            private.append(command)
    return shared, private


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
