"""Shared style fixtures for resolved-chart unit tests.

Keep one source of truth for per-family style fixtures so that adding a
family in a new PR means adding ONE new fixture here — not N copies in N
test files.

Inject via pytest fixture parameter:

    def test_something(bar_style: ResolvedBarStyle) -> None: ...
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaStyle,
    ResolvedBarStyle,
    ResolvedGeoshapeStyle,
    ResolvedHeatmapStyle,
    ResolvedLineStyle,
    ResolvedPieStyle,
    ResolvedPointMapStyle,
    ResolvedScatterStyle,
    ResolvedSeriesLabelStyle,
    ResolvedTableStyle,
)

# Ink palette slot 0 from the default resolved theme.
# This is rcs.single_series_palette[0] for the built-in theme.
# If the default theme's first palette slot changes, update here only.
SINGLE_SERIES_FILL: str = "#4C72B0"


def _series_label() -> ResolvedSeriesLabelStyle:
    """Minimal valid resolved series-label style for cartesian-style fixtures."""
    return ResolvedSeriesLabelStyle(
        font_family="Inter",
        font_size=11.0,
        font_weight="400",
        font_style="normal",
        dark_companion_palette=(),
        gap_px=18.2,
    )


def _endpoint_labels(visible: bool = False):
    """Minimal cascade-merged endpoint-label config for cartesian-style fixtures."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig

    return EndpointLabelsConfig(visible=visible, label_offset=8.0, height=20.0)


def fixture_chart_for_type(chart_type: str):
    """Minimal normalized chart instance for the given cartesian chart_type.

    Shared across style-fixture tests that need to call `_bake_cartesian_axes`
    directly (it takes a `chart` positional arg for chart-authored-format
    fallback) — one source of truth instead of N per-file copies.
    """
    from dbt_charts.core.compile.models.chart.normalized import (
        AreaChart,
        BarChart,
        HeatmapChart,
        LineChart,
        ScatterChart,
    )

    fixture_chart_by_type = {
        "bar": BarChart(id="fixture", type="bar"),
        "line": LineChart(id="fixture", type="line"),
        "area": AreaChart(id="fixture", type="area"),
        "scatter": ScatterChart(id="fixture", type="scatter"),
        "heatmap": HeatmapChart(id="fixture", type="heatmap"),
    }
    return fixture_chart_by_type[chart_type]


def _bake_axes(chart_type: str, x_type: str, y_type: str):
    """Bake a (axis_x, axis_y) pair from the default cascade for style fixtures.

    ``_bake_cartesian_axes`` returns the merged, pre-build ``AxisStyle`` pair
    (not yet narrowed to ``ResolvedAxisStyle``) — these fixtures don't
    exercise own-side align/edge resolution, so ``build_resolved_axis`` is
    called here with no edge (matches ``resolved_axis_style``'s default).
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

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
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band_position,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="fixture",
            format_authored=True,
            format_is_alias=False,
        ),
    )


@pytest.fixture
def bar_style() -> ResolvedBarStyle:
    """Minimal valid ResolvedBarStyle with all required fields populated.

    gap/min_size/max_size are populated (not left at the model's own None
    default) because a resolved chart's mark is always cascade-complete in
    production — a quantitative-x bar with an unauthored size reads them
    unconditionally (``continuous_bar_size_prop``).
    """
    from dbt_charts.core.compile.models.style.theme import BarMarkStyle

    ax, ay = _bake_axes("bar", "ordinal", "quantitative")
    return ResolvedBarStyle(
        series_label=_series_label(),
        mark=BarMarkStyle(gap=3.0, min_size=4.0, max_size=20.0),
        endpoint_labels=_endpoint_labels(),
        single_series_fill=SINGLE_SERIES_FILL,
        tooltip_format="",
        label_font_size=11.0,
        label_usable_ratio=0.8,
        axis_x=ax,
        axis_y=ay,
    )


@pytest.fixture
def line_style() -> ResolvedLineStyle:
    """Minimal valid ResolvedLineStyle with all required fields populated."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedLineMarkStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )

    ax, ay = _bake_axes("line", "temporal", "quantitative")
    return ResolvedLineStyle(
        series_label=_series_label(),
        line_mark=ResolvedLineMarkStyle(
            stroke=ResolvedStrokeStyle(width=2.0),
            halo_multiplier=2.0,
            curve=None,
            labels=PointLabelsStyle(),
        ),
        point_mark=PointMarkStyle(),
        endpoint_labels=_endpoint_labels(),
        single_series_fill=SINGLE_SERIES_FILL,
        tooltip_format="",
        label_usable_ratio=0.8,
        dashes=[],
        axis_x=ax,
        axis_y=ay,
    )


@pytest.fixture
def area_style() -> ResolvedAreaStyle:
    """Minimal valid ResolvedAreaStyle with all required fields populated."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedAreaLineStyle,
        ResolvedAreaMarkStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )

    ax, ay = _bake_axes("area", "temporal", "quantitative")
    return ResolvedAreaStyle(
        series_label=_series_label(),
        area_mark=ResolvedAreaMarkStyle(opacity=0.15, backdrop=True),
        line_mark=ResolvedAreaLineStyle(
            stroke=ResolvedStrokeStyle(width=2.0),
            halo_multiplier=2.0,
            labels=PointLabelsStyle(),
        ),
        point_mark=PointMarkStyle(),
        endpoint_labels=_endpoint_labels(),
        single_series_fill=SINGLE_SERIES_FILL,
        tooltip_format="",
        label_usable_ratio=0.8,
        dashes=[],
        axis_x=ax,
        axis_y=ay,
    )


@pytest.fixture
def scatter_style() -> ResolvedScatterStyle:
    """Minimal valid ResolvedScatterStyle for test injection."""
    from dbt_charts.core.compile.models.style.theme import PointMarkStyle

    ax, ay = _bake_axes("scatter", "quantitative", "quantitative")
    return ResolvedScatterStyle(
        point_mark=PointMarkStyle(),
        single_series_fill=SINGLE_SERIES_FILL,
        tooltip_format="",
        label_usable_ratio=0.8,
        axis_x=ax,
        axis_y=ay,
    )


@pytest.fixture
def heatmap_style() -> ResolvedHeatmapStyle:
    """Minimal valid ResolvedHeatmapStyle for test injection."""
    from dbt_charts.core.compile.models.style.theme import RectMarkStyle

    ax, ay = _bake_axes("heatmap", "nominal", "nominal")
    return ResolvedHeatmapStyle(
        color_gradient=None,
        rect_mark=RectMarkStyle(),
        tooltip_format="",
        label_usable_ratio=0.8,
        axis_x=ax,
        axis_y=ay,
    )


@pytest.fixture
def pie_style() -> ResolvedPieStyle:
    """Minimal valid ResolvedPieStyle — built from the cascade (avoids pinning theme values)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.format import resolve_format
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    rcs = resolve_chart_style_context(get_theme_style())
    slice_mark = rcs.pie.marks.slice
    labels = slice_mark.labels
    assert labels is not None
    slice_mark = slice_mark.model_copy(
        update={
            "labels": labels.model_copy(
                update={"template": labels.default_template.no_color}
            )
        }
    )
    return ResolvedPieStyle(
        inner_radius=0.0,
        slice_mark=slice_mark,
        tooltip_format=resolve_format(rcs.tooltip.format, rcs.formats),
        total_style=rcs.pie.total,
    )


@pytest.fixture
def table_style() -> ResolvedTableStyle:
    """Minimal valid ResolvedTableStyle — built from the cascade (avoids pinning theme values)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.resolve.style.typography import resolve_title_font

    rcs = resolve_chart_style_context(get_theme_style())
    return ResolvedTableStyle(
        table=rcs.table,
        title=rcs.title,
        formats=rcs.formats,
        title_font=resolve_title_font(rcs, width=600.0),
        pagination=rcs.pagination,
    )


@pytest.fixture
def geoshape_style() -> ResolvedGeoshapeStyle:
    """Minimal valid ResolvedGeoshapeStyle for test injection."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.format import resolve_format
    from dbt_charts.core.compile.resolve.chart.geo import (  # noqa: PLC0415
        _with_baked_color_gradient,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    rcs = resolve_chart_style_context(get_theme_style())
    # _with_baked_color_gradient mirrors the production construction path and bakes
    # any named palette gradient — plain model_validate(exclude_none) would yield
    # ResolvedScaleTargetConfig (unbaked) instead of ResolvedNamedPaletteScaleTargetConfig.
    resolved_geoshape = _with_baked_color_gradient(rcs.geoshape)
    return ResolvedGeoshapeStyle(
        geoshape=resolved_geoshape,
        scatter=rcs.scatter,
        tooltip_format=resolve_format(rcs.tooltip.format, rcs.formats),
    )


@pytest.fixture
def point_map_style() -> ResolvedPointMapStyle:
    """Minimal valid ResolvedPointMapStyle for test injection."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.format import resolve_format
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    rcs = resolve_chart_style_context(get_theme_style())
    return ResolvedPointMapStyle(
        point_map=rcs.point_map,
        point_mark=rcs.point_map.marks.point,
        tooltip_format=resolve_format(rcs.tooltip.format, rcs.formats),
        single_series_fill=rcs.single_series_palette[0],
    )


def chart_pane(spec: dict[str, Any]) -> dict[str, Any]:
    """The chart pane of a spec that endpoint labels may have wrapped.

    Vertical stacked bars/lines/areas wrap in ``hconcat`` with the chart at [0];
    horizontal stacked bars wrap in ``vconcat`` with the rail at [0] and the
    chart at [1]. Unwrapped specs pass through.
    """
    if "hconcat" in spec:
        return spec["hconcat"][0]
    if "vconcat" in spec:
        return spec["vconcat"][1]
    return spec
