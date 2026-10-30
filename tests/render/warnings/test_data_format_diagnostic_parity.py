"""Regression: `--format data|json|text|yaml` must report the same diagnostics
as `--format svg` — every format renders the same board once, then chooses
what to emit, so the measurement-warning family and chart-error detection
must not diverge by format.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.diagnostics import ERR_CHART_PAINTED_NO_MARKS
from dbt_charts.core.diagnostics.codes_render import WARN_TABLE_CRAMPED
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

_DATA_FORMATS = ("json", "text", "yaml", "data")

# Ten realistic columns need more width than a narrow table gives —
# reliably fires WARN-TABLE-CRAMPED on svg (see test_table_cramping_capture.py,
# whose fixture this mirrors).
_WIDE_COLUMNS: list[tuple[str, str]] = [
    ("region", "EMEA"),
    ("product_line", "Ingestion Cloud"),
    ("customer_segment", "Mid-Market"),
    ("order_channel", "Partner Reseller"),
    ("fulfillment_status", "Partially Shipped"),
    ("net_revenue", "2,013,880"),
    ("gross_margin", "51.2%"),
    ("units_shipped", "24,553"),
    ("return_rate", "2.1%"),
    ("last_order_date", "2025-11-04"),
]


def _cramped_table_board() -> str:
    rows = [dict(_WIDE_COLUMNS) for _ in range(4)]
    body = "\n".join(
        "      - {" + ", ".join(f"{k}: '{v}'" for k, v in row.items()) + "}"
        for row in rows
    )
    return (
        "title: T\n"
        "queries:\n  q:\n    type: values\n    rows:\n" + body + "\n"
        # `fit`: cramping is the squeeze a slot imposes; `scroll` has none.
        "charts:\n  t: {query: q, type: table, style: {preferred_width: 800, "
        "column_layout: {overflow: fit}}}\n"
        "rows:\n  - t\n"
    )


def _bar_chart_board() -> str:
    return (
        "title: T\n"
        "queries:\n"
        "  q:\n"
        "    type: values\n"
        "    rows:\n"
        "      - {cat: 'A', val: 10}\n"
        "      - {cat: 'B', val: 20}\n"
        "charts:\n"
        "  b: {query: q, type: bar, x: cat, y: val}\n"
        "rows:\n"
        "  - b\n"
    )


def _build_executor(
    board: Board,
    query_registry: dict[str, AnyQuery],
    project: FilesystemProject,
) -> Executor:
    return Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry=query_registry,
    )


def test_measurement_warnings_have_parity_across_formats(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """svg and every data format must agree on the *count* of each warning
    code — not just the set of codes present (WARN-CHART-TITLE-TRUNCATED was
    partially suppressed: present on both sides, but fewer instances on data).
    """
    result = compile_board(_cramped_table_board())
    assert result.success, result.diagnostics
    board = result.board
    assert board is not None
    project = local_project(tmp_path)

    def _codes(format: str) -> Counter[str]:
        executor = _build_executor(board, result.query_registry, project)
        output = render(board, executor, format=format)
        return Counter(w.code for w in output.warnings)

    svg_codes = _codes("svg")
    # `assert svg_codes` alone only proves *some* warning fired — data-family
    # warnings (zero rows, query truncation) already reached data formats
    # before render() drew every format, so that guard would pass even if the
    # svg-only measurement family this test targets stayed suppressed.
    assert WARN_TABLE_CRAMPED.code in svg_codes, (
        f"fixture must fire WARN-TABLE-CRAMPED on svg, got {svg_codes}"
    )

    for format in _DATA_FORMATS:
        data_codes = _codes(format)
        assert data_codes == svg_codes, (
            f"format={format!r} diagnostic counts diverge from svg: "
            f"{data_codes} != {svg_codes}"
        )


def test_painted_no_marks_error_reported_on_data_format_same_as_svg(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A chart that paints no marks despite nonzero data must error identically
    regardless of output format — the data path must not report success on a
    chart the svg path treats as a genuine rendering failure.
    """
    result = compile_board(_bar_chart_board())
    assert result.success, result.diagnostics
    board = result.board
    assert board is not None
    project = local_project(tmp_path)

    marks_free_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" '
        'viewBox="0 0 300 200"></svg>'
    )

    from dbt_charts.core.render.converters import chart as converters_chart

    def _render(format: str) -> RenderResult:
        executor = _build_executor(board, result.query_registry, project)
        with patch.object(
            converters_chart, "render_chart_artifact", return_value=marks_free_svg
        ):
            return render(board, executor, format=format)

    svg_result = _render("svg")
    svg_errors = {e.code for e in svg_result.chart_errors}
    assert ERR_CHART_PAINTED_NO_MARKS.code in svg_errors
    # svg's payload IS the drawing: the no-marks error is exactly what
    # invalidates it, so payload_errors must equal chart_errors here.
    assert svg_result.payload_errors == svg_result.chart_errors

    for format in _DATA_FORMATS:
        data_result = _render(format)
        data_errors = {e.code for e in data_result.chart_errors}
        assert ERR_CHART_PAINTED_NO_MARKS.code in data_errors, (
            f"format={format!r} did not report ERR-CHART-PAINTED-NO-MARKS"
        )
        # The bar chart's data-format walk succeeds (it never draws) — a
        # chart that painted no marks but walked fine does not invalidate a
        # data-bearing payload, so payload_errors must stay empty even though
        # the draw-only error is still reported in chart_errors above.
        assert data_result.payload_errors == [], (
            f"format={format!r} payload_errors should be empty for a "
            f"draw-only failure, got {data_result.payload_errors}"
        )


def test_render_first_is_unconditional_on_format(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """``render_first=True`` must reach ``build_resolved_board`` for every
    format, not just the svg family.

    The parity fixtures above are width-driven (WARN-TABLE-CRAMPED), but
    render-first sizing's whole effect is on measured *heights* (a real
    vl-convert render vs. the static aspect-ratio estimate — e.g. a
    pie/attached-table chart sized 384px in production and 300px on the
    static path, per laws-board.md) — reverting ``renderer.py``'s
    ``render_first=True`` back to a format-conditional expression would
    likely leave those width-driven fixtures green. Pinning the actual kwarg
    passed avoids inventing a pixel-threshold fixture, which would be a
    fragile numeric pin of its own (see dbt-charts/AGENTS.md's "don't pin
    theme/default values" rule) for a fact this test can state exactly.
    """
    from types import EllipsisType

    import dbt_charts.core.render.board_resolve as _board_resolve_mod
    from dbt_charts.core.compile.models.board.normalized import VariableValues
    from dbt_charts.core.compile.models.board.resolved import (
        ChartResolveFailure,
        ResolvedBoard,
    )
    from dbt_charts.core.render.layout_sizing import RenderCache

    result = compile_board(_bar_chart_board())
    assert result.success, result.diagnostics
    board = result.board
    assert board is not None
    project = local_project(tmp_path)

    real_build = _board_resolve_mod.build_resolved_board
    seen_render_first: dict[str, bool | None] = {}
    current_format = ""

    def _spy(
        board: Board,
        executor: Executor,
        variables: VariableValues,
        render_first: bool = True,
        *,
        resolve_errors: dict[str, ChartResolveFailure] | EllipsisType = ...,
        authored_slot_heights: dict[str, float] | EllipsisType = ...,
    ) -> tuple[ResolvedBoard, RenderCache]:
        seen_render_first[current_format] = render_first
        return real_build(
            board,
            executor,
            variables,
            render_first,
            resolve_errors=resolve_errors,
            authored_slot_heights=authored_slot_heights,
        )

    for current_format in ("svg", *_DATA_FORMATS):
        executor = _build_executor(board, result.query_registry, project)
        with patch.object(_board_resolve_mod, "build_resolved_board", _spy):
            render(board, executor, format=current_format)

    assert seen_render_first == dict.fromkeys(("svg", *_DATA_FORMATS), True)
