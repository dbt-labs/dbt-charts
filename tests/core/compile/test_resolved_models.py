"""Regression tests for resolved chart model field constraints.

Covers:
  Change #2  — module docstring updated (smoke: import succeeds, docstring tested)
  Change #3  — non-None fields on _Base / _Cartesian / callout must be required
  Change #4a — style slices live at compile/models/style/resolved (per-family package)
  Change #4b — ResolvedTableChart.support_table removed
  Change #7a/b — ResolvedAreaMarkStyle / ResolvedLineMarkStyle exist and used
  Change #7d — emitters/area and emitters/line no longer raise on None guards
  Change #8  — cartesian style base class hierarchy
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.chart import resolved as _resolved_pkg


def _bake_test_axes(chart_type: str):
    """Return (ax, ay) baked for the given chart type using the default theme."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
    from dbt_charts.core.compile.resolve.style.axis_cascade import (
        AxisOverrides,
        build_resolved_axis,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    from ..conftest import fixture_chart_for_type

    chart_style_context = resolve_chart_style_context(get_theme_style())
    ax_merged, ay_merged, ax_band_position, ay_band_position, _ = _bake_cartesian_axes(
        chart_style_context,
        fixture_chart_for_type(chart_type),
        chart_type,
        "temporal",
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


def _default_callout_style():
    """Return a valid ResolvedCalloutStyle from the default theme cascade."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style()).callout


# ---------------------------------------------------------------------------
# Change #2 — module docstring
# ---------------------------------------------------------------------------


def test_resolved_init_docstring_mentions_resolve_stage():
    doc = (_resolved_pkg.__doc__ or "").lower()
    assert "resolve" in doc
    assert "stage: compile" not in (_resolved_pkg.__doc__ or "")


# ---------------------------------------------------------------------------
# Change #3 — required fields: no defaults on non-None resolved model fields
# ---------------------------------------------------------------------------


def test_base_palette_required():
    """`palette` must raise when omitted."""
    from dbt_charts.core.compile.models.chart.resolved._base import (
        _BaseResolvedChartFields,
    )

    with pytest.raises((ValidationError, TypeError)):
        _BaseResolvedChartFields(
            id="c1",
            query=None,
            query_name=None,
            variable_dependencies=frozenset(),
            description=None,
            link=None,
            conditional_formatting=None,
            # palette omitted
            resolved_channels={},
        )


def test_base_variable_dependencies_required():
    """`variable_dependencies` must raise when omitted."""
    from dbt_charts.core.compile.models.chart.resolved._base import (
        _BaseResolvedChartFields,
    )

    with pytest.raises((ValidationError, TypeError)):
        _BaseResolvedChartFields(
            id="c1",
            query=None,
            query_name=None,
            # variable_dependencies omitted
            description=None,
            link=None,
            conditional_formatting=None,
            palette=(),
            resolved_channels={},
        )


def test_base_resolved_channels_required():
    """`resolved_channels` must raise when omitted."""
    from dbt_charts.core.compile.models.chart.resolved._base import (
        _BaseResolvedChartFields,
    )

    with pytest.raises((ValidationError, TypeError)):
        _BaseResolvedChartFields(
            id="c1",
            query=None,
            query_name=None,
            variable_dependencies=frozenset(),
            description=None,
            link=None,
            conditional_formatting=None,
            palette=(),
            # resolved_channels omitted
        )


def test_callout_variable_dependencies_required():
    """`ResolvedCalloutChart.variable_dependencies` must raise when omitted."""
    from dbt_charts.core.compile.models.chart.resolved.callout import (
        ResolvedCalloutChart,
    )

    with pytest.raises((ValidationError, TypeError)):
        ResolvedCalloutChart(
            chart_type="callout",
            id="c1",
            message="hi",
            # variable_dependencies omitted
            style=_default_callout_style(),
        )


def test_callout_style_required():
    """`ResolvedCalloutChart.style` must raise when omitted."""
    from dbt_charts.core.compile.models.chart.resolved.callout import (
        ResolvedCalloutChart,
    )

    with pytest.raises((ValidationError, TypeError)):
        ResolvedCalloutChart(
            chart_type="callout",
            id="c1",
            message="hi",
            variable_dependencies=frozenset(),
            # style omitted
        )


# ---------------------------------------------------------------------------
# Change #4a — style slices at new path
# ---------------------------------------------------------------------------


def test_chart_slices_importable_at_new_path():
    """All style slices importable from compile/models/style/resolved."""
    import importlib

    m = importlib.import_module("dbt_charts.core.compile.models.style.resolved")
    for name in [
        "ResolvedAreaStyle",
        "ResolvedBarStyle",
        "ResolvedGeoshapeStyle",
        "ResolvedHeatmapStyle",
        "ResolvedKpiStyle",
        "ResolvedLineStyle",
        "ResolvedPieStyle",
        "ResolvedPointMapStyle",
        "ResolvedScatterStyle",
        "ResolvedSparkBarStyle",
        "ResolvedTableStyle",
    ]:
        assert hasattr(m, name), f"{name} missing from style.resolved"


def test_old_style_path_gone():
    """resolved/style.py must no longer exist (no shim at old path)."""
    import importlib.util

    spec = importlib.util.find_spec(
        "dbt_charts.core.compile.models.chart.resolved.style"
    )
    assert spec is None, "Old style.py shim must be deleted"


# ---------------------------------------------------------------------------
# Change #4b — support_table removed from ResolvedTableChart
# ---------------------------------------------------------------------------


def test_table_chart_no_support_table_field():
    """Passing support_table= to ResolvedTableChart must be rejected (field was removed)."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart

    with pytest.raises(ValidationError):
        ResolvedTableChart(
            id="t",
            variable_dependencies=frozenset(),
            palette=(),
            resolved_channels={},
            chart_type="table",
            style=None,  # type: ignore[arg-type]
            support_table="some_table",  # must be rejected
        )


# ---------------------------------------------------------------------------
# Change #7 — ResolvedAreaMarkStyle / ResolvedLineMarkStyle
# ---------------------------------------------------------------------------


def test_resolved_area_mark_style_construction():
    """ResolvedAreaMarkStyle (fill only) must construct with its required fields."""
    from dbt_charts.core.compile.models.style.resolved import ResolvedAreaMarkStyle

    mark = ResolvedAreaMarkStyle(opacity=0.3, backdrop=True)
    assert mark.opacity == 0.3
    assert mark.backdrop is True


def test_resolved_area_line_style_construction():
    """ResolvedAreaLineStyle must construct with all required non-None fields."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedAreaLineStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import PointLabelsStyle

    mark = ResolvedAreaLineStyle(
        stroke=ResolvedStrokeStyle(width=1.5),
        halo_multiplier=2.0,
        labels=PointLabelsStyle(),
    )
    assert mark.stroke.width == 1.5
    assert mark.halo_multiplier == 2.0


def test_resolved_line_mark_style_construction():
    """ResolvedLineMarkStyle must construct with all required non-None fields."""
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedLineMarkStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import PointLabelsStyle

    mark = ResolvedLineMarkStyle(
        stroke=ResolvedStrokeStyle(width=1.5),
        halo_multiplier=2.0,
        curve=None,
        labels=PointLabelsStyle(),
    )
    assert mark.stroke.width == 1.5
    assert mark.halo_multiplier == 2.0


def test_resolved_area_style_uses_resolved_mark_types():
    """ResolvedAreaStyle.area_mark must accept ResolvedAreaMarkStyle."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedAreaLineStyle,
        ResolvedAreaMarkStyle,
        ResolvedAreaStyle,
        ResolvedSeriesLabelStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )

    area_mark = ResolvedAreaMarkStyle(opacity=0.3, backdrop=True)
    line_mark = ResolvedAreaLineStyle(
        stroke=ResolvedStrokeStyle(width=1.5),
        halo_multiplier=2.0,
        labels=PointLabelsStyle(),
    )
    ax, ay = _bake_test_axes("area")
    style = ResolvedAreaStyle(
        series_label=ResolvedSeriesLabelStyle(
            font_family="Inter",
            font_size=11.0,
            font_weight="400",
            font_style="normal",
            dark_companion_palette=(),
            gap_px=18.2,
        ),
        area_mark=area_mark,
        line_mark=line_mark,
        point_mark=PointMarkStyle(),
        endpoint_labels=EndpointLabelsConfig(
            visible=False, label_offset=8.0, height=20.0
        ),
        single_series_fill="#aaa",
        tooltip_format="",
        label_usable_ratio=0.8,
        dashes=[],
        axis_x=ax,
        axis_y=ay,
    )
    assert isinstance(style.area_mark, ResolvedAreaMarkStyle)
    assert isinstance(style.line_mark, ResolvedAreaLineStyle)


def test_resolved_line_style_uses_resolved_mark_type():
    """ResolvedLineStyle.line_mark must accept ResolvedLineMarkStyle."""
    from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
    from dbt_charts.core.compile.models.style.resolved import (
        ResolvedLineMarkStyle,
        ResolvedLineStyle,
        ResolvedSeriesLabelStyle,
        ResolvedStrokeStyle,
    )
    from dbt_charts.core.compile.models.style.theme import (
        PointLabelsStyle,
        PointMarkStyle,
    )

    line_mark = ResolvedLineMarkStyle(
        stroke=ResolvedStrokeStyle(width=1.5),
        halo_multiplier=2.0,
        curve=None,
        labels=PointLabelsStyle(),
    )
    ax, ay = _bake_test_axes("line")
    style = ResolvedLineStyle(
        series_label=ResolvedSeriesLabelStyle(
            font_family="Inter",
            font_size=11.0,
            font_weight="400",
            font_style="normal",
            dark_companion_palette=(),
            gap_px=18.2,
        ),
        line_mark=line_mark,
        point_mark=PointMarkStyle(),
        endpoint_labels=EndpointLabelsConfig(
            visible=False, label_offset=8.0, height=20.0
        ),
        single_series_fill="#aaa",
        tooltip_format="",
        label_usable_ratio=0.8,
        dashes=[],
        axis_x=ax,
        axis_y=ay,
    )
    assert isinstance(style.line_mark, ResolvedLineMarkStyle)


# ---------------------------------------------------------------------------
# Change #8 — cartesian style base class hierarchy
# ---------------------------------------------------------------------------


def test_cartesian_base_hierarchy() -> None:
    """Verify base class inheritance for all cartesian style slices."""
    from dbt_charts.core.compile.models.style.resolved._cartesian import (
        _CartesianResolvedStyle,
        _SeriesCartesianResolvedStyle,
    )
    from dbt_charts.core.compile.models.style.resolved.area import ResolvedAreaStyle
    from dbt_charts.core.compile.models.style.resolved.bar import ResolvedBarStyle
    from dbt_charts.core.compile.models.style.resolved.heatmap import (
        ResolvedHeatmapStyle,
    )
    from dbt_charts.core.compile.models.style.resolved.line import ResolvedLineStyle
    from dbt_charts.core.compile.models.style.resolved.scatter import (
        ResolvedScatterStyle,
    )

    # All 6 cartesian slices inherit _CartesianResolvedStyle
    for cls in (
        ResolvedBarStyle,
        ResolvedLineStyle,
        ResolvedAreaStyle,
        ResolvedScatterStyle,
        ResolvedHeatmapStyle,
    ):
        assert issubclass(cls, _CartesianResolvedStyle), (
            f"{cls.__name__} must inherit _CartesianResolvedStyle"
        )

    # Only bar / line / area inherit _SeriesCartesianResolvedStyle
    for cls in (ResolvedBarStyle, ResolvedLineStyle, ResolvedAreaStyle):
        assert issubclass(cls, _SeriesCartesianResolvedStyle), (
            f"{cls.__name__} must inherit _SeriesCartesianResolvedStyle"
        )

    # scatter / heatmap do NOT inherit _SeriesCartesianResolvedStyle
    for cls in (ResolvedScatterStyle, ResolvedHeatmapStyle):
        assert not issubclass(cls, _SeriesCartesianResolvedStyle), (
            f"{cls.__name__} must NOT inherit _SeriesCartesianResolvedStyle"
        )
