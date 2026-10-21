"""Tests for the chart hover runtime as a host-shipped layer.

The runtime is one static script every host ships beside the board; the theme
facts it needs ride on the board root as data attributes. A board is a picture:
code never ships inside it, and no theme value is templated into the script.
Behavioral coverage of the runtime's own logic lives in
libs/playground/tests/js/chart-interactivity-runtime.test.js.
"""

from __future__ import annotations

from typing import Any


def _resolved_style() -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style("stark"))


def test_the_source_is_static_and_carries_no_placeholders() -> None:
    from dbt_charts.core.render.chart_interactivity import hover_runtime_source

    source = hover_runtime_source()

    assert "__DCT_" not in source
    assert "<script" not in source
    assert "readBoardConfig" in source
    assert "window.dbtChartHover" in source


def test_the_board_root_publishes_the_theme_facts() -> None:
    from dbt_charts.core.render.chart_interactivity import hover_runtime_attributes

    attrs = hover_runtime_attributes(_resolved_style())

    assert 'data-dbt-font-family="' in attrs
    assert 'data-dbt-tooltip-style="' in attrs
    assert 'data-dbt-hover-emphasis="' in attrs


def test_null_display_is_the_shared_constant_not_a_js_literal() -> None:
    """formatValue()'s null case must read the substituted NULL_DISPLAY value,
    not a hardcoded JS literal -- the tooltip surface is the one place a null
    could still diverge from every other surface by editing only one side."""
    import json

    from dbt_charts.core.render.chart_interactivity import hover_runtime_source
    from dbt_charts.core.text.format_d3 import NULL_DISPLAY

    source = hover_runtime_source()

    assert f"return '{NULL_DISPLAY}';" not in source
    assert f"return {json.dumps(NULL_DISPLAY)};" in source


def test_a_rendered_board_carries_the_facts_and_no_script() -> None:
    from .._svg_render import render_board_to_svg  # noqa: PLC0415

    svg = render_board_to_svg()

    assert "<script" not in svg
    assert 'data-dbt-tooltip-style="' in svg


def test_tooltip_style_blob_carries_swatch() -> None:
    """The swatch size/shape is a cascade value in the published style blob,
    not a hardcoded literal in the runtime."""
    from dbt_charts.core.render.chart_interactivity import _build_tooltip_style_dict

    blob = _build_tooltip_style_dict(_resolved_style())

    assert "swatch" in blob
    assert blob["swatch"]["size"] > 0
    assert "radius" in blob["swatch"]


def test_multi_chart_board_isolates_shared_x_marks_per_chart() -> None:
    """Precondition for ``.dbt-chart`` grouping: two charts sharing an x value
    must render into separate subtrees, or a board-wide hover query would
    collect marks from both."""
    import re

    from .._svg_render import render_board_to_svg  # noqa: PLC0415

    board = """
title: Two charts sharing x
queries:
  q:
    type: values
    rows:
      - {month: Jan, region: North, revenue: 100}
      - {month: Jan, region: South, revenue: 40}
      - {month: Feb, region: North, revenue: 150}
      - {month: Feb, region: South, revenue: 60}
charts:
  a: {query: q, type: bar, x: month, y: revenue, color: region}
  b: {query: q, type: bar, x: month, y: revenue, color: region}
rows: [a, b]
"""
    svg = render_board_to_svg(board)
    ia, ib = svg.index('id="chart-a"'), svg.index('id="chart-b"')
    assert ia < ib, "expected two distinct .dbt-chart subtrees on the board"
    assert 'class="dbt-chart" id="chart-a"' in svg
    assert 'class="dbt-chart" id="chart-b"' in svg

    def data_mark_labels(chunk: str) -> list[str]:
        return [
            label
            for label in re.findall(r'aria-label="([^"]*)"', chunk)
            if "Revenue" in label and ("Jan" in label or "Feb" in label)
        ]

    chart_a, chart_b = data_mark_labels(svg[ia:ib]), data_mark_labels(svg[ib:])
    assert any("Jan" in m for m in chart_a) and any("Feb" in m for m in chart_a)
    assert any("Jan" in m for m in chart_b) and any("Feb" in m for m in chart_b)


def test_aria_label_wire_format_matches_runtime_parser_contract() -> None:
    """Pins the emitted aria-label's separator/prefix conventions the JS
    runtime's parseAriaLabel depends on (ROLE_HEADER-prefixed header, '; '
    row separator, 'Label: value' dependent rows). The JS suite hand-builds
    its own labels with these conventions, so a drift in the real emitter
    would round-trip there unnoticed; this renders a real chart.
    """
    import re

    from dbt_charts.core.render.chart.emitters._tooltip import ROLE_HEADER

    from .._svg_render import render_board_to_svg  # noqa: PLC0415

    board = """
title: Aria label wire format
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  a: {query: q, type: bar, x: month, y: revenue}
"""
    svg = render_board_to_svg(board)
    header_labels = [
        label
        for label in re.findall(r'aria-label="([^"]*)"', svg)
        if label.startswith(ROLE_HEADER)
    ]

    # A single row's own value defines the axis domain, so the hover band adds
    # no layer here: only the real bar's aria-label is present.
    assert header_labels == [f"{ROLE_HEADER}Jan; Revenue: 100"]
