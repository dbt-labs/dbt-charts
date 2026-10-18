"""Regression test: agent_api Args models coerce their ``path`` field to Path,
and the free functions / dispatch handlers accept raw JSON-string paths without
crashing on a ``Path``-only method call.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dbt_charts.agent_api.boards import RenderBoardArgs
from dbt_charts.agent_api.describe import DescribeBoardArgs, DescribeBoardResult
from dbt_charts.agent_api.query import ExecuteQueryArgs
from dbt_charts.agent_api.validate import ValidateResult
from dbt_charts.cli.filesystem_project import FilesystemProject

if TYPE_CHECKING:
    from dbt_charts.ai.context import DbtChartsAIContext


class TestPydanticPathCoercion:
    """Pydantic v2 must coerce a JSON string ``path`` to Path."""

    def test_execute_query_args_board_is_path(self) -> None:
        parsed = ExecuteQueryArgs.model_validate(
            {"board": "charts/x.yml", "query": "revenue"}
        )
        assert isinstance(parsed.board, Path)

    def test_render_board_args_path_is_path(self) -> None:
        parsed = RenderBoardArgs.model_validate({"path": "charts/x.yml"})
        assert isinstance(parsed.path, Path)

    def test_describe_board_args_path_is_path(self) -> None:
        parsed = DescribeBoardArgs.model_validate({"path": "charts/x.yml"})
        assert isinstance(parsed.path, Path)


class TestMcpSchemaFormat:
    """The LLM-visible MCP inputSchema encodes ``path`` fields with format=path.

    Pydantic v2 encodes ``Path``-typed fields as ``{"type": "string",
    "format": "path"}`` (wrapped in ``anyOf`` when optional). This pins that
    wire shape so any drift is intentional.
    """

    def _assert_path_format(self, prop: dict[str, Any]) -> None:
        assert prop.get("format") == "path" or any(
            v.get("format") == "path" for v in prop.get("anyOf", [])
        ), f"schema should carry format=path, got: {prop}"

    def test_execute_query_schema_board_path_format(self) -> None:
        from dbt_charts.ai.tool_schemas import EXECUTE_SQL

        schema = EXECUTE_SQL["input_schema"]
        self._assert_path_format(schema["properties"]["board"])


class TestFreeFunctionPathContract:
    """Free functions must accept Path / Project without TypeError.

    These tests confirm the free-function signatures accept a plain pathlib.Path
    (not just str) for path args and a Project for project args. They call with a
    nonexistent project so we never need a real project on disk — the functions are
    expected to return a failure result, not raise TypeError.
    """

    def test_validate_accepts_path_project(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.validate import validate

        result = validate(
            Path("charts/x.yml"), project=local_project(Path("/tmp/nonexistent"))
        )
        assert isinstance(result, ValidateResult)
        assert result.success is False

    def test_describe_board_accepts_path_project(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        result = describe_board(
            Path("charts/x.yml"), project=local_project(Path("/tmp/nonexistent"))
        )
        assert isinstance(result, DescribeBoardResult)
        assert result.success is False

    def test_query_board_accepts_path_project(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.query import QueryBoardResult, query_board
        from dbt_charts.core.execute.adapters import build_adapter_registry

        registry = build_adapter_registry(
            local_project(Path("/tmp/nonexistent")), read_only=True
        )
        result = query_board(
            "revenue",
            Path("charts/x.yml"),
            project=local_project(Path("/tmp/nonexistent")),
            adapter_registry=registry,
        )
        assert isinstance(result, QueryBoardResult)
        assert result.success is False

    def test_resolve_board_or_error_accepts_path_project(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """resolve_board_or_error is the render-path boundary that now accepts
        Path/Project — render_board itself takes a pre-resolved BoardFile."""
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        project = local_project(Path("/tmp/nonexistent"))
        result = resolve_board_or_error(Path("charts/x.yml"), project)
        assert isinstance(result, Diagnostic)


class TestDispatchPathContract:
    """Dispatch handlers must not crash when args contain raw JSON strings for path fields.

    Each handler in ai/tools/__init__.py receives args: dict[str, Any] — the
    raw JSON payload from an LLM/MCP call. Path fields arrive as plain strings.
    A regression where the handler passes the raw string directly to a function
    whose signature becomes Path-typed produces:

        AttributeError: 'str' object has no attribute 'is_absolute'

    These tests pin that specific crash class — the case where a migrated
    production function directly calls a Path-only method on a raw str without
    wrapping. They do NOT trap silent type divergence when the production
    function internally wraps via Path() or resolve_scoped_path; pyright covers
    that case instead.
    """

    def _ctx(
        self, tmp_path: Path
    ) -> tuple[Callable[..., dict[str, Any]], DbtChartsAIContext]:
        from dbt_charts.agent_api import ProjectSession
        from dbt_charts.ai.context import DbtChartsAIContext
        from dbt_charts.ai.tools import dispatch_tool_call

        ctx = DbtChartsAIContext(
            project_session=ProjectSession.open(tmp_path, read_only=True)
        )
        return dispatch_tool_call, ctx

    def test_render_board_dispatch_accepts_string_path(self, tmp_path: Path) -> None:
        dispatch, ctx = self._ctx(tmp_path)
        result = dispatch(
            "render_board", {"path": "charts/x.yml", "as_link": True}, context=ctx
        )
        assert isinstance(result, dict)
        assert result.get("status") == "failed"

    def test_describe_board_dispatch_accepts_string_path(self, tmp_path: Path) -> None:
        dispatch, ctx = self._ctx(tmp_path)
        result = dispatch("describe_board", {"path": "charts/x.yml"}, context=ctx)
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_execute_query_dispatch_accepts_string_board(self, tmp_path: Path) -> None:
        dispatch, ctx = self._ctx(tmp_path)
        result = dispatch(
            "execute_sql",
            {"query": "revenue", "board": "charts/x.yml"},
            context=ctx,
        )
        assert isinstance(result, dict)
        assert result.get("success") is False

    def test_search_files_dispatch_takes_text_only(self, tmp_path: Path) -> None:
        dispatch, ctx = self._ctx(tmp_path)
        result = dispatch("search_files", {"text": "revenue"}, context=ctx)
        assert isinstance(result, dict)
        assert result.get("success") is True
