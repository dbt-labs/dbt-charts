"""Partial-success rendering: per-chart errors render as inline error blocks.

Tests that a single chart runtime failure does not kill the entire board render.
Instead, it produces an inline error SVG block and populates chart_errors on
the RenderResult.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.errors import JinjaError
from dbt_charts.core.compile.models.chart.normalized import Chart, KpiChart
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics import (
    ERR_FILE_NOT_FOUND,
    ERR_INTERNAL,
    Diagnostic,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import Project
from dbt_charts.core.render import (
    board_resolve as _board_resolve_mod,
    layout_sizing as _layout_sizing_mod,
    renderer as _renderer_mod,
)
from dbt_charts.core.render.board_resolve import (
    build_resolved_board,
    build_resolved_board_static,
)
from dbt_charts.core.render.chart import rendering as _rendering_mod
from dbt_charts.core.render.chart.rendering import render_chart_item
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.renderer import render
from dbt_charts.core.render.warnings.base import WarningContext
from dbt_charts.core.render.warnings.pie_dominant_segment import (
    detect as detect_pie_dominant_segment,
)

from .._svg_render import PINNED_RENDER_TIME

# Bound before any test patches the module attribute, so the per-chart patch
# helper can still delegate for the charts it is not simulating a failure for.
_REAL_RESOLVE = resolve_chart_with_runtime_inputs

# ---------------------------------------------------------------------------
# Fixtures / shared helpers
# ---------------------------------------------------------------------------

_TWO_CHART_YAML = """
title: Two Charts
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
  q_bad:
    sql: "SELECT 2 AS value"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
  bad:
    type: kpi
    query: q_bad
    value: value
cols:
  - good
  - bad
"""


def _compile_two_chart_board():
    result = compile(_TWO_CHART_YAML)
    assert result.success, result.errors
    assert result.board is not None
    return result.board


def _make_chart(chart_id: str = "c1") -> Chart:
    q = SqlQuery(sql="SELECT 1", source="test")
    return KpiChart(
        id=chart_id,
        # The normalizer gives every `charts:` entry its authoring path; a
        # chart built without one is a shape production never produces.
        source_path=f"charts.{chart_id}",
        query=q,
        query_name="q",
        type="kpi",
        value="value",
        style=None,
    )


def _executor_good_bad(good_rows, bad_exc):
    """Executor where q_good succeeds and q_bad raises.

    Both ``execute_chart`` (the data-format walk's own lookup, keyed by
    chart id — board_to_dict.py) and ``execute_query`` (the real svg draw's
    lookup, keyed by query name — layout_sizing.build_chart_datasets) must
    fail the same way: render() now draws the board for every format, not
    just svg, so both call sites run regardless of the requested format.
    """
    mock = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))

    def _execute_chart_side_effect(chart, variables):
        if chart.id == "good":
            return good_rows
        raise bad_exc

    def _execute_query_side_effect(query_name, variables=None):
        if query_name == "q_good":
            return good_rows
        raise bad_exc

    mock.execute_chart.side_effect = _execute_chart_side_effect
    mock.execute_query.side_effect = _execute_query_side_effect
    mock._query_errors = {}
    # render() now draws the board for every format (not just svg), and the
    # svg footer/timestamp code iterates this attribute directly — it must be
    # a real (empty) list, not the default MagicMock attribute.
    mock.cache_hit_ats = []
    mock.rendered_at = PINNED_RENDER_TIME
    # An unconfigured MagicMock.is_cached() is truthy for any input, which
    # would route every query through render()'s synchronous cache-hit
    # pre-pass (a plain, un-isolated `execute_query` call) instead of the
    # mocked-out `execute_queries_parallel` — raising bad_exc there, outside
    # any chart-isolation try/except, rather than where each test means it to
    # fire (the per-chart draw/data-walk calls below).
    mock.is_cached.return_value = False
    return mock


# ---------------------------------------------------------------------------
# RenderResult — return type of render()
# ---------------------------------------------------------------------------


def test_render_returns_render_result():
    """render() must return a RenderResult, not raw str/bytes."""
    board = _compile_two_chart_board()

    mock_executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_executor.execute_chart.return_value = [{"value": 42}]
    mock_executor.execute_query.return_value = [{"value": 42}]
    mock_executor._query_errors = {}
    mock_executor.cache_hit_ats = []
    mock_executor.rendered_at = PINNED_RENDER_TIME

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, mock_executor, format="json")

    assert isinstance(result, RenderResult), (
        f"Expected RenderResult, got {type(result)}"
    )


# ---------------------------------------------------------------------------
# Per-chart error isolation — SVG format
# ---------------------------------------------------------------------------


def test_one_runtime_failure_other_charts_render():
    """ExecutionError on one chart → other chart renders; no board_error."""
    board = _compile_two_chart_board()
    bad_exc = ExecutionError("query timeout")
    executor = _executor_good_bad([{"value": 42}], bad_exc)

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor, format="json")

    assert isinstance(result, RenderResult)
    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert result.output is not None


def test_missing_csv_file_code_reaches_chart_error():
    """Typed missing-file execution errors survive into inline chart errors."""
    board = _compile_two_chart_board()
    bad_exc = QueryError("CSV file not found: /tmp/missing.csv", "q_bad")
    bad_exc.code = ERR_FILE_NOT_FOUND
    executor = _executor_good_bad([{"value": 42}], bad_exc)

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert result.chart_errors[0].code == ERR_FILE_NOT_FOUND.code


def test_callout_error_title_uses_registered_code_title():
    """The placard title is the registered ErrorCode's title, not a hand-rolled
    "Query Error: {name}" / "Chart Error: {id}" string — it depends only on
    the diagnostic code, not on whether a query_name happens to be set."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style import resolve_style
    from dbt_charts.core.diagnostics.base import DbtChartsError
    from dbt_charts.core.render.engine import render_error_placard

    diagnostic = DbtChartsError.from_code(
        ERR_FILE_NOT_FOUND, path="missing.csv"
    ).to_diagnostic()
    svg = render_error_placard(
        diagnostic,
        chart_id="c",
        width=400.0,
        height=200.0,
        style=resolve_style(get_theme_style()).chart_defaults.callout_error,
    )

    assert f">{ERR_FILE_NOT_FOUND.title}</text>" in svg


def test_chart_render_error_isolated():
    """RenderError on one chart → inline block; board renders."""
    board = _compile_two_chart_board()
    bad_exc = RenderError("vl-convert crash")
    executor = _executor_good_bad([{"value": 42}], bad_exc)

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is None
    assert len(result.chart_errors) == 1


def test_chart_jinja_error_isolated():
    """JinjaError on one chart → inline block; board renders."""
    board = _compile_two_chart_board()
    bad_exc = JinjaError("bad template")
    executor = _executor_good_bad([{"value": 42}], bad_exc)

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is None
    assert len(result.chart_errors) == 1


def test_chart_internal_exception_isolated():
    """Bare Exception on one chart → wrapped as ERR-INTERNAL."""
    board = _compile_two_chart_board()
    bad_exc = RuntimeError("boom")
    executor = _executor_good_bad([{"value": 42}], bad_exc)

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert result.chart_errors[0].code == ERR_INTERNAL.code


# ---------------------------------------------------------------------------
# Error collector in render_chart_item
# ---------------------------------------------------------------------------


def test_render_chart_item_error_collector():
    """render_chart_item appends to error_collector on failure."""
    _chart = _make_chart("c1")
    mock_exec = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_exec.execute_chart.side_effect = ExecutionError("timeout")

    rs, ctx = resolve_style_and_context(get_theme_style())
    collector: list[Diagnostic] = []
    svg, _height = render_chart_item(
        resolve(_chart, [], chart_style_context=ctx),
        mock_exec,
        {},
        available_width=300.0,
        available_height=200.0,
        resolved_style=rs,
        render_cache={},
        error_collector=collector,
    )

    assert len(collector) == 1
    assert isinstance(collector[0], Diagnostic)
    # Error SVG must still be returned (inline error block)
    assert svg


def test_render_chart_item_error_diagnostic_carries_authored_path():
    """The inline error Diagnostic's .path reuses the same chart-identity
    vocabulary as the SVG's data-authored-path attribute — the shared
    click-to-source grammar a source map can key into."""
    _chart = _make_chart("c1")
    mock_exec = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_exec.execute_chart.side_effect = ExecutionError("timeout")

    rs, ctx = resolve_style_and_context(get_theme_style())
    collector: list[Diagnostic] = []
    render_chart_item(
        resolve(_chart, [], chart_style_context=ctx),
        mock_exec,
        {},
        available_width=300.0,
        available_height=200.0,
        resolved_style=rs,
        render_cache={},
        error_collector=collector,
    )

    assert collector[0].path == "charts.c1"
    # Cloud's render_error_source prefers fields["source_path"] over the
    # charts.<id> reconstruction, which is wrong for an inline chart whose id
    # is generated. The Diagnostic copies fields rather than aliasing them, so
    # this only holds while the stamp happens before to_diagnostic().
    assert collector[0].fields["source_path"] == "charts.c1"


def test_a_failed_imported_chart_carries_its_coordinates_but_no_handle(
    tmp_path: Path,
) -> None:
    """The two channels `Chart.source_path` feeds must split, not starve together.

    `charts: {borrowed: other.yml.charts.broken}` names a real definition in
    another file. #7265 blanked `Chart.source_path` itself to withhold the
    design-panel authoring handle for that case — correct for the handle, but
    it also starved `stamp_chart_diagnostic`/`exc.fields["source_path"]`, so
    Cloud's `render_error_source` short-circuits on `""` instead of falling
    through to `charts.<id>`. A failed imported chart's error should still
    carry `charts.borrowed` — only the handle goes missing, mirroring how
    `Variable.defined_in_other_file` already splits the same two channels on
    the variable side.
    """
    from dbt_charts.cli.filesystem_project import FilesystemProject

    (tmp_path / "other.yml").write_text(
        """
queries:
  q:
    type: values
    rows:
      - {value: 1}
charts:
  broken:
    type: kpi
    query: q
    value: value
"""
    )
    board_yaml = """
title: Borrowing
charts:
  borrowed: other.yml.charts.broken
rows:
  - borrowed
"""
    result = compile(board_yaml, base_dir=FilesystemProject(tmp_path).directory())
    assert result.success, result.errors
    board = result.board
    assert board is not None
    chart = board.charts["borrowed"]

    mock_exec = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_exec.execute_chart.side_effect = ExecutionError("timeout")
    mock_exec.get_query_provenance.return_value = []

    rs, ctx = resolve_style_and_context(get_theme_style())
    collector: list[Diagnostic] = []
    svg, _height = render_chart_item(
        resolve(chart, [], chart_style_context=ctx),
        mock_exec,
        {},
        available_width=300.0,
        available_height=200.0,
        resolved_style=rs,
        render_cache={},
        error_collector=collector,
    )

    assert collector[0].path == "charts.borrowed"
    assert collector[0].fields["source_path"] == "charts.borrowed"
    assert "data-authored-path" not in svg


def test_render_chart_item_no_collector_still_returns_error_block():
    """Without error_collector, render_chart_item still returns an error SVG."""
    _chart = _make_chart("c1")
    mock_exec = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_exec.execute_chart.side_effect = ExecutionError("timeout")

    rs, ctx = resolve_style_and_context(get_theme_style())
    svg, _height = render_chart_item(
        resolve(_chart, [], chart_style_context=ctx),
        mock_exec,
        {},
        available_width=300.0,
        resolved_style=rs,
        render_cache={},
    )
    assert svg  # must return an error block SVG, not raise


# ---------------------------------------------------------------------------
# render_callout_card — code/hint/doc_url
# ---------------------------------------------------------------------------


def test_inline_callout_block_renders_code():
    """render_callout_card with code renders that code string in SVG."""
    from dbt_charts.core.render.engine import render_callout_card

    svg = render_callout_card(
        message="Column missing",
        width=300.0,
        placard={
            "code": "ERR-INTERNAL",
            "code_url": "https://docs.dbtcharts.com/reference/errors/#err-internal",
            "hint": None,
            "doc_url": None,
        },
        style=resolve_style(get_theme_style()).chart_defaults.callout_error,
        chart_id="callout",
    )
    assert "ERR-INTERNAL" in svg


def test_inline_callout_block_renders_hint():
    """render_callout_card with hint includes hint text in SVG."""
    from dbt_charts.core.render.engine import render_callout_card

    svg = render_callout_card(
        message="Column missing",
        width=300.0,
        placard={
            "code": "ERR-INTERNAL",
            "code_url": "https://docs.dbtcharts.com/reference/errors/#err-internal",
            "hint": "Check your column names",
            "doc_url": None,
        },
        style=resolve_style(get_theme_style()).chart_defaults.callout_error,
        chart_id="callout",
    )
    assert "Check your column names" in svg


# ---------------------------------------------------------------------------
# agent_api BoardRenderResult shape
# ---------------------------------------------------------------------------


def test_rendered_dashboard_has_validation_errors_not_errors():
    """BoardRenderResult uses validation_errors, not errors."""
    from dbt_charts.agent_api.boards import BoardRenderResult

    # validation_errors field must exist
    rd = BoardRenderResult(status="failed", validation_errors=[])
    assert hasattr(rd, "validation_errors")
    assert rd.validation_errors == []

    # old 'errors' field must NOT exist
    assert not hasattr(rd, "errors")


def test_rendered_dashboard_has_board_error():
    """BoardRenderResult has board_error: Diagnostic | None."""
    from dbt_charts.agent_api.boards import BoardRenderResult

    rd = BoardRenderResult(status="failed")
    assert rd.board_error is None


def test_render_dashboard_chart_errors_populated(
    tmp_path, local_project: Callable[..., Project]
):
    """render_dashboard returns chart_errors when a chart fails at runtime."""
    from unittest.mock import patch

    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.render.render_result import RenderResult

    # Fake adapter registry
    mock_registry = MagicMock(spec=AdapterRegistry)

    yaml_content = _TWO_CHART_YAML

    from dbt_charts.core.project import InMemoryBoard
    from dbt_charts.core.render.errors import RenderError

    wrapped = RenderError.from_code(ERR_INTERNAL, message="timeout")
    chart_errors = [wrapped.to_diagnostic()]
    render_result = RenderResult(
        output='{"id": "test", "title": "Two Charts", "items": []}',
        chart_errors=chart_errors,
    )

    project = local_project(tmp_path)
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "partial"
    assert len(result.chart_errors) == 1
    assert result.chart_errors[0].code == "ERR-INTERNAL"


def test_render_dashboard_chart_error_gets_range_from_compile_source_map(
    tmp_path, local_project: Callable[..., Project]
):
    """A render-time chart error's `.path` (stamped by ``_render_callout_block``,
    reusing the same "charts.<id>" vocabulary the compile-time source map
    indexes) must resolve a `.range` from the compile-time source map: the
    render stage never re-parses the authored text, it reuses the map compile
    already built via ``result.source_map``.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.compile.compiler import compile as compile_fn
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.render.render_result import RenderResult

    mock_registry = MagicMock(spec=AdapterRegistry)

    compile_result = compile_fn(_TWO_CHART_YAML, file="board.yaml")
    assert compile_result.success, compile_result.errors

    wrapped = RenderError.from_code(ERR_INTERNAL, message="timeout")
    diagnostic = wrapped.to_diagnostic()
    diagnostic.path = "charts.bad"
    render_result = RenderResult(
        output='{"id": "test", "title": "Two Charts", "items": []}',
        chart_errors=[diagnostic],
    )

    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            compile_result=compile_result,
            adapter_registry=mock_registry,
            format="json",
            project=local_project(tmp_path),
            result_cache=None,
        )

    assert result.status == "partial"
    assert len(result.chart_errors) == 1
    err = result.chart_errors[0]
    assert err.range is not None
    assert err.range.file == "board.yaml"
    assert err.range.start_line == 15


# ---------------------------------------------------------------------------
# BoardRenderResult.status tri-state — "ok" / "partial" / "failed"
#
# "partial" (chart_errors non-empty but output produced) is already pinned by
# test_render_dashboard_chart_errors_populated above — a board that renders
# with one broken chart reports status == 'partial', not just success=True
# with a chart_errors list a caller can forget to check.
# ---------------------------------------------------------------------------


def test_render_dashboard_status_ok_for_clean_render(
    tmp_path, local_project: Callable[..., Project]
):
    """A clean render (no chart_errors) reports status == 'ok'."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard
    from dbt_charts.core.render.render_result import RenderResult

    mock_registry = MagicMock(spec=AdapterRegistry)
    render_result = RenderResult(
        output='{"id": "test", "title": "Two Charts", "items": []}',
    )

    project = local_project(tmp_path)
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(_TWO_CHART_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "ok"
    assert not result.chart_errors


def test_render_dashboard_status_failed_when_compile_fails(
    tmp_path, local_project: Callable[..., Project]
):
    """A board that fails to compile reports status == 'failed', never
    'partial' or 'ok' — no output was produced."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard

    mock_registry = MagicMock(spec=AdapterRegistry)
    project = local_project(tmp_path)

    result = render_dashboard(
        board=InMemoryBoard("not: [valid, yaml: :", path=project.path("charts/_t.yml")),
        adapter_registry=mock_registry,
        format="json",
        project=project,
        result_cache=None,
    )

    assert result.status == "failed"
    assert result.data is None


def test_render_dashboard_board_error_preserves_walked_data_payload(
    tmp_path, local_project: Callable[..., Project]
):
    """A board-level draw failure whose data-format walk still produced a
    payload must reach the caller as ``data`` — not just ``board_error``.

    render() already keeps RenderResult.output in this case (see
    test_board_level_draw_failure_still_returns_data_format_payload); before
    this test, render_dashboard's board_error branch dropped it on the floor.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard
    from dbt_charts.core.render.render_result import RenderResult

    mock_registry = MagicMock(spec=AdapterRegistry)
    wrapped = RenderError.from_code(ERR_INTERNAL, message="draw exploded")
    render_result = RenderResult(
        output='{"id": "test", "title": "Two Charts", "items": []}',
        board_error=wrapped.to_diagnostic(),
    )

    project = local_project(tmp_path)
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(_TWO_CHART_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "failed"
    assert result.board_error is not None
    assert result.data == {"id": "test", "title": "Two Charts", "items": []}


# ---------------------------------------------------------------------------
# build_resolved_board exception boundary — render() must catch, not escape
# ---------------------------------------------------------------------------


def test_build_resolved_board_chart_data_error_degrades_to_board_error() -> None:
    """ChartDataError raised from build_resolved_board must degrade to board_error, not escape render()."""
    board = _compile_two_chart_board()
    mock_executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_executor._query_errors = {}

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            side_effect=ChartDataError.from_code(
                ERR_INTERNAL, message="simulated data error"
            ),
        ),
    ):
        result = render(board, mock_executor, format="json")

    assert isinstance(result, RenderResult)
    assert result.board_error is not None


def test_build_resolved_board_unexpected_exception_degrades_to_board_error() -> None:
    """Bare RuntimeError from build_resolved_board degrades to board_error wrapping ERR_INTERNAL."""
    board = _compile_two_chart_board()
    mock_executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_executor._query_errors = {}

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            side_effect=RuntimeError("boom"),
        ),
    ):
        result = render(board, mock_executor, format="json")

    assert isinstance(result, RenderResult)
    assert result.board_error is not None
    assert result.board_error.code == ERR_INTERNAL.code


def test_render_dashboard_survives_build_resolved_board_chart_data_error(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    """render_dashboard() returns BoardRenderResult(status='failed') when build_resolved_board raises ChartDataError.

    Note: this also passes on base because board.py's outer except already catches ChartDataError
    (added by commit 56aeb81334). Tests 1 and 2 above are the regression gate for this fix; this test
    pins the public-API envelope contract.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard

    mock_registry = MagicMock(spec=AdapterRegistry)
    project = local_project(tmp_path)
    mock_executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock_executor._query_errors = {}

    with (
        patch("dbt_charts.core.board.Executor", return_value=mock_executor),
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            side_effect=ChartDataError.from_code(
                ERR_INTERNAL, message="simulated sizing error"
            ),
        ),
    ):
        result = render_dashboard(
            board=InMemoryBoard(_TWO_CHART_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "failed"
    assert result.board_error is not None


# ---------------------------------------------------------------------------
# Resolve-time chart failures isolate to their own tile
#
# The sizing pass resolves every chart before any SVG is written. Until
# `_require_resolved` learned to survive a DbtChartsError, a resolve-stage
# failure in ONE chart produced no output at all — every healthy chart on the
# board disappeared with it. These pin the isolation at each of the shapes
# that used to escape: a real ChartDataError (pie/NULL-theta), a
# ChartDataError raised at a previously-unguarded provider site (table), and
# a bare DbtChartsError that no per-site `except` tuple named.
# ---------------------------------------------------------------------------

_HEALTHY_PLUS_PIE_YAML = """
title: Healthy Plus Broken Pie
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
  q_broken:
    sql: "SELECT 1"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
  broken:
    type: pie
    query: q_broken
    theta: amount
    color: label
    notes: The broken one
cols:
  - good
  - broken
"""

_HEALTHY_PLUS_TABLE_YAML = """
title: Healthy Plus Broken Table
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
  q_broken:
    sql: "SELECT 1"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
  broken:
    type: table
    query: q_broken
cols:
  - good
  - broken
"""

_HEALTHY_PLUS_BAR_YAML = """
title: Healthy Plus Broken Bar
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
  q_broken:
    sql: "SELECT 1"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
  broken:
    type: bar
    query: q_broken
    x: label
    y: amount
cols:
  - good
  - broken
"""

_NESTED_PIE_YAML = """
title: Nested Broken Pie
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
  q_broken:
    sql: "SELECT 1"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
  broken:
    type: pie
    query: q_broken
    theta: amount
    color: label
rows:
  - good
  - cols:
      - broken
"""

_NULL_THETA_ROWS = [{"label": "a", "amount": None}, {"label": "b", "amount": 3}]


def _compile_board(yaml_text: str):
    result = compile(yaml_text)
    assert result.success, result.errors
    assert result.board is not None
    return result.board


def _resolve_stage_executor(rows_by_query: dict[str, list[dict[str, object]]]):
    """Executor double whose query results drive the real resolve path.

    Unlike ``_executor_good_bad``, nothing here raises: the failure must come
    out of chart *resolution*, which is what the sizing pass runs.
    """
    mock = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    mock.execute_query.side_effect = lambda name, variables=None, **_kw: rows_by_query[
        name
    ]
    mock.execute_chart.side_effect = lambda chart, variables: rows_by_query[
        chart.query_name
    ]
    # Route every query through the (patched-out) parallel pre-pass rather than
    # the synchronous cache-hit branch, so a failing query surfaces where it
    # does in production: inside the sizing pass and the render walk, not in
    # render()'s unguarded pre-execution loop.
    mock.is_cached.return_value = False
    mock._query_errors = {}
    mock.query_data_ages = {}
    mock.cache_hit_ats = []
    mock.rendered_at = PINNED_RENDER_TIME
    return mock


def _render_real_resolve(board, executor, format: str = "svg") -> RenderResult:
    """Render through the REAL build_resolved_board — no patched board resolver."""
    with patch.object(_renderer_mod, "execute_queries_parallel"):
        return render(board, executor, format=format)


def _raise_at_resolve(exc: BaseException):
    """Patch the sizing pass's resolve call to raise for the 'broken' chart only."""

    def _side_effect(chart, *_args, **_kwargs):
        if chart.id == "broken":
            raise exc
        return _REAL_RESOLVE(chart, *_args, **_kwargs)

    return patch.object(
        _layout_sizing_mod,
        "resolve_chart_with_runtime_inputs",
        side_effect=_side_effect,
    )


def test_resolve_time_pie_null_theta_isolates_to_its_tile() -> None:
    """A pie with a NULL theta kills its own tile, not the board.

    ``board_error is None`` is the direct proof that renderer.py's
    ``except DbtChartsError`` around ``build_resolved_board`` stopped firing for
    a chart-scoped failure — the handler that produced today's empty output.
    """
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )

    result = _render_real_resolve(board, executor)

    assert result.output is not None
    assert result.board_error is None
    assert [d.code for d in result.chart_errors] == ["ERR-PIE-NULL-THETA"]
    assert result.chart_errors[0].fields["chart_id"] == "broken"
    # The healthy chart survived, and the broken one is visibly an error tile.
    assert 'data-chart-id="good"' in result.output
    assert 'data-chart-id="broken"' in result.output
    assert "ERR-PIE-NULL-THETA" in result.output


def test_resolve_time_table_chart_data_error_isolates() -> None:
    """A table's resolve-time ChartDataError isolates.

    The table branch of the sizing height provider had no try/except at all,
    so this shape used to escape before any other guard could see it.
    """
    board = _compile_board(_HEALTHY_PLUS_TABLE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": [{"label": "a"}]}
    )

    with _raise_at_resolve(
        ChartDataError.from_code(
            ERR_INTERNAL, message="simulated table resolve failure"
        )
    ):
        result = _render_real_resolve(board, executor)

    assert result.output is not None
    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert result.chart_errors[0].fields["chart_id"] == "broken"
    assert 'data-chart-id="good"' in result.output


def test_resolve_time_bare_dbt_charts_error_isolates() -> None:
    """A bare DbtChartsError — neither ExecutionError nor ChartDataError — isolates.

    Every pre-fix call-site guard named a tuple of subclasses, and they had
    already drifted apart from each other. The catch now lives once inside
    ``_require_resolved`` and names the shared base, so a family no site
    happened to list can no longer escape.
    """
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": [{"label": "a", "amount": 3}]}
    )

    with _raise_at_resolve(
        DbtChartsError.from_code(ERR_INTERNAL, message="simulated bare failure")
    ):
        result = _render_real_resolve(board, executor)

    assert result.output is not None
    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert result.chart_errors[0].fields["chart_id"] == "broken"
    assert 'data-chart-id="good"' in result.output


def test_resolve_time_failure_in_nested_board_isolates() -> None:
    """A chart failing inside a nested board isolates to that tile only.

    Nested boards get their own SizingRenderCtx; a context that failed to
    forward the failure map would silently drop the record and crash the
    checkpoint instead.
    """
    board = _compile_board(_NESTED_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )

    result = _render_real_resolve(board, executor)

    assert result.output is not None
    assert result.board_error is None
    assert [d.code for d in result.chart_errors] == ["ERR-PIE-NULL-THETA"]
    assert 'data-chart-id="good"' in result.output
    assert "ERR-PIE-NULL-THETA" in result.output


def test_resolve_failures_do_not_leak_between_renders() -> None:
    """Two boards sharing a chart id in one process stay independent.

    Chart ids come from tree position, so two unrelated boards collide
    routinely. A failure map shared across calls (a mutable parameter
    default) would paint board B's healthy chart as an error tile carrying
    board A's diagnostic — a wrong result that looks right.
    """
    broken_board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    broken_result = _render_real_resolve(
        broken_board,
        _resolve_stage_executor(
            {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
        ),
    )
    assert len(broken_result.chart_errors) == 1

    healthy_board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    healthy_result = _render_real_resolve(
        healthy_board,
        _resolve_stage_executor(
            {"q_good": [{"value": 42}], "q_broken": [{"label": "a", "amount": 3}]}
        ),
    )

    assert healthy_result.board_error is None
    assert healthy_result.chart_errors == []
    assert healthy_result.output is not None
    assert "ERR-PIE-NULL-THETA" not in healthy_result.output


def test_resolve_time_error_tile_carries_full_chart_identity() -> None:
    """The resolve-time error tile's wrapper attributes match the render-time one.

    Both tiles are the same chart failing the same way at two different
    stages, so the host-facing DOM contract — the variable-dependency
    attributes that drive hover-highlight and loading state, the
    notes — must not depend on which stage failed.
    """
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)

    resolve_time_executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )
    resolve_time = _render_real_resolve(board, resolve_time_executor)

    render_time_board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    render_time_executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": [{"label": "a", "amount": 3}]}
    )
    with patch.object(
        _rendering_mod,
        "_render_chart_item_inner",
        side_effect=ChartDataError.from_code(ERR_INTERNAL, message="render stage"),
    ):
        render_time = _render_real_resolve(render_time_board, render_time_executor)

    assert resolve_time.output is not None
    assert render_time.output is not None
    assert _tile_attrs(resolve_time.output, "broken") == _tile_attrs(
        render_time.output, "broken"
    )


def _tile_attrs(svg: str, chart_id: str) -> set[str]:
    """The chart wrapper <g>'s attributes, minus the geometry it is sized by."""
    match = re.search(rf'<g ([^>]*data-chart-id="{chart_id}"[^>]*)>', svg)
    assert match, f"no chart wrapper for {chart_id!r} in output"
    return {
        attr
        for attr in re.findall(r'[\w-]+="[^"]*"', match.group(1))
        if not attr.startswith(("data-chart-width", "data-chart-height"))
    }


def test_resolve_time_diagnostic_matches_data_format_stamp_shape() -> None:
    """The new stamping site publishes the same payload the data walk does.

    ``diagnostic.fields`` is dumped verbatim into the ``_error`` marker that
    agents and CI read, so the resolve-time path must not quietly add keys
    the data-format path never had.
    """
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )

    result = _render_real_resolve(board, executor)

    diagnostic = result.chart_errors[0]
    assert diagnostic.fields["chart_id"] == "broken"
    assert "source_path" not in diagnostic.fields
    assert diagnostic.path == "charts.broken"


def test_data_format_walk_isolates_resolve_failure_unchanged() -> None:
    """``--format data`` resolves independently and already isolated — still does."""
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )

    result = _render_real_resolve(board, executor, format="json")

    assert result.output is not None
    assert result.board_error is None
    assert "ERR-PIE-NULL-THETA" in result.output


def test_warning_detector_tolerates_chart_missing_from_resolved_board() -> None:
    """A real detector runs clean when a failed chart has no entry in ``charts``.

    Detectors iterate ``board_spec.charts``; a failed chart is simply absent
    from it. This pins that absence — not a synthesized placeholder entry — as
    the shape they see, using the one detector that would have fired on this
    chart had it resolved.
    """
    board = _compile_board(_HEALTHY_PLUS_PIE_YAML)
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 42}], "q_broken": _NULL_THETA_ROWS}
    )

    resolved, _cache = build_resolved_board(board, executor, {})

    assert "broken" not in resolved.charts
    assert "good" in resolved.charts
    context = WarningContext(
        board_spec=resolved,
        chart_results={
            "broken": [{"label": "a", "amount": 999}, {"label": "b", "amount": 1}]
        },
        vega_specs={},
    )
    assert detect_pie_dominant_segment(context) == []


def test_query_execution_failure_still_isolates_through_the_real_pipeline() -> None:
    """A broken query isolates to its own tile — the board still renders and
    the healthy chart survives, regardless of which stage reports it."""
    board = _compile_board(_HEALTHY_PLUS_BAR_YAML)
    executor = _resolve_stage_executor({"q_good": [{"value": 42}]})

    def _execute_query(name, variables=None, **_kw):
        if name == "q_broken":
            raise QueryError("column 'nope' does not exist", "q_broken")
        return [{"value": 42}]

    executor.execute_query.side_effect = _execute_query
    executor.execute_chart.side_effect = lambda chart, variables: _execute_query(
        chart.query_name
    )

    result = _render_real_resolve(board, executor)

    assert result.output is not None
    assert result.board_error is None
    assert len(result.chart_errors) == 1
    assert 'data-chart-id="good"' in result.output


# ---------------------------------------------------------------------------
# A query failure must isolate to its own chart on every path that consults
# it: the resolve stage, the inactive-tab fallback, and board-wide
# category-color planning. Real Executor throughout.
# ---------------------------------------------------------------------------


def _write_duckdb_project(root: Path) -> None:
    """Scaffold a real on-disk project with an empty local DuckDB source."""
    import duckdb

    (root / "charts").mkdir(parents=True)
    (root / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: local.duckdb\n"
    )
    duckdb.connect(str(root / "local.duckdb")).close()


_TWO_QUERY_FAILURE_YAML = """
title: Two Queries, One Failing
queries:
  q_good:
    source: db
    sql: "SELECT 42 AS value"
  q_bad:
    source: db
    sql: "SELECT 1 AS value"
charts:
  good:
    type: kpi
    query: q_good
    value: value
  bad:
    type: bar
    query: q_bad
    x: value
    y: value
cols:
  - good
  - bad
"""

_TABS_QUERY_FAILURE_YAML = """
title: Inactive Tab Query Failure
queries:
  q_good:
    source: db
    sql: "SELECT 42 AS value"
  q_bad:
    source: db
    sql: "SELECT 1 AS value"
charts:
  active_chart:
    type: kpi
    query: q_good
    value: value
  hidden_chart:
    type: bar
    query: q_bad
    x: value
    y: value
tabs:
  id: t
  default: Active
  items:
    - title: Active
      rows: [active_chart]
    - title: Hidden
      rows: [hidden_chart]
"""


def _real_executor_with_failing_query(
    tmp_path: Path,
    make_error: Callable[[], Exception],
    yaml_text: str = _TWO_QUERY_FAILURE_YAML,
):
    """A real Executor and adapters, on disk — 'q_bad' fails via ``make_error()``.

    Patches ``resolve_query_references`` (the SQL-composition step inside
    ``execute_query``, Step 4) to raise for 'q_bad' and delegate to the real
    implementation for every other query. Returns ``(board, executor,
    patch_fn)`` — apply ``patch_fn`` via ``patch.object`` at each call site so
    every ``execute_query`` invocation, across threads, sees the same failure.
    """
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.execute import executor as _executor_mod
    from dbt_charts.core.execute.adapters import build_adapter_registry

    _write_duckdb_project(tmp_path)
    board_path = tmp_path / "charts" / "board.yml"
    board_path.write_text(yaml_text)

    project = FilesystemProject(tmp_path)
    result = compile(board_path.read_text(), base_dir=project.directory())
    assert result.success, result.errors
    assert result.board is not None

    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )

    real_resolve_refs = _executor_mod.resolve_query_references

    def _boom(query, *, all_queries, query_name=None):
        if query_name == "q_bad":
            raise make_error()
        return real_resolve_refs(query, all_queries=all_queries, query_name=query_name)

    return result.board, executor, _boom


def test_query_execution_failure_is_recorded_at_resolve_not_swallowed(
    tmp_path: Path,
) -> None:
    """A stored query failure surfaces as a chart resolve failure, not an
    empty-data resolve.

    Runs the real parallel pre-pass so ``executor._query_errors['q_bad']``
    holds the failure (both queries are cache misses, submitted together via
    ``execute_queries_parallel``, as production does), then calls
    ``build_resolved_board`` directly — the same call
    ``agent_api.board_artifact.emit_board_artifact`` makes, with no render
    walk downstream to redundantly re-execute the query.
    """
    from dbt_charts.core.compile.models.board.resolved import ChartResolveFailure
    from dbt_charts.core.execute import executor as _executor_mod
    from dbt_charts.core.execute.collect import collect_all_query_names
    from dbt_charts.core.execute.parallel import execute_queries_parallel

    board, executor, _boom = _real_executor_with_failing_query(
        tmp_path, lambda: JinjaError("simulated bad template", "{{ boom }}")
    )

    query_names = collect_all_query_names(board)
    with patch.object(_executor_mod, "resolve_query_references", side_effect=_boom):
        execute_queries_parallel(executor, query_names, {}, 8)

    assert "q_bad" in executor._query_errors
    assert isinstance(executor._query_errors["q_bad"], JinjaError)

    resolve_errors: dict[str, ChartResolveFailure] = {}
    with patch.object(_executor_mod, "resolve_query_references", side_effect=_boom):
        resolved, _cache = build_resolved_board(
            board, executor, {}, resolve_errors=resolve_errors
        )

    assert "bad" in resolve_errors
    assert resolve_errors["bad"].diagnostic.code == "ERR-JINJA-ERROR"
    assert "bad" not in resolved.charts
    assert "good" in resolved.charts


def test_inactive_tab_query_failure_is_recorded_not_swallowed(tmp_path: Path) -> None:
    """The same guarantee on the inactive-tab fallback path.

    The sizing pass only walks the active tab, so an inactive tab's chart
    resolves through ``_resolve_chart_data_aware`` instead of
    ``_require_resolved`` — a separate function with its own catch.
    """
    from dbt_charts.core.compile.models.board.resolved import ChartResolveFailure
    from dbt_charts.core.execute import executor as _executor_mod

    board, executor, _boom = _real_executor_with_failing_query(
        tmp_path,
        lambda: QueryError("simulated query failure", "q_bad"),
        yaml_text=_TABS_QUERY_FAILURE_YAML,
    )

    resolve_errors: dict[str, ChartResolveFailure] = {}
    with patch.object(_executor_mod, "resolve_query_references", side_effect=_boom):
        resolved, _cache = build_resolved_board(
            board, executor, {}, resolve_errors=resolve_errors
        )

    assert "hidden_chart" in resolve_errors
    assert "hidden_chart" not in resolved.charts
    assert "active_chart" in resolved.charts


def test_category_color_planning_isolates_a_fresh_non_execution_error(
    tmp_path: Path,
) -> None:
    """``plan_board_category_colors`` runs first, before any query has been
    attempted — the cold path ``emit_board_artifact`` takes, with no parallel
    pre-pass. A non-ExecutionError DbtChartsError raised there must isolate
    to its own chart rather than abort the whole board resolution.
    """
    from dbt_charts.core.compile.models.board.resolved import ChartResolveFailure
    from dbt_charts.core.execute import executor as _executor_mod

    board, executor, _boom = _real_executor_with_failing_query(
        tmp_path, lambda: JinjaError("simulated bad template", "{{ boom }}")
    )

    resolve_errors: dict[str, ChartResolveFailure] = {}
    with patch.object(_executor_mod, "resolve_query_references", side_effect=_boom):
        resolved, _cache = build_resolved_board(
            board, executor, {}, resolve_errors=resolve_errors
        )

    assert "bad" in resolve_errors
    assert resolve_errors["bad"].diagnostic.fields["chart_id"] == "bad"
    assert "good" in resolved.charts


def test_emit_board_artifact_reports_failure_for_a_query_error(
    tmp_path: Path,
) -> None:
    """The agent-facing artifact verb — no render walk at all — must isolate
    a broken chart to a clean ``success=False``, not crash or silently
    publish an artifact with an empty-data chart.
    """
    from dbt_charts.agent_api.board_artifact import emit_board_artifact
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.execute import executor as _executor_mod
    from dbt_charts.core.execute.adapters import build_adapter_registry

    def _make_error() -> QueryError:
        err = QueryError("simulated query failure", "q_bad")
        err.code = ERR_FILE_NOT_FOUND
        return err

    _board, _executor, _boom = _real_executor_with_failing_query(tmp_path, _make_error)
    project = FilesystemProject(tmp_path)

    with patch.object(_executor_mod, "resolve_query_references", side_effect=_boom):
        result = emit_board_artifact(
            tmp_path / "charts" / "board.yml",
            tmp_path / "out" / "board.artifact.json",
            tmp_path / "out" / "board.recording.json",
            project=project,
            adapter_registry=build_adapter_registry(project),
        )

    assert result.success is False
    assert len(result.errors) == 1
    assert result.errors[0].code == ERR_FILE_NOT_FOUND.code
    assert result.errors[0].fields.get("chart_id") == "bad"


# ---------------------------------------------------------------------------
# Still fatal: failures that never entered the sizing pass
# ---------------------------------------------------------------------------


_REQUIRED_VARIABLE_YAML = """
title: Needs A Variable
variables:
  region:
    required: true
queries:
  q_good:
    sql: "SELECT 1 AS value"
    source: test
charts:
  good:
    type: kpi
    query: q_good
    value: value
rows:
  - good
"""


def test_missing_required_variables_stays_board_fatal(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    """A required variable with no value stays a board-level fatal.

    It is raised before ``build_resolved_board`` is ever called, so no amount of
    per-chart isolation inside the sizing pass should soften it into a tile.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    executor = _resolve_stage_executor({"q_good": [{"value": 42}]})

    with (
        patch("dbt_charts.core.board.Executor", return_value=executor),
        patch.object(_renderer_mod, "execute_queries_parallel"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(
                _REQUIRED_VARIABLE_YAML, path=project.path("charts/_t.yml")
            ),
            adapter_registry=MagicMock(spec=AdapterRegistry),
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "failed"
    assert result.board_error is not None
    assert result.chart_errors == []


def test_executor_construction_failure_stays_board_fatal(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    """An executor that cannot be built is fatal — there is nothing to isolate."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)

    with patch(
        "dbt_charts.core.board.Executor",
        side_effect=ExecutionError("no warehouse"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(
                _HEALTHY_PLUS_PIE_YAML, path=project.path("charts/_t.yml")
            ),
            adapter_registry=MagicMock(spec=AdapterRegistry),
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "failed"
    assert result.board_error is not None
    assert result.chart_errors == []


def test_board_level_draw_failure_still_returns_data_format_payload() -> None:
    """A board-level draw failure (render_board_svg raising) must not destroy
    the data-format walk's own payload — board_error still surfaces, but the
    walk runs independently of the draw and its payload survives.
    """
    board = _compile_two_chart_board()
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 1}], "q_bad": [{"value": 2}]}
    )

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
        patch.object(
            _renderer_mod,
            "render_board_svg",
            side_effect=RenderError.from_code(ERR_INTERNAL, message="draw exploded"),
        ),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is not None
    assert result.output is not None
    assert "good" in result.output


def test_data_format_walk_exception_becomes_board_error_not_raise() -> None:
    """An exception escaping the data-format walk itself (e.g. a payload
    holding a value its serializer can't handle) must degrade to
    board_error=ERR-INTERNAL rather than propagate out of render() uncaught.
    """
    board = _compile_two_chart_board()
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 1}], "q_bad": [{"value": 2}]}
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise TypeError("Object of type X is not JSON serializable")

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
        patch.object(_renderer_mod, "_data_format_renderer", return_value=_boom),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is not None
    assert result.board_error.code == ERR_INTERNAL.code
    assert result.output is None


def test_draw_board_error_takes_priority_over_walk_board_error() -> None:
    """When both the draw and the data-format walk fail, the draw's
    board_error wins — it is the more fundamental failure (renderer.py's
    ``if board_error is None:`` guard around the walk's except clauses).
    """
    board = _compile_two_chart_board()
    executor = _resolve_stage_executor(
        {"q_good": [{"value": 1}], "q_bad": [{"value": 2}]}
    )

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise TypeError("walk exploded")

    with (
        patch.object(_renderer_mod, "execute_queries_parallel"),
        patch.object(
            _board_resolve_mod,
            "build_resolved_board",
            return_value=(build_resolved_board_static(board), {}),
        ),
        patch.object(
            _renderer_mod,
            "render_board_svg",
            side_effect=RenderError.from_code(ERR_INTERNAL, message="draw exploded"),
        ),
        patch.object(_renderer_mod, "_data_format_renderer", return_value=_boom),
    ):
        result = render(board, executor, format="json")

    assert result.board_error is not None
    assert result.board_error.message == "draw exploded"


# ---------------------------------------------------------------------------
# CLI contract — a resolve-time failure rides the existing exit-code path
# ---------------------------------------------------------------------------


def _write_resolve_error_project(root: Path) -> Path:
    """A real on-disk project whose pie chart has a NULL in its theta column."""
    _write_duckdb_project(root)
    board_path = root / "charts" / "board.yml"
    board_path.write_text(
        """
title: Resolve Error Board
queries:
  q_good:
    source: db
    sql: "SELECT 42 AS value"
  q_broken:
    source: db
    sql: "SELECT 'a' AS label, NULL AS amount UNION ALL SELECT 'b', 3"
charts:
  good:
    type: kpi
    query: q_good
    value: value
  broken:
    type: pie
    query: q_broken
    theta: amount
    color: label
cols:
  - good
  - broken
"""
    )
    return board_path


def test_cli_render_resolve_error_reports_it_as_a_chart_error(tmp_path: Path) -> None:
    """The exit code for this board is 1, and stays 1.

    Deliberately not a gate on the fix — a board-level fatal exits 1 and prints
    the same code, so this passes before and after. It pins the half of the CLI
    contract that must *not* move: making the failure per-chart did not quietly
    turn a broken board green in CI. Its sibling
    ``test_cli_render_resolve_error_allow_chart_errors_writes_the_board`` is the
    test that fails without the fix.
    """
    from typer.testing import CliRunner

    from dbt_charts.cli.main import app

    board_path = _write_resolve_error_project(tmp_path)
    out = tmp_path / "board.svg"

    result = CliRunner().invoke(
        app,
        [
            "render",
            str(board_path),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(out),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "ERR-PIE-NULL-THETA" in result.output


@pytest.mark.parametrize("format", ["yaml", "json", "text", "data"])
def test_cli_render_resolve_error_names_the_chart_on_every_data_format(
    format: str, tmp_path: Path
) -> None:
    """Isolation is a property of the resolve checkpoint, not of the SVG writer.

    Every data format walks the tree itself, so each one has to tolerate an
    item that carries a diagnostic instead of a chart.
    """
    from typer.testing import CliRunner

    from dbt_charts.cli.main import app

    board_path = _write_resolve_error_project(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "render",
            str(board_path),
            "--project-dir",
            str(tmp_path),
            "--format",
            format,
            "--allow-chart-errors",
        ],
    )

    assert "ERR-INTERNAL" not in result.output, result.output
    assert "ERR-PIE-NULL-THETA" in result.output, result.output


def test_cli_render_resolve_error_allow_chart_errors_writes_the_board(
    tmp_path: Path,
) -> None:
    """``--allow-chart-errors`` covers resolve-time failures, not just render-time.

    This is the end of the bug: the healthy charts reach the file instead of
    being thrown away with the broken one.
    """
    from typer.testing import CliRunner

    from dbt_charts.cli.main import app

    board_path = _write_resolve_error_project(tmp_path)
    out = tmp_path / "board.svg"

    result = CliRunner().invoke(
        app,
        [
            "render",
            str(board_path),
            "--project-dir",
            str(tmp_path),
            "--output",
            str(out),
            "--allow-chart-errors",
        ],
    )

    assert result.exit_code == 0, result.output
    written = out.read_text()
    assert 'data-chart-id="good"' in written
    assert "ERR-PIE-NULL-THETA" in written


# ---------------------------------------------------------------------------
# Structural lint warnings wired into render_dashboard (Plan Step 3)
# ---------------------------------------------------------------------------

_CARTESIAN_JOIN_YAML = """\
title: Cartesian Board
queries:
  revenue:
    sql: |
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
    source: analytics
charts:
  trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
rows:
  - trend
"""

_CARTESIAN_JOIN_IGNORED_YAML = """\
title: Cartesian Board
queries:
  revenue:
    sql: |
      -- dct:ignore WARN-MISSING-JOIN-PREDICATE WARN-REAGGREGATION
      SELECT SUM(o.amount), SUM(oi.qty)
      FROM orders o, order_items oi
      GROUP BY o.id
    source: analytics
charts:
  trend:
    query: revenue
    type: bar
    x: o.id
    y: revenue
rows:
  - trend
"""


def test_render_dashboard_emits_structural_lint_warning(
    tmp_path, local_project: Callable[..., Project]
):
    """render_dashboard emits WARN-MISSING-JOIN-PREDICATE from compile-time lint.

    The warning must be stamped (range is not None) because stamp_diagnostics
    runs on result.warnings inside validate_compiled_queries.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard
    from dbt_charts.core.render.render_result import RenderResult

    mock_registry = MagicMock(spec=AdapterRegistry)
    render_result = RenderResult(
        output='{"id": "test", "title": "Cartesian Board", "items": []}'
    )

    project = local_project(tmp_path)
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(
                _CARTESIAN_JOIN_YAML, path=project.path("charts/cartesian.yml")
            ),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert result.status == "ok", result.board_error
    lint_warnings = [
        w for w in result.warnings if w.code == "WARN-MISSING-JOIN-PREDICATE"
    ]
    assert len(lint_warnings) == 1, (
        f"expected exactly one lint warning (not doubled); got {[w.code for w in result.warnings]}"
    )
    assert lint_warnings[0].range is not None, (
        "lint warning must carry a stamped range (stamp_diagnostics must have run)"
    )


def test_render_dashboard_dct_ignore_moves_lint_to_suppressed(
    tmp_path, local_project: Callable[..., Project]
):
    """A ``-- dct:ignore`` comment suppresses the lint warning into suppressed_warnings."""
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.execute.adapters import AdapterRegistry
    from dbt_charts.core.project import InMemoryBoard
    from dbt_charts.core.render.render_result import RenderResult

    mock_registry = MagicMock(spec=AdapterRegistry)
    render_result = RenderResult(
        output='{"id": "test", "title": "Cartesian Board", "items": []}'
    )

    project = local_project(tmp_path)
    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.Executor"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(
                _CARTESIAN_JOIN_IGNORED_YAML, path=project.path("charts/cartesian.yml")
            ),
            adapter_registry=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    active_codes = [w.code for w in result.warnings]
    suppressed_codes = [w.code for w in result.suppressed_warnings]
    assert "WARN-MISSING-JOIN-PREDICATE" not in active_codes, (
        f"ignored warning must not appear in active warnings: {active_codes}"
    )
    assert "WARN-MISSING-JOIN-PREDICATE" in suppressed_codes, (
        f"ignored warning must appear in suppressed_warnings: {suppressed_codes}"
    )
    suppressed_lint = [
        w for w in result.suppressed_warnings if w.code == "WARN-MISSING-JOIN-PREDICATE"
    ]
    assert suppressed_lint[0].range is not None, (
        "suppressed lint warning must carry a stamped range"
    )


def test_render_dashboard_as_link_emits_structural_lint_warning(
    tmp_path, local_project: Callable[..., Project]
):
    """as_link=True path must run validate_compiled_queries like the full render path.

    An agent calling render_board(as_link=True) and render_board() on the same
    board must get the same warning set — both paths must run exactly one lint
    pass per compile result.
    """
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(
            _CARTESIAN_JOIN_YAML, path=project.path("charts/cartesian.yml")
        ),
        as_link=True,
        project=project,
        result_cache=None,
    )

    assert result.status == "ok", result.board_error
    lint_warnings = [
        w for w in result.warnings if w.code == "WARN-MISSING-JOIN-PREDICATE"
    ]
    assert len(lint_warnings) == 1, (
        f"as_link=True path must emit exactly one lint warning; got {[w.code for w in result.warnings]}"
    )


# ---------------------------------------------------------------------------
# spark_bar render-time validation must not escape the sizing pass
# ---------------------------------------------------------------------------

_REVERSED_SPARK_BAR_YAML = """
title: Reversed spark_bar
queries:
  q:
    type: values
    rows:
      - {cat: "Electronics", val: 100}
      - {cat: "Apparel", val: 50}
charts:
  good:
    query: q
    type: bar
    x: cat
    y: val
  bad:
    query: q
    type: spark_bar
    x: cat
    y: val
rows:
  - cols: [good, bad]
"""


def test_reversed_spark_bar_is_isolated_to_its_own_card(
    local_project: Callable[..., Project],
):
    """A spark_bar authored with the cartesian x/y order must degrade to one
    error card, not kill the board.

    spark_bar validates its data at RENDER time, and the render-first sizing
    pass (layout_sizing._chart_height_provider) calls the renderer with no
    per-chart isolation of its own. Before this guard the ChartDataError
    escaped to renderer.py's board-level handler, which returns
    output=None — a single reversed spark_bar rendered *nothing*, not even
    the other charts. Deliberately a whole-board render: every other
    spark_bar test calls render_spark_bar_svg directly and so cannot see
    this at all.
    """
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(_REVERSED_SPARK_BAR_YAML)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, (
        "one bad spark_bar must not become a board-level failure"
    )
    assert render_result.output is not None, "the board must still render"
    codes = [d.code for d in render_result.chart_errors]
    assert "ERR-SPARK-BAR-VALUE-NOT-NUMERIC" in codes, (
        f"expected a per-chart spark_bar diagnostic, got {codes}"
    )
    # Both tiles are laid out: the good chart really rendered, and the bad one
    # occupies its slot as an error card rather than vanishing.
    assert "chart-good" in render_result.output
    assert "chart-bad" in render_result.output


# ---------------------------------------------------------------------------
# spark_bar's numeric verdict must not depend on style.spark_bar.max_bars
# ---------------------------------------------------------------------------

_MAX_BARS_SCOPE_YAML = """
title: max_bars must not change the numeric verdict
queries:
  q:
    type: values
    rows:
      - {cat: "R0", val: null}
      - {cat: "R1", val: null}
      - {cat: "R2", val: null}
      - {cat: "R3", val: null}
      - {cat: "R4", val: null}
      - {cat: "R5", val: null}
      - {cat: "R6", val: null}
      - {cat: "R7", val: null}
      - {cat: "R8", val: null}
      - {cat: "R9", val: null}
      - {cat: "R10", val: 5}
      - {cat: "R11", val: 4}
      - {cat: "R12", val: 3}
      - {cat: "R13", val: 2}
      - {cat: "R14", val: 1}
charts:
  low_cap:
    query: q
    type: spark_bar
    x: val
    y: cat
    style:
      max_bars: 10
  high_cap:
    query: q
    type: spark_bar
    x: val
    y: cat
    style:
      max_bars: 15
rows:
  - cols: [low_cap, high_cap]
"""


def test_spark_bar_numeric_verdict_is_independent_of_max_bars(
    local_project: Callable[..., Project],
):
    """Same board, same query, two ``style.max_bars`` values — same verdict.

    The query's first 10 rows are NULL and its last 5 are real numbers.
    Before the fix, ``_validate_spark_bar_value_field`` scanned only
    ``data[:max_bars]``: at ``max_bars: 10`` (the theme default) the slice is
    all-NULL and the chart raised ERR-SPARK-BAR-VALUE-NOT-NUMERIC; at
    ``max_bars: 15`` the identical data rendered fine. A presentational cap
    decided whether the board errored. Both charts must now render.
    """
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(_MAX_BARS_SCOPE_YAML)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    render_result = render(result.board, executor, format="svg")

    codes = [d.code for d in render_result.chart_errors]
    assert codes == [], (
        "low_cap and high_cap read the same column from the same data; the "
        f"numeric verdict must not depend on style.max_bars. Got errors: {codes}"
    )
    assert "chart-low_cap" in render_result.output
    assert "chart-high_cap" in render_result.output


_NON_NUMERIC_BEYOND_CAP_YAML = """
title: non-numeric value past max_bars must still raise
queries:
  q:
    type: values
    rows:
      - {cat: "R0", val: 1}
      - {cat: "R1", val: 2}
      - {cat: "R2", val: 3}
      - {cat: "R3", val: 4}
      - {cat: "R4", val: 5}
      - {cat: "R5", val: "oops"}
      - {cat: "R6", val: 6}
      - {cat: "R7", val: 7}
charts:
  low_cap:
    query: q
    type: spark_bar
    x: val
    y: cat
    style:
      max_bars: 5
  high_cap:
    query: q
    type: spark_bar
    x: val
    y: cat
    style:
      max_bars: 10
rows:
  - cols: [low_cap, high_cap]
"""


def test_spark_bar_non_numeric_value_beyond_max_bars_still_raises(
    local_project: Callable[..., Project],
):
    """A bad value past the display cap is still a real data-shape bug.

    ``low_cap`` (max_bars: 5) only "sees" the first 5 rows, all clean
    numbers; ``high_cap`` (max_bars: 10) sees row 5's stray string too.
    Before the fix low_cap silently rendered a chart whose underlying column
    is not actually clean, while high_cap raised on the same data — the
    verdict must be the same regardless of the cap, so both must raise once
    the check reads the whole dataset. Deliberately a whole-board render:
    each bad chart must degrade to its own error card, not take the board
    down (the per-chart isolation this task's Context section calls out).
    """
    from dbt_charts.core.execute.adapters import build_adapter_registry

    result = compile(_NON_NUMERIC_BEYOND_CAP_YAML)
    assert result.success, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )

    render_result = render(result.board, executor, format="svg")

    assert render_result.board_error is None, (
        "bad spark_bar charts must not become a board-level failure"
    )
    assert render_result.output is not None, "the board must still render"
    chart_error_codes = {
        d.fields.get("chart_id"): d.code for d in render_result.chart_errors
    }
    assert chart_error_codes.get("low_cap") == "ERR-SPARK-BAR-VALUE-NOT-NUMERIC", (
        f"expected low_cap to raise too once the check reads the full "
        f"dataset, got {chart_error_codes}"
    )
    assert chart_error_codes.get("high_cap") == "ERR-SPARK-BAR-VALUE-NOT-NUMERIC"
    # Both tiles are laid out as error cards, not a vanished board.
    assert "chart-low_cap" in render_result.output
    assert "chart-high_cap" in render_result.output
