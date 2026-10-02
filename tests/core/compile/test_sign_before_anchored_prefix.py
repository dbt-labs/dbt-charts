"""An anchored authored prefix leads the sign; ``before_prefix`` there is rejected."""

from __future__ import annotations

import html

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board

from .._svg_render import render_board_to_svg

_CODE = "ERR-FORMAT-SIGN-BEFORE-ANCHORED-PREFIX"


def _board(chart: str) -> str:
    return f"""
title: T
queries:
  q:
    type: values
    rows:
      - {{month: Jan, net: 120000}}
      - {{month: Feb, net: -40000}}
charts:
  c:
    query: q
{chart}
rows:
  - c
"""


def _error_card(chart: str, code: str = _CODE) -> str:
    """The chart's resolve error, as the board paints it."""
    svg = render_board_to_svg(_board(chart))
    assert code in svg, svg
    return html.unescape(svg)


def test_inline_anchor_repeat_with_before_prefix_fails_compile() -> None:
    result = compile_board(
        _board("""    type: bar
    x: month
    y: net
    style:
      axis_y:
        labels:
          format:
            spec: ",.0f"
            prefix: "€"
            repeat: anchor
            sign_placement: before_prefix""")
    )
    assert [e.code for e in result.errors] == [_CODE]
    assert "axis_y.labels.format" in result.errors[0].message


def test_before_prefix_on_a_default_anchored_axis_fails_resolve() -> None:
    card = _error_card("""    type: line
    x: month
    y: net
    style:
      axis_y:
        ticks:
          count: 5
        labels:
          format:
            spec: number
            prefix: "EUR "
            sign_placement: before_prefix""")
    assert "charts.c.style.axis_y.labels.format" in card


def test_before_prefix_with_repeat_every_renders() -> None:
    svg = render_board_to_svg(
        _board("""    type: bar
    x: month
    y: net
    style:
      axis_y:
        labels:
          format:
            spec: ",.0f"
            prefix: "EUR "
            repeat: every
            sign_placement: before_prefix""")
    )
    assert ">−EUR 50,000<" in svg, svg


def test_before_prefix_via_an_alias_on_an_anchored_layer_axis_fails_resolve() -> None:
    board = _board("""    type: bar
    x: month
    y: net
    layers:
      - type: line
        y: net
        axis_y:
          position: right
          labels:
            format:
              spec: eur
              repeat: anchor""").replace(
        "title: T\n",
        'title: T\nstyle:\n  formats:\n    eur:\n      prefix: "€"\n'
        "      sign_placement: before_prefix\n",
    )
    svg = html.unescape(render_board_to_svg(board))
    assert _CODE in svg, svg
    assert "charts.c.layers[0].axis_y.labels.format" in svg


def test_before_prefix_on_an_anchored_x_axis_fails_resolve() -> None:
    board = _board("""    type: scatter
    x: net
    y: net
    style:
      axis_x:
        labels:
          format:
            spec: eur
            sign_placement: before_prefix""").replace(
        "title: T\n",
        'title: T\nstyle:\n  formats:\n    eur:\n      prefix: "EUR "\n'
        "      repeat: anchor\n",
    )
    svg = html.unescape(render_board_to_svg(board))
    assert _CODE in svg, svg
    assert "charts.c.style.axis_x.labels.format" in svg


@pytest.mark.parametrize("symbol_mode", ["all", "anchors"])
@pytest.mark.parametrize("placement", ["before_prefix", "after_prefix"])
def test_sign_placement_on_a_table_column_fails_resolve(symbol_mode, placement) -> None:
    card = _error_card(
        f"""    type: table
    style:
      symbol_mode: {symbol_mode}
      columns:
        net:
          format:
            spec: ",.0f"
            prefix: "€"
            sign_placement: {placement}""",
        "ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED",
    )
    assert "charts.c.style.columns.net.format" in card


def test_inline_anchor_repeat_with_before_prefix_and_no_prefix_compiles() -> None:
    result = compile_board(
        _board("""    type: bar
    x: month
    y: net
    style:
      axis_y:
        labels:
          format:
            spec: ",.0f"
            suffix: " EUR"
            repeat: anchor
            sign_placement: before_prefix""")
    )
    assert result.errors == []


_TEMPORAL = (
    '      - {month: "2026-01-01", net: -50}\n      - {month: "2026-02-01", net: -40}'
)
_CATEGORY = "      - {month: Jan, net: -50}\n      - {month: Feb, net: -40}"
_NUMERIC = "      - {month: 1, net: -50}\n      - {month: 2, net: -40}"
_ANCHORING_STRIPS = {
    "temporal-x": ("    type: line", _TEMPORAL),
    "sort-null-category": (
        "    type: bar\n    style:\n      orientation: vertical",
        _CATEGORY,
    ),
    # Column block: a series-colored horizontal bar keeps its category order.
    "series-colored-horizontal-bar": (
        "    type: bar\n    color: s",
        "      - {month: Jan, s: A, net: -50}\n      - {month: Feb, s: A, net: -40}",
    ),
}
_REPEATING_STRIPS = {
    "engine-sorted-horizontal-bar": ("    type: bar", _CATEGORY),
    "authored-sort-band-axis": (
        "    type: bar\n    sort:\n      by: net\n      order: desc\n"
        "    style:\n      orientation: vertical",
        _CATEGORY,
    ),
    "left-axis": (
        "    type: line\n    style:\n      axis_y:\n        position: left",
        _TEMPORAL,
    ),
    "quantitative-x": ("    type: line", _NUMERIC),
}


def _strip_board(chart: str, rows: str, sign_placement: str | None) -> str:
    placement = (
        ""
        if sign_placement is None
        else f"\n            sign_placement: {sign_placement}"
    )
    return f"""
title: T
queries:
  q:
    type: values
    rows:
{rows}
charts:
  c:
    query: q
    x: month
    y: net
{chart}
    support_table:
      entries:
        - source: net
          format:
            spec: integer
            prefix: "€"{placement}
rows:
  - c
"""


@pytest.mark.parametrize("strip", _ANCHORING_STRIPS)
def test_before_prefix_on_an_anchoring_strip_fails_render(strip) -> None:
    svg = html.unescape(
        render_board_to_svg(_strip_board(*_ANCHORING_STRIPS[strip], "before_prefix"))
    )
    assert _CODE in svg, svg
    assert "charts.c.support_table.entries[0].format" in svg


@pytest.mark.parametrize("strip", _ANCHORING_STRIPS)
def test_an_anchoring_strip_leads_the_sign_with_the_prefix(strip) -> None:
    svg = html.unescape(
        render_board_to_svg(_strip_board(*_ANCHORING_STRIPS[strip], None))
    )
    assert ">€−50<" in svg and ">−40<" in svg, svg
    assert "ERR-" not in svg


@pytest.mark.parametrize("strip", _REPEATING_STRIPS)
def test_before_prefix_on_a_repeating_strip_renders(strip) -> None:
    svg = html.unescape(
        render_board_to_svg(_strip_board(*_REPEATING_STRIPS[strip], "before_prefix"))
    )
    assert "ERR-" not in svg, svg
    assert ">−€50<" in svg and ">−€40<" in svg, svg
