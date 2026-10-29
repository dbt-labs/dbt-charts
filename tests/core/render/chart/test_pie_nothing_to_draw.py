"""A pie over no rows draws its empty frame; one over all-zero thetas refuses by
name. Both go through the production path (`render_resolved_chart` → session →
engine), not the engine alone, and the warning detectors read them without
raising."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.warnings import (
    legend_position_width_fallback,
    legend_values_unresolved,
    pie_sized_mode_misses_guard,
)
from dbt_charts.core.render.warnings.base import WarningContext

from ..._board_utils import make_test_resolved_board

Rows = list[dict[str, Any]]


def _resolved(rows: Rows) -> tuple[ResolvedPieChart, Any]:
    style, context = resolve_style_and_context(get_theme_style())
    resolved = resolve(
        PieChart(id="p", type="pie", theta="v", color="n"),
        rows,
        chart_style_context=context,
        width=500.0,
    )
    assert isinstance(resolved, ResolvedPieChart)
    return resolved, style


def test_a_pie_over_no_rows_draws_its_empty_frame() -> None:
    resolved, style = _resolved([])
    artifact = render_resolved_chart(resolved, [], style)
    assert artifact.kind == "svg"
    assert isinstance(artifact.payload, str)
    assert "arc mark" not in artifact.payload


def test_a_pie_over_all_zero_thetas_refuses_by_name() -> None:
    rows = [{"n": "a", "v": 0}, {"n": "b", "v": 0}]
    resolved, style = _resolved(rows)
    with pytest.raises(ChartDataError, match="total is zero"):
        render_resolved_chart(resolved, rows, style)


@pytest.mark.parametrize("rows", [[], [{"n": "a", "v": 0}, {"n": "b", "v": 0}]])
def test_the_pie_detectors_read_a_pie_with_nothing_to_draw(rows: Rows) -> None:
    resolved, _ = _resolved(rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
        layout_charts={resolved.id: resolved},
        pie_modes={resolved.id: "direct"},
    )
    for detector in (
        legend_position_width_fallback,
        legend_values_unresolved,
        pie_sized_mode_misses_guard,
    ):
        assert detector.detect(ctx) == []


def test_a_pie_with_slices_cannot_lack_its_full_table_candidate() -> None:
    from pydantic import ValidationError

    resolved, _ = _resolved([{"n": "a", "v": 3}, {"n": "b", "v": 1}])
    broken = {**resolved.model_dump(), "full_attachment": None}
    with pytest.raises(ValidationError, match="full-table"):
        ResolvedPieChart.model_validate(broken)

    empty, _ = _resolved([])
    assert empty.full_attachment is None
