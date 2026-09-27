"""Bug: quantitative right-edge axis with a house-rule format alias must always
resolve label.align = "right".

Forcing is scoped to right-edge axes only:
- Left-edge: VL's own default is already "right" (end-anchored), no override needed.
- Bottom/top (edge=None): orientation has no "right" concept.

An authored axis_y.labels.align on such an axis is silently discarded; the compiler
emits WARN_AXIS_ALIGN_DISCARDED so the user sees it in dct validate output.
Raw d3 specs and non-quantitative axes are NOT affected.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.chart.normalized import BarChart, LineChart
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisYStylePatch,
    BarChartStylePatch,
    LineChartStylePatch,
)
from dbt_charts.core.compile.resolve.chart._axes import (
    AxisOverrides,
    _bake_cartesian_axes,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import partition
from dbt_charts.core.compile.resolve.chart.bar import _resolve_bar
from dbt_charts.core.compile.resolve.chart.line import _resolve_line
from dbt_charts.core.compile.resolve.style.axis_cascade import build_resolved_axis
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

from .conftest import fixture_chart_for_type


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _merged_ay(chart_type: str = "line", y_channel_type: str = "quantitative"):
    """Return a merged AxisYStyle from the default theme for the given chart type."""
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    chart = fixture_chart_for_type(chart_type)
    _, ay, _, ay_band_pos, _, _, _ = _bake_cartesian_axes(
        ctx,
        chart,
        chart_type,
        "temporal",
        y_channel_type,
        AxisOverrides(),
    )
    return ay, ay_band_pos


def test_quantitative_alias_format_forces_right_align_on_right_edge():
    """format_is_alias=True + is_quantitative=True + edge='right' -> label.align == 'right'."""
    ay, ay_band_pos = _merged_ay()
    resolved = build_resolved_axis(
        ay,
        band_position=ay_band_pos,
        edge="right",
        format_authored=True,
        format_is_alias=True,
        is_quantitative=True,
        chart_id="test",
    )
    assert resolved.labels.align == "right"


def test_quantitative_alias_format_no_force_on_left_edge():
    """Left-edge axes are NOT forced: VL's own default is already end-anchored."""
    ay, ay_band_pos = _merged_ay()
    resolved = build_resolved_axis(
        ay,
        band_position=ay_band_pos,
        edge="left",
        format_authored=True,
        format_is_alias=True,
        is_quantitative=True,
        chart_id="test_left",
    )
    # No forced override; align passes through as None (VL handles it).
    assert resolved.labels.align is None


def test_quantitative_alias_format_no_force_on_no_edge():
    """Bottom/top axes (edge=None) are NOT forced: orientation has no side."""
    ay, ay_band_pos = _merged_ay()
    resolved = build_resolved_axis(
        ay,
        band_position=ay_band_pos,
        edge=None,
        format_authored=True,
        format_is_alias=True,
        is_quantitative=True,
        chart_id="test_none",
    )
    assert resolved.labels.align is None


def test_raw_d3_format_does_not_force_right_align():
    """format_is_alias=False (raw d3 spec) does not force right-align."""
    ay, ay_band_pos = _merged_ay()
    ay_with_align = ay.model_copy(
        update={"labels": ay.labels.model_copy(update={"align": "center"})}
    )
    resolved = build_resolved_axis(
        ay_with_align,
        band_position=ay_band_pos,
        edge="right",
        format_authored=True,
        format_is_alias=False,
        is_quantitative=True,
        chart_id="test_raw",
    )
    # No forced right -- the authored "center" passes through.
    assert resolved.labels.align == "center"


def test_non_quantitative_axis_alias_format_does_not_force_right_align():
    """A non-quantitative axis with format_is_alias=True is NOT forced to right."""
    ay, ay_band_pos = _merged_ay(y_channel_type="ordinal")
    ay_with_align = ay.model_copy(
        update={"labels": ay.labels.model_copy(update={"align": "center"})}
    )
    resolved = build_resolved_axis(
        ay_with_align,
        band_position=ay_band_pos,
        edge="right",
        format_authored=True,
        format_is_alias=True,
        is_quantitative=False,
        chart_id="test_ordinal",
    )
    # is_quantitative=False -- no forced override.
    assert resolved.labels.align == "center"


def test_right_edge_house_alias_wired_through_full_resolve():
    """Wiring test: the forced right-align flows through the full resolve pipeline.

    A right-edge y-axis on a line chart with a house-alias format must come
    out with labels.align == 'right', proving is_quantitative is threaded from
    plan_cartesian -> build_cartesian_axes -> build_resolved_axis.
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    patch = LineChartStylePatch(
        axis_y=AxisYStylePatch(
            position="right",
            labels=AxisLabelStylePatch(format="percent"),
        )
    )
    data = [
        {"m": 1, "v": 0.25},
        {"m": 2, "v": 0.50},
        {"m": 3, "v": 0.75},
    ]
    chart = LineChart(id="wire_test", type="line", x="m", y="v", style=patch)
    resolved = _resolve_line(
        chart,
        partition(None, data),
        chart_style_context=ctx,
        width=800.0,
        datasets={},
        automatic_link_candidate=None,
        variables={},
    )
    ay = resolved.style.axis_y
    assert ay.labels.align == "right"


def test_right_edge_theme_default_format_wired_through_full_resolve():
    """A right-edge y-axis that authors NO format at all -- inheriting the
    theme's own baseline (``axis_quantitative.labels.format: number``,
    itself a predefined name) -- must still force label.align == 'right'.

    ``format_is_alias`` used to only ever become True when an author
    explicitly wrote ``format: <alias>`` at an authored layer (board 6-9,
    fallback 10, or chart-local 11-13); the theme-default
    layer (1-3) was invisible to it. Since `number` is the format
    every unformatted quantitative axis actually gets, that gap meant the
    overwhelming common case -- a plain axis with no format authored -- never
    got the auto-right-align guarantee at all.
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    patch = LineChartStylePatch(axis_y=AxisYStylePatch(position="right"))
    data = [
        {"m": 1, "v": 25000},
        {"m": 2, "v": 50000},
        {"m": 3, "v": 75000},
    ]
    chart = LineChart(id="theme_default_test", type="line", x="m", y="v", style=patch)
    resolved = _resolve_line(
        chart,
        partition(None, data),
        chart_style_context=ctx,
        width=800.0,
        datasets={},
        automatic_link_candidate=None,
        variables={},
    )
    ay = resolved.style.axis_y
    assert ay.labels.format == ".3~s"
    assert ay.labels.align == "right"


def test_vertical_bar_house_alias_forces_align_right():
    """Wiring test for bar.py:617 ay_is_quantitative=orientation != 'horizontal'.

    A vertical bar with a house-alias format on the right-edge y-axis must
    resolve labels.align == 'right'. If ay_is_quantitative were inverted
    (always False), the forced align would not fire and this would fail.

    Numeric x values are required: string x causes bar orientation inference to
    return 'horizontal' (categorical x → horizontal bar), which sets
    ay_is_quantitative=False and bypasses the forced-align gate.
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    # Integer x forces vertical orientation (numeric x → vertical bar inference).
    data = [
        {"month": 1, "value": 1_000},
        {"month": 2, "value": 2_000},
        {"month": 3, "value": 3_000},
    ]
    chart = BarChart(
        id="bar_align_test",
        type="bar",
        x="month",
        y="value",
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(
                position="right",
                labels=AxisLabelStylePatch(format="percent"),
            )
        ),
    )
    resolved = _resolve_bar(
        chart,
        partition(None, data),
        chart_style_context=ctx,
        width=800.0,
        datasets={},
        automatic_link_candidate=None,
        variables={},
    )
    assert resolved.style.axis_y.labels.align == "right"


def test_horizontal_bar_house_alias_does_not_force_align_right():
    """Horizontal bar measure axis (ay_is_quantitative=False) is not forced.

    A horizontal bar's measure axis sits on VL's x channel (edge=None),
    so the right-edge gate blocks forced alignment. This proves the
    orientation != 'horizontal' condition in bar.py routes correctly.

    String x values plus numeric y infer horizontal orientation (categorical
    x → horizontal bar). The authored axis_y here is the categorical axis —
    it has no left/right edge for the quantitative measure.
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    # String x with numeric y infers horizontal orientation (categorical x → horizontal bar).
    # The bar model always takes the measure on y; orientation is inferred from x's type.
    data = [
        {"category": "A", "value": 1_000},
        {"category": "B", "value": 2_000},
        {"category": "C", "value": 3_000},
    ]
    chart = BarChart(
        id="hbar_align_test",
        type="bar",
        x="category",
        y="value",
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(
                labels=AxisLabelStylePatch(format="percent"),
            )
        ),
    )
    resolved = _resolve_bar(
        chart,
        partition(None, data),
        chart_style_context=ctx,
        width=800.0,
        datasets={},
        automatic_link_candidate=None,
        variables={},
    )
    # Horizontal bar measure axis is edge=None, so no forced align.
    assert resolved.style.axis_y.labels.align != "right"


def test_compile_time_diagnostic_fires_for_authored_align_with_house_alias():
    """WARN_AXIS_ALIGN_DISCARDED fires when align conflicts with house alias on right-edge axis.

    Warning fires only when position is explicitly "right" — the force can only
    discard an authored align on a right-edge axis.
    """
    from dbt_charts.core.compile.compiler import compile as _compile
    from dbt_charts.core.diagnostics import WARN_AXIS_ALIGN_DISCARDED

    board_yaml = """
queries:
  q:
    type: values
    rows:
      - {month: 1, value: 0.5}
      - {month: 2, value: 0.75}
charts:
  rev:
    type: line
    x: month
    y: value
    query: q
    style:
      axis_y:
        position: right
        labels:
          align: left
          format: percent
rows:
  - rev
"""
    result = _compile(board_yaml)
    assert result.errors == [], f"Unexpected compile errors: {result.errors}"
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_ALIGN_DISCARDED.code in codes, (
        f"Expected WARN-AXIS-ALIGN-DISCARDED in compile warnings; got: {codes}"
    )


def test_compile_time_diagnostic_fires_for_theme_default_format_no_chart_format():
    """WARN_AXIS_ALIGN_DISCARDED fires even when the chart authors NO format
    at all -- inheriting the theme's own baseline (number, a
    predefined name) -- as long as align is authored on an explicit
    right-edge axis. Mirrors the force-right fix: the diagnostic must see
    the same theme-default case _force_right does, not just a chart-local
    format override.
    """
    from dbt_charts.core.compile.compiler import compile as _compile
    from dbt_charts.core.diagnostics import WARN_AXIS_ALIGN_DISCARDED

    board_yaml = """
queries:
  q:
    type: values
    rows:
      - {month: 1, value: 25000}
      - {month: 2, value: 50000}
charts:
  rev:
    type: line
    x: month
    y: value
    query: q
    style:
      axis_y:
        position: right
        labels:
          align: left
rows:
  - rev
"""
    result = _compile(board_yaml)
    assert result.errors == [], f"Unexpected compile errors: {result.errors}"
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_ALIGN_DISCARDED.code in codes, (
        f"Expected WARN-AXIS-ALIGN-DISCARDED in compile warnings; got: {codes}"
    )


def test_compile_time_diagnostic_does_not_fire_without_explicit_right_position():
    """WARN_AXIS_ALIGN_DISCARDED does not fire when position is unset or not 'right'.

    An axis without an explicit position='right' may resolve to the left side at
    render time, in which case the house-format force never fires and the authored
    align is honored.
    """
    from dbt_charts.core.compile.compiler import compile as _compile
    from dbt_charts.core.diagnostics import WARN_AXIS_ALIGN_DISCARDED

    board_yaml = """
queries:
  q:
    type: values
    rows:
      - {month: 1, value: 0.5}
      - {month: 2, value: 0.75}
charts:
  rev:
    type: line
    x: month
    y: value
    query: q
    style:
      axis_y:
        labels:
          align: left
          format: percent
rows:
  - rev
"""
    result = _compile(board_yaml)
    assert result.errors == [], f"Unexpected compile errors: {result.errors}"
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_ALIGN_DISCARDED.code not in codes, (
        f"WARN-AXIS-ALIGN-DISCARDED fired without explicit position=right; got: {codes}"
    )


def test_horizontal_bar_explicit_right_position_does_not_force_align_right():
    """Horizontal bar with explicit position='right' is not forced to right-align.

    Even when axis_y.position is explicitly 'right', _force_right requires
    ay_is_quantitative=True. For a horizontal bar, the measure axis is on VL's
    x channel and ay_is_quantitative=False, so the force is blocked by the
    is_quantitative gate regardless of the authored position.

    This test proves the is_quantitative guard blocks the force, not just the
    edge-is-None guard (which would also block it for 'auto'/unset position).
    """
    ctx = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    # String x + numeric y infers horizontal bar (categorical x -> horizontal).
    data = [
        {"category": "A", "value": 1_000},
        {"category": "B", "value": 2_000},
        {"category": "C", "value": 3_000},
    ]
    chart = BarChart(
        id="hbar_explicit_right",
        type="bar",
        x="category",
        y="value",
        style=BarChartStylePatch(
            axis_y=AxisYStylePatch(
                position="right",
                labels=AxisLabelStylePatch(format="percent"),
            )
        ),
    )
    resolved = _resolve_bar(
        chart,
        partition(None, data),
        chart_style_context=ctx,
        width=800.0,
        datasets={},
        automatic_link_candidate=None,
        variables={},
    )
    # Horizontal bar: ay_is_quantitative=False -> _force_right blocked even with
    # explicit position='right' and a house alias format.
    assert resolved.style.axis_y.labels.align != "right", (
        "Horizontal bar measure axis must not be forced to right-align "
        "even with explicit position='right' and a house-alias format"
    )


def test_diagnostic_does_not_fire_for_bar_chart():
    """WARN_AXIS_ALIGN_DISCARDED does not fire for BarChart.

    A bar chart's orientation is data-dependent (inferred from the x column type
    at resolve time). The diagnostic cannot determine at compile time whether the
    axis is quantitative (vertical bar) or categorical (horizontal bar), so it
    skips all BarChart instances to avoid false positives.
    """
    from dbt_charts.core.compile.compiler import compile as _compile
    from dbt_charts.core.diagnostics import WARN_AXIS_ALIGN_DISCARDED

    board_yaml = """
queries:
  q:
    type: values
    rows:
      - {month: 1, value: 0.5}
      - {month: 2, value: 0.75}
charts:
  rev:
    type: bar
    x: month
    y: value
    query: q
    style:
      axis_y:
        position: right
        labels:
          align: left
          format: percent
rows:
  - rev
"""
    result = _compile(board_yaml)
    assert result.errors == [], f"Unexpected compile errors: {result.errors}"
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_ALIGN_DISCARDED.code not in codes, (
        f"WARN-AXIS-ALIGN-DISCARDED must not fire for BarChart; got: {codes}"
    )


def test_diagnostic_does_not_fire_for_non_tabular_chart_font():
    """WARN_AXIS_ALIGN_DISCARDED does not fire when the axis font is non-tabular.

    _force_right requires tabular_figures=True when column_forming=True. If the
    chart-local axis font is non-tabular (e.g. Arial), the force never fires and
    the authored align is honored, so the diagnostic must not fire.
    """
    from dbt_charts.core.compile.compiler import compile as _compile
    from dbt_charts.core.diagnostics import WARN_AXIS_ALIGN_DISCARDED

    board_yaml = """
queries:
  q:
    type: values
    rows:
      - {month: 1, value: 0.5}
      - {month: 2, value: 0.75}
charts:
  rev:
    type: line
    x: month
    y: value
    query: q
    style:
      axis_y:
        position: right
        labels:
          align: left
          format: percent
          font:
            family: Arial
rows:
  - rev
"""
    result = _compile(board_yaml)
    assert result.errors == [], f"Unexpected compile errors: {result.errors}"
    codes = {w.code for w in result.warnings}
    assert WARN_AXIS_ALIGN_DISCARDED.code not in codes, (
        f"WARN-AXIS-ALIGN-DISCARDED must not fire for non-tabular chart font; got: {codes}"
    )


def test_column_forming_axis_with_non_tabular_font_does_not_force_align_right():
    """Column-forming axis with a non-tabular font is not forced to right-align.

    The _force_right gate requires tabular_figures=True when column_forming=True:
    the suffix-field reservation device only works with fixed-advance digit spacing.
    A non-tabular font degrades to the away-side default (no explicit labelAlign).

    Regression guard for axis_cascade.py lines 358-363.
    """
    ay, ay_band_pos = _merged_ay()
    # Override font family to a non-tabular board font so font_is_tabular() returns False.
    ay_nontabular = ay.model_copy(
        update={
            "labels": ay.labels.model_copy(
                update={"font": ay.labels.font.model_copy(update={"family": "Arial"})}
            )
        }
    )
    resolved = build_resolved_axis(
        ay_nontabular,
        band_position=ay_band_pos,
        edge="right",
        format_authored=True,
        format_is_alias=True,
        is_quantitative=True,
        column_forming=True,
        chart_id="nontabular_test",
    )
    # With a non-tabular font and column_forming=True, _force_right is False.
    # align stays at None (VL's own per-orient default applies).
    assert resolved.labels.align is None, (
        f"Non-tabular font must not force right-align; got: {resolved.labels.align!r}"
    )
