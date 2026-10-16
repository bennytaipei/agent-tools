"""Load browser tool manifests from the catalog. The files are the contract."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

TOOL_NAMES = (
    "browser.navigate",
    "browser.read",
    "browser.screenshot",
    "browser.handoff",
    "browser.click",
    "browser.type",
    "browser.scroll",
    "browser.select",
    "browser.wait_for",
)

_FILENAMES = {
    "browser.navigate": "navigate.yaml",
    "browser.read": "read.yaml",
    "browser.screenshot": "screenshot.yaml",
    "browser.handoff": "handoff.yaml",
    "browser.click": "click.yaml",
    "browser.type": "type.yaml",
    "browser.scroll": "scroll.yaml",
    "browser.select": "select.yaml",
    "browser.wait_for": "wait_for.yaml",
}


class CatalogError(RuntimeError):
    pass


def mcp_tool_name(qualified: str) -> str:
    """MCP name for a catalog manifest.

    Grok Build and Claude Code admit only letters, digits, underscores, and
    hyphens in a tool name. The harness server name is the namespace, so the
    exposed name is the tool segment of `<namespace>.<tool>`.
    """

    namespace, separator, tool = qualified.partition(".")
    if not separator or not namespace or not tool or "." in tool:
        raise CatalogError(f"{qualified!r} is not a namespace.tool name")
    return tool


def mcp_tool_bindings(manifests: dict[str, dict]) -> list[tuple[str, str]]:
    """Pairs of (exposed MCP name, catalog name) in catalog order."""

    bindings: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in manifests:
        exposed = mcp_tool_name(name)
        if exposed in seen:
            raise CatalogError(f"duplicate MCP tool name {exposed}")
        seen.add(exposed)
        bindings.append((exposed, name))
    return bindings


def repo_root_from(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / "catalog" / "browser" / "navigate.yaml").is_file():
            return parent
    return None


def find_repo_root() -> Path:
    override = os.environ.get("AGENT_TOOLS_ROOT", "").strip()
    if override:
        root = Path(override).resolve()
        if (root / "catalog" / "browser" / "navigate.yaml").is_file():
            return root
        raise CatalogError(f"AGENT_TOOLS_ROOT does not contain catalog/browser: {root}")
    found = repo_root_from(Path(__file__).resolve()) or repo_root_from(Path.cwd().resolve())
    if found is None:
        raise CatalogError("catalog/browser not found from the runtime package or the working directory")
    return found


def load_browser_manifests(root: Path | None = None) -> dict[str, dict]:
    repo = root if root is not None else find_repo_root()
    folder = repo / "catalog" / "browser"
    manifests: dict[str, dict] = {}
    for name, filename in _FILENAMES.items():
        path = folder / filename
        if not path.is_file():
            raise CatalogError(f"missing manifest {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CatalogError(f"{path} is not a mapping")
        if data.get("name") != name:
            raise CatalogError(f"{path} name is {data.get('name')!r}, expected {name}")
        manifests[name] = data
    if tuple(manifests) != TOOL_NAMES:
        raise CatalogError(f"unexpected browser tools: {tuple(manifests)}")
    return manifests
