"""A nested board's variable band is compact: shorter controls, a set gap above.

The root board's band keeps the theme's input height. Sizing and render read the
same style, so the band they reserve and draw agree.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.render.boards import _render_variables_band
from dbt_charts.core.render.sizing import compute_variable_controls_height

_BOARD = """
title: T
variables:
  root_pick: {input: select, options: {static: [x, y]}, default: x}
queries:
  q: {type: values, rows: [{k: a, v: 1}]}
charts:
  c1: {query: q, type: bar, x: k, y: v}
rows:
  - title: Nested
    text: Subtitle.
    style:
      variables:
        position: top
    variables:
      nested_pick: {input: select, options: {static: [x, y]}, default: x}
      nested_buttons: {input: radio, options: {static: [p, q]}}
    rows: [c1]
"""


def _boards():
    result = compile_board(_BOARD)
    assert result.success, result.diagnostics
    root = result.board
    return root, root.layout.items[0].board


def test_a_nested_band_uses_the_compact_height_and_the_root_keeps_its_own() -> None:
    root, nested = _boards()
    compact = nested.resolved_style.variables.compact_height

    nested_vars = nested.resolved_style.variables
    assert nested_vars.input.height == compact
    assert nested_vars.container_height == compact
    root_vars = root.resolved_style.variables
    assert root_vars.input.height != compact
    assert root_vars.container_height != compact


class _NoQueries:
    cache_hit_ats: list = []

    def execute_query(self, name, variables):
        raise AssertionError(name)


def test_the_band_sizing_reserves_is_the_band_render_draws() -> None:
    _, nested = _boards()
    style = nested.resolved_style
    width = 400.0

    reserved = compute_variable_controls_height(
        nested.visible_variables, width, {}, style.variables
    )
    svg, drawn = _render_variables_band(
        nested.visible_variables, {}, width, _NoQueries(), style, "start", "variables"
    )

    assert reserved == drawn
    heights = {
        float(e.get("height"))
        for e in ET.fromstring(
            f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>"
        ).iter()
        if e.get("data-dbt-field") in ("input", "buttons")
    }
    assert heights == {style.variables.compact_height}


def test_the_gap_above_a_nested_band_is_the_variables_gap() -> None:
    from dbt_charts.core.render.sizing import compute_board_content_box

    _, nested = _boards()
    gap = float(nested.resolved_style.variables.gap)
    other_gap = gap + 7.0

    with_title = compute_board_content_box(
        title_height=20.0,
        text_height=0.0,
        variables_height=24.0,
        inline_band_height=0.0,
        gap=other_gap,
        variables_gap=gap,
        has_layout_items=False,
        opens_with_text=False,
        card_padding=0.0,
    )
    without_band = compute_board_content_box(
        title_height=20.0,
        text_height=0.0,
        variables_height=0.0,
        inline_band_height=0.0,
        gap=other_gap,
        variables_gap=gap,
        has_layout_items=False,
        opens_with_text=False,
        card_padding=0.0,
    )

    assert with_title.non_layout_height - without_band.non_layout_height == 24.0 + gap


def test_a_nested_board_that_authors_its_own_height_keeps_it() -> None:
    authored = _BOARD.replace(
        "        position: top\n",
        "        position: top\n        input:\n          height: 40\n",
    )
    result = compile_board(authored)
    assert result.success, result.diagnostics
    nested = result.board.layout.items[0].board

    nested_vars = nested.resolved_style.variables
    assert nested_vars.input.height == 40
    assert nested_vars.container_height == nested_vars.compact_height
