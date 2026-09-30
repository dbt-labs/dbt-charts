"""Regression tests: authored axis_x.scale.type/domain on the cartesian x-path
(bar/line/area).

Headline case: `scale.type: temporal` forces a continuous temporal x scale
(same mechanism as the pre-existing `axis_x.type: temporal`), and an authored
ISO-date domain then extends the visible range past the data extent.

Error case: an authored domain with no `scale.type: temporal` escape hatch,
against x data that resolves to a bucketed-ordinal scale, raises
ERR-SCALE-DOMAIN-REQUIRES-CONTINUOUS-X rather than silently collapsing
every mark onto the first of the domain's two "categories" (the historical
"single giant rectangle" symptom).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedScaleContinuousStyle,
    ResolvedScaleStyle,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError

# Sparse yearly dates mirroring the original bug-report repro board. Kept inside a
# narrow ~10-year window (not the repro's full 1957-2024 span) so gap-fill's
# synthesized missing-year rows stay well under MAX_ORDINAL_BUCKETS(60) — a
# wide span would gap-fill enough rows to trip the *density gate* onto a
# temporal scale on its own, defeating the ordinal-resolution test below.
_SPARSE_YEARLY_DATES = [
    "2015-01-01",
    "2016-01-01",
    "2018-01-01",
    "2020-01-01",
    "2022-01-01",
    "2024-01-01",
]
_AUTHORED_TEMPORAL_DOMAIN = ["1955-01-01", "2026-01-01"]


def _yearly_data(y_field: str) -> list[dict[str, Any]]:
    return [
        {"date": d, y_field: float(i + 1)} for i, d in enumerate(_SPARSE_YEARLY_DATES)
    ]


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)
_C: dict[str, Any] = {
    "id": "test_chart",
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}


def _axis_x_with_scale(chart_type: str, scale: ResolvedScaleStyle) -> Any:
    """Bake axis_x for chart_type (ordinal x, quantitative y) with a scale override."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    ax_merged, _, ax_band_position, _, _ = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type(chart_type),
        chart_type,
        "ordinal",
        "quantitative",
        AxisOverrides(),
    )
    ax = build_resolved_axis(
        ax_merged,
        band_position=ax_band_position,
        chart_id="test",
        format_raw=None,
    )
    return dataclasses.replace(ax, scale=scale)


# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------


def test_v2_bar_scale_type_temporal_applies_authored_domain(bar_style) -> None:
    """scale.type: temporal forces a continuous x scale; the authored ISO-date
    domain then extends the visible axis range past the data extent."""
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    scale = ResolvedScaleStyle(
        continuous=ResolvedScaleContinuousStyle(
            type="temporal", domain=_AUTHORED_TEMPORAL_DOMAIN
        )
    )
    ax = _axis_x_with_scale("bar", scale)
    chart = ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="date",
        y="total",
        style=bar_style.model_copy(update={"axis_x": ax}),
        **_C,
    )

    spec = BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _yearly_data("total")))
    x_enc = spec.encoding["x"]
    assert x_enc["type"] == "temporal", f"expected continuous x type, got {x_enc}"
    assert x_enc["scale"]["domain"] == _AUTHORED_TEMPORAL_DOMAIN


def test_v2_bar_domain_without_temporal_type_raises_on_ordinal_x(bar_style) -> None:
    """A domain authored without scale.continuous.type: temporal, against date data
    that resolves to an ordinal bucketed scale, must raise rather than silently
    collapse every bar onto the domain's first 'category'."""
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    scale = ResolvedScaleStyle(
        continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_TEMPORAL_DOMAIN)
    )
    ax = _axis_x_with_scale("bar", scale)
    chart = ResolvedBarChart(
        panel_axes=(),
        chart_type="bar",
        x="date",
        y="total",
        style=bar_style.model_copy(update={"axis_x": ax}),
        **_C,
    )

    with pytest.raises(ChartDataError) as exc_info:
        BarEmitter().emit(chart, _DEFAULT_BOX, regroup((), _yearly_data("total")))
    assert exc_info.value.code.code == "ERR-SCALE-DOMAIN-REQUIRES-CONTINUOUS-X"
    assert "scale.type: temporal" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Line
# ---------------------------------------------------------------------------


def test_v2_line_scale_type_temporal_applies_authored_domain(line_style) -> None:
    from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    scale = ResolvedScaleStyle(
        continuous=ResolvedScaleContinuousStyle(
            type="temporal", domain=_AUTHORED_TEMPORAL_DOMAIN
        )
    )
    ax = _axis_x_with_scale("line", scale)
    chart = ResolvedLineChart(
        panel_axes=(),
        chart_type="line",
        x="date",
        y="total",
        style=line_style.model_copy(update={"axis_x": ax}),
        **_C,
    )

    spec = LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _yearly_data("total")))
    x_enc = spec.encoding["x"]
    assert x_enc["type"] == "temporal"
    assert x_enc["scale"]["domain"] == _AUTHORED_TEMPORAL_DOMAIN


def test_v2_line_domain_without_temporal_type_raises_on_ordinal_x(line_style) -> None:
    """Line defaults to continuous temporal for a bucketed-year grain (see
    value-driven-axis-type-inference), so this regression forces the ordinal
    escape hatch explicitly (axis_x.type: ordinal) to still exercise the
    domain-requires-continuous-x guard."""
    import dataclasses

    from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    scale = ResolvedScaleStyle(
        continuous=ResolvedScaleContinuousStyle(domain=_AUTHORED_TEMPORAL_DOMAIN)
    )
    ax = dataclasses.replace(_axis_x_with_scale("line", scale), type="ordinal")
    chart = ResolvedLineChart(
        panel_axes=(),
        chart_type="line",
        x="date",
        y="total",
        style=line_style.model_copy(update={"axis_x": ax}),
        **_C,
    )

    with pytest.raises(ChartDataError) as exc_info:
        LineEmitter().emit(chart, _DEFAULT_BOX, regroup((), _yearly_data("total")))
    assert exc_info.value.code.code == "ERR-SCALE-DOMAIN-REQUIRES-CONTINUOUS-X"


# ---------------------------------------------------------------------------
# Area
# ---------------------------------------------------------------------------


def test_v2_area_scale_type_temporal_applies_authored_domain(area_style) -> None:
    from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    scale = ResolvedScaleStyle(
        continuous=ResolvedScaleContinuousStyle(
            type="temporal", domain=_AUTHORED_TEMPORAL_DOMAIN
        )
    )
    ax = _axis_x_with_scale("area", scale)
    chart = ResolvedAreaChart(
        panel_axes=(),
        chart_type="area",
        x="date",
        y="total",
        stack=None,
        style=area_style.model_copy(update={"axis_x": ax}),
        **_C,
    )

    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _yearly_data("total")))
    x_enc = spec.encoding["x"]
    assert x_enc["type"] == "temporal"
    assert x_enc["scale"]["domain"] == _AUTHORED_TEMPORAL_DOMAIN
