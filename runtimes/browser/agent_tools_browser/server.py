"""MCP stdio server. Tool names, descriptions, and schemas come from the catalog."""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from agent_tools_browser.catalog import TOOL_NAMES, CatalogError, load_browser_manifests
from agent_tools_browser.session import BrowserSession
from agent_tools_browser.validate import (
    declared_codes,
    input_failure_code,
    operation_failure_code,
    schema_errors,
)

log = logging.getLogger("agent_tools_browser")


class BrowserRuntime:
    def __init__(self, manifests: dict[str, dict]) -> None:
        self._manifests = manifests
        self._browser = BrowserSession()
        self._lock = asyncio.Lock()
        self._mcp = Server(
            "agent-tools-browser",
            version="1.0.0",
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )

    async def serve(self) -> None:
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self._mcp.run(
                    read_stream,
                    write_stream,
                    self._mcp.create_initialization_options(),
                )
        finally:
            await self._browser.aclose()

    async def _list_tools(
        self,
        _ctx: ServerRequestContext,
        _params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        tools = []
        for name in TOOL_NAMES:
            manifest = self._manifests[name]
            tools.append(
                Tool(
                    name=manifest["name"],
                    description=manifest["when_to_use"],
                    input_schema=manifest["inputs"],
                    output_schema=manifest["outputs"],
                )
            )
        return ListToolsResult(tools=tools)

    async def _call_tool(
        self,
        _ctx: ServerRequestContext,
        params: CallToolRequestParams,
    ) -> CallToolResult:
        manifest = self._manifests.get(params.name)
        if manifest is None:
            # Not a tool result code. The protocol reports an unknown method.
            raise ValueError(f"unknown tool {params.name}")
        arguments = params.arguments if isinstance(params.arguments, dict) else {}
        problems = schema_errors(manifest["inputs"], arguments)
        if problems:
            log.info("rejected %s input: %s", params.name, "; ".join(problems))
            return _error(input_failure_code(manifest))
        async with self._lock:
            try:
                outcome = await self._dispatch(params.name, arguments)
            except Exception:
                log.exception("tool %s failed", params.name)
                outcome = operation_failure_code(manifest)
        if isinstance(outcome, str):
            if outcome not in declared_codes(manifest):
                log.error("refusing undeclared code %s for %s", outcome, params.name)
                outcome = operation_failure_code(manifest)
            return _error(outcome)
        output_problems = schema_errors(manifest["outputs"], outcome)
        if output_problems:
            log.error("result for %s failed its output schema: %s", params.name, "; ".join(output_problems))
            return _error(operation_failure_code(manifest))
        return _success(outcome)

    async def _dispatch(self, name: str, arguments: dict) -> dict | str:
        if name == "browser.navigate":
            return await self._browser.navigate(str(arguments["url"]))
        if name == "browser.read":
            return await self._browser.read()
        if name == "browser.screenshot":
            return await self._browser.screenshot()
        if name == "browser.handoff":
            return await self._browser.handoff(str(arguments["reason"]), int(arguments["timeout"]))
        raise ValueError(name)


def _error(code: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"code": code}))],
        is_error=True,
    )


def _success(payload: dict) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=False,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(name)s %(levelname)s %(message)s",
    )
    try:
        manifests = load_browser_manifests()
    except CatalogError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    try:
        asyncio.run(BrowserRuntime(manifests).serve())
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
