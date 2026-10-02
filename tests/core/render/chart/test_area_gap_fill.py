"""TDD: area gap-fill densification for axis_x.fill: linear (and other modes).

V1 materializes missing time-bucket rows via complete_ordinal_time_series before
encoding. V2 AreaEmitter must do the same so the rendered data is dense.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.render.chart.spec import RenderBox

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
from dbt_charts.core.compile.models.chart.resolved import ResolvedAreaStyle
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.style.theme import PaddingStyle

from ...conftest import baked_format


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_C: dict[str, Any] = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "canvas": _DEFAULT_CHARTS.ink_canvas,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
}


def _area_axis(fill: str = "null", time_unit: str = "yearweek") -> Any:
    """Resolved x-axis with given fill mode and yearweek time_unit."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ...conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    baked = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type("area"),
        "area",
        "temporal",
        "quantitative",
        AxisOverrides(),
    )
    ax_merged, ax_band_position = baked.x.style, baked.x.band_position
    ax = build_resolved_axis(
        ax_merged,
        format=baked_format(ax_merged),
        band_position=ax_band_position,
        chart_id="test",
        formats=None,
    )
    return dataclasses.replace(ax, fill=fill, time_unit=time_unit, type=None)


# 3 actual weekly observations for 2 series; there are 5 ISO weeks between
# 2024-01-07 (week 2) and 2024-02-04 (week 5).  After densification we expect
# 5 buckets × 2 series = 10 rows.
_SPARSE_WEEKLY_DATA = [
    {"date": "2024-01-07", "value": 100.0, "series": "A"},
    {"date": "2024-01-28", "value": 130.0, "series": "A"},
    {"date": "2024-02-04", "value": 140.0, "series": "A"},
    {"date": "2024-01-07", "value": 200.0, "series": "B"},
    {"date": "2024-01-28", "value": 250.0, "series": "B"},
    {"date": "2024-02-04", "value": 260.0, "series": "B"},
]
_SPARSE_WEEKLY_SERIES_CHANNEL: dict[str, Any] = {
    "color": __import__(
        "dbt_charts.core.compile.models.chart.resolved",
        fromlist=["ResolvedStyleChannel"],
    ).ResolvedStyleChannel(channel="color", mode="series", data_field="series")
}


@pytest.fixture
def area_with_gap_fill_axis(area_style: ResolvedAreaStyle) -> ResolvedAreaChart:
    """Area chart with yearweek axis + fill='linear' and sparse weekly data."""
    return ResolvedAreaChart(
        panel_axes=(),
        id="area_gf",
        chart_type="area",
        x="date",
        y="value",
        color="series",
        stack=None,
        style=area_style.model_copy(
            update={
                "axis_x": _area_axis(fill="linear", time_unit="yearweek"),
            }
        ),
        resolved_channels=_SPARSE_WEEKLY_SERIES_CHANNEL,
        **{k: v for k, v in _C.items() if k not in ("resolved_channels",)},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_area_emitter_gap_fills_sparse_weekly_data(
    area_with_gap_fill_axis: ResolvedAreaChart,
) -> None:
    """AreaEmitter with fill='linear' densifies sparse data to full bucket scaffold.

    Input: 3 obs × 2 series = 6 rows spanning 5 yearweek buckets.
    Expected output: 5 buckets × 2 series = 10 rows in spec.data.
    """
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    spec = AreaEmitter().emit(
        area_with_gap_fill_axis, _DEFAULT_BOX, regroup((), _SPARSE_WEEKLY_DATA)
    )
    assert spec.data is not None, "spec.data must be populated when gap-fill fires"
    assert len(spec.data) == 10, (
        f"Expected 10 rows (5 buckets × 2 series) after gap-fill; got {len(spec.data)}"
    )


def test_area_emitter_interpolates_missing_values_linearly(
    area_with_gap_fill_axis: ResolvedAreaChart,
) -> None:
    """Synthetic bucket values are linearly interpolated between anchors."""
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    spec = AreaEmitter().emit(
        area_with_gap_fill_axis, _DEFAULT_BOX, regroup((), _SPARSE_WEEKLY_DATA)
    )
    assert spec.data is not None
    # Find the synthesized 2024-01-14 row for series A
    # A anchors: 2024-01-07=100, 2024-01-28=130; gap width=3 buckets (positions 0,1,2,3)
    # 2024-01-14 is 1/3 of the way → 100 + (130-100)/3 = 110.0
    a_14 = next(
        (
            r
            for r in spec.data
            if r.get("date") == "2024-01-14" and r.get("series") == "A"
        ),
        None,
    )
    assert a_14 is not None, (
        "Synthetic 2024-01-14 row for series A missing from spec.data"
    )
    assert abs(a_14["value"] - 110.0) < 1e-6, (
        f"Linear interpolation wrong: expected ~110.0, got {a_14['value']}"
    )


def test_area_emitter_no_gap_fill_when_fill_null(
    area_style: ResolvedAreaStyle,
) -> None:
    """fill='null' still synthesizes rows (scaffold) but with None values, not
    interpolated — axis_x.time_unit is explicitly authored here (a deliberate
    bucketing request), so the implicit-temporal-default skip does not apply.
    """
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    chart = ResolvedAreaChart(
        panel_axes=(),
        id="area_null_fill",
        chart_type="area",
        x="date",
        y="value",
        color="series",
        stack=None,
        style=area_style.model_copy(
            update={
                "axis_x": _area_axis(fill="null", time_unit="yearweek"),
            }
        ),
        resolved_channels=_SPARSE_WEEKLY_SERIES_CHANNEL,
        **{k: v for k, v in _C.items() if k not in ("resolved_channels",)},
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), _SPARSE_WEEKLY_DATA))
    assert spec.data is not None
    # Should still have 10 rows (scaffold created for all buckets)
    assert len(spec.data) == 10, (
        f"Expected 10 rows with fill='null'; got {len(spec.data)}"
    )
    # Synthetic rows should have None value
    a_14 = next(
        (
            r
            for r in spec.data
            if r.get("date") == "2024-01-14" and r.get("series") == "A"
        ),
        None,
    )
    assert a_14 is not None
    assert a_14["value"] is None, (
        f"fill='null' synthetic row should have None value, got {a_14['value']}"
    )


def test_area_emitter_no_gap_fill_for_temporal_axis(
    area_style: ResolvedAreaStyle,
) -> None:
    """Temporal escape hatch: fill does NOT fire when axis type='temporal'.

    Uses pre-aggregated single-series data (one row per date) so
    validate_preaggregated_data passes — the chart has no color dimension.
    """
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    # One row per date — no duplicates for a chart with no color channel.
    single_series_data = [
        {"date": "2024-01-07", "value": 100.0},
        {"date": "2024-01-28", "value": 130.0},
        {"date": "2024-02-04", "value": 140.0},
    ]
    ax = _area_axis(fill="linear", time_unit="yearweek")
    ax = dataclasses.replace(ax, type="temporal")  # escape hatch
    chart = ResolvedAreaChart(
        panel_axes=(),
        id="area_temporal",
        chart_type="area",
        x="date",
        y="value",
        stack=None,
        style=area_style.model_copy(update={"axis_x": ax}),
        **_C,
    )
    spec = AreaEmitter().emit(chart, _DEFAULT_BOX, regroup((), single_series_data))
    # data not set by emitter → session would set it later; None here means no densification
    assert spec.data is None, (
        "Temporal axis should skip gap-fill (spec.data stays None)"
    )
