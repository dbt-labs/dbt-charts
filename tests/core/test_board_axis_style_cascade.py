"""TDD: board-level `style.charts.axis*` must cascade to rendered charts.

Two independent drops used to swallow a board-authored axis leaf on a
quantitative channel:

- M1 (slot precedence): board-authored `charts.axis_y`/`axis_x` merges into
  the same cascade slot the theme owns, and the theme's `axis_quantitative`
  (Layer 3) unconditionally overwrites it afterward.
- M2 (provenance loss): even when a value survives M1, the non-compacting
  tick-label bake rewrites an SI-spec format to plain digits unless the
  layer that set it is tracked as "authored" -- which no board-level layer
  used to do.

`test_board_level_axis_y_si_format_matches_chart_level_render` is the
acceptance test: it covers both bugs together, end to end, through a real
SVG render. The rest isolate one mechanism each at the style-cascade level.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import (
    AxisLabelStylePatch,
    AxisYStylePatch,
    LineChartStylePatch,
    StylePatch,
)
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    _merge_axis_cascade,
    chart_type_axis_patch as get_chart_type_axis_patch,
    resolved_axis_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.render.chart.vega_lite import render_chart

_DATA = [
    {"date": "2024-01-01", "revenue": 0},
    {"date": "2024-01-02", "revenue": 10000},
    {"date": "2024-01-03", "revenue": 20000},
    {"date": "2024-01-04", "revenue": 30000},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _line_chart(chart_id: str, **kwargs) -> LineChart:
    return LineChart(
        id=chart_id,
        type="line",
        source_path=f"charts.{chart_id}",
        x="date",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test_profile"),
        query_name="q",
        **kwargs,
    )


def _dollar_labels(svg: str) -> set[str]:
    return set(re.findall(r">\$[0-9][^<]*<", svg))


def test_board_level_axis_y_si_format_matches_chart_level_render():
    """Acceptance test: board-level `charts.axis_y.labels.format: "$,.3~s"`
    renders the same $-formatted y-axis tick labels as the identical
    per-chart declaration. Covers M1 and M2 together."""
    board_patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "$,.3~s"}}}}
    )
    board_style, board_ctx = resolve_style_and_context(get_theme_style(), board_patch)
    board_svg = render_chart(
        _line_chart("board_level"),
        board_style,
        board_ctx,
        _DATA,
        format="svg",
        width=400,
        height=250,
    )

    chart_style, chart_ctx = resolve_style_and_context(get_theme_style())
    chart_local = _line_chart(
        "chart_level",
        style=LineChartStylePatch(
            axis_y=AxisYStylePatch(labels=AxisLabelStylePatch(format="$,.3~s"))
        ),
    )
    chart_svg = render_chart(
        chart_local,
        chart_style,
        chart_ctx,
        _DATA,
        format="svg",
        width=400,
        height=250,
    )

    board_labels = _dollar_labels(board_svg)
    chart_labels = _dollar_labels(chart_svg)
    assert chart_labels, (
        f"expected $-formatted labels from chart-level; got {chart_svg}"
    )
    assert board_labels == chart_labels, (
        f"board-level axis_y.labels.format did not match chart-level: "
        f"board={board_labels} chart={chart_labels}"
    )


def test_board_level_axis_y_non_si_format_isolates_m1():
    """Non-SI format (`$,.0f`) never enters the tick-bake branch (M2), so a
    failure here is M1 alone: the theme's axis_quantitative default
    overwriting the board-authored axis_y slot."""
    patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "$,.0f"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.format == "$,.0f"


def test_board_level_axis_x_format_cascades_on_quantitative_channel():
    """style.charts.axis_x.labels.format applies when x is the measure axis
    (a horizontal bar's x channel) -- the axis_x variant of M1."""
    patch = StylePatch.model_validate(
        {"charts": {"axis_x": {"labels": {"format": "$,.0f"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_x", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.format == "$,.0f"


def test_chart_local_axis_y_still_wins_over_board_level():
    """Precedence is not inverted: a chart-local style.axis_y beats the
    board-level declaration."""
    board_patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "$,.3~s"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), board_patch)
    chart_local = AxisYStylePatch(labels=AxisLabelStylePatch(format="$,.0f"))
    resolved = resolved_axis_style(
        ctx,
        "axis_y",
        "quantitative",
        axis_overrides=AxisOverrides(y=chart_local),
        chart_type="",
        label_authored=False,
    )
    assert resolved.labels.format == "$,.0f"


def test_board_level_axis_y_beats_theme_default():
    """Board-level still beats the theme, and picking up the fix does not
    touch theme-internal Layer 2/3 ordering (test_unified_axis_emit.py
    pins that separately)."""
    base = get_theme_style("clarity")
    patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "$,.3~s"}}}}
    )
    board_format = resolved_axis_style(
        resolve_chart_style_context(base, patch),
        "axis_y",
        "quantitative",
        chart_type="",
        label_authored=False,
    ).labels.format

    assert board_format == "$,.3~s"


def test_board_level_axis_y_padding_leaf_regression():
    """A non-shadowed leaf (labels.padding) that already cascaded correctly
    must keep working."""
    patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"padding": 42}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.padding == 42


def test_board_axis_global_cascades():
    """board charts.axis (the global slot) cascades to rendered charts."""
    patch = StylePatch.model_validate(
        {"charts": {"axis": {"labels": {"format": "$,.0f"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.format == "$,.0f"


def test_board_axis_quantitative_beats_board_channel():
    """board axis_quantitative wins over board axis_y when both authored."""
    patch = StylePatch.model_validate(
        {
            "charts": {
                "axis_y": {"labels": {"format": "$,.0f"}},
                "axis_quantitative": {"labels": {"format": ",.1%"}},
            }
        }
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.format == ",.1%"


def test_chart_fallback_format_beats_board_axis_y():
    """chart.format / style.number_format wins over board-level axis_y.labels.format."""
    board_patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"format": "$,.0f"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), board_patch)
    resolved = resolved_axis_style(
        ctx,
        "axis_y",
        "quantitative",
        chart_fallback_format=",.1%",
        chart_type="",
        label_authored=False,
    )
    assert resolved.labels.format == ",.1%"


def test_board_family_axis_y_beats_board_axis_y():
    """board charts.<family>.axis_y wins over board charts.axis_y.

    Routed through render_chart rather than calling _merge_axis_cascade with a
    hand-supplied chart_type, because the only thing that makes the family layer
    reachable in production is `chart_type=chart_type` in
    compile/resolve/chart/_axes.py. A direct call supplies that argument itself,
    so it passes even if the wiring is deleted and every real render silently
    drops the more-specific board key.
    """
    patch = StylePatch.model_validate(
        {
            "charts": {
                "axis_y": {"labels": {"format": "$,.0f"}},
                "line": {"axis_y": {"labels": {"format": ",.2f"}}},
            }
        }
    )
    style, ctx = resolve_style_and_context(get_theme_style(), patch)
    svg = render_chart(
        _line_chart("fam"), style, ctx, _DATA, format="svg", width=400, height=250
    )

    assert not _dollar_labels(svg), (
        f"board-global $ format leaked past the more-specific family key: "
        f"{_dollar_labels(svg)}"
    )
    assert re.search(r">[0-9,]+\.00<", svg), (
        f"expected the family's ,.2f labels in the render; got {svg[:400]}"
    )


def test_null_board_axis_leaf_does_not_crash():
    """An explicit null on a board axis leaf does not crash the compile."""
    # In YAML, `padding:` with an empty scalar becomes None — must not clear
    # the theme's required value and trigger build_resolved_axis's _require guard.
    patch = StylePatch.model_validate(
        {"charts": {"axis_y": {"labels": {"padding": None}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    resolved = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=False
    )
    assert resolved.labels.padding is not None


def test_theme_family_axis_leaf_loses_to_board_channel():
    """A theme-family axis leaf (Layer 4) loses to a board-channel override (Layer 6).

    _base.yaml sets charts.bar.axis_x.labels.padding: 6.0 — a theme-authored value
    baked into the commingled chart_type_axis_patch for bar. A board authoring
    charts.axis_x.labels.padding: 42 must win because board layer 6 (channel) is
    applied AFTER Layer 4. This test fails if the layer ordering is wrong or if
    the board-channel patch is not reaching _merge_axis_cascade.
    """
    board_patch = StylePatch.model_validate(
        {"charts": {"axis_x": {"labels": {"padding": 42}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), board_patch)
    bar_axis_x_patch = get_chart_type_axis_patch(ctx, "bar", "axis_x")
    # Layer 4 carries theme-authored padding 6.0; board layer 6 must win.
    merged, _, _, _, _ = _merge_axis_cascade(
        ctx,
        "axis_x",
        "ordinal",
        bar_axis_x_patch,
        chart_type="bar",
        label_authored=False,
    )
    assert merged.labels.padding == 42


def test_board_title_visible_false_beats_label_forced_default():
    """A board-level charts.axis_y.title.visible: false wins over the
    label-forced title default (#7278's layer 5).

    An authored x_label/y_label defaults the axis title to visible (layer 5).
    The board tier sits above it (layers 6-9), so a board author's explicit
    title.visible: false suppresses the title despite the authored label. This
    fails if the label default is moved back above the board tier — which would
    reintroduce #7278's own bug one scope up.
    """
    ctx = resolve_chart_style_context(
        get_theme_style(),
        StylePatch.model_validate(
            {"charts": {"axis_y": {"title": {"visible": False}}}}
        ),
    )
    merged, _, _, _, _ = _merge_axis_cascade(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=True
    )
    assert merged.title.visible is False


def test_label_forced_title_default_shows_without_board_override():
    """With an authored label and no title.visible override, the label-forced
    default (layer 5) makes the title visible over the theme's suppression —
    #7278's behavior, preserved through the board-tier integration.
    """
    ctx = resolve_chart_style_context(get_theme_style())
    merged, _, _, _, _ = _merge_axis_cascade(
        ctx, "axis_y", "quantitative", chart_type="", label_authored=True
    )
    assert merged.title.visible is True


def test_board_family_axis_reaches_resolved_axis_style_via_chart_type():
    """The board family scope (layer 9) must reach the resolved_axis_style
    wrapper, not just the direct _merge_axis_cascade render path.

    axis_offset (the support-table geometry path) resolves axis state through
    resolved_axis_style. Before chart_type was threaded through that wrapper,
    a board-level charts.bar.axis.labels.padding was applied on the render
    path (which passes chart_type) but skipped on the geometry path (which
    did not) — so the support-table strip mis-reserved space against the axis.
    This pins that resolved_axis_style honors the family scope when chart_type
    is threaded, and ignores it when it is not.
    """
    ctx = resolve_chart_style_context(
        get_theme_style("clarity"),
        StylePatch.model_validate(
            {"charts": {"bar": {"axis": {"labels": {"padding": 40}}}}}
        ),
    )
    with_family = resolved_axis_style(
        ctx, "axis_x", "band", chart_type="bar", label_authored=False
    )
    without_family = resolved_axis_style(
        ctx, "axis_x", "band", chart_type="", label_authored=False
    )
    assert with_family.labels.padding == 40
    assert without_family.labels.padding != 40


def test_board_family_axis_x_beats_board_axis_x():
    """board charts.<family>.axis_x wins over board charts.axis_x on a
    quantitative x-axis (scatter, whose x channel is a measure).

    The axis_x mirror of test_board_family_axis_y_beats_board_axis_y. Routed
    through render_chart because `chart_type=chart_type` at
    compile/resolve/chart/_axes.py is the only thing that makes the board
    family scope reach the emitted x-axis; a direct _merge_axis_cascade call
    supplies chart_type itself and passes even if that wiring is deleted.
    """
    from dbt_charts.core.compile.models.chart.normalized import ScatterChart

    scatter_data = [
        {"xval": 0, "revenue": 0},
        {"xval": 10000, "revenue": 10000},
        {"xval": 20000, "revenue": 20000},
        {"xval": 30000, "revenue": 30000},
    ]
    patch = StylePatch.model_validate(
        {
            "charts": {
                "axis_x": {"labels": {"format": "$,.0f"}},
                "scatter": {"axis_x": {"labels": {"format": ",.2f"}}},
            }
        }
    )
    style, ctx = resolve_style_and_context(get_theme_style(), patch)
    chart = ScatterChart(
        id="sc",
        type="scatter",
        x="xval",
        y="revenue",
        query=SqlQuery(sql="SELECT 1", source="test_profile"),
        query_name="q",
    )
    svg = render_chart(
        chart, style, ctx, scatter_data, format="svg", width=400, height=250
    )

    assert not _dollar_labels(svg), (
        "board-global $ format leaked past the more-specific family key onto "
        f"the scatter's quantitative x-axis: {_dollar_labels(svg)}"
    )
    assert re.search(r">[0-9,]+\.00<", svg), (
        f"expected the family's ,.2f labels on the x-axis; got {svg[:400]}"
    )
