"""MCP Server — thin shim over dbt_charts.agent_api.

Resources: dct://boards, dct://board/{path},
           dct://guide/*

Tools: all tools from dbt_charts.ai.tool_schemas.ALL_TOOLS (render_board,
       execute_sql, search_files, describe_board, list_skills,
       get_skill, list_diagnostic_codes, get_diagnostic_code)

Usage: dct mcp serve
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from dbt_charts.agent_api import Project
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.execute.cache_backend import QueryResultCache

from pydantic import AnyUrl, TypeAdapter

from dbt_charts._install_hint import install_hint
from dbt_charts.agent_api.query_csv import model_json
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tool_schemas import ALL_TOOLS, SINGLE_VALUE_RULE
from dbt_charts.ai.tools import dispatch_tool_call

logger = logging.getLogger(__name__)

# The skill a connected host is pointed at first; it must load on the "tool"
# surface (the CLI-only `intro` would raise SkillNotFound there).
ENTRY_SKILL_NAME = "chart"

# A connected host gets no system prompt from us, so `instructions` is the one
# place to point it at the guidance a shell agent gets from `dct skills chart`.
# Kept to a pointer: `list_skills`/`get_skill` exist so this inlines nothing.
# Cloud's connector extends it with how its users save a board.
MCP_INSTRUCTIONS = (
    "Before authoring or analyzing anything, call "
    f"get_skill(name={ENTRY_SKILL_NAME!r}) — the dbt Charts board and chart "
    "grammar and the build-render-fix loop. Call list_skills() to see the rest. "
    "Answer a data question with a chart: render_board draws it inline for the "
    "user, while execute_sql's rows reach only you. Render a stored board by "
    "'path' when one already answers the question; otherwise author "
    "whole-board YAML as render_board's 'yaml_content'. "
    f"{SINGLE_VALUE_RULE}"
)

_BASE_RESOURCES = [
    (
        "dct://boards",
        "application/json",
        "Dashboard List",
        "List of all dashboards in the project with metadata",
    ),
    (
        "dct://guide/chart",
        "text/markdown",
        "Chart and Board Guide",
        "The board and chart grammar plus the build-render-fix loop. Read this when creating or editing a board, choosing chart types, or designing layouts.",
    ),
    (
        "dct://guide/report-design",
        "text/markdown",
        "Report Design Guide",
        "Design principles for creating data-driven reports. Read this when creating narrative analyses, answering specific business questions, or writing investigative reports.",
    ),
]


def _read_resource_content(uri: str, context: DbtChartsAIContext) -> str:
    from dbt_charts.agent_api.boards import get_board, list_boards
    from dbt_charts.ai.prompts import load_shared_prompt

    if uri == "dct://boards":
        return list_boards(context.project_session.project).model_dump_json(
            indent=2, by_alias=True, exclude_none=True
        )

    if uri.startswith("dct://guide/"):
        slug = uri.removeprefix("dct://guide/")
        return load_shared_prompt(slug) or json.dumps(
            {"error": f"Unknown resource: {uri}"}
        )

    if uri.startswith("dct://board/"):
        path = uri.replace("dct://board/", "")
        try:
            resolved_path = context.resolve_dashboard_path(Path(path))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return get_board(
            path=resolved_path,
            project=context.project_session.project,
            include_raw=True,
        ).model_dump_json(indent=2, exclude_none=True)

    return json.dumps({"error": f"Unknown resource: {uri}"})


def _is_domain_error(result: dict[str, Any]) -> bool:
    # Three failure shapes flow through dispatch: most handlers emit
    # {"success": false, ...}; render_board emits BoardRenderResult's
    # tri-state {"status": "ok"/"partial"/"failed", ...} instead (it has no
    # "success" key at all — "partial" is a real success, not an error);
    # dispatch fallbacks emit {"error": str}.
    # The "success is not True" guard keeps a future {"success": True,
    # "error": "warning..."} happy path from being misclassified.
    if result.get("success") is False:
        return True
    if result.get("success") is True:
        return False
    if result.get("status") == "failed":
        return True
    if result.get("status") in ("ok", "partial"):
        return False
    err = result.get("error")
    return isinstance(err, str) and bool(err)


def create_server(context: DbtChartsAIContext) -> Any:
    try:
        from mcp.server import Server
        from mcp.types import (
            CallToolResult,
            ContentBlock,
            Resource,
            ResourceTemplate,
            TextContent,
            Tool,
            ToolAnnotations,
        )
    except ImportError as e:
        raise ImportError(
            "MCP server requires the 'mcp' package. "
            f"Install with: {install_hint('mcp')}"
        ) from e

    server = Server("dbt-charts", instructions=MCP_INSTRUCTIONS)

    def _uri(value: str) -> AnyUrl:
        return TypeAdapter(AnyUrl).validate_python(value)

    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return [
            Resource(uri=_uri(u), mimeType=m, name=n, description=d)
            for u, m, n, d in _BASE_RESOURCES
        ]

    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="dct://board/{path}",
                name="Dashboard Content",
                description="YAML content and compiled structure of a specific dashboard",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def handle_read_resource(uri: AnyUrl) -> str:
        return _read_resource_content(str(uri), context=context)

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name=cast(str, t["name"]),
                description=cast(str | None, t["description"]),
                inputSchema=cast(dict[str, Any], t["input_schema"]),
                annotations=ToolAnnotations(**t["annotations"]),
            )
            for t in ALL_TOOLS
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> CallToolResult:
        arguments = arguments or {}
        result = dispatch_tool_call(name, arguments, context=context)
        content: list[ContentBlock] = [
            TextContent(type="text", text=model_json(result))
        ]
        return CallToolResult(content=content, isError=_is_domain_error(result))

    return server


async def run_server(project: "Project", cache: "QueryResultCache | None") -> None:
    """Run the MCP server in stdio mode with embedded HTTP server.

    Both the project and its cache are injected by the composition root
    (``dct mcp serve``): this surface consumes a ``Project``, it does not build
    one, and the caller owns the cache's lifecycle. ``cache=None`` — the
    default, since a cache would outlive a warehouse rebuild for the life of
    the process — runs every tool call live.
    """
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as e:
        raise ImportError(
            "MCP server requires the 'mcp' package. "
            f"Install with: {install_hint('mcp')}"
        ) from e

    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.core.execute.adapters import LOCAL_AUTHORING_REGISTRY_KWARGS
    from dbt_charts.core.serve import embedded

    with ProjectSession.from_project(
        project, cache=cache, **LOCAL_AUTHORING_REGISTRY_KWARGS
    ) as project_session:
        http_server, listener = embedded.build_embedded_server(
            cast("FilesystemProject", project_session.project)
        )
        server = create_server(
            context=DbtChartsAIContext(
                project_session=project_session,
                server_port=listener.getsockname()[1],
            )
        )

        logger.info("Starting dbt-charts MCP server (stdio mode)")
        http_task = await embedded.start_embedded_server(http_server, listener)

        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
        finally:
            http_server.should_exit = True
            await http_task
