"""TDD tests for compile/resolve — B4 Phase 2.

resolve(compiled, data, board_style) → ResolvedChart bakes the style cascade,
channel resolution, and palette into the resolved model so render never needs to
reach back to compile.resolve.style/channel/palette/colors.

This is the production resolver — there is no parallel legacy stack.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import dbt_charts
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    GeoshapeChart,
    HeatmapChart,
    KpiChart,
    LineChart,
    PieChart,
    PointMapChart,
    ScatterChart,
    SparkBarChart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedBarStyle,
    ResolvedCalloutChart,
    ResolvedGeoshapeChart,
    ResolvedGeoshapeStyle,
    ResolvedHeatmapChart,
    ResolvedHeatmapStyle,
    ResolvedKpiChart,
    ResolvedKpiStyle,
    ResolvedLineChart,
    ResolvedPieChart,
    ResolvedPieStyle,
    ResolvedPointMapChart,
    ResolvedPointMapStyle,
    ResolvedScatterChart,
    ResolvedSparkBarChart,
    ResolvedSparkBarStyle,
    ResolvedTableChart,
    ResolvedTableStyle,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.theme import PaddingStyle

assert dbt_charts.__file__
DBT_CHARTS_PKG_DIR = Path(dbt_charts.__file__).resolve().parent


def _sql(sql: str = "SELECT 1") -> SqlQuery:
    return SqlQuery(sql=sql, source="t")


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

# Required non-None base fields (no defaults after Change #3).
_B: dict = {
    "variable_dependencies": frozenset(),
    "palette": (),
    "resolved_channels": {},
    "legend": _default_legend(),
    "background": _DEFAULT_CHARTS.background,
    "title_style": _DEFAULT_CHARTS.title,
    "layout_padding": PaddingStyle(left=0.0, right=0.0, top=0.0, bottom=0.0),
}
_C: dict = dict(_B)


_BAR_DATA: list[dict[str, Any]] = [
    {"month": "Jan", "revenue": 100},
    {"month": "Feb", "revenue": 200},
    {"month": "Mar", "revenue": 150},
]

_NUMERIC_DATA: list[dict[str, Any]] = [
    {"x": 1.0, "y": 10.5},
    {"x": 2.0, "y": 20.1},
]

_KPI_DATA: list[dict[str, Any]] = [{"revenue": 12345.0}]


def _default_board_style():
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _bar_compiled(**kwargs: Any) -> BarChart:
    defaults: dict[str, Any] = {
        "id": "bar1",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return BarChart(**defaults)


def _kpi_compiled(**kwargs: Any) -> KpiChart:
    defaults: dict[str, Any] = {
        "id": "kpi1",
        "type": "kpi",
        "value": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return KpiChart(**defaults)


def _table_compiled(**kwargs: Any) -> TableChart:
    defaults: dict[str, Any] = {
        "id": "tbl1",
        "type": "table",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return TableChart(**defaults)


# ---------------------------------------------------------------------------
# B4: resolve() basic dispatch and typing
# ---------------------------------------------------------------------------


def test_bar_resolve_returns_resolved_bar_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled()
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)


def test_bar_resolve_has_baked_palette() -> None:
    """palette is populated from the baked cascade (shared envelope, ADR-012)."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert len(resolved.palette) > 0


def test_bar_resolve_palette_stops_present() -> None:
    """Palette stops are baked into the shared envelope — no reach-back needed."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert len(resolved.palette) > 0


def test_bar_resolve_style_slice_is_resolved_bar_style() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved.style, ResolvedBarStyle)


def test_bar_orientation_inferred_discrete_x() -> None:
    """String (discrete) x-axis → horizontal orientation."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    # "month" is a string column → discrete → horizontal
    assert resolved.orientation == "horizontal"


def test_bar_orientation_vertical_numeric_x() -> None:
    """Numeric x-axis → vertical orientation."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = BarChart(id="b", type="bar", x="x", y="y", query=_sql(), query_name="q")
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert resolved.orientation == "vertical"


def test_bar_x_y_preserved() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert resolved.x == "month"
    assert resolved.y == "revenue"


_CATEGORICAL_Y_DATA: list[dict[str, Any]] = [
    {"revenue": 100, "channel": "Direct"},
    {"revenue": 200, "channel": "Organic"},
]


def test_bar_categorical_y_raises_df_compile_error() -> None:
    """Bar semantics fix x=category, y=measure regardless of orientation (see
    _bake_cartesian_axes docstring). A non-numeric y must raise a clear
    compile-domain error rather than silently baking a NaN axis with zero marks."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = BarChart(
        id="b", type="bar", x="revenue", y="channel", query=_sql(), query_name="q"
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-BAR-Y-NOT-NUMERIC"
    assert "channel" in str(exc_info.value)


def test_bar_categorical_y_error_hints_swap_when_x_is_numeric() -> None:
    """x ('revenue') is numeric here, so the error should suggest swapping x/y."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = BarChart(
        id="b", type="bar", x="revenue", y="channel", query=_sql(), query_name="q"
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    hint = exc_info.value.to_diagnostic().hint
    assert hint is not None
    assert "swap" in hint.lower()


def test_bar_categorical_y_error_no_hint_when_x_also_categorical() -> None:
    """Neither x nor y is numeric here — no swap hint would help."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = BarChart(
        id="b",
        type="bar",
        x="channel",
        y="channel",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    assert exc_info.value.to_diagnostic().hint is None


def test_line_resolve_returns_resolved_line_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = LineChart(
        id="l", type="line", x="date", y="val", query=_sql(), query_name="q"
    )
    # board_style required for line: background halo field must be baked
    resolved = resolve(
        compiled, _NUMERIC_DATA, chart_style_context=_default_board_style()
    )
    assert isinstance(resolved, ResolvedLineChart)
    assert len(resolved.palette) > 0


def test_line_categorical_y_raises_df_compile_error() -> None:
    """Line semantics fix x=dimension, y=value — the axis cascade hardcodes y to
    quantitative regardless of the real column type. A non-numeric y must raise a
    clear compile-domain error rather than silently baking a NaN axis with zero marks.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = LineChart(
        id="l", type="line", x="revenue", y="channel", query=_sql(), query_name="q"
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LINE-Y-NOT-NUMERIC"
    assert "channel" in str(exc_info.value)


def test_line_categorical_y_error_hints_swap_when_x_is_numeric() -> None:
    """x ('revenue') is numeric here, so the error should suggest swapping x/y."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = LineChart(
        id="l", type="line", x="revenue", y="channel", query=_sql(), query_name="q"
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    hint = exc_info.value.to_diagnostic().hint
    assert hint is not None
    assert "swap" in hint.lower()


def test_line_categorical_y_error_no_hint_when_x_also_categorical() -> None:
    """Neither x nor y is numeric here — no swap hint would help."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = LineChart(
        id="l",
        type="line",
        x="channel",
        y="channel",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_DATA, _default_board_style())
    assert exc_info.value.to_diagnostic().hint is None


def test_area_resolve_zero_anchors_near_zero_data() -> None:
    """Area with data near zero (min/max <= 0.25) bakes axis_y.scale.zero=True.

    Mirrors V1's smart-zero heuristic (compile.enrich._pick_scale) so the area
    fills to a zero baseline rather than floating.
    """
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a", type="area", x="month", y="rev", query=_sql(), query_name="q"
    )
    data = [{"month": "Jan", "rev": 10}, {"month": "Feb", "rev": 200}]  # ratio 0.05
    resolved = resolve(compiled, data, chart_style_context=_default_board_style())
    assert resolved.style.axis_y is not None
    assert resolved.style.axis_y.scale is not None
    assert resolved.style.axis_y.scale.continuous is not None
    assert resolved.style.axis_y.scale.continuous.zero is True


def test_area_resolve_zero_anchors_even_far_from_zero() -> None:
    """Area with data far from zero (min/max > 0.25) still bakes scale.zero=True.

    Area is not an optional-zero family (see enrich._OPTIONAL_ZERO_CHART_TYPES):
    the fill is the magnitude encoding, so it always zero-anchors positive data
    like bar, regardless of the ratio the optional-zero heuristic would use.
    """
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a", type="area", x="month", y="rev", query=_sql(), query_name="q"
    )
    data = [{"month": "Jan", "rev": 100}, {"month": "Feb", "rev": 200}]  # ratio 0.5
    resolved = resolve(compiled, data, chart_style_context=_default_board_style())
    assert resolved.style.axis_y is not None
    assert resolved.style.axis_y.scale is not None
    assert resolved.style.axis_y.scale.continuous is not None
    assert resolved.style.axis_y.scale.continuous.zero is True


_CATEGORICAL_Y_AREA_DATA: list[dict[str, Any]] = [
    {"revenue": 100, "channel": "Direct"},
    {"revenue": 200, "channel": "Organic"},
]

# Mirrors the real audit repro (examples/rockets spacex_launches.yml swapped):
# a plain int year column is numeric-looking (classify_column_type says
# "numeric"), so it evades the categorical-y check above — the stack-based
# check is what actually catches this shape.
_ROTATED_STREAM_AREA_DATA: list[dict[str, Any]] = [
    {"year_label": 2018, "orbit": "LEO", "launches": 10},
    {"year_label": 2018, "orbit": "GEO", "launches": 4},
    {"year_label": 2018, "orbit": "ISS", "launches": 3},
    {"year_label": 2019, "orbit": "LEO", "launches": 14},
    {"year_label": 2019, "orbit": "GEO", "launches": 5},
    {"year_label": 2019, "orbit": "ISS", "launches": 2},
]


def test_area_categorical_y_raises_df_compile_error() -> None:
    """Area semantics fix x=dimension, y=value — there is no style.orientation
    knob like bar has. Swapping x/y so y is plain-categorical (a string label)
    must raise a clear compile-domain error rather than silently baking a garbage
    axis (the axis cascade hardcodes y to quantitative regardless of type)."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="revenue",
        y="channel",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_AREA_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"
    assert "channel" in str(exc_info.value)


def test_area_categorical_y_error_hints_swap_when_x_is_numeric() -> None:
    """x ('revenue') is numeric here, so the error should suggest swapping x/y."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="revenue",
        y="channel",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_AREA_DATA, _default_board_style())
    hint = exc_info.value.to_diagnostic().hint
    assert hint is not None
    assert "swap" in hint.lower()


def test_area_categorical_y_error_no_hint_when_x_also_categorical() -> None:
    """Neither x nor y is numeric here — no swap hint would help."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="channel",
        y="channel",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _CATEGORICAL_Y_AREA_DATA, _default_board_style())
    assert exc_info.value.to_diagnostic().hint is None


def test_area_resolve_normal_encoding_unaffected() -> None:
    """Normal area (x=dimension, y=numeric value) still resolves cleanly —
    the guard must not false-positive on the correct encoding."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a", type="area", x="month", y="rev", query=_sql(), query_name="q"
    )
    data = [{"month": "Jan", "rev": 100}, {"month": "Feb", "rev": 200}]
    resolved = resolve(compiled, data, chart_style_context=_default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)


def test_area_rotated_streamgraph_numeric_x_with_center_stack_raises() -> None:
    """Real audit repro (rotated-area-error task): x=launches (numeric),
    y=year_label (numeric-looking int, evades the categorical-y check),
    color=orbit, stack: center. The swapped x/y collapses the streamgraph
    to a sliver because a continuous numeric x can't share positions across
    color groups the way stack: center requires."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="launches",
        y="year_label",
        color="orbit",
        stack="center",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _ROTATED_STREAM_AREA_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"
    hint = exc_info.value.to_diagnostic().hint
    assert hint is not None
    assert "swap" in hint.lower()


def test_area_rotated_percent_numeric_x_with_normalize_stack_raises() -> None:
    """Real audit repro variant: stack: normalize with numeric x — the same
    collapse that printed "201800%"-style axis labels in the worksheet."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="launches",
        y="year_label",
        color="orbit",
        stack="normalize",
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError) as exc_info:
        resolve(compiled, _ROTATED_STREAM_AREA_DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-AREA-ENCODING-SWAPPED"


def test_area_correct_orientation_with_center_stack_unaffected() -> None:
    """The un-swapped, correctly-authored version of the same repro
    (x=year_label, y=launches, color=orbit, stack: center) must resolve
    cleanly — this is the shape shipped in examples/rockets/spacex_launches.yml.
    year_label is still numeric-looking (evades check 1) but x is not the
    quantitative measure here, so the stack-based check must not fire."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = AreaChart(
        id="a",
        type="area",
        x="year_label",
        y="launches",
        color="orbit",
        stack="center",
        query=_sql(),
        query_name="q",
    )
    resolved = resolve(compiled, _ROTATED_STREAM_AREA_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)


def test_area_correct_orientation_string_year_label_unaffected() -> None:
    """The production shape in examples/rockets/spacex_launches.yml casts
    year_label to VARCHAR (a string), not left as a bare int — must still
    resolve cleanly regardless of that representation choice."""
    from dbt_charts.core.compile.resolve import resolve

    string_year_data = [
        {**row, "year_label": str(row["year_label"])}
        for row in _ROTATED_STREAM_AREA_DATA
    ]
    compiled = AreaChart(
        id="a",
        type="area",
        x="year_label",
        y="launches",
        color="orbit",
        stack="center",
        query=_sql(),
        query_name="q",
    )
    resolved = resolve(compiled, string_year_data, _default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)


def test_area_correct_orientation_date_x_normalize_stack_unaffected() -> None:
    """The production shape in examples/rockets/crowded_space.yml: x is a
    genuine date column, y is a 0-1 share, color=country, stack: normalize —
    must resolve cleanly (mirrors the "Country share of launches by year"
    100%-stacked area)."""
    import datetime

    from dbt_charts.core.compile.resolve import resolve

    date_share_data = [
        {"launch_at": datetime.date(2018, 1, 1), "country": "USA", "share": 0.5},
        {"launch_at": datetime.date(2018, 1, 1), "country": "China", "share": 0.5},
        {"launch_at": datetime.date(2019, 1, 1), "country": "USA", "share": 0.6},
        {"launch_at": datetime.date(2019, 1, 1), "country": "China", "share": 0.4},
    ]
    compiled = AreaChart(
        id="a",
        type="area",
        x="launch_at",
        y="share",
        color="country",
        stack="normalize",
        query=_sql(),
        query_name="q",
    )
    resolved = resolve(compiled, date_share_data, _default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)


def test_scatter_resolve_returns_resolved_scatter_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="s", type="scatter", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert len(resolved.palette) > 0


def test_heatmap_resolve_color_gradient_baked() -> None:
    """Heatmap style slice carries baked color_gradient from theme cascade."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = HeatmapChart(
        id="h", type="heatmap", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedHeatmapChart)
    assert isinstance(resolved.style, ResolvedHeatmapStyle)
    # color_gradient is set by theme
    assert resolved.style.color_gradient is not None


def test_pie_resolve_inner_radius_baked() -> None:
    """Pie style slice carries inner_radius from cascade."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = PieChart(
        id="p", type="pie", theta="amount", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, [{"amount": 42}], _default_board_style())
    assert isinstance(resolved, ResolvedPieChart)
    assert isinstance(resolved.style, ResolvedPieStyle)
    assert resolved.style.inner_radius == 0.0


def test_pie_resolve_finalizes_direct_mode_before_render() -> None:
    from dbt_charts.core.compile.resolve import resolve

    chart = PieChart(id="p", type="pie", theta="value", color="series")
    resolved = resolve(
        chart,
        [{"series": "Only", "value": 100}],
        _default_board_style(),
        width=600.0,
    )

    assert isinstance(resolved, ResolvedPieChart)
    assert resolved.resolution_width == 600.0
    assert resolved.attached_table is None
    assert resolved.attached_row_indices == ()
    assert resolved.attached_table_placement == "none"
    assert resolved.attached_table_width == 0.0
    assert resolved.wheel_width == 0.0
    assert resolved.attached_heading == ""
    assert resolved.attached_heading_font is None
    assert resolved.style.slice_mark.labels is not None
    assert resolved.slice_label_indices == (0,)


def test_stark_pie_direct_mode_legend_suppressed() -> None:
    """stark's theme cascade must suppress the legend for a direct-mode donut.

    Pins a theme-cascade decision reaching resolve, not a tunable cosmetic
    default.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    stark_board_style = resolve_chart_style_context(get_theme_style("stark"))
    chart = PieChart(id="p", type="donut", theta="value", color="series")
    resolved = resolve(
        chart,
        [{"series": "Only", "value": 100}],
        stark_board_style,
        width=600.0,
    )

    assert isinstance(resolved, ResolvedPieChart)
    assert resolved.attached_table is None  # direct mode, not hybrid/full_table
    assert resolved.legend.visible is False


def test_stark_stacked_negative_measure_bar_keeps_its_legend() -> None:
    """stark's bar-family override must survive the root legend suppression.

    A stacked bar with a negative (zero-crossing) measure disqualifies the
    endpoint-label rail (`_bar_endpoint_labels_for_stack`'s negative-measure
    check — the cumulative midpoint the rail anchors on breaks across zero)
    and, being stacked rather than grouped, `top_legend` resolves to "off"
    too — this shape has no `multiples:`, so `_multiples_wants_top_legend`
    can't force it back on either. Without `stark.yaml`'s
    `charts.bar.legend.visible: true` override this chart would resolve with
    neither a legend nor series labels.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    stark_board_style = resolve_chart_style_context(get_theme_style("stark"))
    chart = BarChart(
        id="b",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="bar",
        x="month",
        y="value",
        color="series",
        style=BarChartStylePatch(stack="zero"),
    )
    data = [
        {"month": "Jan", "value": 100, "series": "A"},
        {"month": "Jan", "value": -50, "series": "B"},
        {"month": "Feb", "value": 200, "series": "A"},
        {"month": "Feb", "value": 80, "series": "B"},
    ]
    resolved = resolve(chart, data, stark_board_style, width=600.0)

    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.endpoint_labels.visible is False
    assert resolved.legend.visible is True


def test_pie_resolve_finalizes_hybrid_attachment_before_render() -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
    from dbt_charts.core.compile.resolve import resolve

    chart = PieChart(id="p", type="pie", theta="value", color="series")
    data = [
        {"series": "Big", "value": 63},
        {"series": "Rest", "value": 30},
        {"series": "Small", "value": 6},
        {"series": "Tiny", "value": 1},
    ]
    resolved = resolve(chart, data, _default_board_style(), width=600.0)

    assert isinstance(resolved, ResolvedPieChart)
    assert isinstance(resolved.attached_table, ResolvedTableChart)
    assert resolved.legend.visible is False
    assert resolved.style.slice_mark.labels is not None
    assert resolved.attached_row_indices == (2, 3)
    assert resolved.slice_label_indices == (0, 1)
    assert resolved.attached_heading == "Too small to label"
    assert resolved.attached_heading_font is not None
    assert resolved.attached_table_width is not None
    assert resolved.wheel_width is not None


def test_pie_resolve_finalizes_full_table_before_render() -> None:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
    from dbt_charts.core.compile.resolve import resolve

    chart = PieChart(id="p", type="pie", theta="value", color="series")
    names = [
        "Enterprise",
        "Mid-Market",
        "SMB",
        "Self-serve",
        "Channel",
        "Partners",
        "Education",
        "Tail",
    ]
    values = [14.0, 14.0, 14.0, 14.0, 14.0, 14.0, 14.5, 1.5]
    data = [
        {"series": name, "value": value}
        for name, value in zip(names, values, strict=True)
    ]
    resolved = resolve(chart, data, _default_board_style(), width=300.0)

    assert isinstance(resolved, ResolvedPieChart)
    assert isinstance(resolved.attached_table, ResolvedTableChart)
    assert resolved.legend.visible is False
    assert resolved.style.slice_mark.labels is None
    assert resolved.attached_row_indices == tuple(range(len(names)))
    assert resolved.attached_heading == ""
    assert resolved.slice_label_indices == ()


def test_pie_hybrid_falls_back_when_companion_table_starves_the_wheel() -> None:
    """Wheel dominance must be judged at the width the wheel actually gets.

    A right-placed companion table takes width off the card, so slice labels
    that clear the dominance floor against the *card* can still starve the
    wheel that is left over. Two long labels either side of a donut plus a
    one-row "too small to label" table collapsed the plotting box to a sliver
    and rendered a ~40px-radius ring.
    """
    from dbt_charts.core.compile.resolve import resolve

    chart = PieChart(id="p", type="donut", theta="value", color="series")
    data = [
        {"series": "Out for signature", "value": 57242},
        {"series": "Fully signed", "value": 55888},
        {"series": "draft", "value": 5},
    ]
    resolved = resolve(chart, data, _default_board_style(), width=564.0)

    assert isinstance(resolved, ResolvedPieChart)
    assert resolved.style.slice_mark.labels is None
    assert resolved.slice_label_indices == ()
    assert resolved.attached_row_indices == (0, 1, 2)
    assert resolved.attached_heading == ""


def test_donut_resolve_returns_resolved_pie_chart() -> None:
    """Compiled PieChart(type='donut') must resolve to ResolvedPieChart (not ValueError)."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = PieChart(
        id="d", type="donut", theta="share", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, [{"share": 0.5}], _default_board_style())
    assert isinstance(resolved, ResolvedPieChart)


def test_kpi_resolve_returns_resolved_kpi_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_kpi_compiled(), _KPI_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedKpiChart)


def test_kpi_resolve_style_slice_populated() -> None:
    """ResolvedKpiStyle.kpi is populated from cascade (non-None)."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_kpi_compiled(), _KPI_DATA, _default_board_style())
    assert isinstance(resolved.style, ResolvedKpiStyle)
    assert resolved.style.kpi is not None


def test_kpi_resolve_family_title_override_reaches_resolved_style() -> None:
    """A per-family style.charts.kpi.title override is merged onto the board
    title at resolve time (build_chart_style_context) and lands complete on
    ResolvedKpiChart.style.title, so render reads one field unconditionally
    instead of choosing between a sparse per-family value and the board
    default itself."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    theme = get_theme_style(get_default_theme_name())
    patch = StylePatch.model_validate(
        {"charts": {"kpi": {"title": {"min_height": 40}}}}
    )
    ctx = resolve_chart_style_context(theme, patch)
    default_ctx = _default_board_style()

    resolved = resolve(_kpi_compiled(), _KPI_DATA, ctx)
    assert resolved.style.title.min_height == 40
    assert resolved.style.title.font.family == default_ctx.title.font.family


def test_kpi_resolved_style_has_palette() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_kpi_compiled(), _KPI_DATA, _default_board_style())
    assert len(resolved.palette) > 0


def test_table_resolve_returns_resolved_table_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_table_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedTableChart)


def test_table_resolve_style_slice_populated() -> None:
    """ResolvedTableStyle.table is populated from cascade."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_table_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved.style, ResolvedTableStyle)
    assert resolved.style.table is not None


def test_geoshape_resolve_returns_resolved_geoshape_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = GeoshapeChart(
        id="g",
        type="geoshape",
        query=_sql(),
        query_name="q",
        geo="geo_field",
        lookup="id",
        value="count",
    )
    resolved = resolve(compiled, [{"id": "CA", "count": 100}], _default_board_style())
    assert isinstance(resolved, ResolvedGeoshapeChart)
    assert isinstance(resolved.style, ResolvedGeoshapeStyle)
    assert resolved.style.geoshape is not None
    assert resolved.style.scatter is not None


def test_point_map_resolve_returns_resolved_point_map_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = PointMapChart(
        id="pm",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
    )
    resolved = resolve(compiled, [{"lat": 37.7, "lon": -122.4}], _default_board_style())
    assert isinstance(resolved, ResolvedPointMapChart)
    assert isinstance(resolved.style, ResolvedPointMapStyle)
    assert resolved.style.point_map is not None


def test_resolve_point_map_defaults_projection_to_albersusa() -> None:
    """projection=None at compile time must bake to 'albersUsa' at resolve time."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = PointMapChart(
        id="pm2",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        projection=None,
    )
    resolved = resolve(compiled, [{"lat": 37.7, "lon": -122.4}], _default_board_style())
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.projection == "albersUsa"


def test_resolve_point_map_keeps_explicit_projection() -> None:
    """An explicit projection must survive the resolve step unchanged."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = PointMapChart(
        id="pm3",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        projection="mercator",
    )
    resolved = resolve(compiled, [{"lat": 37.7, "lon": -122.4}], _default_board_style())
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.projection == "mercator"


def test_geoshape_sub_dollar_color_measure_tooltip_avoids_si_milli() -> None:
    """geoshape's tooltip votes on its color measure -- a sub-$1 color
    column must not misread as SI milli (see resolve_format_for_values)."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = GeoshapeChart(
        id="g2",
        type="geoshape",
        query=_sql(),
        query_name="q",
        geo="geo_field",
        lookup="id",
        color="cents",
    )
    data = [{"id": "CA", "cents": 0.42}, {"id": "NY", "cents": 0.25}]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedGeoshapeChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency_full"]


def test_geoshape_value_field_spelling_votes_not_color_channel() -> None:
    """A choropleth authored with `value:` (not `color:`) paints value_field
    in its tooltip (emitters/geo.py's chart.value_field, which
    _resolve_choropleth_value_field defines as normalized.value or the color
    channel -- value: wins when authored). The vote must read that same
    field, not the color channel directly, or an authored value: silently
    outvotes it -- regression for the case with no color channel at all."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = GeoshapeChart(
        id="g3",
        type="geoshape",
        query=_sql(),
        query_name="q",
        geo="geo_field",
        lookup="id",
        value="cents",
    )
    data = [{"id": "CA", "cents": 0.42}, {"id": "NY", "cents": 0.25}]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedGeoshapeChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency_full"]


def test_resolve_point_map_sub_dollar_size_measure_tooltip_avoids_si_milli() -> None:
    """point_map's tooltip votes on its size measure -- a sub-$1 size
    column must not misread as SI milli (see resolve_format_for_values)."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = PointMapChart(
        id="pm5",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        size="cents",
    )
    data = [
        {"lat": 37.7, "lon": -122.4, "cents": 0.42},
        {"lat": 34.0, "lon": -118.2, "cents": 0.25},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency_full"]


def test_resolve_point_map_color_measure_never_votes_only_size_does() -> None:
    """geo_tooltip.py formats only size_field with tooltip_format -- the
    color entry gets no format at all. A sub-$1 color column (rate) must
    not drag a large size column (revenue) down to two decimals -- that
    would misrender $1.2M as the ugly-but-technically-correct
    $1,200,000.00 for a column the tooltip never even applies the sub-$1
    rule to, since color is never formatted with tooltip_format at all."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = PointMapChart(
        id="pm6",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        size="revenue",
        color="rate",
    )
    data = [
        {"lat": 37.7, "lon": -122.4, "revenue": 1_200_000.0, "rate": 0.42},
        {"lat": 34.0, "lon": -118.2, "revenue": 2_400_000.0, "rate": 0.25},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency"]


def test_resolve_point_map_negative_size_row_never_votes() -> None:
    """A negative size row never paints (emitters/geo.py's
    row_has_negative_size filter drops it -- mark AREA cannot be negative),
    so it must not vote either. Isolated: the only in-band value is
    negative, so a fix that forgot the exclusion would floor and this would
    fail with currency_full instead of the unchanged SI spec."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = PointMapChart(
        id="pm7",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        size="delta",
    )
    data = [
        {"lat": 37.7, "lon": -122.4, "delta": -0.42},
        {"lat": 34.0, "lon": -118.2, "delta": 100.0},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency"]


def test_heatmap_sub_dollar_color_measure_tooltip_avoids_si_milli() -> None:
    """emitters/heatmap.py and structured_tooltip.py both format the color
    channel with tooltip_format -- heatmap's tooltip IS in scope for the
    sub-$1 vote, not "adjacent Vega-painted" out-of-scope work. A sub-$1
    color column must not misread as SI milli."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = HeatmapChart(id="h2", type="heatmap", x="day", y="hour", color="cents")
    data = [
        {"day": "Mon", "hour": "9", "cents": 0.42},
        {"day": "Tue", "hour": "10", "cents": 0.25},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedHeatmapChart)
    assert isinstance(resolved.style, ResolvedHeatmapStyle)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency_full"]


def test_faceted_scatter_x_never_votes_since_the_facet_tooltip_never_paints_it() -> (
    None
):
    """_scatter_roles's own applies-to gate requires `chart.multiples is
    None` -- a faceted scatter's structured tooltip never formats x at all
    (emitters/scatter.py only ever formats y unconditionally). x must not
    vote there, or a sub-$1 x column (cpc) drags a correctly-SI'd y column
    (revenue, $1.2M) to the two-decimal register with nothing painting the
    x side to justify it."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = ScatterChart(
        id="sc1",
        type="scatter",
        x="cpc",
        y="revenue",
        multiples=MultiplesConfig(columns="region"),
    )
    data = [
        {"cpc": 0.42, "revenue": 1_200_000.0, "region": "east"},
        {"cpc": 0.25, "revenue": 2_400_000.0, "region": "west"},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency"]


def test_layered_scatter_x_never_votes_since_the_layer_tooltip_never_paints_it() -> (
    None
):
    """Same gate, the other half: _scatter_roles also requires `not
    chart.layers`. A layered scatter's x must not vote either."""
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.authored import ScatterLayer
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    tooltip_patch = StylePatch.model_validate(
        {"charts": {"tooltip": {"format": "currency"}}}
    )
    board_style = resolve_chart_style_context(
        get_theme_style(get_default_theme_name()), tooltip_patch
    )
    compiled = ScatterChart(
        id="sc2",
        type="scatter",
        x="cpc",
        y="revenue",
        layers=[ScatterLayer(type="scatter", y="target")],
    )
    data = [
        {"cpc": 0.42, "revenue": 1_200_000.0, "target": 50.0},
        {"cpc": 0.25, "revenue": 2_400_000.0, "target": 60.0},
    ]
    resolved = resolve(compiled, data, board_style)
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.tooltip_format == PREDEFINED_SPECS["currency"]


def test_resolve_point_map_style_marks_point_override_propagates() -> None:
    """A chart-root `style.marks.point` override must reach resolved.style.point_mark.

    Regression: point_mark was sourced from `chart_style_context.scatter.marks.point`,
    which `merge_onto_base(chart_style_context.point_map, primary)` never touches — so
    a point-map-family or chart-local point-mark override validated but was silently
    discarded. Must read from the merged `point_map` slot instead.
    """
    from dbt_charts.core.compile.resolve import resolve

    compiled = PointMapChart(
        id="pm4",
        type="point_map",
        query=_sql(),
        query_name="q",
        latitude="lat",
        longitude="lon",
        style={"marks": {"point": {"size": 99.0}}},
    )
    resolved = resolve(compiled, [{"lat": 37.7, "lon": -122.4}], _default_board_style())
    assert isinstance(resolved, ResolvedPointMapChart)
    assert resolved.style.point_mark.size == 99.0


def test_spark_bar_resolve_returns_resolved_spark_bar_chart() -> None:
    from dbt_charts.core.compile.resolve import resolve

    compiled = SparkBarChart(
        id="sb",
        type="spark_bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
    )
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedSparkBarChart)
    assert isinstance(resolved.style, ResolvedSparkBarStyle)
    assert resolved.style.spark_bar is not None


def test_spark_bar_multi_y_raises() -> None:
    """spark_bar with list y of len > 1 raises CompilationError — not silently drop."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = SparkBarChart(
        id="sb",
        type="spark_bar",
        x="month",
        y=["rev_a", "rev_b"],
        query=_sql(),
        query_name="q",
    )
    with pytest.raises(CompilationError, match="single y column"):
        resolve(compiled, _BAR_DATA, _default_board_style())


def test_callout_resolve_returns_resolved_callout_chart() -> None:
    from dbt_charts.core.compile.models.chart.normalized import CalloutChart
    from dbt_charts.core.compile.resolve import resolve

    compiled = CalloutChart(id="co", type="callout", message="Error: no data")
    resolved = resolve(compiled, [], _default_board_style())
    assert isinstance(resolved, ResolvedCalloutChart)
    assert resolved.message == "Error: no data"


def test_callout_resolve_carries_full_style_from_board_cascade() -> None:
    """resolved.style must be the full cascaded ResolvedCalloutStyle.

    v1's oracle: board_style.callout (style_cascade.py's default charts
    cascade, no chart-local override). Field-by-field so a partial port (e.g.
    only tone carried) fails loudly instead of passing on a truncated compare.
    """
    from dbt_charts.core.compile.models.chart.normalized import CalloutChart
    from dbt_charts.core.compile.resolve import resolve

    board_style = _default_board_style()
    compiled = CalloutChart(id="co", type="callout", message="no override")
    resolved = resolve(compiled, [], board_style)

    expected = board_style.callout
    assert resolved.style == expected
    assert resolved.style.tone == expected.tone
    assert resolved.style.background == expected.background
    assert resolved.style.border == expected.border
    assert resolved.style.padding == expected.padding
    assert resolved.style.section_gap == expected.section_gap
    assert resolved.style.title == expected.title
    assert resolved.style.message == expected.message


def test_callout_resolve_carries_chart_local_style_override() -> None:
    """A chart-local style: patch must merge onto the board cascade, not replace it."""
    from dbt_charts.core.compile.models.chart.normalized import CalloutChart
    from dbt_charts.core.compile.models.style.authored import CalloutChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    board_style = _default_board_style()
    patch = CalloutChartStylePatch.model_validate({"tone": "positive"})
    compiled = CalloutChart(id="co", type="callout", message="overridden", style=patch)
    resolved = resolve(compiled, [], board_style)

    assert resolved.style.tone == "positive"
    # Unset fields still inherit from the board cascade, not defaulted/blank.
    assert resolved.style.padding == board_style.callout.padding
    assert resolved.style.section_gap == board_style.callout.section_gap


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------


def test_color_channel_resolved_when_authored() -> None:
    """When color= is set on compiled chart, resolved_channels['color'] is populated."""
    from dbt_charts.core.compile.resolve import resolve

    data = [{"month": "Jan", "revenue": 100, "region": "US"}]
    compiled = _bar_compiled(color="region")
    resolved = resolve(compiled, data, _default_board_style())
    assert "color" in resolved.resolved_channels


def test_no_color_channel_when_not_authored() -> None:
    """When color= is not set, resolved_channels may be empty for color."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    # color is optional; bar with no color= has no color channel
    # (series mode channels may still exist, but we just assert no error)
    assert resolved.resolved_channels is not None


# ---------------------------------------------------------------------------
# No render reach-back in new path
# ---------------------------------------------------------------------------


def test_no_render_imports_in_resolve_package() -> None:
    """compile/resolve must import nothing from dbt_charts.core.render."""
    resolve_dir = DBT_CHARTS_PKG_DIR / "core" / "compile" / "resolve"
    assert resolve_dir.is_dir(), f"resolve package not found at {resolve_dir}"
    for f in resolve_dir.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "from dbt_charts.core.render" not in src, (
            f"render reach-back in {f.name}: "
            "compile/resolve must not import from render"
        )
        assert "import dbt_charts.core.render" not in src, (
            f"render reach-back in {f.name}"
        )


def _containing_package(f: Path) -> str:
    """Dotted package name of the package *containing* module file `f`.

    Same computation whether `f` is a regular module (`foo.py`, whose
    containing package is its parent directory) or an `__init__.py` (whose
    *own* package name is also its parent directory) — both drop exactly the
    file's own basename. Anchored on the *last* ``dbt_charts`` path
    component: the real repo path is ``dbt-charts/src/dbt_charts/...`` — anchoring on the
    checkout-level ``dbt-charts`` component would be the wrong anchor (it would
    resolve through a nonexistent ``dbt-charts.src.dbt_charts...`` prefix).
    Searching for ``dbt_charts`` skips straight to the real package root, and
    the last occurrence keeps this correct for a synthetic `tmp_path`-rooted
    file in the self-test below too.
    """
    parts = f.with_suffix("").parts
    idx = len(parts) - 1 - parts[::-1].index("dbt_charts")
    return ".".join(parts[idx:-1])


def _resolved_import_targets(f: Path, node: ast.ImportFrom) -> list[str]:
    """Every absolute dotted module `node` could actually reach from file `f`.

    Handles `node.level == 0` (already absolute) and `node.level > 0`
    (relative — resolved against `f`'s containing package per Python's own
    algorithm: level=1 is that package unchanged, each extra level ascends
    one more). When `node.module` is None (bare `from . import X` /
    `from .. import X`), each imported name is itself a candidate submodule
    of the resolved base (e.g. `from .. import chart` inside
    `resolve/style/` reaches `resolve.chart`), so every alias name is
    combined with the base separately; otherwise every alias comes from the
    one resolved `base.module` target.
    """
    if node.level == 0:
        return [node.module] if node.module else []
    base_parts = _containing_package(f).split(".")
    ascended = base_parts[: len(base_parts) - (node.level - 1)]
    base = ".".join(ascended)
    if node.module:
        return [f"{base}.{node.module}" if base else node.module]
    return [f"{base}.{alias.name}" if base else alias.name for alias in node.names]


def test_no_style_imports_resolve_chart() -> None:
    """compile/resolve/style must not import compile/resolve/chart.

    The split direction is chart/ -> style/, one-way: chart-family resolvers
    read the style cascade, never the reverse. AST-based (not a substring
    grep) because "resolve.chart" / "resolve/chart" legitimately appears in
    prose inside style/ docstrings (e.g. resolve/style/__init__.py's own
    module docstring, board.py's resolve_chart_style_context docstring)
    describing this very boundary.

    Resolves relative imports (`from ..chart import x`, `from .. import
    chart`) against each file's actual package rather than only matching
    absolute `dbt_charts.core.compile.resolve.chart...` module strings —
    relative imports are this package's own convention
    (`resolve/__init__.py`'s `from .chart import (...)`,
    `resolve/chart/__init__.py`'s `from ._dispatch import ...`), so a
    same-shaped `from ..chart import x` inside `resolve/style/` is the most
    likely real way this boundary breaks, not an edge case.
    """
    style_dir = DBT_CHARTS_PKG_DIR / "core" / "compile" / "resolve" / "style"
    assert style_dir.is_dir(), f"resolve/style package not found at {style_dir}"
    banned_prefix = "dbt_charts.core.compile.resolve.chart"
    violations: list[str] = []
    for f in sorted(style_dir.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for target in _resolved_import_targets(f, node):
                    if target == banned_prefix or target.startswith(
                        banned_prefix + "."
                    ):
                        violations.append(
                            f"{f.name}: from {node.module} import ... "
                            f"(level={node.level}) -> {target}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == banned_prefix or alias.name.startswith(
                        banned_prefix + "."
                    ):
                        violations.append(f"{f.name}: import {alias.name}")
    assert not violations, (
        "resolve/style must not import resolve/chart (one-way split):\n"
        + "\n".join(violations)
    )


def test_resolved_import_targets_handles_relative_forms(tmp_path: Path) -> None:
    """`_resolved_import_targets` must resolve relative imports, not just absolute ones.

    A plain module-string match on `node.module` misses every relative form
    (`from ..chart import x`, `from ..chart.tick_values import y`,
    `from .. import chart`) since `node.module` is `None` or a bare
    unqualified name for those — the exact blind spot a real
    `resolve/style/` file could hit, since relative imports are this
    package's own convention elsewhere (`resolve/__init__.py`'s
    `from .chart import (...)`).

    The synthetic path below matches the real repo layout
    (``.../dbt-charts/src/dbt_charts/...``: checkout-level component directory,
    then the actual importable package root) — anchoring
    `_containing_package` on the checkout-level `dbt-charts` component instead
    of the `dbt_charts` package root silently resolves through a nonexistent
    `dbt-charts.src.dbt_charts...` prefix and every assertion below would fail
    to match.
    """
    f = (
        tmp_path
        / "dbt-charts"
        / "src"
        / "dbt_charts"
        / "core"
        / "compile"
        / "resolve"
        / "style"
        / "sneaky.py"
    )
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n")

    def _targets(src: str) -> list[str]:
        tree = ast.parse(src, str(f))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))
        return _resolved_import_targets(f, node)

    assert _targets("from ..chart.tick_values import numeric_domain_bounds\n") == [
        "dbt_charts.core.compile.resolve.chart.tick_values"
    ]
    assert _targets("from ..chart import tick_values\n") == [
        "dbt_charts.core.compile.resolve.chart"
    ]
    assert _targets("from .. import chart\n") == [
        "dbt_charts.core.compile.resolve.chart"
    ]
    assert _targets("from .palette import color\n") == [
        "dbt_charts.core.compile.resolve.style.palette"
    ]


# ---------------------------------------------------------------------------
# Empty data handling
# ---------------------------------------------------------------------------


def test_bar_resolve_empty_data_does_not_raise() -> None:
    from dbt_charts.core.compile.resolve import resolve

    # Must not raise — inferences just return empty
    resolved = resolve(_bar_compiled(), [], _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)


def test_kpi_resolve_empty_data_does_not_raise() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_kpi_compiled(), [], _default_board_style())
    assert isinstance(resolved, ResolvedKpiChart)


# ---------------------------------------------------------------------------
# HIGH regression: FormatConfig must survive the resolve boundary
# ---------------------------------------------------------------------------


def test_kpi_format_config_preserved_through_resolve() -> None:
    """A FormatConfig authored on style.value.format is not silently dropped to
    None — resolve() finalizes it (defaulting unset notation to narrative)
    rather than losing it outright."""
    from dbt_charts.core.compile.models.primitives import FormatConfig
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    fmt = FormatConfig(spec=",.2f", prefix="$")
    compiled = _kpi_compiled(
        style=KpiChartStylePatch.model_validate({"value": {"format": fmt}})
    )
    resolved = resolve(compiled, [], _default_board_style())
    assert isinstance(resolved, ResolvedKpiChart)
    expected = FormatConfig(spec=",.2f", prefix="$", notation="narrative")
    assert resolved.format == expected, (
        f"FormatConfig was silently dropped; got {resolved.format!r}"
    )


# ---------------------------------------------------------------------------
# HIGH regression: format on cartesian charts must survive compile → resolve
# ---------------------------------------------------------------------------


def test_bar_compiled_accepts_format_field() -> None:
    """_CartesianCompiledChartFields must have a format field so authored format
    is not silently dropped during normalization."""
    compiled = _bar_compiled(format="$0.0a")
    assert compiled.format == "$0.0a"


def test_bar_resolve_format_reaches_resolved() -> None:
    """format authored on a cartesian compiled chart must appear on the resolved chart."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled(format="$0.0a")
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.format == "$0.0a", (
        f"format was silently dropped; got {resolved.format!r}"
    )


# ---------------------------------------------------------------------------
# HIGH regression: cartesian format/y type narrowing drops legal authored values
# ---------------------------------------------------------------------------


def test_cartesian_resolved_accepts_format_config(bar_style: ResolvedBarStyle) -> None:
    """_CartesianResolvedChartFields.format must be FormatState (str | FormatConfig | None),
    not str | None — compiled format is FormatState and resolve forwards it verbatim."""
    from dbt_charts.core.compile.models.primitives import FormatConfig

    fmt = FormatConfig(spec="$,.2f")
    # Pydantic raises ValidationError if the field type is too narrow
    resolved = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="m",
        y="v",
        style=bar_style,
        format=fmt,
        **_C,
    )
    assert resolved.format == fmt, (
        f"FormatConfig was coerced or dropped; got {resolved.format!r}"
    )


def test_cartesian_resolved_wide_measures_accepted(bar_style: ResolvedBarStyle) -> None:
    """wide_measures carries the multi-y field list; y holds the fold value
    field. wide_measure_labels_for derives display labels from
    wide_measures on demand."""
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_VALUE_FIELD,
        wide_measure_labels_for,
    )

    resolved = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="m",
        y=WIDE_VALUE_FIELD,
        wide_measures=("rev", "cost"),
        style=bar_style,
        **_C,
    )
    assert resolved.wide_measures == ("rev", "cost")
    assert resolved.y == WIDE_VALUE_FIELD
    assert wide_measure_labels_for(resolved.wide_measures) == {
        "rev": "rev",
        "cost": "cost",
    }


# ---------------------------------------------------------------------------
# HIGH-2 regression: geoshape with no geo_source and no geo.source raises
# ---------------------------------------------------------------------------


def test_geoshape_no_geo_source_raises_compilation_error() -> None:
    """geoshape with geo=None and geo_source=None must raise CompilationError at
    resolve time — not silently produce geo_url='' that renders nothing."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    compiled = GeoshapeChart(
        id="gs",
        type="geoshape",
        query=_sql(),
        query_name="q",
        geo=None,
        geo_source=None,
        lookup="id",
        value="count",
    )
    with pytest.raises(CompilationError, match="geo_source"):
        resolve(compiled, [{"id": "CA", "count": 1}], _default_board_style())


# ---------------------------------------------------------------------------
# Baked axis styles (resolved_axis_x / resolved_axis_y)
# ---------------------------------------------------------------------------


def test_bar_resolved_axis_x_y_baked_non_none() -> None:
    """resolve() must bake resolved_axis_x and resolved_axis_y for bar charts."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert isinstance(resolved.style.axis_x, ResolvedAxisStyle), (
        "resolved_axis_x must be a ResolvedAxisStyle, got None"
    )
    assert isinstance(resolved.style.axis_y, ResolvedAxisStyle), (
        "resolved_axis_y must be a ResolvedAxisStyle, got None"
    )


def test_bar_resolved_axis_y_grid_present() -> None:
    """axis_y (measure axis) baked style must carry a concrete grid config."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert resolved.style.axis_y is not None
    # grid is always set on a fully resolved axis (theme guarantees it)
    assert resolved.style.axis_y.grid is not None


def test_scatter_resolved_axis_x_y_baked_non_none() -> None:
    """resolve() must bake resolved_axis_x and resolved_axis_y for scatter charts."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="s", type="scatter", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert isinstance(resolved.style.axis_x, ResolvedAxisStyle), (
        "resolved_axis_x must be a ResolvedAxisStyle for scatter, got None"
    )
    assert isinstance(resolved.style.axis_y, ResolvedAxisStyle), (
        "resolved_axis_y must be a ResolvedAxisStyle for scatter, got None"
    )


def test_heatmap_resolved_axis_x_y_baked_non_none() -> None:
    """resolve() must bake resolved_axis_x/y for heatmap (both nominal cascade)."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = HeatmapChart(
        id="h", type="heatmap", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedHeatmapChart)
    assert isinstance(resolved.style.axis_x, ResolvedAxisStyle)
    assert isinstance(resolved.style.axis_y, ResolvedAxisStyle)


def test_scatter_resolve_style_slice_is_resolved_scatter_style() -> None:
    """_resolve_scatter must populate a ResolvedScatterStyle on the chart."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedScatterStyle
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="s", type="scatter", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert isinstance(resolved.style, ResolvedScatterStyle)
    # tooltip_format is a resolved string (may be non-empty from theme)
    assert isinstance(resolved.style.tooltip_format, str)
    assert resolved.style.single_series_fill.startswith("#")
    # Needed by resolve_axis_x_overlap (via resolve_cartesian_x) so a
    # temporal scatter x gets the same categorical-axis overlap heuristic as
    # bar/line/area/heatmap — see 'heatmap-scatter-temporal-label-vocabulary'.
    assert resolved.style.label_usable_ratio > 0


def test_scatter_nominal_y_resolves_zero_anchored_false() -> None:
    """A scatter rotated into a dot plot (categorical y, numeric x) has no
    numeric y column to anchor -- resolve() must publish that as False, not
    inherit whatever the last numeric chart happened to decide."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="s", type="scatter", x="value", y="region", query=_sql(), query_name="q"
    )
    data = [{"region": "a", "value": 5}, {"region": "b", "value": -3}]
    resolved = resolve(compiled, data, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.axis_y.zero_anchored is False


def test_scatter_all_null_y_resolves_zero_anchored_false() -> None:
    """A y column with no numeric values at all has nothing to anchor --
    resolve() must publish False rather than claim a decision that never
    ran."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="s", type="scatter", x="x", y="y", query=_sql(), query_name="q"
    )
    data: list[dict[str, Any]] = [{"x": 1, "y": None}, {"x": 2, "y": None}]
    resolved = resolve(compiled, data, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.axis_y.zero_anchored is False


def test_scatter_nominal_y_with_numeric_overlay_and_authored_ticks_count() -> None:
    """A nominal-y scatter with a shared-scale numeric line overlay and an
    authored ``ticks.count`` -- the un-anchored base ladder resolve() bakes
    onto the shared axis, pinned so a regression to the pre-fix anchored
    ladder (0, 50, 100) is caught here rather than only in a rendered spec."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.resolve import resolve

    payload: dict[str, Any] = {
        "id": "s",
        "type": "scatter",
        "x": "v",
        "y": "cat",
        "layers": [{"type": "line", "y": "y2", "label": "t"}],
        "style": {"axis_y": {"ticks": {"count": 4}}},
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    data = [
        {"v": 10, "cat": c, "y2": w} for c, w in zip("abc", [30, 50, 70], strict=True)
    ]
    resolved = resolve(chart, data, _default_board_style(), width=420.0)
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.axis_y.zero_anchored is False
    assert resolved.style.axis_y.tick_values == (20.0, 40.0, 60.0, 80.0)


# ---------------------------------------------------------------------------
# ResolvedAxisStyle.is_quantitative / .zero_anchored -- published per axis,
# per family, honestly: a channel-type fact (is_quantitative) and a
# scale-anchor fact (zero_anchored) that must match what the axis's own
# scale actually carries, not the label-alignment geometry other code reads
# off the same call sites.
# ---------------------------------------------------------------------------


def test_scatter_quantitative_x_and_y_publishes_is_quantitative_true_on_both_axes() -> (
    None
):
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.resolve import resolve

    payload: dict[str, Any] = {
        "id": "s",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "query": _sql(),
        "query_name": "q",
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    resolved = resolve(chart, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.axis_x.is_quantitative is True
    assert resolved.style.axis_y.is_quantitative is True


def test_bar_vertical_publishes_is_quantitative_true_on_measure_axis_only() -> None:
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.axis_x.is_quantitative is False
    assert resolved.style.axis_y.is_quantitative is True


def test_bar_horizontal_still_publishes_axis_y_is_quantitative_true() -> None:
    """Bar semantics fix y=measure regardless of orientation (see bar.py's
    own comment on this) -- a horizontal bar's axis_y is the measure axis
    just as much as a vertical bar's, so the published channel-type fact
    must not flip with orientation the way the render geometry does."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.resolve import resolve

    payload: dict[str, Any] = {
        "id": "b",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "style": {"orientation": "horizontal"},
        "query": _sql(),
        "query_name": "q",
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    resolved = resolve(chart, _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.axis_y.is_quantitative is True


def test_heatmap_publishes_is_quantitative_false_on_both_axes() -> None:
    """Both of a heatmap's channels are nominal -- its magnitude lives on
    the color channel, not either axis."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = HeatmapChart(
        id="h", type="heatmap", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedHeatmapChart)
    assert resolved.style.axis_x.is_quantitative is False
    assert resolved.style.axis_y.is_quantitative is False


def test_line_publishes_is_quantitative_true_on_both_axes_for_quantitative_x() -> None:
    """A connected-scatter line (numeric x, not year-shaped) has a genuinely
    quantitative x -- both axes must publish True, not just axis_y."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = LineChart(
        id="l", type="line", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.axis_x.is_quantitative is True
    assert resolved.style.axis_y.is_quantitative is True


def test_streamgraph_publishes_zero_anchored_false() -> None:
    """A center-stacked area's y=0 is the silhouette's visual centerline, not
    a floor -- the published fact must say so, matching the resolve-time
    bake skip (see area.py's own center-stack carve-out)."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month": f"2026-0{i}-01", "segment": seg, "revenue": base + i * 10}
        for i in range(1, 4)
        for seg, base in (("Core", 148), ("Growth", 132))
    ]
    payload: dict[str, Any] = {
        "id": "a",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "color": "segment",
        "style": {"stack": "center"},
        "query": _sql(),
        "query_name": "q",
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    resolved = resolve(chart, data, _default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)
    assert resolved.style.axis_y.zero_anchored is False


def test_log_scale_area_publishes_zero_anchored_false() -> None:
    """A log-scaled axis carries no zero anchor -- log(0) is undefined, so
    the published fact must not claim one, even when the all-positive data
    would otherwise trip the smart-zero heuristic."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.resolve import resolve

    data = [
        {"month": "Jan", "revenue": 200.0},
        {"month": "Feb", "revenue": 50.0},
    ]
    payload: dict[str, Any] = {
        "id": "a",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "style": {"axis_y": {"scale": {"continuous": {"type": "log"}}}},
        "query": _sql(),
        "query_name": "q",
    }
    chart = TypeAdapter(Chart).validate_python(payload)
    resolved = resolve(chart, data, _default_board_style())
    assert isinstance(resolved, ResolvedAreaChart)
    assert resolved.style.axis_y.zero_anchored is False


def test_heatmap_resolve_style_slice_is_resolved_heatmap_style() -> None:
    """_resolve_heatmap must populate a full ResolvedHeatmapStyle on the chart."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedHeatmapStyle
    from dbt_charts.core.compile.resolve import resolve

    compiled = HeatmapChart(
        id="h", type="heatmap", x="x", y="y", query=_sql(), query_name="q"
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedHeatmapChart)
    assert isinstance(resolved.style, ResolvedHeatmapStyle)
    assert isinstance(resolved.style.tooltip_format, str)
    assert resolved.style.label_usable_ratio > 0


# ---------------------------------------------------------------------------
# Palette override propagation for non-pie families (issue: palette dropped)
# ---------------------------------------------------------------------------


def test_bar_chart_local_palette_override_survives_resolve() -> None:
    """style.palette on a bar chart must be reflected in resolved.palette."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    custom = ["#aabbcc", "#112233", "#445566"]
    compiled = _bar_compiled(
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": custom}}}
        )
    )
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert list(resolved.palette) == custom, (
        f"chart-local palette override lost at resolve; got {list(resolved.palette)!r}"
    )


def test_bar_chart_alias_palette_sets_requested_alias_palette_on_resolve() -> None:
    """A chart-local WARN-PALETTE-UNSUPPORTED alias (e.g. RdYlGn) survives all
    the way onto the resolved chart's shared-envelope field, where a
    render-stage detector can see it — palette() itself resolves silently."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled(
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": "RdYlGn"}}}
        )
    )
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert resolved.requested_alias_palette == "RdYlGn"
    assert len(resolved.palette) > 0


def test_bar_chart_non_alias_palette_leaves_requested_alias_palette_none() -> None:
    """A non-alias chart-local palette must not fabricate a requested alias."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled(
        style=BarChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": ["#aabbcc", "#112233"]}}}
        )
    )
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert resolved.requested_alias_palette is None


def test_line_chart_local_palette_override_survives_resolve() -> None:
    """style.palette on a line chart must be reflected in resolved.palette."""
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    custom = ["#ff0000", "#00ff00", "#0000ff"]
    compiled = LineChart(
        id="l2",
        type="line",
        x="date",
        y="val",
        query=_sql(),
        query_name="q",
        style=LineChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": custom}}}
        ),
    )
    resolved = resolve(
        compiled, _NUMERIC_DATA, chart_style_context=_default_board_style()
    )
    assert list(resolved.palette) == custom, (
        f"line chart-local palette override lost; got {list(resolved.palette)!r}"
    )


def test_scatter_chart_local_palette_override_survives_resolve() -> None:
    """style.palette on a scatter chart must be reflected in resolved.palette."""
    from dbt_charts.core.compile.models.style.authored import ScatterChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    custom = ["#aaa111", "#bbb222"]
    compiled = ScatterChart(
        id="sc2",
        type="scatter",
        x="x",
        y="y",
        query=_sql(),
        query_name="q",
        style=ScatterChartStylePatch.model_validate(
            {"color": {"categorical": {"palette": custom}}}
        ),
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert list(resolved.palette) == custom, (
        f"scatter chart-local palette override lost; got {list(resolved.palette)!r}"
    )


def test_scatter_family_legend_visible_true_baked_into_resolved() -> None:
    """scatter.legend.visible=true from _base.yaml must be in the resolved chart's legend.

    The default theme sets legend.visible=False globally but overrides it for scatter
    via the scatter family slot. _base_kwargs must apply the family patch so scatter
    charts get visible=True, not the global False.
    """
    from dbt_charts.core.compile.resolve import resolve

    compiled = ScatterChart(
        id="sc3",
        type="scatter",
        x="x",
        y="y",
        query=_sql(),
        query_name="q",
    )
    resolved = resolve(compiled, _NUMERIC_DATA, _default_board_style())
    assert resolved.legend.visible is True, (
        f"scatter family legend.visible=True from _base.yaml was lost at resolve; "
        f"got {resolved.legend.visible!r}"
    )


# ---------------------------------------------------------------------------
# single_series_fill: palette[0] and single_series_palette both control the ink
# ---------------------------------------------------------------------------
# style.palette[0] sets the single-series fill (via _effective_single_series_fill
# in compile/resolve/chart/_palette.py). style.color.categorical.single_series_palette
# is a higher-specificity override.


def test_palette_and_single_series_palette_both_set_single_series_fill() -> None:
    """style.palette[0] and style.color.categorical.single_series_palette set the single-series fill.

    style.palette[0] is read by _effective_single_series_fill in
    compile/resolve/chart/_palette.py.
    style.color.categorical.single_series_palette is a more specific override that takes precedence
    only over the board rhythm slot, not over an explicit per-chart palette.
    """
    from dbt_charts.core.compile.models.style.authored import LineChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    board = _default_board_style()

    def _fill(patch: dict[str, Any]) -> str:
        compiled = LineChart(
            id="l",
            type="line",
            x="date",
            y="val",
            query=_sql(),
            query_name="q",
            style=LineChartStylePatch.model_validate(patch),
        )
        return resolve(compiled, _NUMERIC_DATA, board).style.single_series_fill

    # chart-local palette[0] sets the single-series fill (V1 parity)
    assert _fill({"color": {"categorical": {"palette": ["#ff0000"]}}}) == "#ff0000"
    # single_series_palette is also respected (now authored under color.categorical)
    assert (
        _fill({"color": {"categorical": {"single_series_palette": ["#00ff00"]}}})
        == "#00ff00"
    )


# ---------------------------------------------------------------------------
# D-03: tick_values baked at resolve()
# ---------------------------------------------------------------------------


def _line_compiled(**kwargs: Any) -> LineChart:
    defaults: dict[str, Any] = {
        "id": "line1",
        "type": "line",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return LineChart(**defaults)


def _scatter_compiled(**kwargs: Any) -> ScatterChart:
    defaults: dict[str, Any] = {
        "id": "sc1",
        "type": "scatter",
        "x": "x",
        "y": "y",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return ScatterChart(**defaults)


def _area_compiled(**kwargs: Any) -> AreaChart:
    defaults: dict[str, Any] = {
        "id": "area1",
        "type": "area",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
    }
    defaults.update(kwargs)
    return AreaChart(**defaults)


def test_bar_resolve_bakes_tick_values_on_axis_y() -> None:
    """resolve() bakes nice tick values onto axis_y.tick_values for bar."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    assert resolved.style.axis_y.tick_values != ()


def test_line_resolve_bakes_tick_values_on_axis_y() -> None:
    """resolve() bakes nice tick values onto axis_y.tick_values for line."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_line_compiled(), _BAR_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedLineChart)
    assert resolved.style.axis_y.tick_values != ()


def test_area_resolve_bakes_tick_values_on_axis_y() -> None:
    """resolve() bakes nice tick values onto axis_y.tick_values for area."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_area_compiled(), _BAR_DATA, _default_board_style())
    assert resolved.style.axis_y.tick_values != ()


def test_scatter_resolve_bakes_tick_values_on_axis_y() -> None:
    """resolve() bakes nice tick values onto axis_y.tick_values for scatter."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_scatter_compiled(), _NUMERIC_DATA, _default_board_style())
    assert isinstance(resolved, ResolvedScatterChart)
    assert resolved.style.axis_y.tick_values != ()


def test_bar_stacked_resolve_tick_values_cover_stacked_domain() -> None:
    """resolve() tick_values for stacked bars span the stacked domain max."""
    from dbt_charts.core.compile.resolve import resolve

    stacked_data = [
        {"month": "Jan", "cat": "A", "revenue": 100},
        {"month": "Jan", "cat": "B", "revenue": 50},
        {"month": "Feb", "cat": "A", "revenue": 200},
        {"month": "Feb", "cat": "B", "revenue": 80},
    ]
    # stack="zero" as a top-level BarChart field (the normalizer-facing path)
    compiled = _bar_compiled(color="cat", stack="zero")
    resolved = resolve(compiled, stacked_data, _default_board_style())
    assert isinstance(resolved, ResolvedBarChart)
    # stacked totals: Jan=150, Feb=280 → max tick >= 280
    ticks = resolved.style.axis_y.tick_values
    assert ticks != ()
    assert max(ticks) >= 280.0


def test_bar_normalize_stack_has_empty_tick_values() -> None:
    """resolve() skips tick computation for normalize-stack bars ([0,1] domain)."""
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled(color="month", stack="normalize")
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    assert resolved.style.axis_y.tick_values == ()


def test_resolve_empty_data_has_empty_tick_values() -> None:
    """resolve() with no data bakes empty tick_values (no crash)."""
    from dbt_charts.core.compile.resolve import resolve

    resolved = resolve(_bar_compiled(), [], _default_board_style())
    assert resolved.style.axis_y.tick_values == ()


def test_bar_authored_scale_domain_used_for_tick_bounds() -> None:
    """Authored axis_y.scale.domain pins tick range, not data extent."""
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve

    compiled = _bar_compiled(
        style=BarChartStylePatch.model_validate(
            {"axis_y": {"scale": {"continuous": {"domain": [0, 500]}}}}
        ),
    )
    resolved = resolve(compiled, _BAR_DATA, _default_board_style())
    # All ticks should be within [0, 500]
    ticks = resolved.style.axis_y.tick_values
    assert ticks != ()
    assert min(ticks) >= 0.0
    assert max(ticks) <= 500.0


def test_bar_chart_local_bracket_role_token_resolves_per_theme() -> None:
    """Bracket role tokens in a chart-local style palette resolve through the
    board theme's role bindings on the v2 resolve path (same contract as the
    v1 style_cascade path)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.palette import (
        palette as resolve_palette,
        variant,
    )

    for theme, family in (("stark", "vivid-10"), ("clarity", "editorial-10")):
        compiled = _bar_compiled(
            style=BarChartStylePatch.model_validate(
                {"color": {"categorical": {"palette": ["category[3].dark"]}}}
            )
        )
        resolved = resolve(
            compiled, _BAR_DATA, resolve_chart_style_context(get_theme_style(theme))
        )
        assert list(resolved.palette) == [
            variant(resolve_palette(family)[2], "dark")
        ], (
            f"chart-local bracket token must follow the {theme} theme's "
            f"category role, dark variant; got {list(resolved.palette)!r}"
        )
