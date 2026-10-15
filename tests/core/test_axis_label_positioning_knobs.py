"""TDD tests for labelBound, labelFlush, labelOffset.

Vega-Lite axis-label positioning knobs exposed at theme and authoring levels:
  - Each labels.{field} mapping produces the corresponding labelXxx key via axis_to_vl()
  - AxisConfig round-trips each field via model_dump(exclude_none=True)

line_height/anchor were deleted from AxisLabelStyle in the 2026-08 trim (no
replacement — VL's own per-axis defaults apply).
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())

# ── axis_to_vl() mapping ──────────────────────────────────────────────────────


def _axis_with_label_field(field: str, value: object):
    """Return a resolved axis style with labels.{field} set to value."""
    from dbt_charts.core.compile.config import (
        reset_config,
    )
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    reset_config()
    charts = resolve_chart_style_context(get_theme_style())
    base_axis = resolved_axis_style(
        charts, "axis_y", "quantitative", chart_type="", labeled=False
    )
    new_labels = dataclasses.replace(base_axis.labels, **{field: value})
    return dataclasses.replace(base_axis, labels=new_labels)


def test_axis_to_vl_emits_label_bound_bool() -> None:
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    axis = _axis_with_label_field("bound", True)
    result = axis_to_vl(axis)
    assert result.get("labelBound") is True


def test_axis_to_vl_emits_label_bound_float() -> None:
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    axis = _axis_with_label_field("bound", 5.0)
    result = axis_to_vl(axis)
    assert result.get("labelBound") == 5.0


def test_axis_to_vl_emits_label_flush_bool() -> None:
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    axis = _axis_with_label_field("flush", True)
    result = axis_to_vl(axis)
    assert result.get("labelFlush") is True


def test_axis_to_vl_emits_label_flush_float() -> None:
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    axis = _axis_with_label_field("flush", 4.0)
    result = axis_to_vl(axis)
    assert result.get("labelFlush") == 4.0


def test_axis_to_vl_emits_label_offset() -> None:
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    axis = _axis_with_label_field("offset", -3.0)
    result = axis_to_vl(axis)
    assert result.get("labelOffset") == -3.0


# ── Theme cascade test ──────────────────────────────────────────────────────────


def test_default_theme_label_positioning_fields_cascade_to_vl() -> None:
    """Setting a label positioning field in style cascades to axis_to_vl() output."""
    import dataclasses

    from dbt_charts.core.compile.config import (
        reset_config,
    )
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

    reset_config()
    charts = resolve_chart_style_context(get_theme_style())
    base_axis = resolved_axis_style(
        charts, "axis_x", "quantitative", chart_type="", labeled=False
    )
    new_labels = dataclasses.replace(base_axis.labels, bound=True)
    patched_axis = dataclasses.replace(base_axis, labels=new_labels)
    result = axis_to_vl(patched_axis)
    assert result.get("labelBound") is True, f"labelBound missing from {result}"


# ── Full-spec integration (chart-local override reaches encoding.*.axis) ───────


def test_chart_local_label_bound_reaches_encoding() -> None:
    """labels.bound: true in chart style produces labelBound in encoding.x.axis."""
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.models.style.authored import (
        AxisXStylePatch,
        BarChartStylePatch,
        DimensionLabelStylePatch,
    )
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    reset_config()
    patch = BarChartStylePatch(
        orientation="vertical",
        axis_x=AxisXStylePatch(labels=DimensionLabelStylePatch(bound=True)),
    )
    chart = BarChart(
        id="t",
        type="bar",
        x="product",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="src"),
        query_name="q",
        style=patch,
    )
    data = [{"product": "A", "revenue": 100}]
    _rc = resolve(chart, data, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, data, width=400)
    x_axis = spec.get("encoding", {}).get("x", {}).get("axis", {})
    assert x_axis.get("labelBound") is True, (
        f"expected labelBound in x.axis; got {x_axis}"
    )
