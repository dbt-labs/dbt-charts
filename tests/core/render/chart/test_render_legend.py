"""Tests for legend.title.visible propagation through v2 emitters.

Regression coverage for the feature added in PR #4597: legend.title.visible: false
must propagate to v2 bar/pie/line/area/scatter encoding.color.legend.title.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedPieChart,
    ResolvedPieStyle,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.render.chart.spec import RenderBox

from ...conftest import baked_format

_DEFAULT_BOX = RenderBox(width=600.0, height=300.0)
_ZERO_PADDING = PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0)


def _make_legend(title_visible: bool | None = None):
    """Build a ResolvedLegendStyle with title.visible set.

    Returns the theme-default legend with visible=True (legend shown) so
    legend.title.visible is meaningful (visible=False suppresses the whole legend).
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = resolve_chart_style_context(get_theme_style()).legend
    return base.model_copy(
        update={
            "visible": True,
            "title": base.title.model_copy(update={"visible": title_visible}),
        }
    )


def _default_charts():
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


def _baked_bar_axes() -> tuple[Any, Any]:
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
        fixture_chart_for_type("bar"),
        "bar",
        "ordinal",
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


def _bar_with_color(
    bar_style: ResolvedBarStyle, legend_title_visible: bool | None = None
) -> ResolvedBarChart:
    """Bar chart with a color channel, vertical orientation."""
    legend = _make_legend(legend_title_visible)
    color_ch = ResolvedStyleChannel(
        channel="color", mode="series", data_field="category"
    )
    ax, ay = _baked_bar_axes()
    return ResolvedBarChart(
        panel_axes=(),
        id="bar_color",
        chart_type="bar",
        x="month",
        y="revenue",
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={"color": color_ch},
        legend=legend,
        background=_default_charts().background,
        canvas=_default_charts().ink_canvas,
        title_style=_default_charts().title,
        layout_padding=_ZERO_PADDING,
    )


def _pie_with_color(
    pie_style: ResolvedPieStyle, legend_title_visible: bool | None = None
) -> ResolvedPieChart:
    color_ch = ResolvedStyleChannel(
        channel="color", mode="series", data_field="category"
    )
    legend = _make_legend(legend_title_visible)
    data = [
        {"sales": 100, "category": "A"},
        {"sales": 200, "category": "B"},
    ]
    return ResolvedPieChart(
        id="pie_color",
        chart_type="pie",
        resolution_width=600.0,
        outer_fraction=0.9,
        attached_table_gap=12.0,
        hybrid_heading_gap=6.0,
        presentation_fingerprint=pie_presentation_fingerprint(data),
        slice_label_indices=(),
        theta="sales",
        color="category",
        style=pie_style,
        dark_companion_stops=(),
        variable_dependencies=frozenset(),
        palette=(),
        resolved_channels={"color": color_ch},
        legend=legend,
        background=_default_charts().background,
        canvas=_default_charts().ink_canvas,
        title_style=_default_charts().title,
        layout_padding=_ZERO_PADDING,
    )


# ---------------------------------------------------------------------------
# bar: legend.title.visible: false → encoding.color.legend.title == null
# ---------------------------------------------------------------------------


def test_bar_legend_title_visible_false_emits_null_title(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical bar with color channel + legend.title.visible:false → title:null in VL."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _bar_with_color(bar_style, legend_title_visible=False)
    spec = get_emitter(chart).emit(
        chart,
        _DEFAULT_BOX,
        regroup((), [{"month": "Jan", "revenue": 100, "category": "A"}]),
    )
    color_enc = spec.encoding.get("color")
    assert color_enc is not None, "color encoding must be present"
    legend_enc = color_enc.get("legend")
    # When legend.title.visible is False, VL legend must be a dict with title=null
    assert isinstance(legend_enc, dict), (
        f"expected legend to be a dict when title.visible=False, got {legend_enc!r}"
    )
    assert "title" in legend_enc, (
        f"expected legend.title key when title.visible=False, got {legend_enc!r}"
    )
    assert legend_enc["title"] is None, (
        f"expected legend.title=null when title.visible=False, got {legend_enc!r}"
    )


def test_bar_legend_title_visible_default_no_title_suppression(
    bar_style: ResolvedBarStyle,
) -> None:
    """Vertical bar with color channel + default legend → title not suppressed in VL."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _bar_with_color(bar_style, legend_title_visible=None)
    spec = get_emitter(chart).emit(
        chart,
        _DEFAULT_BOX,
        regroup((), [{"month": "Jan", "revenue": 100, "category": "A"}]),
    )
    color_enc = spec.encoding.get("color")
    assert color_enc is not None, "color encoding must be present"
    # Default legend: if a "legend" key exists, it must NOT have title:None
    legend_val = color_enc.get("legend")
    if legend_val is not None and isinstance(legend_val, dict):
        assert "title" not in legend_val or legend_val["title"] is not None, (
            "legend.title must not be suppressed when title.visible is default"
        )


# ---------------------------------------------------------------------------
# pie: legend.title.visible: false still works after refactor (regression)
# ---------------------------------------------------------------------------


def test_pie_legend_title_visible_false_still_works(
    pie_style: ResolvedPieStyle,
) -> None:
    """Pie with color channel + legend.title.visible:false → title:null in VL encoding."""
    from dbt_charts.core.render.chart.emitters import get_emitter

    chart = _pie_with_color(pie_style, legend_title_visible=False)
    data = [
        {"sales": 100, "category": "A"},
        {"sales": 200, "category": "B"},
    ]
    spec = get_emitter(chart).emit(chart, _DEFAULT_BOX, regroup((), data))
    # Pie emitter returns a layered spec; arc layer is layers[0]
    arc_spec = spec.layers[0]
    color_enc = arc_spec.encoding.get("color")
    assert color_enc is not None, "pie arc must have color encoding"
    legend_enc = color_enc.get("legend")
    assert isinstance(legend_enc, dict), (
        f"expected legend to be a dict for pie when title.visible=False, got {legend_enc!r}"
    )
    assert "title" in legend_enc, (
        f"expected legend.title key for pie when title.visible=False, got {legend_enc!r}"
    )
    assert legend_enc["title"] is None, (
        f"expected legend.title=null for pie when title.visible=False, got {legend_enc!r}"
    )
