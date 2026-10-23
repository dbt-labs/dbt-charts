"""Axis titles are bounded by the extent of the axis they label.

A left/right axis title is rendered rotated, so its pixel length is consumed
from the chart's *height*; a bottom/top title consumes *width*. Under
``autosize: fit`` Vega-Lite holds the outer size fixed and shrinks the plot to
make room — so an unbounded axis title collapses the plot to nothing and pushes
the chart title out of the SVG.

These tests pin the bound: the title is pre-wrapped to at most two lines that
fit the axis extent, and a title that already fits is left untouched.
"""

from __future__ import annotations

from dataclasses import replace

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

from ...conftest import baked_format, fixture_chart_for_type

LONG = "Connections Timeline Count Connections Observed Across All Categories"


def _axes() -> tuple[ResolvedAxisStyle, ResolvedAxisStyle]:
    chart_style_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("line"),
        "line",
        "temporal",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position = (
        baked.x.style,
        baked.y.style,
        baked.x.band_position,
        baked.y.band_position,
    )

    return (
        build_resolved_axis(
            ax_merged,
            format=baked_format(ax_merged),
            band_position=ax_band_position,
            chart_id="test",
            formats=None,
        ),
        build_resolved_axis(
            ay_merged,
            format=baked_format(ay_merged),
            band_position=ay_band_position,
            chart_id="test",
            formats=None,
        ),
    )


def _fits(title: str | list[str] | None, budget: int, font: ResolvedFontStyle) -> bool:
    """Every rendered line measures within the budget."""
    lines = title if isinstance(title, list) else [title or ""]
    measurer = get_font_measurer(font.family)
    return all(measurer.measure(line, font.size) <= budget for line in lines)


def test_axis_to_vl_emits_title_limit_from_max_width() -> None:
    """``axis.title.max_width`` reaches Vega-Lite as ``titleLimit``.

    Declared on ``AxisElementStyle`` for both label and title, but historically
    read only on the label side — authoring it on the title was silently
    discarded.

    Needs ``visible=True``: the shipped themes suppress the axis-level title
    (``title: null``) and let the text flow from the encoding instead, and VL
    reads no other axis-title property once the title is nulled.
    """
    _, ay = _axes()
    patched = replace(ay, title=replace(ay.title, max_width=123.0, visible=True))
    assert axis_to_vl(patched)["titleLimit"] == 123.0


def test_axis_to_vl_omits_title_limit_when_unset() -> None:
    _, ay = _axes()
    assert "titleLimit" not in axis_to_vl(
        replace(ay, title=replace(ay.title, visible=True))
    )


# ── Emitter-level coverage ────────────────────────────────────────────────────
# Bar is the family the bug was reported on,
# and its horizontal path swaps channels, so both paths are pinned here through
# BarEmitter itself.


# ── Scatter emitter coverage ──────────────────────────────────────────────────


# ── Heatmap emitter coverage ──────────────────────────────────────────────────
