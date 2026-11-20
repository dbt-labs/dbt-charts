"""A pie's `style.key` decides its form at resolve: `legend` is the full attached
table (no wedge labels, no classic legend), `visible: false` is the bare wheel."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedPieChart
from dbt_charts.core.compile.models.primitives import PieMode
from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.pie_layout import sized_pie_mode

from ...._results import rows_result

_ROWS = [{"n": n, "v": v} for n, v in [("A", 40), ("B", 30), ("C", 20), ("D", 10)]]


def _resolve_and_size(
    chart_type: Literal["pie", "donut"], key: dict[str, Any]
) -> tuple[ResolvedPieChart, PieMode]:
    board_style, context = resolve_style_and_context(get_theme_style())
    chart = PieChart(
        id="p",
        type=chart_type,
        is_donut=chart_type == "donut",
        theta="v",
        color="n",
        style=PieChartStylePatch.model_validate({"key": key}),
    )
    resolved = resolve(chart, _ROWS, chart_style_context=context, width=600.0)
    assert isinstance(resolved, ResolvedPieChart)
    return resolved, sized_pie_mode(resolved, rows_result(_ROWS), board_style)


@pytest.mark.parametrize("chart_type", ["pie", "donut"])
def test_key_type_legend_is_the_full_table_without_labels_or_legend(
    chart_type: Literal["pie", "donut"],
) -> None:
    resolved, mode = _resolve_and_size(chart_type, {"type": "legend"})
    assert mode == "full_table"
    assert resolved.slice_label_indices == ()
    assert resolved.hybrid_attachment is None
    assert resolved.full_attachment is not None
    assert resolved.legend.visible is False


def test_key_not_visible_shows_neither_labels_nor_table() -> None:
    resolved, mode = _resolve_and_size("pie", {"visible": False})
    assert mode == "direct"
    assert resolved.slice_label_indices == ()
    assert resolved.hybrid_attachment is None
    assert resolved.legend.visible is False
