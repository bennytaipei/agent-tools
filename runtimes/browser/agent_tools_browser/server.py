"""MCP stdio server. Tool names, descriptions, and schemas come from the catalog."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import replace
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ImageContent,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from agent_tools_browser.catalog import CatalogError, load_browser_manifests, mcp_tool_bindings
from agent_tools_browser.session import READ_MAX_VIEWPORTS_DEFAULT, BrowserSession, normalize_max_viewports
from agent_tools_browser.validate import (
    ToolFailure,
    declared_codes,
    input_failure_code,
    error_detail,
    input_hint,
    operation_failure_code,
    schema_errors,
)


class _LooseCallParams(CallToolRequestParams):
    """tools/call arguments are a JSON value. scroll accepts a number, click a string."""

    arguments: Any = None


class _BareArguments:
    """Turn a bare scroll number or click string into an object before spec checks.

    The MCP wire schema requires arguments to be an object. A scroll number
    becomes ``{"scroll_y": N}``. A click string becomes ``{"target": text}``.
    Any other non-object scroll value becomes ``{}`` so the tool returns
    ``invalid_target`` instead of a protocol error. A number is never rewritten
    to ``{}``.
    """

    async def __call__(self, ctx: ServerRequestContext, call_next):  # type: ignore[no-untyped-def]
        params = ctx.params
        if ctx.method == "tools/call" and isinstance(params, dict):
            arguments = params.get("arguments")
            name = params.get("name")
            if name == "scroll" and not isinstance(arguments, dict):
                if isinstance(arguments, (int, float)) and not isinstance(arguments, bool):
                    arguments = {"scroll_y": int(arguments)}
                else:
                    arguments = {}
                ctx = replace(ctx, params={**params, "arguments": arguments})
            elif name == "click" and isinstance(arguments, str) and arguments.strip():
                ctx = replace(ctx, params={**params, "arguments": {"target": arguments}})
        return await call_next(ctx)

log = logging.getLogger("agent_tools_browser")


class BrowserRuntime:
    def __init__(self, manifests: dict[str, dict]) -> None:
        self._manifests = manifests
        # Exposed names are the tool segment. The harness server name is the namespace.
        self._bindings = mcp_tool_bindings(manifests)
        self._by_mcp_name = {exposed: qualified for exposed, qualified in self._bindings}
        self._browser = BrowserSession()
        self._lock = asyncio.Lock()
        self._mcp = Server(
            "agent-tools-browser",
            version="1.0.0",
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )
        # The stock params model only accepts an object. Replace it so a bare
        # number or string reaches schema validation instead of becoming {}.
        self._mcp.add_request_handler("tools/call", _LooseCallParams, self._call_tool)
        self._mcp.middleware.insert(0, _BareArguments())

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        exit_code = 0
        try:
            async with stdio_server() as (read_stream, write_stream):
                run_task = asyncio.create_task(
                    self._mcp.run(
                        read_stream,
                        write_stream,
                        self._mcp.create_initialization_options(),
                    )
                )

                def _stop(code: int) -> None:
                    nonlocal exit_code
                    exit_code = code
                    if not run_task.done():
                        run_task.cancel()

                for sig, code in (
                    (signal.SIGINT, 130),
                    (signal.SIGTERM, 143),
                    (signal.SIGHUP, 129),
                ):
                    try:
                        loop.add_signal_handler(sig, _stop, code)
                    except (NotImplementedError, RuntimeError):
                        log.warning("no signal handler for %s", sig)
                    else:
                        installed.append(sig)
                try:
                    await run_task
                except asyncio.CancelledError:
                    # stdin is still open, so the stdio task group would wait
                    # forever. Save and close here, then leave the process.
                    log.info("browser server stopping")
                    for sig in installed:
                        loop.remove_signal_handler(sig)
                    installed.clear()
                    await self._browser.aclose()
                    os._exit(exit_code or 143)
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)
            await self._browser.aclose()

    async def _list_tools(
        self,
        _ctx: ServerRequestContext,
        _params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        tools = []
        for exposed, qualified in self._bindings:
            manifest = self._manifests[qualified]
            tools.append(
                Tool(
                    name=exposed,
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
        try:
            return await self._call_tool_body(params)
        finally:
            # After dispatch, on this loop. Do not await the debounced save here.
            self._browser.note_activity()

    async def _call_tool_body(self, params: CallToolRequestParams) -> CallToolResult:
        qualified = self._by_mcp_name.get(params.name)
        if qualified is None:
            # Not a tool result code. The protocol reports an unknown method.
            raise ValueError(f"unknown tool {params.name}")
        manifest = self._manifests[qualified]
        raw = params.arguments
        if qualified in {"browser.scroll", "browser.click"}:
            arguments: object = {} if raw is None else raw
        else:
            arguments = raw if isinstance(raw, dict) else {}
        # A bare number or string is not an object. The published schema still
        # says type object so tools/list can serialize it. Validate that form here.
        if _bare_argument_ok(qualified, arguments):
            problems = []
        else:
            problems = schema_errors(manifest["inputs"], _schema_instance(qualified, arguments))
        if problems:
            detail = input_hint(qualified)
            log.info("rejected %s input: %s; %s", params.name, "; ".join(problems), detail)
            return _error(input_failure_code(manifest), detail)
        async with self._lock:
            try:
                outcome = await self._dispatch(qualified, arguments)
            except Exception:
                log.exception("tool %s failed", params.name)
                outcome = operation_failure_code(manifest)
        if isinstance(outcome, ToolFailure):
            if outcome.code not in declared_codes(manifest):
                log.error("refusing undeclared code %s for %s", outcome.code, params.name)
                code = operation_failure_code(manifest)
                return _error(code, outcome.detail or error_detail(qualified, code))
            return _error(outcome.code, outcome.detail or error_detail(qualified, outcome.code))
        if isinstance(outcome, str):
            if outcome not in declared_codes(manifest):
                log.error("refusing undeclared code %s for %s", outcome, params.name)
                outcome = operation_failure_code(manifest)
            if outcome == "invalid_target":
                detail = input_hint(qualified)
            else:
                detail = error_detail(qualified, outcome)
            return _error(outcome, detail)
        output_problems = schema_errors(manifest["outputs"], outcome)
        if output_problems:
            log.error("result for %s failed its output schema: %s", params.name, "; ".join(output_problems))
            code = operation_failure_code(manifest)
            return _error(code, error_detail(qualified, code))
        return _success(outcome, _result_images(qualified, outcome))

    async def _dispatch(self, name: str, arguments: object) -> dict | str | ToolFailure:
        if name == "browser.click":
            return await self._browser.click(arguments)
        if name == "browser.scroll":
            return await self._browser.scroll(arguments)
        if not isinstance(arguments, dict):
            arguments = {}
        if name == "browser.navigate":
            return await self._browser.navigate(str(arguments["url"]))
        if name == "browser.read":
            raw = arguments.get("max_viewports", READ_MAX_VIEWPORTS_DEFAULT)
            limit = normalize_max_viewports(raw)
            if limit is None:
                return "no_page"
            return await self._browser.read(limit)
        if name == "browser.screenshot":
            return await self._browser.screenshot()
        if name == "browser.handoff":
            done_when = arguments.get("done_when")
            return await self._browser.handoff(
                str(arguments["reason"]),
                int(arguments["timeout"]),
                done_when if isinstance(done_when, dict) else None,
            )
        if name == "browser.type":
            return await self._browser.type_text(arguments)
        if name == "browser.select":
            return await self._browser.select(arguments)
        if name == "browser.wait_for":
            return await self._browser.wait_for(arguments)
        raise ValueError(name)


def _schema_instance(qualified: str, arguments: object) -> object:
    """Schema view of arguments. A string target and a numeric delta stay in the real call."""

    if not isinstance(arguments, dict):
        return arguments
    copied = dict(arguments)
    target = copied.get("target")
    if isinstance(target, str):
        copied["target"] = {"css": target}
    if qualified == "browser.scroll":
        delta = copied.get("delta")
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            copied["delta"] = {"dx": 0, "dy": delta}
    return copied


def _bare_argument_ok(qualified: str, arguments: object) -> bool:
    """scroll 2200 and click "Search…" are accepted even though type is object."""

    if qualified == "browser.scroll" and isinstance(arguments, (int, float)) and not isinstance(arguments, bool):
        return True
    if qualified == "browser.click" and isinstance(arguments, str) and arguments.strip():
        return True
    return False


def _error(code: str, detail: str | None = None) -> CallToolResult:
    body: dict[str, str] = {"code": code}
    if detail:
        body["detail"] = detail
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(body))],
        is_error=True,
    )


def _result_images(name: str, payload: dict) -> list[tuple[str, str]]:
    """Labels and base64 PNGs to emit as MCP image content.

    An empty label means the image has no preceding text of its own. Read puts
    a viewport label before each image. Screenshot adds the image only.
    """

    if name == "browser.screenshot":
        image = payload.get("image")
        if isinstance(image, str):
            return [("", image)]
        return []
    action = {
        "browser.click": "click",
        "browser.type": "type",
        "browser.scroll": "scroll",
        "browser.select": "select",
        "browser.wait_for": "wait_for",
    }.get(name)
    if action is not None:
        image = payload.get("image")
        if not isinstance(image, str):
            return []
        scroll_y = payload.get("scroll_y", 0)
        return [(f"after {action}, scroll_y {int(scroll_y)}", image)]
    if name != "browser.read":
        return []
    viewports = payload.get("viewports")
    if not isinstance(viewports, list):
        return []
    total = len(viewports)
    pairs: list[tuple[str, str]] = []
    for item in viewports:
        if not isinstance(item, dict):
            continue
        image = item.get("image")
        if not isinstance(image, str):
            continue
        index = item.get("index", 0)
        scroll_y = item.get("scroll_y", 0)
        label = f"viewport {int(index) + 1} of {total}, scroll_y {int(scroll_y)}"
        pairs.append((label, image))
    return pairs


def _success(payload: dict, images: list[tuple[str, str]] | None = None) -> CallToolResult:
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=json.dumps(payload))]
    for label, data in images or []:
        if label:
            content.append(TextContent(type="text", text=label))
        content.append(ImageContent(type="image", data=data, mime_type="image/png"))
    return CallToolResult(
        content=content,
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
