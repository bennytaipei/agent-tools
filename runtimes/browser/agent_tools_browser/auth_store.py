"""Shared login snapshot on the profile directory.

Chromium writes cookies on a timer and on a clean exit. A killed container
misses both. This file is the copy that survives, including session cookies.
Concurrent sessions merge into it. They do not share one Chrome profile.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from pathlib import Path

AUTH_NAME = "agent-tools-auth.json"
LOCK_NAME = "agent-tools-auth.lock"
PRIMARY_LOCK_NAME = ".agent-tools-primary.lock"
SINGLETON_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket")
_SAMESITE = {"Lax", "None", "Strict"}

CookieKey = tuple[str, str, str]

_seq_lock = threading.Lock()
_seq = 0


def next_save_seq() -> int:
    """Bump the write generation. An older in-flight write then skips."""

    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def _current_seq() -> int:
    with _seq_lock:
        return _seq


def auth_path(profile: Path) -> Path:
    return profile / AUTH_NAME


def load_state(profile: Path) -> dict:
    path = auth_path(profile)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {"cookies": [], "origins": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"cookies": [], "origins": []}
    if not isinstance(data, dict):
        return {"cookies": [], "origins": []}
    if not isinstance(data.get("cookies"), list):
        data["cookies"] = []
    if not isinstance(data.get("origins"), list):
        data["origins"] = []
    return data


def cookie_key(cookie: dict) -> CookieKey | None:
    name = cookie.get("name")
    if not isinstance(name, str) or not name:
        return None
    domain = cookie.get("domain", "")
    path = cookie.get("path", "/")
    if not isinstance(domain, str):
        domain = ""
    if not isinstance(path, str) or not path:
        path = "/"
    return (name, domain.lower(), path)


def _expires(cookie: dict) -> float:
    raw = cookie.get("expires", -1)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return -1.0


def fresh_cookies(cookies: list, now: float) -> list[dict]:
    fresh: list[dict] = []
    for cookie in cookies:
        if not isinstance(cookie, dict) or cookie_key(cookie) is None:
            continue
        exp = _expires(cookie)
        if exp > 0 and exp < now:
            continue
        fresh.append(cookie)
    return fresh


def cookie_for_browser(cookie: dict) -> dict | None:
    """Playwright add_cookies shape. Session cookies keep expires -1."""

    key = cookie_key(cookie)
    if key is None:
        return None
    value = cookie.get("value")
    if not isinstance(value, str):
        return None
    domain = cookie.get("domain")
    if not isinstance(domain, str) or not domain:
        return None
    path = cookie.get("path")
    if not isinstance(path, str) or not path:
        path = "/"
    shaped: dict = {
        "name": key[0],
        "value": value,
        "domain": domain,
        "path": path,
        "expires": _expires(cookie),
    }
    if isinstance(cookie.get("httpOnly"), bool):
        shaped["httpOnly"] = cookie["httpOnly"]
    secure = cookie.get("secure")
    if isinstance(secure, bool):
        shaped["secure"] = secure
    same = cookie.get("sameSite")
    if same in _SAMESITE and not (same == "None" and secure is not True):
        shaped["sameSite"] = same
    partition = cookie.get("partitionKey")
    if isinstance(partition, str):
        shaped["partitionKey"] = partition
    return shaped


def _origin_map(state: dict) -> dict[str, dict]:
    found: dict[str, dict] = {}
    origins = state.get("origins")
    if not isinstance(origins, list):
        return found
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        key = origin.get("origin")
        if isinstance(key, str) and key:
            found[key] = origin
    return found


def _storage_items(origin: dict | None) -> dict[str, str]:
    items: dict[str, str] = {}
    if not isinstance(origin, dict):
        return items
    raw = origin.get("localStorage")
    if not isinstance(raw, list):
        return items
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            items[name] = value
    return items


def baseline_from_state(state: dict) -> tuple[set[CookieKey], dict[str, set[str]]]:
    """Keys this context currently has. Not the merged file."""

    cookies: set[CookieKey] = set()
    for cookie in state.get("cookies") or []:
        if isinstance(cookie, dict):
            key = cookie_key(cookie)
            if key is not None:
                cookies.add(key)
    storage: dict[str, set[str]] = {}
    for key, origin in _origin_map(state).items():
        storage[key] = set(_storage_items(origin))
    return cookies, storage


def merge_states(
    disk: dict,
    baseline_cookies: set[CookieKey],
    baseline_storage: dict[str, set[str]],
    current: dict,
    now: float,
) -> dict:
    """This context wins keys it has. Keys it dropped are deleted. Others stay."""

    disk_cookies: dict[CookieKey, dict] = {}
    for cookie in disk.get("cookies") or []:
        if isinstance(cookie, dict):
            key = cookie_key(cookie)
            if key is not None:
                disk_cookies[key] = cookie
    current_cookies: dict[CookieKey, dict] = {}
    for cookie in current.get("cookies") or []:
        if isinstance(cookie, dict):
            key = cookie_key(cookie)
            if key is not None:
                current_cookies[key] = cookie

    merged_cookies = dict(disk_cookies)
    for key in baseline_cookies:
        if key not in current_cookies:
            merged_cookies.pop(key, None)
    merged_cookies.update(current_cookies)
    fresh: list[dict] = []
    for cookie in merged_cookies.values():
        exp = _expires(cookie)
        if exp > 0 and exp < now:
            continue
        fresh.append(cookie)

    disk_origins = _origin_map(disk)
    current_origins = _origin_map(current)
    merged_origins: dict[str, dict] = {}
    for key, origin in disk_origins.items():
        kept: dict = {
            "origin": key,
            "localStorage": [
                {"name": name, "value": value} for name, value in _storage_items(origin).items()
            ],
        }
        if "indexedDB" in origin:
            kept["indexedDB"] = origin["indexedDB"]
        merged_origins[key] = kept

    for key, origin in current_origins.items():
        # An origin absent from current was not visited. Leave the disk copy.
        items = _storage_items(merged_origins.get(key))
        current_items = _storage_items(origin)
        for name in baseline_storage.get(key, ()):
            if name not in current_items:
                items.pop(name, None)
        items.update(current_items)
        new_origin: dict = {
            "origin": key,
            "localStorage": [
                {"name": name, "value": value} for name, value in items.items()
            ],
        }
        if "indexedDB" in origin:
            new_origin["indexedDB"] = origin["indexedDB"]
        elif key in merged_origins and "indexedDB" in merged_origins[key]:
            new_origin["indexedDB"] = merged_origins[key]["indexedDB"]
        merged_origins[key] = new_origin

    result = {key: value for key, value in disk.items() if key not in {"cookies", "origins"}}
    result["cookies"] = fresh
    result["origins"] = list(merged_origins.values())
    return result


def save_merged(
    profile: Path,
    baseline_cookies: set[CookieKey],
    baseline_storage: dict[str, set[str]],
    current: dict,
    seq: int,
    now: float | None = None,
) -> bool:
    """Atomically merge under fcntl. False when a newer save superseded this one."""

    moment = time.time() if now is None else now
    profile.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(profile / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if seq != _current_seq():
            return False
        disk = load_state(profile)
        merged = merge_states(disk, set(baseline_cookies), baseline_storage, current, moment)
        payload = json.dumps(merged, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        tmp = profile / f".{AUTH_NAME}.tmp"
        tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(tmp_fd, payload)
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        os.replace(tmp, profile / AUTH_NAME)
        os.chmod(profile / AUTH_NAME, 0o600)
        return True
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def try_primary(profile: Path) -> int | None:
    """Non-blocking exclusive lock. The returned fd holds the role until closed."""

    profile.mkdir(parents=True, exist_ok=True)
    fd = os.open(profile / PRIMARY_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    os.set_inheritable(fd, False)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_primary(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def clear_stale_singleton(profile: Path) -> None:
    """Drop Chrome's singleton files, including a socket and a foreign-host symlink."""

    for name in SINGLETON_NAMES:
        path = profile / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            try:
                os.unlink(path)
            except OSError:
                pass


def local_storage_script(origins: dict[str, dict[str, str]]) -> str:
    """Restore localStorage once per tab. about:blank has no sessionStorage."""

    payload = json.dumps(origins, ensure_ascii=True)
    return (
        "(() => {\n"
        f"  var payload = {payload};\n"
        "  try {\n"
        "    if (sessionStorage.getItem('__agentToolsAuth')) return;\n"
        "  } catch (e) { return; }\n"
        "  var origin = '';\n"
        "  try { origin = location.origin; } catch (e) { return; }\n"
        "  if (!origin || origin === 'null') return;\n"
        "  var items = payload[origin];\n"
        "  try {\n"
        "    if (items) {\n"
        "      for (var name in items) {\n"
        "        if (Object.prototype.hasOwnProperty.call(items, name)) {\n"
        "          localStorage.setItem(name, String(items[name]));\n"
        "        }\n"
        "      }\n"
        "    }\n"
        "    sessionStorage.setItem('__agentToolsAuth', '1');\n"
        "  } catch (e) { return; }\n"
        "})();\n"
    )
