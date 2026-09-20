"""Tool-call dispatch shim for the MCP server and the chat agent loops.

Thin wrappers over dbt_charts.agent_api. No business logic here. Tool *schemas*
live in dbt_charts.ai.tool_schemas; hosts turn them into an OpenAI payload with
dbt_charts.ai.llm.normalize_openai_tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

import dbt_charts.agent_api.describe_query as _describe_query
from dbt_charts.agent_api import (
    boards as _boards,
    files as _files,
    query as _query,
    search as _search,
    skills as _skills,
)
from dbt_charts.agent_api._paths import resolve_board_or_error
from dbt_charts.agent_api.query import (
    ExecuteQueryArgs as _ExecuteQueryArgs,
    variables_to_dict as _vars_to_dict,
)
from dbt_charts.agent_api.validate import (
    ValidateBoardArgs as _ValidateBoardArgs,
    validate_board as _validate_board_func,
)
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.project import InMemoryBoard

ToolHandler = Callable[[dict[str, Any], DbtChartsAIContext], dict[str, Any]]

# Cap on rows embedded per query in model-facing render output (json/text/yaml
# formats). Truncation is explicit in the output; svg/terminal are unaffected.
MODEL_MAX_ROWS_PER_QUERY = 50


def _handle_render(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    """Build the BoardFile for this render call and delegate to ProjectSession.

    'path' is boards-first resolved to a stored board; 'yaml_content' becomes a
    pathless in-memory board (no meta.yml cascade — matches the deleted
    agent_api dispatch's yaml_content arm). Exactly one of the two is required.
    """
    parsed = _boards.RenderBoardArgs.model_validate(args)
    if parsed.path is None and parsed.yaml_content is None:
        raise ValueError("Provide one of 'path' or 'yaml_content'")
    if parsed.path is not None and parsed.yaml_content is not None:
        raise ValueError("Provide only one of 'path' or 'yaml_content', not both")

    if parsed.path is not None:
        resolved = resolve_board_or_error(parsed.path, ctx.project_session.project)
        if isinstance(resolved, Diagnostic):
            return _boards.BoardRenderResult(
                status="failed", validation_errors=[resolved]
            ).model_dump(mode="json", exclude_none=True)
        board = resolved
    else:
        assert parsed.yaml_content is not None  # guaranteed by the checks above
        board = InMemoryBoard(parsed.yaml_content, path=None)

    return ctx.project_session.render_board(
        board=board,
        chart=parsed.chart,
        variables=(
            _vars_to_dict(parsed.variables) if parsed.variables is not None else None
        ),
        format=parsed.format or "json",
        as_link=parsed.as_link,
        server_port=ctx.server_port,
        max_rows_per_query=MODEL_MAX_ROWS_PER_QUERY,
    ).model_dump(mode="json", exclude_none=True)


def _handle_query(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _ExecuteQueryArgs.model_validate(args)
    return _query.execute_query(
        sql=parsed.sql,
        variables=(
            _vars_to_dict(parsed.variables) if parsed.variables is not None else None
        ),
        source=parsed.source or ctx.default_source,
        limit=parsed.limit or 50,
        adapter_registry=ctx.project_session.adapter_registry,
    ).model_dump(mode="json", exclude_none=True)


def _handle_describe_query(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    return _describe_query.describe_query(
        sql=args.get("sql", ""),
        source=args.get("source"),
        dialect=args.get("dialect"),
        adapter_registry=ctx.project_session.adapter_registry,
    ).model_dump(mode="json", exclude_none=True)


def _handle_query_board(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    parsed = _query.QueryBoardArgs.model_validate(args)
    return _query.query_board(
        name=parsed.name,
        path=parsed.path,
        project=ctx.project_session.project,
        vars=_vars_to_dict(parsed.vars) if parsed.vars is not None else None,
        limit=parsed.limit,
        adapter_registry=ctx.project_session.adapter_registry,
    ).model_dump(mode="json", exclude_none=True)


def _handle_search(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _search.SearchBoardsArgs.model_validate(args)
    project = (
        FilesystemProject(ctx.dashboards_directory)
        if ctx.dashboards_directory is not None
        else ctx.project_session.project
    )
    limit_kwargs: dict[str, Any] = (
        {"limit": parsed.limit} if parsed.limit is not None else {}
    )
    return _search.search_boards(
        parsed.query,
        project,
        tags=parsed.tags,
        **limit_kwargs,
    ).model_dump(mode="json", exclude_none=True)


def _handle_docs(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    from dbt_charts.agent_api.docs import DocsArgs, docs as _docs

    parsed = DocsArgs.model_validate(args)
    return _docs(
        topic=parsed.topic,
        search=parsed.search,
        limit=parsed.limit,
    ).model_dump(mode="json")


def _handle_describe_board(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    from dbt_charts.agent_api.describe import DescribeBoardArgs, describe_board

    parsed = DescribeBoardArgs.model_validate(args)
    return describe_board(
        path=parsed.path,
        project=ctx.project_session.project,
    ).model_dump(mode="json", exclude_none=True)


def _handle_list_skills(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    return _skills.list_skills(
        surface="tool", project=ctx.project_session.project
    ).model_dump(
        mode="json",
        exclude_none=True,
        exclude={"skills": {"__all__": _skills.SKILL_LIST_EXCLUDE_FIELDS}},
    )


def _handle_get_skill(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _skills.GetSkillArgs.model_validate(args)
    try:
        skill = _skills.get_skill(
            parsed.name, surface="tool", project=ctx.project_session.project
        )
    except _skills.SkillNotFound as exc:
        return {"success": False, "errors": [str(exc)]}
    # `Skill` is a domain model, not a result envelope — it carries no verdict
    # of its own, and it must not grow one (it nests inside `SkillList.skills`,
    # where a per-item `success` means nothing). The envelope is added here, so
    # this payload states its outcome like every other tool's.
    return {
        "success": True,
        **skill.model_dump(
            mode="json", exclude_none=True, exclude=_skills.SKILL_WIRE_EXCLUDE_FIELDS
        ),
    }


def _handle_search_skills(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    parsed = _skills.SearchSkillsArgs.model_validate(args)
    return _skills.search_skills(
        parsed.query,
        limit=parsed.limit,
        surface="tool",
        project=ctx.project_session.project,
    ).model_dump(mode="json", exclude_none=True)


def _handle_validate(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _ValidateBoardArgs.model_validate(args)
    return _validate_board_func(
        path=parsed.path,
        yaml_content=parsed.yaml_content,
        project=ctx.project_session.project,
    ).model_dump(mode="json", exclude_none=True)


def _handle_list_diagnostic_codes(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    from dbt_charts.agent_api import diagnostics as _diag

    parsed = _diag.ListDiagnosticCodesArgs.model_validate(args)
    return _diag.list_diagnostic_codes(level=parsed.level).model_dump(
        mode="json", exclude_none=True
    )


def _handle_get_diagnostic_code(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    from dbt_charts.agent_api import diagnostics as _diag

    parsed = _diag.GetDiagnosticCodeArgs.model_validate(args)
    return _diag.get_diagnostic_code(parsed.code).model_dump(
        mode="json", exclude_none=True
    )


def _handle_read_file(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.ReadFileArgs.model_validate(args)
    result = _files.read_file(parsed.path, project=ctx.project_session.project)
    # Only a read that produced content licenses a later overwrite — a
    # not-found or unreadable path tells the agent nothing about what it would
    # be replacing.
    if result.success:
        ctx.files_read.add(parsed.path)
    return result.model_dump(mode="json", exclude_none=True)


def _handle_write_file(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.WriteFileArgs.model_validate(args)
    refusal = _files.refuse_blind_overwrite(
        parsed.path, ctx.project_session.project, ctx.files_read
    )
    if refusal is not None:
        return refusal.model_dump(mode="json", exclude_none=True)
    result = _files.write_file(
        parsed.path, parsed.content, project=ctx.project_session.project
    )
    # The agent has now seen these bytes — it wrote them. Without this, the
    # build-render-fix-resave loop refuses the agent its own file one step
    # later, for content nobody else could have changed.
    if result.success:
        ctx.files_read.add(parsed.path)
    return result.model_dump(mode="json", exclude_none=True)


def _handle_edit_file(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.EditFileArgs.model_validate(args)
    # Deliberately does NOT license a later whole-file overwrite: edit_file
    # reads the file internally but returns no content, so the agent has been
    # shown nothing. A search_boards hit carries enough chart text to build a
    # unique old_string for a board never opened. read_file is the only tool
    # that puts the content in front of the model.
    return _files.edit_file(
        parsed.path,
        parsed.old_string,
        parsed.new_string,
        project=ctx.project_session.project,
    ).model_dump(mode="json", exclude_none=True)


def _handle_move_file(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.MoveFileArgs.model_validate(args)
    return _files.move_file(
        parsed.source_path,
        parsed.destination_path,
        project=ctx.project_session.project,
    ).model_dump(mode="json", exclude_none=True)


def _handle_delete_file(
    args: dict[str, Any], ctx: DbtChartsAIContext
) -> dict[str, Any]:
    parsed = _files.DeleteFileArgs.model_validate(args)
    return _files.delete_file(
        parsed.path, project=ctx.project_session.project
    ).model_dump(mode="json", exclude_none=True)


def _handle_glob_files(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.GlobFilesArgs.model_validate(args)
    return _files.glob_files(
        parsed.pattern, project=ctx.project_session.project
    ).model_dump(mode="json", exclude_none=True)


def _handle_grep_files(args: dict[str, Any], ctx: DbtChartsAIContext) -> dict[str, Any]:
    parsed = _files.GrepFilesArgs.model_validate(args)
    return _files.grep_files(
        parsed.pattern,
        project=ctx.project_session.project,
        glob=parsed.glob,
    ).model_dump(mode="json", exclude_none=True)


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "validate_board": _handle_validate,
    "render_board": _handle_render,
    "execute_query": _handle_query,
    "describe_query": _handle_describe_query,
    "query_board": _handle_query_board,
    "search_boards": _handle_search,
    "docs": _handle_docs,
    "describe_board": _handle_describe_board,
    "read_file": _handle_read_file,
    "write_file": _handle_write_file,
    "edit_file": _handle_edit_file,
    "glob_files": _handle_glob_files,
    "grep_files": _handle_grep_files,
    "move_file": _handle_move_file,
    "delete_file": _handle_delete_file,
    "list_skills": _handle_list_skills,
    "get_skill": _handle_get_skill,
    "search_skills": _handle_search_skills,
    "list_diagnostic_codes": _handle_list_diagnostic_codes,
    "get_diagnostic_code": _handle_get_diagnostic_code,
}


def handle_tool_call(
    function_name: str, function_args: dict[str, Any], *, context: DbtChartsAIContext
) -> str:
    return json.dumps(
        dispatch_tool_call(function_name, function_args, context=context), default=str
    )


def tool_call_outcome(result: dict[str, Any]) -> Literal["ok", "partial", "error"]:
    """Classify a tool handler's return envelope.

    Three conventions coexist in this codebase's tool results, and dispatch
    itself only builds two of them (a bare ``{"error": ...}`` for an unknown
    tool, and ``{"success": False, ...}`` for a raised handler): every other
    failure is a normal return from the handler that dispatch cannot
    distinguish from success on its own — the ``{"success": False, ...}``
    shape mirrored by Cloud's skills/board handlers, and
    ``BoardRenderResult``'s ``status: "ok" | "partial" | "failed"`` (no
    ``success``/``error`` field at all). Evaluated once here, beside
    ``dispatch_tool_call``, so no caller re-derives it from ``result``.
    """
    if result.get("error"):
        return "error"
    if result.get("success") is False:
        return "error"
    status = result.get("status")
    if status == "failed":
        return "error"
    if status == "partial":
        return "partial"
    return "ok"


def dispatch_tool_call(
    function_name: str,
    function_args: dict[str, Any],
    *,
    context: DbtChartsAIContext,
    extra_handlers: dict[str, ToolHandler] | None = None,
    tool_overrides: dict[str, ToolHandler] | None = None,
) -> dict[str, Any]:
    """Route a tool call by name.

    ``tool_overrides`` lets a host (Cloud) replace a CORE tool name's generic
    local handler with its own governed one — e.g. routing ``move_file`` to a
    service that runs ``can()`` and a binding re-key instead of a raw file
    move — one tool name and contract across hosts, two backends. Checked
    BEFORE ``TOOL_HANDLERS``, unlike ``extra_handlers`` (which is for tools
    with no meaning to dbt_charts.core at all, e.g. the Cloud-only placement tool,
    and is checked after — it never shadows a core tool name).
    """
    handler = None
    if tool_overrides is not None:
        handler = tool_overrides.get(function_name)
    if handler is None:
        handler = TOOL_HANDLERS.get(function_name)
    if handler is None and extra_handlers is not None:
        handler = extra_handlers.get(function_name)
    if handler is None:
        return {"error": f"Unknown tool: {function_name}"}
    context.project_session.refresh()  # per-call refresh — MCP refresh policy
    try:
        return handler(dict(function_args), context)
    except Exception as exc:  # noqa: BLE001 — last-resort catch at dispatch boundary
        return {"success": False, "errors": [str(exc)]}
