"""Regression tests: a ``type: bar`` chart whose x resolves to a genuinely
continuous temporal scale (no timeUnit banding — sparse fine-grain data past
the scaffold budget, or an authored ``axis_x.type: temporal`` /
``time_unit: none``) must render real bars fully inside the plot.

Two halves, mirroring ``test_bar_quantitative_x_scale_padding.py``:

- Width: with no banded slot to size against, ``{"band": f}`` is meaningless
  (VL renders a fixed ~18px fallback) and a max_size literal ignores how
  tightly the dates pack — the emitter must size off the scale itself via
  ``continuous_bar_size_prop``'s live min-gap expression, which needs the
  temporal values coerced to epoch milliseconds.
- Padding: Vega-Lite centers each fixed-width bar on its date; on a
  continuous scale ``encoding.x.scale.padding`` (VL continuousPadding,
  pixels) is the native fix that keeps the min/max bars on-plot — the same
  half-bar reservation the quantitative branch already makes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.style.resolved import ResolvedBarMarkStyle
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.emitters.bar import BarEmitter
from dbt_charts.core.render.chart.spec import RenderBox

from ...conftest import baked_format, fixture_chart_for_type

_DEFAULT_BOX = RenderBox(width=300.0, height=300.0)


def _sparse_daily_data() -> list[dict[str, Any]]:
    """10 rows spaced 45 days apart — detected daily grain, far past the
    ordinal scaffold budget, so the x resolves continuous temporal."""
    first = dt.date(2023, 1, 1)
    return [
        {"d": (first + dt.timedelta(days=45 * i)).isoformat(), "v": i + 1}
        for i in range(10)
    ]


def _temporal_bar_axes():
    """Bake axis_x/axis_y for a bar chart with a temporal x channel."""
    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("bar"),
        "bar",
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


def _build_chart(bar_style):
    ax, ay = _temporal_bar_axes()
    style = bar_style.model_copy(update={"axis_x": ax, "axis_y": ay})
    charts = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    return ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="d",
        y="v",
        style=style,
        id="test_chart",
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={},
        legend=charts.legend,
        background=charts.background,
        canvas=charts.ink_canvas,
        title_style=charts.title,
        layout_padding=PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
    )


def test_continuous_temporal_x_has_no_time_unit_banding(bar_style) -> None:
    chart = _build_chart(bar_style)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _sparse_daily_data()))

    x_enc = spec.encoding["x"]
    assert x_enc["type"] == "temporal"
    assert "timeUnit" not in x_enc


def test_continuous_temporal_x_bar_gets_half_bar_width_padding(bar_style) -> None:
    """Authored bar size: the scale reserves half that width in pixels so the
    first/last date's bar sits fully on-plot instead of straddling the edge."""
    band_px = 20.0
    bar_style = bar_style.model_copy(
        update={"mark": ResolvedBarMarkStyle(size=band_px, band_width=0.8, padding=0.0)}
    )
    chart = _build_chart(bar_style)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _sparse_daily_data()))

    x_enc = spec.encoding["x"]
    assert spec.mark_props["width"] == band_px
    assert x_enc["scale"]["padding"] == band_px / 2
    assert "domain" not in x_enc["scale"]


def test_continuous_temporal_x_bar_width_tracks_scale_gap(bar_style) -> None:
    """No authored bar.size: width comes from the live min-gap-on-scale
    expression (epoch-ms probe points), clamped to [min_size, max_size] —
    never the band-fraction shorthand, which has no band to resolve against,
    and never a bare max_size literal that ignores how the dates pack."""
    bar_style = bar_style.model_copy(
        update={
            "mark": ResolvedBarMarkStyle(
                gap=3.0, min_size=4.0, max_size=20.0, band_width=0.8, padding=0.0
            )
        }
    )
    chart = _build_chart(bar_style)

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _sparse_daily_data()))

    width = spec.mark_props["width"]
    assert isinstance(width, dict) and "expr" in width
    assert "scale('x'" in width["expr"]
    # The two probe points are epoch milliseconds exactly one min-gap apart.
    import re

    probes = [float(m) for m in re.findall(r"scale\('x', ([\d.e+]+)\)", width["expr"])]
    assert len(probes) == 2
    assert abs(probes[0] - probes[1]) == 45 * 24 * 3600 * 1000
    assert spec.encoding["x"]["scale"]["padding"] == 20.0 / 2
