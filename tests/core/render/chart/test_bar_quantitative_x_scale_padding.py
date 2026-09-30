"""Regression test: a ``type: bar`` chart whose x field resolves to a
continuous quantitative scale (numeric x, not the binned ``histogram``
path) must not let its min/max bars overhang the plot's left/right edges.

Vega-Lite centers each fixed-width (``mark.width``) bar mark on its
data value; on a continuous scale, ``encoding.x.scale.padding`` dispatches to
VL's own ``continuousPadding`` (pixels on each side of the domain), which is
the native fix — no explicit domain, no data inspection. The theme's
``axis_x.scale.padding: 0`` default (meant for the categorical/band gutter
case, `_base.yaml`) already populates this key for every bar chart, so the
fix takes the larger of that resolved value and the half-bar-width the mark
geometrically needs, rather than only filling in a missing key.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.style.resolved import ResolvedScaleStyle
from dbt_charts.core.compile.models.style.theme import (
    BarMarkStyle,
    PaddingStyle,
)
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.spec import RenderBox

from ...conftest import fixture_chart_for_type

_DEFAULT_BOX = RenderBox(width=300.0, height=300.0)

# Mirrors the distribution_hist repro: query pre-aggregates via GROUP BY, so x
# is discrete numeric days-to-hire values, not evenly spaced.
_DAYS_TO_HIRE = [3, 4, 5, 6, 7, 8, 9, 10, 12, 15]


def _hires_data() -> list[dict[str, Any]]:
    return [{"days_to_hire": d, "hires": i + 1} for i, d in enumerate(_DAYS_TO_HIRE)]


def _quantitative_bar_axes():
    """Bake axis_x/axis_y for a bar chart with a quantitative x channel."""
    chart_style_context = resolve_chart_style_context(get_theme_style())
    ax_merged, ay_merged, ax_band_position, ay_band_position, _ = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("bar"),
        "bar",
        "quantitative",
        "quantitative",
        AxisOverrides(),
    )
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band_position,
            chart_id="test",
            format_raw=None,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="test",
            format_raw=None,
        ),
    )


def _default_legend():
    return resolve_chart_style_context(get_theme_style(get_default_theme_name())).legend


def _default_charts():
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _build_chart(bar_style, ax=None):
    default_ax, ay = _quantitative_bar_axes()
    style = bar_style.model_copy(
        update={"axis_x": ax if ax is not None else default_ax, "axis_y": ay}
    )
    charts = _default_charts()
    return ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="days_to_hire",
        y="hires",
        style=style,
        id="test_chart",
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={},
        legend=_default_legend(),
        background=charts.background,
        canvas=charts.ink_canvas,
        title_style=charts.title,
        layout_padding=PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
    )


def test_quantitative_x_bar_gets_half_bar_width_padding(bar_style) -> None:
    """With no author-set padding, the theme's categorical-gutter default (0)
    is raised to half the bar's own pixel width so it fully fits on-plot.

    The emitter returns a flat spec with the literal ``width`` in ``mark_props``;
    BarHoverBandFeature promotes it to layered and moves mark_props to sub-layer 0.
    """
    band_px = 20.0
    bar_style = bar_style.model_copy(
        update={"mark": BarMarkStyle(size=band_px, band_width=0.8, padding=0.0)}
    )
    chart = _build_chart(bar_style)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _hires_data()))

    x_enc = spec.encoding["x"]
    assert x_enc["type"] == "quantitative"
    # Emitter returns a flat spec; BarHoverBandFeature promotes to layered later.
    assert spec.mark_props["width"] == band_px
    assert x_enc["scale"]["padding"] == band_px / 2
    assert "domain" not in x_enc["scale"]


def test_quantitative_x_bar_reserves_padding_against_max_size_when_no_bar_size(
    bar_style,
) -> None:
    """No authored bar.size — the padding floor reserves against max_size,
    the ceiling the computed-default width can never exceed, so a bar
    computed right up to that ceiling still can't overhang the plot."""
    max_px = 20.0
    bar_style = bar_style.model_copy(
        update={
            "mark": BarMarkStyle(
                gap=3.0, min_size=4.0, max_size=max_px, band_width=0.8, padding=0.0
            )
        }
    )
    chart = _build_chart(bar_style)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _hires_data()))

    x_enc = spec.encoding["x"]
    assert "continuousBandSize" not in spec.mark_props
    assert x_enc["scale"]["padding"] == max_px / 2


def test_quantitative_x_bar_larger_authored_padding_wins(bar_style) -> None:
    """An author-set axis_x.scale.padding larger than half the bar's width
    is preserved, not shrunk to the geometric floor."""
    band_px = 20.0
    bar_style = bar_style.model_copy(
        update={"mark": BarMarkStyle(size=band_px, band_width=0.8, padding=0.0)}
    )
    default_ax, _ = _quantitative_bar_axes()
    ax = dataclasses.replace(default_ax, scale=ResolvedScaleStyle(padding=50.0))
    chart = _build_chart(bar_style, ax=ax)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _hires_data()))

    assert spec.encoding["x"]["scale"]["padding"] == 50.0


def test_quantitative_x_bar_smaller_authored_padding_is_raised_to_floor(
    bar_style,
) -> None:
    """An author-set axis_x.scale.padding smaller than half the bar's width
    is raised to the geometric floor — bars must not overhang regardless."""
    band_px = 20.0
    bar_style = bar_style.model_copy(
        update={"mark": BarMarkStyle(size=band_px, band_width=0.8, padding=0.0)}
    )
    default_ax, _ = _quantitative_bar_axes()
    ax = dataclasses.replace(default_ax, scale=ResolvedScaleStyle(padding=2.0))
    chart = _build_chart(bar_style, ax=ax)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _hires_data()))

    assert spec.encoding["x"]["scale"]["padding"] == band_px / 2
