"""Scope guards for an authored FormatConfig axis affix."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import (
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYLabelStylePatch,
    AxisYStylePatch,
    DimensionLabelStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._axes import (
    AxisOverrides,
    _bake_cartesian_axes,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    build_resolved_axis,
    resolved_axis_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
)

from .conftest import fixture_chart_for_type


def _bake_ay_with_format(chart_type: str, y_channel_type: str, fmt: FormatConfig):
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type(chart_type)
    overrides = AxisOverrides(
        y=AxisYStylePatch(labels=AxisYLabelStylePatch(format=fmt))
    )
    return _bake_cartesian_axes(
        ctx, chart, chart_type, "nominal", y_channel_type, overrides, False, False
    )


def _bake_ay_with_format_and_expr(chart_type: str, fmt: str | FormatConfig, expr: str):
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type(chart_type)
    overrides = AxisOverrides(
        y=AxisYStylePatch(labels=AxisYLabelStylePatch(format=fmt, expr=expr))
    )
    return _bake_cartesian_axes(
        ctx, chart, chart_type, "nominal", "quantitative", overrides, False, False
    )


def test_quantitative_channel_extracts_the_authored_affix():
    """Sanity check: the happy path this feature exists for still works."""
    baked = _bake_ay_with_format(
        "bar", "quantitative", FormatConfig(spec=",.0f", prefix="€")
    )
    assert (baked.y.format.prefix, baked.y.format.suffix) == ("€", "")


def test_temporal_channel_drops_the_affix_extraction():
    """A strftime spec is not a d3 number spec; compose_axis_format must
    never see a temporal axis's affix (it would hand "%b %Y" to Vega's
    number formatter and blank the whole chart)."""
    baked = _bake_ay_with_format(
        "bar", "temporal", FormatConfig(spec="%b %Y", prefix="FY ")
    )
    assert (baked.y.format.prefix, baked.y.format.suffix) == ("", "")


def test_nominal_channel_rejects_the_authored_affix():
    """A categorical axis's format is judged by the engine's label-format gate at render (reads the
    plain `format` key).
    """
    with pytest.raises(CompilationError) as exc_info:
        _bake_ay_with_format("bar", "nominal", FormatConfig(spec=",.0f", prefix="€"))
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NOMINAL-AXIS-UNSUPPORTED"


def test_ordinal_channel_rejects_the_authored_affix():
    """Same rejection for the ordinal half of the categorical pair."""
    with pytest.raises(CompilationError) as exc_info:
        _bake_ay_with_format("bar", "ordinal", FormatConfig(spec=",.0f", suffix=" pts"))
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NOMINAL-AXIS-UNSUPPORTED"


_EMPTY_SCATTER = ScatterChart(
    id="s",
    type="scatter",
    query=SqlQuery(sql="SELECT 1", source="t"),
    query_name="q",
    x="sold_date",
    y="price",
    style=ScatterChartStylePatch.model_validate(
        {"axis_y": {"labels": {"format": "currency_compact"}}}
    ),
)
_EMPTY_LINE = LineChart(
    id="l",
    type="line",
    query=SqlQuery(sql="SELECT 1", source="t"),
    query_name="q",
    x="n",
    y="price",
    style=LineChartStylePatch.model_validate(
        {"axis_x": {"labels": {"format": "compact"}}}
    ),
)


@pytest.mark.parametrize(
    ("chart", "rows"),
    [
        pytest.param(_EMPTY_SCATTER, [], id="scatter_axis_y_no_rows"),
        pytest.param(_EMPTY_LINE, [], id="line_axis_x_no_rows"),
        pytest.param(
            _EMPTY_SCATTER,
            [{"sold_date": date(2024, 1, 1), "price": None}],
            id="scatter_axis_y_all_null",
        ),
        pytest.param(
            _EMPTY_LINE,
            [{"n": None, "price": 1.0}],
            id="line_axis_x_all_null",
        ),
    ],
)
def test_unclassified_channel_does_not_reject_the_affix(chart, rows):
    """A column with no non-null value has nothing to classify, so its nominal
    default is not evidence of a categorical axis, and the axis paints no
    tick labels to protect."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolve(chart, rows, ctx)


_AUTHORED_NOMINAL_LINE = LineChart(
    id="l",
    type="line",
    query=SqlQuery(sql="SELECT 1", source="t"),
    query_name="q",
    x="n",
    y="price",
    style=LineChartStylePatch.model_validate(
        {"axis_x": {"type": "nominal", "labels": {"format": "compact"}}}
    ),
)
_HEATMAP = HeatmapChart(
    id="h",
    type="heatmap",
    query=SqlQuery(sql="SELECT 1", source="t"),
    query_name="q",
    x="n",
    y="price",
    style=HeatmapChartStylePatch.model_validate(
        {"axis_x": {"labels": {"format": "compact"}}}
    ),
)


@pytest.mark.parametrize(
    ("chart", "rows"),
    [
        pytest.param(_AUTHORED_NOMINAL_LINE, [], id="authored_nominal_no_rows"),
        pytest.param(
            _AUTHORED_NOMINAL_LINE,
            [{"n": None, "price": 1.0}],
            id="authored_nominal_all_null",
        ),
        pytest.param(_HEATMAP, [], id="heatmap_no_rows"),
        pytest.param(_HEATMAP, [{"n": None, "price": 1.0}], id="heatmap_all_null"),
    ],
)
def test_declared_nominal_channel_rejects_the_affix_without_values(chart, rows):
    """A nominal channel the author or the family declares is not a classifier
    default: it rejects the affix whether or not the column holds values, so a
    board that passes against an empty warehouse cannot fail once data
    arrives."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, rows, ctx)
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NOMINAL-AXIS-UNSUPPORTED"


def test_nominal_x_channel_rejects_the_authored_affix():
    """The x-axis half of the same rejection: axis_x.labels.format authored
    with an affix while the x channel resolves nominal."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type("bar")
    overrides = AxisOverrides(
        x=AxisXStylePatch(
            labels=DimensionLabelStylePatch(
                format=FormatConfig(spec=",.0f", prefix="Step ")
            )
        )
    )
    with pytest.raises(CompilationError) as exc_info:
        _bake_cartesian_axes(
            ctx, chart, "bar", "nominal", "quantitative", overrides, False, False
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NOMINAL-AXIS-UNSUPPORTED"


def test_house_alias_ruler_suppresses_the_authored_affix_override():
    """A house-rule alias (SI-shaped, e.g. "currency") already carries its own currency-
    symbol mechanism through ResolvedRulerAxis/.ResolvedTickLabel.
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type("bar")
    overrides = AxisOverrides(
        y=AxisYStylePatch(
            labels=AxisYLabelStylePatch(
                format=FormatConfig(spec="currency", prefix="€", notation="narrative")
            )
        )
    )
    baked = _bake_cartesian_axes(
        ctx, chart, "bar", "nominal", "quantitative", overrides, False, False
    )
    resolved = build_resolved_axis(
        baked.y.style,
        format=baked.y.format,
        band_position=baked.y.band_position,
        edge="left",
        tick_values=(0.0, 100_000.0, 200_000.0, 300_000.0, 400_000.0, 500_000.0),
        is_quantitative=True,
        chart_id="test",
        formats=None,
    )
    assert resolved.ruler is not None, "test requires a compacting house-rule ladder"
    # The affix stays on labels.format; axis_to_vl skips compose_axis_format
    # for a ladder, which composes it itself.
    assert resolved.labels.format.prefix == "€"
    assert resolved.ruler.register == "narrative"


def test_authored_affix_persists_on_labels_format_alongside_a_ladder():
    """ResolvedAxisStyle.authored_affix is what a value label / stack
    total's format-inheritance path reads to find the axis's own authored
    affix. build_resolved_axis blanks ResolvedAxisElementStyle.
    prefix/.suffix/.notation once a real ladder forms (to avoid the axis's
    OWN double-paint against the ladder's composition) -- so the property
    must follow the affix to ResolvedRulerAxis.authored_prefix/.suffix/
    .notation instead of reading the now-blanked label fields, or an
    inheriting value label silently loses the currency the axis right next
    to it carries."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type("bar")
    overrides = AxisOverrides(
        y=AxisYStylePatch(
            labels=AxisYLabelStylePatch(
                format=FormatConfig(spec=".3~s", prefix="€", notation="narrative")
            )
        )
    )
    baked = _bake_cartesian_axes(
        ctx, chart, "bar", "nominal", "quantitative", overrides, False, False
    )
    resolved = build_resolved_axis(
        baked.y.style,
        format=baked.y.format,
        band_position=baked.y.band_position,
        edge="left",
        tick_values=(0.0, 100_000.0, 200_000.0, 300_000.0, 400_000.0, 500_000.0),
        is_quantitative=True,
        chart_id="test",
        formats=None,
    )
    assert resolved.ruler is not None, "test requires a real compacting ladder"
    assert resolved.labels.format.prefix == "€"
    assert resolved.labels.format.notation == "narrative"
    assert resolved.ruler.register == "narrative"


def test_resolved_axis_style_wrapper_does_not_crash_on_a_formatconfig():
    """resolved_axis_style()'s own wrapper (used by compile/support_table.py's
    axis_offset() for board-level axis geometry) never resolves an authored
    FormatConfig to a bare spec the way _bake_cartesian_axes does -- it must
    not crash when is_d3_si_spec() reaches a FormatConfig instead of a str."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    ctx = dataclasses.replace(
        ctx,
        axis=ctx.axis.model_copy(
            update={
                "labels": ctx.axis.labels.model_copy(
                    update={"format": FormatConfig(spec=",.0f", prefix="EUR ")}
                )
            }
        ),
    )
    resolved = resolved_axis_style(
        ctx, "axis_x", "band", chart_type="bar", labeled=False
    )
    assert resolved.labels.padding is not None


def test_labels_expr_with_a_plain_spec_and_no_affix_still_resolves():
    """The precedence `compose_axis_format` documents."""
    baked = _bake_ay_with_format_and_expr("bar", ",.0f", "datum.label + ' /mo'")
    assert baked.y.style.labels.expr == "datum.label + ' /mo'"
    assert baked.y.style.labels.format == ",.0f"
    assert (baked.y.format.prefix, baked.y.format.suffix) == ("", "")


def _resolved_ay_axis(
    ctx, chart_type: str, fmt: str | FormatConfig, tick_values: tuple[float, ...]
):
    chart = fixture_chart_for_type(chart_type)
    overrides = AxisOverrides(
        y=AxisYStylePatch(labels=AxisYLabelStylePatch(format=fmt))
    )
    baked = _bake_cartesian_axes(
        ctx, chart, chart_type, "nominal", "quantitative", overrides, False, False
    )
    return build_resolved_axis(
        baked.y.style,
        format=baked.y.format,
        band_position=baked.y.band_position,
        edge="left",
        tick_values=tick_values,
        is_quantitative=True,
        chart_id="test",
        formats=None,
    )


def test_alias_spec_less_affix_takes_the_same_non_compacting_ladder_as_inline():
    """A style.formats alias naming a spec-less affix (``eur."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    ctx = dataclasses.replace(ctx, formats={"eur": FormatConfig(prefix="EUR ")})
    tick_values = (0.0, 0.18, 0.42, 0.95)

    alias_resolved = _resolved_ay_axis(ctx, "bar", "eur", tick_values)
    inline_resolved = _resolved_ay_axis(
        ctx, "bar", FormatConfig(prefix="EUR "), tick_values
    )

    assert alias_resolved.ruler is None, (
        "alias spelling must not fall through to the compacting SI ladder"
    )
    assert alias_resolved.tick_label is not None
    assert (alias_resolved.ruler is None) == (inline_resolved.ruler is None)
    assert (alias_resolved.tick_label is None) == (inline_resolved.tick_label is None)
