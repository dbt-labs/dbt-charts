"""A layered chart whose legend is hidden names each series by a colored support_table header."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.resolved import ResolvedSupportTableSource
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_CONTEXT = resolve_chart_style_context(get_theme_style())
_DATA = [
    {"month": "Jan", "after": 84.0, "before": 99.0},
    {"month": "Feb", "after": 93.0, "before": 98.0},
]
_NON_PALETTE_BORDER = "#8a5a2b"


def _inks(
    *,
    chart_type: str = "bar",
    layer: dict[str, Any] | None = None,
    legend_visible: bool = False,
    style: dict[str, Any] | None = None,
) -> tuple[list[str | None], Any]:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "c",
            "type": chart_type,
            "x": "month",
            "y": "after",
            "query": SqlQuery(sql="SELECT 1", source="db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "after"}, {"source": "before"}]},
            "layers": [layer or {"type": "bar", "y": "before"}],
            "style": {"legend": {"visible": legend_visible}, **(style or {})},
        }
    )
    resolved = resolve(chart, _DATA, chart_style_context=_CONTEXT)
    assert resolved.support_table is not None
    entries = resolved.support_table.entries
    assert all(isinstance(e, ResolvedSupportTableSource) for e in entries)
    return [
        e.ink for e in entries if isinstance(e, ResolvedSupportTableSource)
    ], resolved


def _outline(color: str) -> dict[str, Any]:
    return {
        "type": "bar",
        "y": "before",
        "style": {
            "marks": {"bar": {"opacity": 0, "border": {"width": 4, "color": color}}}
        },
    }


def test_a_palette_fill_reads_by_its_companion_ink() -> None:
    inks, resolved = _inks()
    palette = list(resolved.palette)
    companions = resolved.style.series_label.dark_companion_palette
    assert inks[0] == companions[palette.index(resolved.style.single_series_fill)]
    assert inks[1] is not None


def test_an_outline_layer_reads_by_its_border_nudged_to_read_on_the_canvas() -> None:
    inks, resolved = _inks(layer=_outline(_NON_PALETTE_BORDER))
    assert inks[1] is not None
    assert inks[1] != _NON_PALETTE_BORDER
    assert wcag_contrast(inks[1], resolved.canvas) >= 4.5


def test_a_chart_local_dark_canvas_floors_the_ink_against_that_canvas() -> None:
    inks, resolved = _inks(
        layer=_outline(_NON_PALETTE_BORDER), style={"background": "#101820"}
    )
    assert inks[1] is not None
    assert wcag_contrast(inks[1], resolved.canvas) >= 4.5


@pytest.mark.parametrize("chart_type", ["bar", "line", "area"])
@pytest.mark.parametrize("layer_type", ["bar", "line", "area", "scatter"])
def test_every_family_and_layer_type_names_its_series(
    chart_type: str, layer_type: str
) -> None:
    inks, _ = _inks(chart_type=chart_type, layer={"type": layer_type, "y": "before"})
    assert all(ink is not None for ink in inks)


def test_a_visible_legend_leaves_the_headers_uncolored() -> None:
    inks, _ = _inks(legend_visible=True)
    assert inks == [None, None]


def test_a_measure_two_marks_claim_stays_uncolored() -> None:
    inks, _ = _inks(layer={"type": "line", "y": "after"})
    assert inks[0] is None
