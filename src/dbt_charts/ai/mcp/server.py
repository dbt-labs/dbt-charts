"""MCP Server — thin shim over dbt_charts.agent_api.

Resources: dct://boards, dct://board/{path},
           dct://docs/all, dct://docs/{topic}, dct://guide/*

Tools: all tools from dbt_charts.ai.tool_schemas.ALL_TOOLS (render_board,
       execute_sql, search_files, docs, describe_board, list_skills,
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
from dbt_charts.ai.tool_schemas import ALL_TOOLS
from dbt_charts.ai.tools import dispatch_tool_call

logger = logging.getLogger(__name__)

_BASE_RESOURCES = [
    (
        "dct://boards",
        "application/json",
        "Dashboard List",
        "List of all dashboards in the project with metadata",
    ),
    (
        "dct://docs/all",
        "text/markdown",
        "dbt charts YAML Reference (full)",
        "Syntax guide plus the generated field reference — same content as `dct docs all`",
    ),
    (
        "dct://docs/reference",
        "text/markdown",
        "dbt charts YAML Field Reference (generated)",
        "Auto-generated field-level spec for every YAML field, rendered from the compiler's models.",
    ),
    (
        "dct://docs/error-reference",
        "text/markdown",
        "dbt charts Error Reference (generated)",
        "Auto-generated reference of every ERR-* error code.",
    ),
    (
        "dct://docs/warning-reference",
        "text/markdown",
        "dbt charts Warning Reference (generated)",
        "Auto-generated reference of every WARN-* warning code.",
    ),
    (
        "dct://guide/board-design",
        "text/markdown",
        "Dashboard Design Guide",
        "Design principles for creating effective dashboards. Read this when creating a new dashboard, choosing chart types, or designing layouts for at-a-glance monitoring.",
    ),
    (
        "dct://guide/report-design",
        "text/markdown",
        "Report Design Guide",
        "Design principles for creating data-driven reports. Read this when creating narrative analyses, answering specific business questions, or writing investigative reports.",
    ),
    (
        "dct://guide/board-build",
        "text/markdown",
        "Dashboard Build Workflow",
        "Best practices for building dashboards incrementally. Read this before starting work — covers the recommended build-test-iterate workflow, caching, and tool usage patterns.",
    ),
    (
        "dct://guide/board-review",
        "text/markdown",
        "Dashboard Review",
        "Primary review skill for a dbt charts board — runs structural review (YAML + dct validate) and visual review (PNG + vision-model evaluation), synthesizes a ranked findings list.",
    ),
]


def _docs_topic_resources() -> list[tuple[str, str, str, str]]:
    """One static resource per docs topic, derived from the live topic index.

    The index also carries the generated references, which _BASE_RESOURCES
    already names with their own titles — skip those rather than advertise
    the same URI twice.
    """
    from dbt_charts.agent_api.docs import docs as docs_verb

    base_uris = {uri for uri, *_ in _BASE_RESOURCES}
    return [
        (
            f"dct://docs/{entry.id}",
            "text/markdown",
            f"dbt charts YAML Reference — {entry.title}",
            entry.description or f"`dct docs {entry.id}` section",
        )
        for entry in docs_verb().topics
        if f"dct://docs/{entry.id}" not in base_uris
    ]


def _read_resource_content(uri: str, context: DbtChartsAIContext) -> str:
    from dbt_charts.agent_api.boards import get_board, list_boards
    from dbt_charts.agent_api.docs import docs as docs_verb
    from dbt_charts.ai.prompts import load_shared_prompt

    if uri == "dct://boards":
        return list_boards(context.project_session.project).model_dump_json(
            indent=2, by_alias=True, exclude_none=True
        )

    # "all" is a topic the docs verb serves like any other — routing it here
    # keeps this resource and `dct docs all` the same bytes.
    if uri.startswith("dct://docs/"):
        slug = uri.replace("dct://docs/", "")
        result = docs_verb(topic=slug)
        if result.topic is None:
            # A missing generated file says which recipe regenerates it —
            # "Unknown topic" for a topic we ship would send the reader nowhere.
            return json.dumps(
                {
                    "error": result.errors[0]
                    if result.errors
                    else f"Unknown topic: {slug}"
                }
            )
        return result.topic.content

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

    server = Server("dbt-charts")

    def _uri(value: str) -> AnyUrl:
        return TypeAdapter(AnyUrl).validate_python(value)

    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return [
            Resource(uri=_uri(u), mimeType=m, name=n, description=d)
            for u, m, n, d in (*_BASE_RESOURCES, *_docs_topic_resources())
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
            ResourceTemplate(
                uriTemplate="dct://docs/{topic}",
                name="dbt charts YAML Reference Section",
                description="One H2 section of DBT_CHARTS_SYNTAX.md (e.g. board, charts, cheatsheet, all)",
                mimeType="text/markdown",
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
