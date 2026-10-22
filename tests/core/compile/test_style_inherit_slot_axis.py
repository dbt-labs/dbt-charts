"""Regression: axis_x/axis_y/axis_quantitative are authored-only overlays on axis.

Proves:
1. Setting style.charts.axis.labels.font.color reaches axis_x and axis_y at
   emit time (resolved_axis_style Layer 1), since neither authors its own
   label.font.color.
2. A nested board that overrides style.charts.axis changes the axis_x/axis_y
   emitted result too, so child board axes reflect the override, not the
   parent value.

axis_x/axis_y/axis_quantitative carry SkipInheritSlots (not InheritSlot):
apply_inherit never fills their unset leaves from the shared `axis` global.
Only resolved_axis_style()'s render-time merge combines them with `axis` — so
these tests assert through resolved_axis_style(), not through the raw
charts.axis_x/axis_y fields (which stay sparse/authored-only).
"""

from __future__ import annotations

import textwrap
from collections.abc import Generator

import pytest

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    reset_config()
    yield
    reset_config()


def _compile(yaml_str: str):  # type: ignore[return]
    from dbt_charts.core.compile.compiler import compile as df_compile

    result = df_compile(textwrap.dedent(yaml_str))
    assert result.success, result.errors
    return result.board


# ── Board-level InheritSlot ────────────────────────────────────────────────────


def test_axis_x_inherits_label_color_from_axis() -> None:
    """style.charts.axis.labels.font.color reaches axis_x/axis_y at emit time."""
    board = _compile(
        """\
        title: Board
        extends: stark
        style:
          charts:
            axis:
              labels:
                font:
                  color: '#aa1122'
        queries:
          q:
            type: values
            rows:
              - {x: 1, y: 2}
        charts:
          c:
            query: q
            type: bar
            x: x
            y: y
        rows:
          - c
        """
    )
    ctx = board.chart_style_context
    x = resolved_axis_style(ctx, "axis_x", "quantitative", chart_type="", labeled=False)
    y = resolved_axis_style(ctx, "axis_y", "quantitative", chart_type="", labeled=False)
    assert x.labels.font.color == "#aa1122"
    assert y.labels.font.color == "#aa1122"


def test_axis_x_explicit_override_wins_over_axis() -> None:
    """Explicit style.charts.axis_x.labels.font.color beats the axis fallback."""
    board = _compile(
        """\
        title: Board
        extends: stark
        style:
          charts:
            axis:
              labels:
                font:
                  color: '#aabbcc'
            axis_x:
              labels:
                font:
                  color: '#ff0000'
        queries:
          q:
            type: values
            rows:
              - {x: 1, y: 2}
        charts:
          c:
            query: q
            type: bar
            x: x
            y: y
        rows:
          - c
        """
    )
    ctx = board.chart_style_context
    x = resolved_axis_style(ctx, "axis_x", "quantitative", chart_type="", labeled=False)
    y = resolved_axis_style(ctx, "axis_y", "quantitative", chart_type="", labeled=False)
    assert x.labels.font.color == "#ff0000"
    assert y.labels.font.color == "#aabbcc"  # not overridden


# ── Per-family category axis label color (bar/layered x-axis) ───────────────────
#
# The bar family hides its x grid + ticks, so charts.bar.axis_x is materialized.
# Its label color must still resolve to the theme's axis label color at render —
# not prose ink (Style.font.color). Assert through resolved_axis_style, the
# render-time axis cascade (single source of truth for emitted axis state), so
# the test pins the rendered contract regardless of whether bar.axis_x.labels is
# materialized or left None to inherit the global axis at merge time.


def test_bar_category_axis_label_color_matches_band_axis() -> None:
    rs = resolve_chart_style_context(get_theme_style())
    emitted = resolved_axis_style(
        rs, "axis_x", "ordinal", chart_type="bar", labeled=False
    )
    assert emitted.labels.font.color == rs.axis_band.labels.font.color


def test_bar_category_axis_label_color_is_band_axis_not_prose_font() -> None:
    """bar x-axis label color follows the band axis color, not the prose font color.

    Distinct axis color and prose font color prove the emitted bar category axis
    label follows the axis cascade rather than Style.font (the ink-leak source).
    """

    board = _compile(
        """\
        title: Board
        style:
          font:
            color: '#123456'
          charts:
            axis_band:
              labels:
                font:
                  color: '#aa1122'
        queries:
          q:
            type: values
            rows:
              - {x: 1, y: 2}
        charts:
          c:
            query: q
            type: bar
            x: x
            y: y
        rows:
          - c
        """
    )
    ctx = board.chart_style_context
    emitted = resolved_axis_style(
        ctx, "axis_x", "ordinal", chart_type="bar", labeled=False
    )
    assert emitted.labels.font.color == "#aa1122"


# ── Nested-board regression ─────────────────────────────────────────────────────


def test_nested_board_axis_override_rederives_axis_x() -> None:
    """Nested board overriding style.axis re-derives axis_x from that override.

    This is the key Lane B acceptance requirement: when a child board sets
    style.charts.axis.labels.font.color, the child's resolved axis_x must
    reflect the child's axis value, not the parent's.
    """
    board = _compile(
        """\
        title: Parent
        extends: stark
        style:
          charts:
            axis:
              labels:
                font:
                  color: '#aabbcc'
        queries:
          q:
            type: values
            rows:
              - {x: 1, y: 2}
        rows:
          - title: Child
            style:
              charts:
                axis:
                  labels:
                    font:
                      color: '#ff0000'
            charts:
              c:
                query: q
                type: bar
                x: x
                y: y
            rows:
              - c
        """
    )
    parent_ctx = board.chart_style_context
    child = board.layout.items[0].board
    assert child is not None
    child_ctx = child.chart_style_context

    parent_x = resolved_axis_style(
        parent_ctx, "axis_x", "quantitative", chart_type="", labeled=False
    )
    assert parent_x.labels.font.color == "#aabbcc"
    # Child overrides axis → axis_x/axis_y must re-derive from child's axis value.
    assert child_ctx.axis.labels.font.color == "#ff0000"
    child_x = resolved_axis_style(
        child_ctx, "axis_x", "quantitative", chart_type="", labeled=False
    )
    child_y = resolved_axis_style(
        child_ctx, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert child_x.labels.font.color == "#ff0000"
    assert child_y.labels.font.color == "#ff0000"


# ── axis_quantitative Layer 3: authored-only, no inherited-value clobber ────
#
# `charts.axis_x`/`axis_y`/`axis_quantitative` carry SkipInheritSlots, not
# InheritSlot: apply_inherit never fills their unset leaves from the shared
# `axis` global, so each stays sparse — only the fields the theme actually
# authors on that slot are non-None. This makes the old clobber failure mode
# (an inherited-not-authored axis_quantitative field looking identical to an
# explicit override and beating axis_x/axis_y's own deviation from the same
# global) structurally impossible: there is nothing to inherit, so a None
# field on axis_quantitative can never masquerade as an override.
#
# These tests read scenario preconditions off the live theme rather than
# pinning literal theme values, per the "don't pin theme/default values"
# convention.


def test_axis_quantitative_unauthored_field_never_clobbers_axis_x() -> None:
    """A field axis_quantitative never authors leaves axis_x's own value intact."""

    rs = resolve_chart_style_context(get_theme_style())
    # Scenario preconditions: axis_quantitative never authors its own
    # grid.width or ticks.visible (both None — SkipInheritSlots means no
    # fallback fill from the global axis), and axis_x deviates from that
    # global for both fields.
    assert rs.axis_quantitative.grid.width is None
    assert rs.axis_x.grid.width != rs.axis.grid.width
    assert rs.axis_quantitative.ticks.visible is None
    assert rs.axis_x.ticks.visible != rs.axis.ticks.visible

    emitted = resolved_axis_style(
        rs, "axis_x", "quantitative", chart_type="", labeled=False
    )
    assert emitted.grid.width == rs.axis_x.grid.width
    # axis_x.ticks.visible is "auto" (resolved to a concrete bool, never the
    # literal string) rather than a value directly comparable to the raw
    # theme field -- assert the resolved bool still reflects axis_x's own
    # deviation (a clobber would produce axis's shared False instead).
    assert emitted.ticks.visible != rs.axis.ticks.visible


def test_axis_quantitative_authored_field_still_wins_over_axis_x() -> None:
    """An explicitly-authored axis_quantitative field still overrides axis_x.

    Guards the overcorrection direction: a field axis_quantitative genuinely
    authors must keep winning so the fix doesn't swing into ignoring
    axis_quantitative's Layer-3 overrides entirely.
    """

    rs = resolve_chart_style_context(get_theme_style())
    authored_ticks_width = rs.axis_quantitative.ticks.width
    assert authored_ticks_width is not None
    assert authored_ticks_width != rs.axis_x.ticks.width

    emitted = resolved_axis_style(
        rs, "axis_x", "quantitative", chart_type="", labeled=False
    )
    assert emitted.ticks.width == authored_ticks_width


# ── Per-family x-grid policy on quantitative x (RJ decisions, 2026-07-16) ────
#
# Scatter (quant×quant): vertical and horizontal gridlines render at the same
# weight — the lighter axis_x grid weight is an intentional asymmetry for
# category/temporal x-axes only. The even weight previously arrived by accident
# (the inherit-fill clobber these tests' siblings pin down); it is now authored
# explicitly at the chart-type layer (charts.scatter.axis_x.grid.width).
#
# Histogram: histograms are bar charts — the parallel-to-bar (vertical)
# gridlines are hidden, matching charts.bar.axis_x.grid.visible.


def test_scatter_grid_is_even() -> None:
    """Emitted x-grid width equals emitted y-grid width for scatter."""

    rs = resolve_chart_style_context(get_theme_style())
    emitted_x = resolved_axis_style(
        rs,
        "axis_x",
        "quantitative",
        chart_type="scatter",
        labeled=False,
    )
    emitted_y = resolved_axis_style(
        rs,
        "axis_y",
        "quantitative",
        chart_type="scatter",
        labeled=False,
    )
    assert emitted_x.grid.width == emitted_y.grid.width


def test_histogram_hides_x_grid_like_bar() -> None:
    """Histogram's emitted x-grid visibility matches bar's (hidden)."""

    rs = resolve_chart_style_context(get_theme_style())
    hist_x = resolved_axis_style(
        rs,
        "axis_x",
        "quantitative",
        chart_type="histogram",
        labeled=False,
    )
    bar_x = resolved_axis_style(
        rs,
        "axis_x",
        "ordinal",
        chart_type="bar",
        labeled=False,
    )
    assert bar_x.grid.visible is False  # scenario precondition: bar hides x-grid
    assert hist_x.grid.visible is False


def test_heatmap_hides_both_grids() -> None:
    """Heatmap cells tile the plot area — cell-crossing gridlines are always
    wrong (they bisect every cell through its center on the band scale).
    Both axes must suppress grid, unlike bar/histogram which only hide the
    axis parallel to the mark.
    """

    rs = resolve_chart_style_context(get_theme_style())
    heatmap_x = resolved_axis_style(
        rs,
        "axis_x",
        "nominal",
        chart_type="heatmap",
        labeled=False,
    )
    heatmap_y = resolved_axis_style(
        rs,
        "axis_y",
        "nominal",
        chart_type="heatmap",
        labeled=False,
    )
    assert heatmap_x.grid.visible is False
    assert heatmap_y.grid.visible is False
