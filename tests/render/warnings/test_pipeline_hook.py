"""Tests for render-warning hook integration in render().

These tests verify that render() returns warnings on RenderResult,
and that registered detectors fire and their output appears on the result.

Also covers:
- BoardRenderResult.model_dump() includes warnings (MCP serialization).
- dispatch_tool_call("render_board", ...) wire JSON contains warnings.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics import WARN_REDUNDANT_ENCODING, Diagnostic
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render
from dbt_charts.core.render.warnings import registry as _registry

_BOARD_YAML = """
title: Test
charts:
  c:
    query: q
    type: bar
    x: category
    y: value
queries:
  q:
    sql: SELECT 'a' AS category, 1 AS value
    source: test_source
rows:
  - c
"""


def _make_executor(board, query_registry, rows=None):
    ok = Mock()
    ok.is_success = True
    ok.data = rows if rows is not None else [{"category": "a", "value": 1}]
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def test_render_result_has_render_warnings_field_empty_by_default() -> None:
    """With no detectors registered, render_warnings is an empty list."""
    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)
    render_result = render(result.board, executor, format="json")
    assert render_result.warnings == []


def test_seven_slice_pie_with_no_dominant_slice_emits_no_pie_warning() -> None:
    """A pie's slice count alone is not a defect: wheel-first labeling handles it."""
    yaml_text = """
title: Test
charts:
  p:
    query: q
    type: pie
    theta: value
    color: category
queries:
  q:
    sql: SELECT 1
    source: test_source
rows:
  - p
"""
    rows = [{"category": f"c{i}", "value": 10 + i} for i in range(7)]
    result = compile(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry, rows)

    render_result = render(result.board, executor, format="json")

    assert [
        w.code for w in render_result.warnings if w.code.startswith("WARN-PIE-")
    ] == []


def test_render_result_propagates_detector_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monkeypatched detector's warnings appear on render_result.warnings."""
    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None
    executor = _make_executor(result.board, result.query_registry)

    fake = ModuleType("fake_pipeline_detector")

    def _detect(ctx):
        return [
            Diagnostic.from_code(
                WARN_REDUNDANT_ENCODING, message="pipeline warning", chart="c"
            )
        ]

    fake.detect = _detect  # type: ignore[attr-defined]
    monkeypatch.setattr(_registry, "DETECTORS", [fake])

    render_result = render(result.board, executor, format="json")
    assert len(render_result.warnings) == 1
    assert render_result.warnings[0].code == WARN_REDUNDANT_ENCODING.code
    assert render_result.warnings[0].chart == "c"


def test_rendered_dashboard_model_dump_includes_warnings() -> None:
    """BoardRenderResult.model_dump() serializes warnings — covers MCP wire."""
    from dbt_charts.core.board import BoardRenderResult

    warning = Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="m")
    dashboard = BoardRenderResult(
        status="ok",
        warnings=[warning],
    )
    dumped = dashboard.model_dump()
    assert "warnings" in dumped
    assert dumped["warnings"] == [warning.model_dump()]


def test_failed_query_chart_omitted_from_chart_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chart whose query raises must be absent from WarningContext.chart_results.

    This guards the contract that detectors rely on: absent = failed execute,
    present-with-empty-list = genuine zero rows.
    """
    from dbt_charts.core.render.warnings import WarningContext

    result = compile(_BOARD_YAML)
    assert result.success
    assert result.board is not None

    # Executor whose only chart query raises.
    failing_ok = Mock()
    failing_ok.is_success = True
    failing_ok.data = []
    failing_ok.resolved_relations = None
    mock_registry = Mock()
    mock_registry.execute.side_effect = RuntimeError("query failed")
    failing_executor = Executor(
        result.board,
        adapter_registry=mock_registry,
        query_registry=result.query_registry,
    )

    captured_ctx: list[WarningContext] = []

    fake = ModuleType("ctx_capture_detector")

    def _detect(ctx: WarningContext) -> list[Diagnostic]:
        captured_ctx.append(ctx)
        return []

    fake.detect = _detect  # type: ignore[attr-defined]
    monkeypatch.setattr(_registry, "DETECTORS", [fake])

    render(result.board, failing_executor, format="json")

    assert len(captured_ctx) == 1
    # Failed chart must be absent — not present as an empty list.
    assert "c" not in captured_ctx[0].chart_results


_INLINE_KPI_YAML = (
    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
)


def test_dispatch_render_board_wire_includes_warnings_key(
    tmp_path: Path,
) -> None:
    """dispatch_tool_call('render_board') JSON result includes warnings."""
    from dbt_charts.agent_api import ProjectSession
    from dbt_charts.ai.context import DbtChartsAIContext
    from dbt_charts.ai.tools import dispatch_tool_call

    context = DbtChartsAIContext(
        project_session=ProjectSession.open(tmp_path, read_only=False)
    )
    result = dispatch_tool_call(
        "render_board",
        {"yaml_content": _INLINE_KPI_YAML},
        context=context,
    )
    assert result["status"] == "ok"
    assert "warnings" in result
    assert result["warnings"] == []
