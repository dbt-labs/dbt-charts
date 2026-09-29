"""TDD smoke tests for the per-family discriminated resolved chart union.

Phase 1 — types only; no production wiring. Tests:
- Each family model constructs with minimal required fields.
- TypeAdapter(ResolvedChart).validate_python dispatches on chart_type.
- Frozen enforcement: field mutation raises ValidationError.
- extra="forbid": alien fields are rejected.
- Style slice isolation: each family carries its own slice.
- Required-field enforcement (pie.theta, kpi.value, callout.message).
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedAreaStyle,
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedCalloutChart,
    ResolvedChart,
    ResolvedGeoshapeChart,
    ResolvedGeoshapeStyle,
    ResolvedHeatmapChart,
    ResolvedHeatmapStyle,
    ResolvedKpiChart,
    ResolvedKpiStyle,
    ResolvedLineChart,
    ResolvedLineStyle,
    ResolvedPieChart,
    ResolvedPieStyle,
    ResolvedPointMapChart,
    ResolvedPointMapStyle,
    ResolvedScatterChart,
    ResolvedScatterStyle,
    ResolvedSparkBarChart,
    ResolvedSparkBarStyle,
    ResolvedTableChart,
    ResolvedTableStyle,
)
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)

_adapter = TypeAdapter(ResolvedChart)
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)


def _default_legend():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.legend


def _default_resolved_table_style() -> ResolvedTableStyle:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.typography import resolve_title_font

    charts = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    return ResolvedTableStyle(
        table=charts.table,
        title=charts.title,
        formats=charts.formats,
        title_font=resolve_title_font(charts, 600.0),
        pagination=charts.pagination,
    )


def _default_callout_style() -> ResolvedCalloutStyle:
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.callout


def _default_charts():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style(get_default_theme_name())).chart_defaults


_DEFAULT_CHARTS = _default_charts()
_KPI_STYLE = ResolvedKpiStyle(title=_DEFAULT_CHARTS.title)

# Required base fields (no defaults on non-None resolved model fields).
_B: dict = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": _ZERO_PADDING,
}
_C: dict = dict(_B)
# ResolvedKpiChart inherits _BaseResolvedChartFields directly (extra="forbid"),
# not _SharedResolvedChartFields — it has neither background nor title_style.
_KPI_B: dict = {k: v for k, v in _B.items() if k not in ("background", "title_style")}


# ---------------------------------------------------------------------------
# Construction — minimal required fields
# ---------------------------------------------------------------------------


def test_bar_constructs(bar_style: ResolvedBarStyle) -> None:
    c = ResolvedBarChart(panel_axes=(), id="b", chart_type="bar", style=bar_style, **_C)
    assert c.chart_type == "bar"
    assert c.id == "b"


def test_line_constructs(line_style: ResolvedLineStyle) -> None:
    c = ResolvedLineChart(
        panel_axes=(), id="l", chart_type="line", style=line_style, **_C
    )
    assert c.chart_type == "line"


def test_area_constructs(area_style: ResolvedAreaStyle) -> None:
    c = ResolvedAreaChart(
        panel_axes=(), id="a", chart_type="area", style=area_style, **_C
    )
    assert c.chart_type == "area"


def test_scatter_constructs(scatter_style: ResolvedScatterStyle) -> None:
    c = ResolvedScatterChart(
        panel_axes=(),
        id="s",
        chart_type="scatter",
        style=scatter_style,
        **_C,
    )
    assert c.chart_type == "scatter"


def test_heatmap_constructs(heatmap_style: ResolvedHeatmapStyle) -> None:
    c = ResolvedHeatmapChart(
        panel_axes=(), id="h", chart_type="heatmap", style=heatmap_style, **_C
    )
    assert c.chart_type == "heatmap"


def test_pie_constructs(pie_style: ResolvedPieStyle) -> None:
    c = ResolvedPieChart(
        id="p",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint([]),
        slice_label_indices=(),
        theta="share",
        style=pie_style,
        dark_companion_stops=(),
        **_B,
    )
    assert c.theta == "share"


def test_pie_rejects_partial_attachment(pie_style: ResolvedPieStyle) -> None:
    with pytest.raises(ValidationError, match="attachment fields must all be absent"):
        ResolvedPieChart(
            id="p",
            chart_type="pie",
            resolution_width=600.0,
            outer_fraction=0.9,
            attached_table_gap=12.0,
            hybrid_heading_gap=6.0,
            presentation_fingerprint=pie_presentation_fingerprint([]),
            slice_label_indices=(),
            theta="share",
            style=pie_style,
            dark_companion_stops=(),
            attached_table_width=200.0,
            **_B,
        )


def test_kpi_constructs() -> None:
    c = ResolvedKpiChart(
        id="k", chart_type="kpi", value="revenue", style=_KPI_STYLE, **_KPI_B
    )
    assert c.value == "revenue"


def test_table_constructs(table_style: ResolvedTableStyle) -> None:
    c = ResolvedTableChart(id="t", chart_type="table", style=table_style, **_B)
    assert c.chart_type == "table"


def test_point_map_constructs(point_map_style: ResolvedPointMapStyle) -> None:
    c = ResolvedPointMapChart(
        id="pm", chart_type="point_map", style=point_map_style, collapse=False, **_B
    )
    assert c.chart_type == "point_map"


def test_geoshape_constructs(geoshape_style: ResolvedGeoshapeStyle) -> None:
    c = ResolvedGeoshapeChart(id="g", chart_type="geoshape", style=geoshape_style, **_B)
    assert c.chart_type == "geoshape"


def test_callout_constructs() -> None:
    c = ResolvedCalloutChart(
        id="co",
        chart_type="callout",
        message="hello",
        variable_dependencies=frozenset(),
        style=_default_callout_style(),
        layout_padding=_ZERO_PADDING,
    )
    assert c.message == "hello"


def test_spark_bar_constructs() -> None:
    c = ResolvedSparkBarChart(
        id="sb", chart_type="spark_bar", style=ResolvedSparkBarStyle(), **_B
    )
    assert c.chart_type == "spark_bar"


# ---------------------------------------------------------------------------
# TypeAdapter dispatch on chart_type
# ---------------------------------------------------------------------------


import dataclasses


def _default_axis_d(chart_type: str, x_type: str, y_type: str) -> tuple[dict, dict]:
    """Return (axis_x dict, axis_y dict) for dispatch test payloads."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ..conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    ax_merged, ay_merged, ax_band_position, ay_band_position, _, _, _ = (
        _bake_cartesian_axes(
            chart_style_context,
            fixture_chart_for_type(chart_type),
            chart_type,
            x_type,
            y_type,
            AxisOverrides(),
        )
    )
    ax = build_resolved_axis(
        ax_merged,
        band_position=ax_band_position,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    ay = build_resolved_axis(
        ay_merged,
        band_position=ay_band_position,
        chart_id="test",
        format_authored=True,
        format_is_alias=False,
    )
    return dataclasses.asdict(ax), dataclasses.asdict(ay)


_BAR_AX_D, _BAR_AY_D = _default_axis_d("bar", "ordinal", "quantitative")
_LINE_AX_D, _LINE_AY_D = _default_axis_d("line", "temporal", "quantitative")
_AREA_AX_D, _AREA_AY_D = _default_axis_d("area", "temporal", "quantitative")
_SCATTER_AX_D, _SCATTER_AY_D = _default_axis_d(
    "scatter", "quantitative", "quantitative"
)
_HEATMAP_AX_D, _HEATMAP_AY_D = _default_axis_d("heatmap", "nominal", "nominal")

_FONT = {
    "family": "sans-serif",
    "color": "#000000",
    "size": 12.0,
    "weight": "normal",
    "style": "normal",
    "decoration": "none",
    "case": "none",
    "line_height": 1.25,
}
# Required base fields for all _BaseResolvedChartFields subclasses as dicts.
_ZERO_PADDING_D: dict = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
_BD: dict = {
    "variable_dependencies": [],
    "palette": [],
    "resolved_channels": {},
    "legend": _default_legend().model_dump(),
    "background": _DEFAULT_CHARTS.background,
    "title_style": _DEFAULT_CHARTS.title.model_dump(),
    "layout_padding": _ZERO_PADDING_D,
}
_CD: dict = {**_BD, "panel_axes": []}
_KPI_BD: dict = {k: v for k, v in _BD.items() if k not in ("background", "title_style")}
# Resolved mark dicts.
_LINE_MARK_D = {"stroke": {"width": 2.0}, "halo_multiplier": 2.0, "labels": {}}
_AREA_MARK_D = {"opacity": 0.15, "backdrop": True}
# Area's line_mark carries top-edge stroke/halo/labels (see ResolvedAreaLineStyle) —
# area's own mark (_AREA_MARK_D) is fill-only (opacity/curve).
_AREA_LINE_MARK_D = {"stroke": {"width": 2.0}, "halo_multiplier": 2.0, "labels": {}}
_AREA_POINT_MARK_D: dict = {}
_SERIES_LABEL_D = {
    "font_family": "Inter",
    "font_size": 11.0,
    "font_weight": "400",
    "font_style": "normal",
    "dark_companion_palette": [],
    "gap_px": 18.0,
}
_ENDPOINT_LABELS_D = {"visible": False, "label_offset": 8.0, "height": 20.0}
# Table style has many required nested fields — dump the real cascade rather
# than hand-enumerate them (TableChartStyle isn't constructible from {}).
_TABLE_STYLE_D = _default_resolved_table_style().model_dump()


@pytest.mark.parametrize(
    ("payload", "expected_cls"),
    [
        (
            {
                "id": "b",
                "chart_type": "bar",
                "style": {
                    "series_label": _SERIES_LABEL_D,
                    "mark": {},
                    "endpoint_labels": _ENDPOINT_LABELS_D,
                    "single_series_fill": "#4C72B0",
                    "tooltip_format": "",
                    "label_font_size": 11.0,
                    "label_usable_ratio": 0.8,
                    "axis_x": _BAR_AX_D,
                    "axis_y": _BAR_AY_D,
                },
                **_CD,
            },
            ResolvedBarChart,
        ),
        (
            {
                "id": "l",
                "chart_type": "line",
                "style": {
                    "series_label": _SERIES_LABEL_D,
                    "line_mark": _LINE_MARK_D,
                    "point_mark": {},
                    "endpoint_labels": _ENDPOINT_LABELS_D,
                    "single_series_fill": "#4C72B0",
                    "tooltip_format": "",
                    "label_usable_ratio": 0.8,
                    "dashes": [],
                    "axis_x": _LINE_AX_D,
                    "axis_y": _LINE_AY_D,
                },
                **_CD,
            },
            ResolvedLineChart,
        ),
        (
            {
                "id": "a",
                "chart_type": "area",
                "style": {
                    "series_label": _SERIES_LABEL_D,
                    "area_mark": _AREA_MARK_D,
                    "line_mark": _AREA_LINE_MARK_D,
                    "point_mark": _AREA_POINT_MARK_D,
                    "endpoint_labels": _ENDPOINT_LABELS_D,
                    "single_series_fill": "#4C72B0",
                    "tooltip_format": "",
                    "label_usable_ratio": 0.8,
                    "dashes": [],
                    "axis_x": _AREA_AX_D,
                    "axis_y": _AREA_AY_D,
                },
                **_CD,
            },
            ResolvedAreaChart,
        ),
        (
            {
                "id": "s",
                "chart_type": "scatter",
                "style": {
                    "point_mark": {},
                    "single_series_fill": "#4C72B0",
                    "tooltip_format": "",
                    "label_usable_ratio": 0.8,
                    "axis_x": _SCATTER_AX_D,
                    "axis_y": _SCATTER_AY_D,
                },
                **_CD,
            },
            ResolvedScatterChart,
        ),
        (
            {
                "id": "h",
                "chart_type": "heatmap",
                "style": {
                    "color_gradient": None,
                    "rect_mark": {},
                    "tooltip_format": "",
                    "label_usable_ratio": 0.8,
                    "axis_x": _HEATMAP_AX_D,
                    "axis_y": _HEATMAP_AY_D,
                },
                **_CD,
            },
            ResolvedHeatmapChart,
        ),
        (
            {
                "id": "p",
                "chart_type": "pie",
                "resolution_width": 600.0,
                "outer_fraction": 0.9,
                "attached_table_gap": 12.0,
                "hybrid_heading_gap": 6.0,
                "presentation_fingerprint": pie_presentation_fingerprint([]),
                "slice_label_indices": [],
                "theta": "share",
                "dark_companion_stops": [],
                "style": {
                    "inner_radius": 0.0,
                    "slice_mark": {},
                    "tooltip_format": "",
                    "total_style": {
                        "value": {"font": _FONT},
                        "label": {"font": _FONT},
                    },
                },
                **_BD,
            },
            ResolvedPieChart,
        ),
        (
            {
                "id": "k",
                "chart_type": "kpi",
                "value": "rev",
                "style": {"title": _DEFAULT_CHARTS.title},
                **_KPI_BD,
            },
            ResolvedKpiChart,
        ),
        (
            {"id": "t", "chart_type": "table", "style": _TABLE_STYLE_D, **_BD},
            ResolvedTableChart,
        ),
        (
            {
                "id": "pm",
                "chart_type": "point_map",
                "collapse": False,
                "style": {
                    "point_mark": {},
                    "single_series_fill": "#4C72B0",
                    "tooltip_format": "",
                },
                **_BD,
            },
            ResolvedPointMapChart,
        ),
        (
            {
                "id": "g",
                "chart_type": "geoshape",
                "style": {
                    "tooltip_format": "",
                },
                **_BD,
            },
            ResolvedGeoshapeChart,
        ),
        (
            {
                "id": "co",
                "chart_type": "callout",
                "message": "hi",
                "variable_dependencies": [],
                "style": _default_callout_style().model_dump(),
                "layout_padding": _ZERO_PADDING_D,
            },
            ResolvedCalloutChart,
        ),
        (
            {"id": "sb", "chart_type": "spark_bar", "style": {}, **_BD},
            ResolvedSparkBarChart,
        ),
    ],
)
def test_dispatch(payload: dict, expected_cls: type) -> None:
    result = _adapter.validate_python(payload)
    assert isinstance(result, expected_cls)


def test_dispatch_missing_discriminator_raises() -> None:
    with pytest.raises(ValidationError):
        _adapter.validate_python({"id": "x"})


# ---------------------------------------------------------------------------
# Frozen enforcement
# ---------------------------------------------------------------------------


def test_bar_is_frozen(bar_style: ResolvedBarStyle) -> None:
    c = ResolvedBarChart(panel_axes=(), id="b", chart_type="bar", style=bar_style, **_C)
    with pytest.raises(ValidationError):
        c.id = "mutated"  # type: ignore[misc]


def test_kpi_is_frozen() -> None:
    c = ResolvedKpiChart(
        id="k", chart_type="kpi", value="rev", style=_KPI_STYLE, **_KPI_B
    )
    with pytest.raises(ValidationError):
        c.value = "mutated"  # type: ignore[misc]


def test_callout_is_frozen() -> None:
    c = ResolvedCalloutChart(
        id="co",
        chart_type="callout",
        message="msg",
        variable_dependencies=frozenset(),
        style=_default_callout_style(),
        layout_padding=_ZERO_PADDING,
    )
    with pytest.raises(ValidationError):
        c.message = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# extra="forbid" — alien fields rejected
# ---------------------------------------------------------------------------


def test_bar_rejects_theta(bar_style: ResolvedBarStyle) -> None:
    with pytest.raises(ValidationError):
        ResolvedBarChart(
            panel_axes=(),
            id="b",
            chart_type="bar",
            theta="share",
            style=bar_style,
            **_C,
        )  # type: ignore[call-arg]


def test_pie_rejects_x() -> None:
    with pytest.raises(ValidationError):
        ResolvedPieChart(  # type: ignore[call-arg]
            id="p",
            chart_type="pie",
            resolution_width=600.0,
            outer_fraction=0.9,
            attached_table_gap=12.0,
            hybrid_heading_gap=6.0,
            presentation_fingerprint=pie_presentation_fingerprint([]),
            slice_label_indices=(),
            theta="share",
            x="month",
            dark_companion_stops=(),
            **_B,
        )


def test_kpi_rejects_x() -> None:
    with pytest.raises(ValidationError):
        ResolvedKpiChart(  # type: ignore[call-arg]
            id="k",
            chart_type="kpi",
            value="rev",
            style=_KPI_STYLE,
            x="col",
            **_B,
        )


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


def test_pie_requires_theta() -> None:
    with pytest.raises(ValidationError):
        ResolvedPieChart(id="p", chart_type="pie")


def test_kpi_requires_value() -> None:
    with pytest.raises(ValidationError):
        ResolvedKpiChart(id="k", chart_type="kpi")


def test_callout_requires_message() -> None:
    with pytest.raises(ValidationError):
        ResolvedCalloutChart(id="co", chart_type="callout")


def test_kpi_requires_style() -> None:
    with pytest.raises(ValidationError):
        ResolvedKpiChart(id="k", chart_type="kpi", value="rev")


def test_table_requires_style() -> None:
    with pytest.raises(ValidationError):
        ResolvedTableChart(id="t", chart_type="table")


def test_geoshape_requires_style() -> None:
    with pytest.raises(ValidationError):
        ResolvedGeoshapeChart(id="g", chart_type="geoshape")


def test_point_map_requires_style() -> None:
    with pytest.raises(ValidationError):
        ResolvedPointMapChart(id="pm", chart_type="point_map")


def test_spark_bar_requires_style() -> None:
    with pytest.raises(ValidationError):
        ResolvedSparkBarChart(id="sb", chart_type="spark_bar")


# ---------------------------------------------------------------------------
# Family-specific fields
# ---------------------------------------------------------------------------


def test_bar_has_stack_and_orientation(bar_style: ResolvedBarStyle) -> None:
    c = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        stack="zero",
        orientation="horizontal",
        style=bar_style,
        **_C,
    )
    assert c.stack == "zero"
    assert c.orientation == "horizontal"


def test_scatter_has_size_and_shape(scatter_style: ResolvedScatterStyle) -> None:
    c = ResolvedScatterChart(
        panel_axes=(),
        id="s",
        chart_type="scatter",
        size="pop",
        shape="cat",
        style=scatter_style,
        **_C,
    )
    assert c.size == "pop"
    assert c.shape == "cat"


def test_table_has_column_configs(table_style: ResolvedTableStyle) -> None:
    c = ResolvedTableChart(
        id="t",
        chart_type="table",
        header_overflow="truncate",
        style=table_style,
        **_B,
    )
    assert c.header_overflow == "truncate"
    assert c.columns is None


# ---------------------------------------------------------------------------
# Style slice isolation
# ---------------------------------------------------------------------------


def test_resolved_bar_style_has_stack_order(bar_style: ResolvedBarStyle) -> None:
    s = bar_style.model_copy(update={"stack_order": "value"})
    assert s.stack_order == "value"


def test_resolved_bar_style_rejects_kpi_fields() -> None:
    with pytest.raises(ValidationError):
        ResolvedBarStyle(font={})  # type: ignore[call-arg]


def test_resolved_heatmap_style_has_color_gradient(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfig

    _cg = ScaleTargetConfig.model_validate({"palette": "blues"})
    s = heatmap_style.model_copy(update={"color_gradient": _cg})
    assert s.color_gradient is not None and s.color_gradient.palette == "blues"


def test_resolved_pie_style_has_inner_radius(pie_style: ResolvedPieStyle) -> None:
    s = pie_style.model_copy(update={"inner_radius": 60})
    assert s.inner_radius == 60


def test_resolved_callout_style_has_tone() -> None:
    s = _default_callout_style()
    s = s.model_copy(update={"tone": "warning"})
    assert s.tone == "warning"


def test_bar_style_slice_is_family_narrowed(bar_style: ResolvedBarStyle) -> None:
    """ResolvedBarChart.style is a ResolvedBarStyle, not a full charts-style bag."""
    c = ResolvedBarChart(panel_axes=(), id="b", chart_type="bar", style=bar_style, **_C)
    assert isinstance(c.style, ResolvedBarStyle)


def test_heatmap_style_slice_is_family_narrowed(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    c = ResolvedHeatmapChart(
        panel_axes=(), id="h", chart_type="heatmap", style=heatmap_style, **_C
    )
    assert isinstance(c.style, ResolvedHeatmapStyle)
