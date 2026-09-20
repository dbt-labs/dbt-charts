"""Unit tests for ``dbt_charts.ai.tools`` after the Phase 0 thin-shim collapse."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tools import TOOL_HANDLERS, dispatch_tool_call, handle_tool_call

_FIXTURE_PROJECT_SCAFFOLD = (
    Path(__file__).parent.parent.parent / "fixtures" / "project-scaffold"
)

_INLINE_BOARD_YAML = (
    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
)


class TestDispatchToolCall:
    """dispatch_tool_call routes to the correct agent_api handler."""

    def test_handle_tool_call_passes_context(
        self, monkeypatch, context: DbtChartsAIContext
    ) -> None:
        captured: dict[str, object] = {}

        def fake_dispatch(function_name, function_args, *, context=None, **kwargs):
            captured["function_name"] = function_name
            captured["function_args"] = function_args
            captured["context"] = context
            return {"success": True}

        monkeypatch.setattr("dbt_charts.ai.tools.dispatch_tool_call", fake_dispatch)

        result = json.loads(handle_tool_call("docs", {}, context=context))

        assert result == {"success": True}
        assert captured["function_name"] == "docs"
        assert captured["function_args"] == {}
        assert captured["context"] is context

    def test_dispatch_uses_context_project_for_search(
        self,
        monkeypatch,
        make_context: Callable[..., DbtChartsAIContext],
        tmp_path: Path,
    ) -> None:
        """search_boards receives the context's dashboards_directory project."""
        from dbt_charts.cli.filesystem_project import FilesystemProject

        captured: dict[str, object] = {}

        def fake_search_boards(query, project, *, tags=None, limit=10):
            from dbt_charts.agent_api.search import SearchResult

            captured["project"] = project
            return SearchResult(success=True, errors=[], results=[])

        monkeypatch.setattr(
            "dbt_charts.agent_api.search.search_boards",
            fake_search_boards,
        )

        context = make_context(dashboards_directory=tmp_path)
        dispatch_tool_call("search_boards", {"query": "orders"}, context=context)

        assert isinstance(captured["project"], FilesystemProject)
        assert captured["project"].root == tmp_path.resolve()

    def test_dispatch_search_rejects_unknown_field(
        self, context: DbtChartsAIContext
    ) -> None:
        """project_dir is no longer a wire field; passing it raises a validation error
        caught at the dispatch boundary."""
        result = dispatch_tool_call(
            "search_boards",
            {"query": "orders", "project_dir": "/some/path"},
            context=context,
        )
        assert result["success"] is False

    def test_dispatch_search_boards_subdir_returns_results(
        self,
        make_context: Callable[..., DbtChartsAIContext],
        tmp_path: Path,
    ) -> None:
        """Dispatch search on a project with charts/ dir finds the board."""
        from dbt_charts.agent_api import ProjectSession

        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "sales.yml").write_text(
            "title: Sales Dashboard\nnotes: zendesk metrics\n"
            "queries:\n  q:\n    sql: SELECT 1\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )

        context = make_context(
            project_session=ProjectSession.open(tmp_path, read_only=False)
        )
        result = dispatch_tool_call(
            "search_boards",
            {"query": "zendesk"},
            context=context,
        )
        assert result["success"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "Sales Dashboard"

    def test_dispatch_routes_to_extra_handler(
        self, context: DbtChartsAIContext
    ) -> None:
        """A host (e.g. Cloud) can supply per-call handlers for tools with no
        meaning to dbt_charts.core, without dispatch_tool_call knowing their name."""
        captured: dict[str, object] = {}

        def handle_host_only(
            args: dict[str, Any], ctx: DbtChartsAIContext
        ) -> dict[str, Any]:
            captured["args"] = args
            captured["ctx"] = ctx
            return {"ok": True}

        result = dispatch_tool_call(
            "host_only_tool",
            {"x": 1},
            context=context,
            extra_handlers={"host_only_tool": handle_host_only},
        )

        assert result == {"ok": True}
        assert captured["args"] == {"x": 1}
        assert captured["ctx"] is context

    def test_dispatch_unknown_tool_not_in_extra_handlers_errors(
        self, context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "nonexistent_tool",
            {},
            context=context,
            extra_handlers={"other_tool": lambda args, ctx: {}},
        )
        assert result == {"error": "Unknown tool: nonexistent_tool"}

    def test_dispatch_tool_overrides_take_priority_over_core_registry(
        self, context: DbtChartsAIContext
    ) -> None:
        """tool_overrides is consulted BEFORE TOOL_HANDLERS -- unlike
        extra_handlers (which never shadows a core tool name), a host can
        route an existing tool name (e.g. move_file) to its own governed
        handler instead of the generic local one."""
        captured: dict[str, object] = {}

        def governed_move_file(
            args: dict[str, Any], ctx: DbtChartsAIContext
        ) -> dict[str, Any]:
            captured["args"] = args
            return {"success": True, "governed": True}

        result = dispatch_tool_call(
            "move_file",
            {"source_path": "a.yml", "destination_path": "b.yml"},
            context=context,
            tool_overrides={"move_file": governed_move_file},
        )

        assert result == {"success": True, "governed": True}
        assert captured["args"] == {
            "source_path": "a.yml",
            "destination_path": "b.yml",
        }

    def test_dispatch_without_tool_overrides_uses_core_registry(
        self, context: DbtChartsAIContext
    ) -> None:
        """No tool_overrides -> falls through to TOOL_HANDLERS as before."""
        result = dispatch_tool_call(
            "move_file",
            {"source_path": "nonexistent.yml", "destination_path": "dest.yml"},
            context=context,
        )
        assert "governed" not in result


class TestDispatchWireShape:
    """Wire format from handlers omits None-valued optional fields."""

    def test_render_board_wire_omits_url_without_server_port(
        self, context: DbtChartsAIContext
    ) -> None:
        result = dispatch_tool_call(
            "render_board",
            {
                "yaml_content": (
                    "title: T\nqueries:\n  q:\n    columns: [v]\n"
                    "    values:\n      - [1]\n"
                    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\n"
                    "rows:\n  - c\n"
                )
            },
            context=context,
        )
        assert result["status"] == "ok"
        assert "url" not in result, f"expected url to be absent, got {result}"


class TestAsLinkDispatch:
    """render_board(as_link=True) returns URL without executing queries."""

    def test_as_link_returns_url_for_existing_dashboard(
        self, tmp_path: Path, make_context: Callable[..., DbtChartsAIContext]
    ) -> None:
        (tmp_path / "dash.yml").write_text(
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )

        ctx = make_context(server_port=9999)
        result = dispatch_tool_call(
            "render_board",
            {"path": str(tmp_path / "dash.yml"), "as_link": True},
            context=ctx,
        )
        assert result["status"] == "ok"
        assert "url" in result
        assert "9999" in result["url"]

    def test_as_link_false_does_full_render(self, context: DbtChartsAIContext) -> None:
        result = dispatch_tool_call(
            "render_board",
            {
                "yaml_content": (
                    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
                    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
                ),
                "as_link": False,
            },
            context=context,
        )
        assert result["status"] == "ok"
        assert "data" in result


# ---------------------------------------------------------------------------
# FR-003: Every TOOL_HANDLERS entry exercised via dispatch_tool_call.
# FR-010: Warehouse-dependent tools (execute_query, query_board) use CSV fixture.
# ---------------------------------------------------------------------------


def _project_scaffold_context(
    tmp_path: Path,
) -> DbtChartsAIContext:
    """Copy project-scaffold fixture to tmp_path and build a CSV-backed context."""
    dest = tmp_path / "proj"
    shutil.copytree(_FIXTURE_PROJECT_SCAFFOLD, dest)
    return DbtChartsAIContext(
        project_session=ProjectSession.open(dest, read_only=False),
        dashboards_directory=dest / "charts",
    )


# Happy-path args for each TOOL_HANDLERS entry. query_board needs the scaffold
# fixture path injected; search_boards needs a safe tmp directory (the
# default cwd may contain YAML files that fail list_dashboards).
_TOOL_HAPPY_PATH_ARGS: dict[str, dict[str, Any]] = {
    "validate_board": {"path": "nonexistent.yml"},
    "render_board": {"yaml_content": _INLINE_BOARD_YAML},
    "execute_query": {"sql": "SELECT 1 AS x"},
    "describe_query": {"sql": "SELECT 1 AS x"},
    "query_board": {"name": "metrics"},  # "path" injected at test time
    "search_boards": {"query": "overview"},
    "docs": {"topic": "board"},
    "describe_board": {"path": "nonexistent.yml"},
    # "project_dir" injected at test time (writes stay inside tmp_path).
    "read_file": {"path": "nonexistent.yml"},
    "write_file": {"path": "out.yml", "content": "title: X\n"},
    "edit_file": {"path": "nonexistent.yml", "old_string": "a", "new_string": "b"},
    "glob_files": {"pattern": "*.yml"},
    "grep_files": {"pattern": "anything"},
    "move_file": {"source_path": "nonexistent.yml", "destination_path": "dest.yml"},
    "delete_file": {"path": "nonexistent.yml"},
    "list_skills": {},
    "get_skill": {"name": "kpi-row"},
    "search_skills": {"query": "kpi"},
    "list_diagnostic_codes": {},
    "get_diagnostic_code": {"code": "WARN-REDUNDANT-ENCODING"},
}


class TestAllToolHandlersHappyPath:
    """FR-003 / FR-010 — every TOOL_HANDLERS entry returns a dict.

    Warehouse-dependent tools use a real CSV-backed fixture (no live warehouse).
    FR-015: if a tool is added to TOOL_HANDLERS but not here, the
    test_all_tool_handlers_covered guard fails naming the missing tool.
    """

    @pytest.mark.parametrize(
        "tool_name",
        [pytest.param(name, id=name) for name in _TOOL_HAPPY_PATH_ARGS],
    )
    def test_tool_returns_dict(
        self,
        tool_name: str,
        tmp_path: Path,
        make_context: Callable[..., DbtChartsAIContext],
    ) -> None:
        base_args = dict(_TOOL_HAPPY_PATH_ARGS[tool_name])

        if tool_name == "query_board":
            ctx = _project_scaffold_context(tmp_path)
            base_args["path"] = str(tmp_path / "proj" / "charts" / "overview.yml")
        else:
            # File tools resolve against the session project (rooted at tmp_path);
            # no model-supplied project_dir is trusted anymore.
            ctx = make_context()

        result = dispatch_tool_call(tool_name, base_args, context=ctx)

        assert isinstance(result, dict), (
            f"{tool_name} returned {type(result).__name__}, expected dict"
        )

    def test_all_tool_handlers_covered(self) -> None:
        """FR-015 guard: adding a tool to TOOL_HANDLERS forces a row here."""
        assert set(_TOOL_HAPPY_PATH_ARGS) == set(TOOL_HANDLERS), (
            f"TOOL_HANDLERS coverage gap — add rows for: "
            f"{set(TOOL_HANDLERS) - set(_TOOL_HAPPY_PATH_ARGS)}"
        )


# ---------------------------------------------------------------------------
# FR-005 / FR-013: Per-tool error scenarios with schematic envelope assertions.
# ---------------------------------------------------------------------------


class TestProjectSkillsDispatch:
    """list_skills/get_skill/search_skills union the calling project's own
    skills/ into the built-in registry — routed through ctx.project_session.project
    (the same branch-scoped seam Cloud's CloudManagedProject and the CLI's
    FilesystemProject both implement), never a bare filesystem read."""

    def test_list_skills_includes_project_skill(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "skills" / "my-metric"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-metric\ndescription: Project metric guidance.\n"
            "kind: pattern\n---\nBody.\n"
        )

        result = dispatch_tool_call("list_skills", {}, context=context)

        names = {s["name"] for s in result["skills"]}
        assert "my-metric" in names
        by_name = {s["name"]: s for s in result["skills"]}
        assert by_name["my-metric"]["source"] == "project"

    def test_list_skills_is_an_index_without_bodies(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / "skills" / "my-metric"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-metric\ndescription: Project metric guidance.\n"
            "kind: pattern\n---\nSpecial project body.\n"
        )

        result = dispatch_tool_call("list_skills", {}, context=context)

        assert "my-metric" in {s["name"] for s in result["skills"]}
        assert [s["name"] for s in result["skills"] if "body" in s] == []

    def test_get_skill_loads_project_skill(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        skill_dir = tmp_path / ".claude" / "skills" / "my-metric"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-metric\ndescription: Project metric guidance.\n"
            "kind: pattern\n---\nSpecial project body.\n"
        )

        result = dispatch_tool_call("get_skill", {"name": "my-metric"}, context=context)

        assert "Special project body." in result["body"]
        assert result["source"] == "project"


class TestToolErrorEnvelopes:
    """FR-005 / FR-013 — common-error paths return the correct envelope shape.

    Assertions use isinstance checks, never message-text matching.
    """

    def test_unknown_tool_returns_error_envelope_shape(
        self, context: DbtChartsAIContext
    ) -> None:
        """FR-013: dispatch unknown tool → {"error": <non-empty str>}."""
        result = dispatch_tool_call("does-not-exist", {}, context=context)
        assert isinstance(result.get("error"), str) and result["error"]

    def test_get_skill_unknown_name_returns_structured_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """get_skill with unknown name → success=False + errors list."""
        result = dispatch_tool_call(
            "get_skill", {"name": "no-such-skill-xyz"}, context=context
        )
        assert result["success"] is False
        assert isinstance(result["errors"], list) and result["errors"]

    def test_execute_query_missing_sql_returns_error(
        self, context: DbtChartsAIContext
    ) -> None:
        """execute_query with empty SQL → success=False and non-empty errors list."""
        result = dispatch_tool_call("execute_query", {"sql": ""}, context=context)
        assert result["success"] is False
        assert isinstance(result.get("errors"), list) and result["errors"]

    def test_render_board_no_args_returns_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """render_board with no path/yaml_content → success=False."""
        result = dispatch_tool_call("render_board", {}, context=context)
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_render_board_both_path_and_content_returns_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """render_board with both path and yaml_content → success=False + errors.

        Relocated from the deleted ``agent_api.dashboards.render_dashboard``
        dispatch: ``_handle_render`` now owns this mutual-exclusion check
        directly, and the raised ValueError propagates to dispatch_tool_call's
        central exception handler as a failure envelope.
        """
        result = dispatch_tool_call(
            "render_board",
            {"path": "foo.yml", "yaml_content": "title: test"},
            context=context,
        )
        assert result["success"] is False
        assert result.get("errors"), "expected at least one error"

    def test_render_board_missing_path_returns_not_found_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """render_board with a nonexistent path → success=False + not-found error.

        Relocated from the deleted dispatch's not-found handling: _handle_render
        now resolves 'path' via resolve_board_or_error and returns its structured
        ERR-FILE-NOT-FOUND error directly.
        """
        result = dispatch_tool_call(
            "render_board", {"path": "nope.yml"}, context=context
        )
        assert result["status"] == "failed"
        assert any(
            "not found" in e["message"].lower() for e in result["validation_errors"]
        )

    def test_query_board_missing_required_args_returns_structured_envelope(
        self, context: DbtChartsAIContext
    ) -> None:
        """query_board with no required args returns the structured failure envelope.

        Boundary contract: dispatch_tool_call catches pydantic.ValidationError so a
        malformed call surfaces as {"success": False, "errors": [...]} rather than
        leaking through handle_call_tool as an MCP transport error.
        """
        result = dispatch_tool_call("query_board", {}, context=context)
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert isinstance(result.get("errors"), list) and result["errors"]

    def test_get_skill_missing_required_args_returns_structured_envelope(
        self, context: DbtChartsAIContext
    ) -> None:
        """get_skill with no required args returns the structured failure envelope.

        Sibling coverage: pins the dispatch-boundary catch for every handler that
        validates args via Pydantic, not just query_board.
        """
        result = dispatch_tool_call("get_skill", {}, context=context)
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert isinstance(result.get("errors"), list) and result["errors"]

    def test_query_board_valid_args_nonexistent_board_returns_structured_failure(
        self, tmp_path: Path, make_context: Callable[..., DbtChartsAIContext]
    ) -> None:
        """query_board with valid-shape args but missing board → success=False + errors list.

        This is the FR-005 coverage for query_board: schema-valid input that fails
        downstream returns the structured envelope, not an exception.
        """
        ctx = make_context(dashboards_directory=tmp_path)
        result = dispatch_tool_call(
            "query_board",
            {"name": "revenue", "path": str(tmp_path / "nonexistent-board-xyz.yml")},
            context=ctx,
        )
        assert result["success"] is False
        assert isinstance(result.get("errors"), list) and result["errors"]

    def test_render_board_list_format_returns_structured_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """render_board with a list format value → success=False + non-empty errors.

        Regression: a list value for 'format' previously caused a TypeError that
        escaped dispatch_tool_call and crashed generation silently. Both bugs are
        fixed together: _handle_render validates via Pydantic (catches the bad type)
        and dispatch_tool_call catches Exception (catches any residual deviation).
        """
        result = dispatch_tool_call(
            "render_board",
            {"yaml_content": "title: t", "format": ["svg", "html"]},
            context=context,
        )
        assert result["success"] is False
        assert isinstance(result.get("errors"), list) and result["errors"]

    def test_validate_board_neither_path_nor_content_returns_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """validate_board with no path and no yaml_content → success=False + errors.

        ValueError raised by validate_board() propagates to dispatch_tool_call's
        central exception handler and is surfaced as a failure envelope.
        """
        result = dispatch_tool_call("validate_board", {}, context=context)
        assert result["success"] is False
        assert result.get("errors"), "expected at least one error"

    def test_validate_board_both_path_and_content_returns_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """validate_board with both path and yaml_content → success=False + errors."""
        result = dispatch_tool_call(
            "validate_board",
            {"path": "some.yml", "yaml_content": "title: t\n"},
            context=context,
        )
        assert result["success"] is False
        assert result.get("errors"), "expected at least one error"


class TestToolCallOutcome:
    """``tool_call_outcome`` is the single predicate over the three failure
    envelope conventions this repo's tool results actually use — pinned
    against every shape that occurs, not just the two ``dispatch_tool_call``
    constructs itself (a bare ``{"error": ...}`` and ``{"success": False,
    ...}``)."""

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            # dispatch_tool_call's own unknown-tool shape.
            ({"error": "Unknown tool: bogus"}, "error"),
            # Raised handler / Cloud board_tools.py / skills_tool.py.
            ({"success": False, "errors": ["boom"]}, "error"),
            # BoardRenderResult (core/board.py) — failed render.
            ({"status": "failed", "board_error": {"code": "X"}}, "error"),
            # BoardRenderResult — a render with chart errors.
            ({"status": "partial", "chart_errors": [{"code": "Y"}]}, "partial"),
            ({"success": True, "data": []}, "ok"),
            ({"status": "ok", "data": {}}, "ok"),
            # Read-only tools (docs, get_skill) declare neither convention.
            ({"content": "..."}, "ok"),
        ],
    )
    def test_outcome_matches_envelope(
        self, result: dict[str, object], expected: str
    ) -> None:
        from dbt_charts.ai.tools import tool_call_outcome

        assert tool_call_outcome(result) == expected

    def test_real_dispatch_failed_render_maps_to_error(
        self, context: DbtChartsAIContext
    ) -> None:
        from dbt_charts.ai.tools import tool_call_outcome

        result = dispatch_tool_call(
            "render_board", {"path": "nope.yml"}, context=context
        )
        assert result["status"] == "failed"
        assert tool_call_outcome(result) == "error"


# ---------------------------------------------------------------------------
# default_source fallback: ctx.default_source used when model omits source
# ---------------------------------------------------------------------------


class TestDefaultSourceFallback:
    """ctx.default_source is injected into execute_query when the model omits source."""

    def test_execute_query_falls_back_to_default_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        make_context: Callable[..., DbtChartsAIContext],
    ) -> None:
        """_handle_query uses ctx.default_source when parsed.source is None."""
        captured: dict[str, object] = {}

        def fake_execute_query(
            *, sql, variables=None, source=None, limit, adapter_registry
        ):
            captured["source"] = source
            from dbt_charts.agent_api.query import ExecuteQueryResult

            return ExecuteQueryResult(
                success=True,
                data=[],
                columns=[],
                errors=[],
                row_count=0,
                truncated=False,
            )

        monkeypatch.setattr(
            "dbt_charts.agent_api.query.execute_query", fake_execute_query
        )

        ctx = make_context(default_source="dundersign_db")
        dispatch_tool_call("execute_query", {"sql": "SELECT 1"}, context=ctx)

        assert captured["source"] == "dundersign_db"

    def test_execute_query_explicit_source_wins_over_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        make_context: Callable[..., DbtChartsAIContext],
    ) -> None:
        """Explicit source in tool args is not overwritten by ctx.default_source."""
        captured: dict[str, object] = {}

        def fake_execute_query(
            *, sql, variables=None, source=None, limit, adapter_registry
        ):
            captured["source"] = source
            from dbt_charts.agent_api.query import ExecuteQueryResult

            return ExecuteQueryResult(
                success=True,
                data=[],
                columns=[],
                errors=[],
                row_count=0,
                truncated=False,
            )

        monkeypatch.setattr(
            "dbt_charts.agent_api.query.execute_query", fake_execute_query
        )

        ctx = make_context(default_source="dundersign_db")
        dispatch_tool_call(
            "execute_query", {"sql": "SELECT 1", "source": "examples_db"}, context=ctx
        )

        assert captured["source"] == "examples_db"

    def test_no_default_source_leaves_source_as_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        make_context: Callable[..., DbtChartsAIContext],
    ) -> None:
        """When ctx.default_source is None, source is passed through unchanged (None)."""
        captured: dict[str, object] = {}

        def fake_execute_query(
            *, sql, variables=None, source=None, limit, adapter_registry
        ):
            captured["source"] = source
            from dbt_charts.agent_api.query import ExecuteQueryResult

            return ExecuteQueryResult(
                success=True,
                data=[],
                columns=[],
                errors=[],
                row_count=0,
                truncated=False,
            )

        monkeypatch.setattr(
            "dbt_charts.agent_api.query.execute_query", fake_execute_query
        )

        ctx = make_context()  # no default_source
        dispatch_tool_call("execute_query", {"sql": "SELECT 1"}, context=ctx)

        assert captured["source"] is None


class TestVariableBindingsWireShape:
    """Tool dispatch converts array-of-pairs variables to dict at the boundary.

    The model emits ``[{"name": "k", "value": "v"}]``; the engine receives
    ``{"k": "v"}``. Duplicate names are a caller error, rejected before reaching
    the engine.
    """

    def test_execute_query_with_variable_bindings(
        self,
        make_context: Callable[..., DbtChartsAIContext],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Array-of-pairs variables are converted to dict before reaching the engine."""
        captured: dict[str, object] = {}

        def fake_execute_query(
            *, sql, variables=None, source=None, limit, adapter_registry
        ):
            captured["variables"] = variables
            from dbt_charts.agent_api.query import ExecuteQueryResult

            return ExecuteQueryResult(
                success=True,
                data=[],
                columns=[],
                errors=[],
                row_count=0,
                truncated=False,
            )

        monkeypatch.setattr(
            "dbt_charts.agent_api.query.execute_query", fake_execute_query
        )
        ctx = make_context()
        dispatch_tool_call(
            "execute_query",
            {"sql": "SELECT 1 AS x", "variables": [{"name": "region", "value": "US"}]},
            context=ctx,
        )
        assert captured["variables"] == {"region": "US"}

    def test_execute_query_duplicate_variable_name_returns_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        """Duplicate variable names in the binding list → success=False."""
        result = dispatch_tool_call(
            "execute_query",
            {
                "sql": "SELECT 1",
                "variables": [
                    {"name": "region", "value": "US"},
                    {"name": "region", "value": "EU"},
                ],
            },
            context=context,
        )
        assert result.get("success") is False
        assert result.get("errors") or result.get("error")

    def test_render_board_with_variable_bindings(
        self, context: DbtChartsAIContext
    ) -> None:
        """render_board with array-of-pairs variables doesn't crash."""
        yaml_content = (
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )
        result = dispatch_tool_call(
            "render_board",
            {
                "yaml_content": yaml_content,
                "variables": [{"name": "foo", "value": "bar"}],
            },
            context=context,
        )
        assert isinstance(result, dict)
        # variables don't apply to this board (no {{ foo }}) — still renders ok
        assert result.get("status") == "ok"


def test_render_board_caps_rows_in_model_facing_data(
    context: DbtChartsAIContext,
) -> None:
    """Agent renders cap embedded rows explicitly — the model gets a bounded,
    marked-partial data payload, never an unbounded dump."""
    from dbt_charts.ai.tools import MODEL_MAX_ROWS_PER_QUERY

    total = MODEL_MAX_ROWS_PER_QUERY + 5
    values = "\n".join(f"      - [r{i}, {i}]" for i in range(total))
    yaml_content = (
        "title: Cap\nqueries:\n  q:\n    columns: [x, y]\n    values:\n"
        f"{values}\n"
        "charts:\n  c:\n    query: q\n    type: bar\n    x: x\n    y: y\n"
        "rows:\n  - c\n"
    )
    result = dispatch_tool_call(
        "render_board",
        {"yaml_content": yaml_content, "format": "yaml"},
        context=context,
    )
    assert result["status"] == "ok", result
    head = MODEL_MAX_ROWS_PER_QUERY - MODEL_MAX_ROWS_PER_QUERY // 2
    tail = MODEL_MAX_ROWS_PER_QUERY // 2
    assert f"first {head} and last {tail} of {total} rows" in result["data"]


class TestToolResultSuccessContract:
    """Every tool's payload states its own outcome.

    The chat UI reads `success` off the payload to decide whether a call
    resolved — a missing key is falsy there, so a payload that omits it renders
    a successful call as a failed one. `get_skill` shipped that way: it dumped
    a bare `Skill` (a domain model with no verdict field) while every sibling
    returned a `*Result` envelope carrying `success: bool = True`, so every
    successful skill load displayed as "Skill not loaded".
    """

    def test_get_skill_success_payload_states_success(
        self, context: DbtChartsAIContext
    ) -> None:
        payload = dispatch_tool_call(
            "get_skill", {"name": "board-build"}, context=context
        )

        assert payload["success"] is True
        assert payload["name"] == "board-build"
        assert payload["body"]

    def test_get_skill_unknown_payload_states_failure(
        self, context: DbtChartsAIContext
    ) -> None:
        payload = dispatch_tool_call(
            "get_skill", {"name": "no-such-skill-xyz"}, context=context
        )

        assert payload["success"] is False
        assert payload["errors"]


class TestWriteFileNeverClobbersUnreadContent:
    """An overwrite of an existing file requires having read it first.

    The failure this closes: asked to edit one chart, the agent wrote a
    single-chart baseline it had built for an ephemeral comparison over the
    whole saved board, and every other chart in the dashboard was gone. It
    never read the file it replaced. Prompt rules already said not to
    (CLOUD_TOOL_GUIDANCE's preview-is-the-user's-click), and did not hold —
    so the tool refuses instead.
    """

    def test_creating_a_new_file_needs_no_read(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        result = dispatch_tool_call(
            "write_file", {"path": "new.yml", "content": "title: X\n"}, context=context
        )
        assert result["success"] is True
        assert (tmp_path / "new.yml").read_text() == "title: X\n"

    def test_overwriting_an_unread_file_is_refused(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        (tmp_path / "board.yml").write_text("title: Real board\ncharts: {}\n")

        result = dispatch_tool_call(
            "write_file",
            {"path": "board.yml", "content": "title: Baseline\n"},
            context=context,
        )

        assert result["success"] is False
        assert "read_file" in result["error"]
        # The whole point: the bytes on disk are untouched.
        assert (tmp_path / "board.yml").read_text() == "title: Real board\ncharts: {}\n"

    def test_overwriting_is_allowed_after_reading(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        (tmp_path / "board.yml").write_text("title: Real board\n")

        dispatch_tool_call("read_file", {"path": "board.yml"}, context=context)
        result = dispatch_tool_call(
            "write_file",
            {"path": "board.yml", "content": "title: Edited\n"},
            context=context,
        )

        assert result["success"] is True
        assert (tmp_path / "board.yml").read_text() == "title: Edited\n"

    def test_a_failed_read_does_not_count_as_having_read_it(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        # Reading a directory fails; it must not license an overwrite of a
        # file that happens to share the path string later.
        (tmp_path / "board.yml").mkdir()
        dispatch_tool_call("read_file", {"path": "board.yml"}, context=context)
        (tmp_path / "board.yml").rmdir()
        (tmp_path / "board.yml").write_text("title: Real board\n")

        result = dispatch_tool_call(
            "write_file",
            {"path": "board.yml", "content": "title: Baseline\n"},
            context=context,
        )

        assert result["success"] is False

    def test_writing_a_file_licenses_rewriting_it(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        # build → render → fix → re-save is the primary loop: refusing the agent
        # its own file one step later would be a gate on nobody's mistake.
        dispatch_tool_call(
            "write_file", {"path": "new.yml", "content": "title: v1\n"}, context=context
        )
        result = dispatch_tool_call(
            "write_file", {"path": "new.yml", "content": "title: v2\n"}, context=context
        )

        assert result["success"] is True
        assert (tmp_path / "new.yml").read_text() == "title: v2\n"

    def test_editing_a_file_does_not_license_rewriting_it(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        # edit_file reads internally but returns no content, so the agent has
        # been shown nothing. A search hit carries enough chart text to build a
        # unique old_string for a board never opened — licensing a whole-file
        # write off that would widen the gate to the case it exists to stop.
        (tmp_path / "board.yml").write_text("title: Real board\ncharts: {}\n")
        dispatch_tool_call(
            "edit_file",
            {"path": "board.yml", "old_string": "Real", "new_string": "Edited"},
            context=context,
        )

        result = dispatch_tool_call(
            "write_file",
            {"path": "board.yml", "content": "title: Rewritten\n"},
            context=context,
        )

        assert result["success"] is False
        assert (
            tmp_path / "board.yml"
        ).read_text() == "title: Edited board\ncharts: {}\n"

    def test_a_path_the_gate_cannot_check_is_refused_not_waved_through(
        self, context: DbtChartsAIContext, tmp_path: Path
    ) -> None:
        # `exists()` rejects a backslash ref; `write_text` does not. Treating
        # "I could not check" as "go ahead" wrote a junk `charts\board.yml` at
        # the root on POSIX — and on Windows that string *is* `charts/board.yml`
        # and overwrites the unread board this gate exists to protect.
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "board.yml").write_text("title: Real\ncharts: {}\n")

        result = dispatch_tool_call(
            "write_file",
            {"path": "charts\\board.yml", "content": "title: junk\n"},
            context=context,
        )

        assert result["success"] is False
        assert (tmp_path / "charts" / "board.yml").read_text() == (
            "title: Real\ncharts: {}\n"
        )
        assert not (tmp_path / "charts\\board.yml").exists()

    def test_an_unresolvable_path_still_reports_as_a_typed_write_result(
        self, context: DbtChartsAIContext
    ) -> None:
        # The gate's existence probe raises on a path the project cannot
        # resolve. Letting that escape would swap write_file's typed result
        # (with `path`, and `error`) for the dispatch layer's blind catch
        # (no `path`, and `errors`) on a documented class of bad argument.
        result = dispatch_tool_call(
            "write_file", {"path": "../escape.yml", "content": "x\n"}, context=context
        )

        assert result["success"] is False
        assert result["path"] == "../escape.yml"
        assert "error" in result
