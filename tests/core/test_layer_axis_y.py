"""Tests for per-layer y-axis chrome extensions (defects #11, #12, #16).

#11 — LayerAxisYStyle extended with scale.domain, ticks.count, grid.visible,
      labels.format; each propagates through resolved → render VL output.
#12 — chart-level axis_y.ticks.count / format reach BOTH dual y-axes.
#16 — chart-level axis_y.scale.domain with split-side dual-axis raises
      ERR-LAYERS-AMBIGUOUS-Y-DOMAIN at compile (resolve) time.
"""

from __future__ import annotations

import pytest

# ── helpers (mirror test_layers_on_cartesian.py setup) ───────────────────


def _default_board_style():  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _sql(sql: str = "SELECT 1"):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    return SqlQuery(sql=sql, source="t")


def _bar_normalized(**kwargs):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart

    defaults = {
        "id": "bar1",
        "type": "bar",
        "x": "month",
        "y": "revenue",
        "query": _sql(),
        "query_name": "q",
        "variable_dependencies": set(),
    }
    defaults.update(kwargs)
    return NBarChart(**defaults)


_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "target": 4000.0},
    {"month": "Feb", "revenue": 200.0, "target": 5000.0},
    {"month": "Mar", "revenue": 150.0, "target": 4500.0},
]


# ── #11: LayerAxisYStyle authoring accepts new fields ────────────────────


def test_layer_axis_y_scale_domain_parses() -> None:
    """axis_y.scale.domain on a layer parses as a list of floats."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"scale": {"domain": [0, 5000]}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.scale is not None
    assert layer.axis_y.scale.domain == [0.0, 5000.0]


def test_layer_axis_y_ticks_count_parses() -> None:
    """axis_y.ticks.count on a layer parses as int."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"ticks": {"count": 4}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.ticks is not None
    assert layer.axis_y.ticks.count == 4


def test_layer_axis_y_grid_visible_parses() -> None:
    """axis_y.grid.visible on a layer parses as bool."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"grid": {"visible": False}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.grid is not None
    assert layer.axis_y.grid.visible is False


def test_layer_axis_y_label_format_parses() -> None:
    """axis_y.labels.format on a layer parses as a string."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer

    layer = LineLayer.model_validate(
        {"type": "line", "y": "target", "axis_y": {"labels": {"format": "$,.0f"}}}
    )
    assert layer.axis_y is not None
    assert layer.axis_y.labels is not None
    assert layer.axis_y.labels.format == "$,.0f"


# ── #11: per-layer fields survive through resolve ─────────────────────────


def test_layer_axis_y_scale_domain_survives_resolve() -> None:
    """axis_y.scale.domain on a layer survives _resolve_one_layer unchanged."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(
        type="line", y="target", axis_y={"scale": {"domain": [0.0, 5000.0]}}
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _DATA, _default_board_style())
    rl = resolved.layers[0]
    assert isinstance(rl, ResolvedLineLayer)
    assert rl.axis_y.scale is not None
    assert rl.axis_y.scale.domain == [0.0, 5000.0]


def test_layer_axis_y_ticks_count_survives_resolve() -> None:
    """axis_y.ticks.count on a layer survives _resolve_one_layer unchanged."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target", axis_y={"ticks": {"count": 3}})
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _DATA, _default_board_style())
    rl = resolved.layers[0]
    assert isinstance(rl, ResolvedLineLayer)
    assert rl.axis_y.ticks is not None
    assert rl.axis_y.ticks.count == 3


# ── #11: per-layer fields reach VL output ────────────────────────────────


# ── #12: chart-level tick count reaches both dual y-axes ─────────────────


def _bar_with_right_layer_and_style(**style_kwargs):  # type: ignore[no-untyped-def]
    """NBarChart with a right-pinned line overlay and chart-level style overrides."""
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.style.authored import BarChartStylePatch

    return NBarChart(
        id="bar1",
        type="bar",
        x="month",
        y="revenue",
        query=_sql(),
        query_name="q",
        variable_dependencies=set(),
        layers=[LineLayer(type="line", y="target", axis_y={"position": "right"})],
        style=BarChartStylePatch.model_validate(style_kwargs) if style_kwargs else None,
    )


# ── #16: ambiguous chart-level domain on split-scale dual-axis ────────────


def test_chart_level_domain_with_split_dual_axis_raises_error() -> None:
    """axis_y.scale.domain set on a chart with independent dual y-axes must raise.

    Error code: ERR-LAYERS-AMBIGUOUS-Y-DOMAIN — caught at resolve time.
    """
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve import resolve

    # left base + right overlay → independent y scales
    chart = _bar_with_right_layer_and_style(
        axis_y={"scale": {"continuous": {"domain": [0, 300]}}}
    )

    with pytest.raises(CompilationError) as exc_info:
        resolve(chart, _DATA, _default_board_style())
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LAYERS-AMBIGUOUS-Y-DOMAIN"
    assert "sets axis_y.scale.continuous.domain=[0, 300]" in str(exc_info.value)


# ── Dual-axis base orientation ────────────────────────────────────────────
#
# Regression: a right-pinned overlay on a base whose theme-default y-axis is
# ALSO "right" (e.g. stark) must push the base to the opposite (left) side, not
# collide both series on the right. The overlay pins the base to the free side
# rather than assuming the theme default is "left".


# ── Stale labelPadding after base orient repin ────────────────────────────
#
# Regression: when a layer pins its y-axis to "right", the overlay emitter
# re-pins the base to "left" (line 663 of _overlay.py). Before the fix,
# labelPadding and labelAlign computed for the original right-side (by the
# house-format force) survived the repin as stale values on the left-side base.


# ── a layer's sub-unit axis guard is baked at resolve ────────────────────

_SUB_UNIT_DATA: list[dict] = [
    {"month": "Jan", "revenue": 100.0, "target": 0.2},
    {"month": "Feb", "revenue": 200.0, "target": 0.7},
    {"month": "Mar", "revenue": 150.0, "target": 0.4},
]


def _resolve_layer(layer_axis_y: dict, data: list[dict] = _SUB_UNIT_DATA):  # type: ignore[no-untyped-def]
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(type="line", y="target", axis_y=layer_axis_y)
    chart = _bar_normalized(layers=[layer])
    return resolve(chart, data, _default_board_style()).layers[0]


def test_layer_spec_less_affix_resolves_the_guarded_tick_label_and_tooltip() -> None:
    """A spec-less affix on a layer axis resolves the per-tick guarded label."""
    from dbt_charts.core.text.numeral_scale import sub_unit_digit_format
    from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

    resolved_layer = _resolve_layer(
        {"position": "right", "labels": {"format": {"prefix": "EUR "}}}
    )
    guarded = sub_unit_digit_format(".3~s")
    assert resolved_layer.axis_y.tick_label is not None
    assert resolved_layer.axis_y.tick_label.format == guarded
    assert resolved_layer.axis_y.tick_label.si_format == ".3~s"
    assert resolved_layer.axis_y.labels.format.prefix == "EUR "
    tooltip = resolved_layer.axis_y.tooltip_format
    assert (tooltip.spec, tooltip.prefix) == (
        PREDEFINED_SPECS["number_full"].d3,
        "EUR ",
    )


def test_layer_alias_to_a_preset_resolves_the_guarded_tick_label() -> None:
    """An alias naming a preset keeps the preset's sub-unit guard."""
    import dataclasses

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve

    ctx = dataclasses.replace(_default_board_style(), formats={"mine": "number"})
    layer = LineLayer(
        type="line",
        y="target",
        axis_y={"position": "right", "labels": {"format": "mine"}},
    )
    resolved = resolve(_bar_normalized(layers=[layer]), _SUB_UNIT_DATA, ctx)
    assert resolved.layers[0].axis_y.tick_label is not None


def test_layer_literal_si_spec_resolves_no_guard() -> None:
    resolved_layer = _resolve_layer(
        {
            "position": "right",
            "labels": {"format": {"spec": ".3~s", "prefix": "EUR "}},
        }
    )
    assert resolved_layer.axis_y.tick_label is None
    assert resolved_layer.axis_y.tooltip_format is None


def test_layer_bare_preset_above_one_keeps_the_tooltip_unguarded() -> None:
    """Values of 1 or more compact under the preset in the tooltip, as on the base
    chart.
    """
    large = [{**row, "target": row["target"] * 1e7} for row in _SUB_UNIT_DATA]
    resolved_layer = _resolve_layer(
        {"position": "right", "labels": {"format": "currency"}}, large
    )
    assert resolved_layer.axis_y.tick_label is not None
    assert resolved_layer.axis_y.tooltip_format is None


def test_layer_tooltip_with_mixed_magnitude_data_takes_the_base_chart_vote() -> None:
    """A layer tooltip votes like every base-chart tooltip."""
    mixed = [
        {**row, "target": value}
        for row, value in zip(_SUB_UNIT_DATA, [0.5, 1234.5, 2456789.0], strict=True)
    ]
    resolved_layer = _resolve_layer(
        {"position": "right", "labels": {"format": "currency"}}, mixed
    )
    assert resolved_layer.axis_y.tooltip_format.spec == "$,.2f"


def test_layer_with_an_own_query_missing_from_datasets_raises() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    layer = LineLayer(
        type="line",
        y="target",
        query="goals",
        axis_y={"position": "right", "labels": {"format": "currency"}},
    )
    chart = _bar_normalized(layers=[layer])
    with pytest.raises(ChartDataError, match="goals"):
        resolve(chart, _DATA, _default_board_style(), datasets={"other": []})


def test_unformatted_right_axis_layer_query_absent_from_datasets_resolves() -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve

    layer = LineLayer(
        type="line", y="target", query="goals", axis_y={"position": "right"}
    )
    chart = _bar_normalized(layers=[layer])
    resolved = resolve(chart, _DATA, _default_board_style(), datasets={"q": _DATA})
    assert resolved.layers[0].query_name == "goals"


def test_right_axis_layer_votes_from_its_own_query_at_runtime() -> None:
    """A right-axis own-query layer is never gathered for the primary scale, yet its
    format vote still reads its own rows.
    """
    from unittest.mock import MagicMock

    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.execute.chart_resolution import (
        resolve_chart_with_runtime_inputs,
    )

    layer = LineLayer(
        type="line",
        y="goal",
        query="goals",
        axis_y={"position": "right", "labels": {"format": "currency"}},
    )
    executor = MagicMock()
    executor.execute_query.return_value = [{"month": "Jan", "goal": 0.67}]
    resolved = resolve_chart_with_runtime_inputs(
        _bar_normalized(layers=[layer]),
        _DATA,
        _default_board_style(),
        400,
        executor,
        {},
        filter_variables=frozenset(),
        link_open="page",
    )
    assert resolved.layers[0].axis_y.tooltip_format.spec == "$,.2f"
    executor.execute_query.assert_called_once_with("goals", {})


# ── layer axis_y.title needs a split axis to land on ─────────────────────


def _resolve_layer_axes(*layer_axes: dict[str, object]) -> None:
    from dbt_charts.core.compile.models.chart.authored._layer import LineLayer
    from dbt_charts.core.compile.resolve import resolve

    layers = [LineLayer(type="line", y="target", axis_y=a) for a in layer_axes]
    resolve(_bar_normalized(layers=layers), _DATA, _default_board_style())


def test_layer_axis_y_title_without_any_pinned_position_raises() -> None:
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError) as exc_info:
        _resolve_layer_axes({"title": "Target"})
    assert exc_info.value.code is not None
    assert exc_info.value.code.code == "ERR-LAYER-AXIS-TITLE-WITHOUT-POSITION"
    assert "axis_y.position" in str(exc_info.value)


def test_layer_axis_y_title_with_pinned_position_compiles() -> None:
    _resolve_layer_axes({"title": "Target", "position": "right"})


def test_layer_axis_y_title_compiles_when_another_layer_pins_a_side() -> None:
    """The axis split is chart-wide: an unpinned layer's title still lands."""
    _resolve_layer_axes({"position": "right"}, {"title": "Target"})


def test_layer_axis_follows_the_query_format() -> None:
    """A layer's own y axis takes the query's `formats:` entry for its measure."""
    from dbt_charts.core.compile.models.chart.authored._layer import (
        LayerAxisYStyle,
        LineLayer,
    )
    from dbt_charts.core.compile.models.chart.normalized import BarChart as NBarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve import resolve

    rows = [
        {"date": "2025-01", "revenue": 42000.0, "target": 0.31},
        {"date": "2025-02", "revenue": 51000.0, "target": 0.34},
    ]
    chart = NBarChart(
        id="bar1",
        type="bar",
        x="date",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="t", formats={"target": "percent"}),
        query_name="q",
        variable_dependencies=set(),
        layers=[
            LineLayer(type="line", y="target", axis_y=LayerAxisYStyle(position="right"))
        ],
    )
    resolved = resolve(chart, rows, _default_board_style())
    labels = resolved.layers[0].axis_y.labels
    assert labels is not None
    assert "%" in labels.format.spec


def test_layer_axis_follows_its_own_query_format() -> None:
    """A layer reading its own query takes that query's `formats:`, not the base's."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.resolve import resolve

    result = compile(
        """
queries:
  base:
    columns: [x, n]
    values:
      - [a, 1200]
      - [b, 1500]
    formats:
      n: integer
  lay:
    columns: [x, share]
    values:
      - [a, 0.123]
      - [b, 0.2567]
    formats:
      share: percent_whole
charts:
  bars:
    type: bar
    query: base
    x: x
    y: n
    layers:
      - type: line
        query: lay
        y: share
        axis_y:
          position: right
rows:
  - bars
"""
    )
    assert result.success, result.errors
    assert result.board is not None
    chart = result.board.charts["bars"]
    resolved = resolve(
        chart,
        [{"x": "a", "n": 1200}, {"x": "b", "n": 1500}],
        result.board.chart_style_context,
        datasets={"lay": [{"x": "a", "share": 0.123}, {"x": "b", "share": 0.2567}]},
    )
    labels = resolved.layers[0].axis_y.labels
    assert labels is not None
    assert "%" in labels.format.spec
