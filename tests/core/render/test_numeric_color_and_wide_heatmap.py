"""Vega draws a numeric `color:` as a continuous scale and a multi-measure heatmap as
the measures' values on the y bands; the engine draws the same."""

from __future__ import annotations

import re

from .._svg_render import render_board_to_svg


def _render(chart: str, rows: str) -> str:
    return render_board_to_svg(f"""
title: T
queries:
  q:
    type: values
    rows:
{rows}
charts:
  c:
    query: q
{chart}
rows:
  - c
""")


_SCORES = (
    "      - {month: Jan, orders: 5, score: 10}\n"
    "      - {month: Feb, orders: 7, score: 90}"
)


def test_a_numeric_color_on_a_bar_paints_each_bar_on_the_scale_with_a_gradient_legend() -> (
    None
):
    svg = _render(
        "    type: bar\n    x: month\n    y: orders\n    color: score", _SCORES
    )
    fills = re.findall(r'<path d="[^"]*" fill="(rgb[^"]*)"', svg)
    assert len(set(fills)) == 2, fills
    assert svg.count("<linearGradient") == 1
    assert "role-legend-symbol" not in svg


def test_a_numeric_color_on_a_line_draws_one_line_with_a_symbol_legend_of_the_scale() -> (
    None
):
    svg = _render(
        "    type: line\n    x: month\n    y: orders\n    color: score", _SCORES
    )
    assert svg.count("<linearGradient") == 0
    assert svg.count("role-legend-symbol") > 2
    assert len(set(re.findall(r'data-dct-role="point"[^>]*', svg))) <= 2


def test_a_multi_measure_heatmap_puts_each_measures_values_on_the_y_bands() -> None:
    svg = _render(
        "    type: heatmap\n    x: region\n    y: [a, b]",
        "      - {region: N, a: 1, b: 2}\n"
        "      - {region: S, a: 2, b: 3}\n"
        "      - {region: E, a: 1, b: 3}",
    )
    texts = re.findall(r">([^<>]+)</text>", svg)
    assert [t for t in texts if t in {"1", "2", "3"}] == ["1", "2", "3"]
    assert {"A", "B"} <= set(texts)
    assert "ERR-" not in svg
    assert 'aria-label="region: N; a: 1"' in svg


def test_an_authored_hidden_legend_stays_hidden_under_a_numeric_color_with_endpoint_labels() -> (
    None
):
    def legend_marks(legend: str) -> int:
        svg = _render(
            "    type: bar\n    x: month\n    y: orders\n    color: score\n"
            "    style:\n      endpoint_labels:\n        visible: true\n"
            f"      legend:\n        visible: {legend}",
            _SCORES,
        )
        return svg.count("role-legend")

    assert legend_marks("false") == 0
    assert legend_marks("auto") > 0


def test_a_numeric_color_bar_tooltip_prints_the_color_with_the_columns_format() -> None:
    svg = render_board_to_svg("""
title: T
style:
  formats:
    score_format: "$,.0f"
queries:
  q:
    type: values
    formats:
      score: score_format
    rows:
      - {month: Jan, orders: 5, score: 10}
      - {month: Feb, orders: 7, score: 90}
charts:
  c:
    query: q
    type: bar
    x: month
    y: orders
    color: score
rows:
  - c
""")
    assert "\u2062$10;" in svg and "\u2062$90;" in svg


def test_a_null_numeric_color_cell_draws_no_bar_but_keeps_its_category() -> None:
    svg = _render(
        "    type: bar\n    x: month\n    y: orders\n    color: score",
        "      - {month: Jan, orders: 5, score: 10}\n"
        "      - {month: Feb, orders: 7, score: null}\n"
        "      - {month: Mar, orders: 6, score: 90}",
    )
    assert "ERR-" not in svg
    assert "Jan, Feb, Mar" in svg
    assert "\u2061Feb;" not in svg
    assert "\u2061Jan;" in svg and "\u2061Mar;" in svg


def test_a_multi_measure_heatmap_draws_themed_axes_and_honors_authored_titles() -> None:
    svg = _render(
        "    type: heatmap\n    x: region\n    y: [a, b]\n"
        "    x_label: Sales region\n    y_label: Score",
        "      - {region: N, a: 1, b: 2}\n      - {region: S, a: 2, b: 3}",
    )
    assert re.search(r"fill=\"#222222\">N<", svg)
    assert re.search(r"font-size=\"11\"[^>]*>Sales region<", svg)
    assert re.search(r"font-size=\"11\"[^>]*>Score<", svg)
    assert 'fill="#000"' not in svg and "rotate(270)" not in svg


_GROUPED = (
    "      - {month: Jan, grp: x, orders: 5, score: 10, extra: 3}\n"
    "      - {month: Feb, grp: x, orders: 7, score: 90, extra: 4}\n"
    "      - {month: Jan, grp: y, orders: 6, score: 40, extra: 2}\n"
    "      - {month: Feb, grp: y, orders: 8, score: 70, extra: 5}"
)


def test_a_numeric_color_on_faceted_lines_colors_each_panel_and_names_the_scale_once() -> (
    None
):
    svg = _render(
        "    type: line\n    x: month\n    y: orders\n    color: score\n"
        "    multiples:\n      columns: grp",
        _GROUPED,
    )
    strokes = set(re.findall(r'<path d="M[^"]*" fill="none" stroke="(rgb[^"]*)"', svg))
    assert len(strokes) == 2, strokes
    assert svg.count("role-legend-symbol") > 2 and svg.count(' role-legend"') == 1


def test_a_numeric_color_under_a_bar_layer_keeps_one_gradient_and_only_the_layers_series() -> (
    None
):
    svg = _render(
        "    type: bar\n    x: month\n    y: orders\n    color: score\n"
        "    layers:\n      - type: line\n        y: extra",
        _GROUPED,
    )
    texts = re.findall(r">([^<>]+)</text>", svg)
    assert svg.count("<linearGradient") == 1
    assert "Extra" in texts and not {"10", "70", "90"} & set(texts)


def test_rows_at_one_x_with_different_numeric_colors_stack_one_segment_each() -> None:
    svg = _render(
        "    type: bar\n    x: month\n    y: orders\n    color: score",
        "      - {month: Jan, orders: 5, score: 10}\n"
        "      - {month: Jan, orders: 4, score: 90}\n"
        "      - {month: Feb, orders: 6, score: 50}",
    )
    fills = re.findall(
        r'<(?:path|rect)[^>]*fill="(rgb[^"]*)"[^>]*data-dct-role="bar"', svg
    )
    assert len(fills) == 3 and len(set(fills)) == 3, fills


def test_a_support_table_on_a_chart_with_no_x_names_the_missing_x() -> None:
    from dbt_charts.core.compile import compile as compile_board

    from .._svg_render import render_board_svg_result

    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {a: 1}
charts:
  c:
    query: q
    type: line
    y: a
    support_table:
      entries:
        - source: a
rows:
  - c
"""
    assert compile_board(board).success
    errors = render_board_svg_result(board).chart_errors
    assert [e.code for e in errors] == ["ERR-SUPPORT-TABLE-NO-X-ENCODING"]


def test_a_link_naming_a_channel_the_chart_does_not_encode_raises_a_coded_error() -> (
    None
):
    from .._svg_render import render_board_svg_result

    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {m: Jan, v: 1}
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
    link: "/x?c={{ color }}"
rows:
  - c
"""
    errors = render_board_svg_result(board).chart_errors
    assert [e.code for e in errors] == ["ERR-LINK-CHANNEL-NOT-ENCODED"]
    assert "{{ color }}" in errors[0].message


def test_a_pie_channel_in_a_cartesian_link_raises_the_coded_error() -> None:
    from .._svg_render import render_board_svg_result

    board = """
title: T
queries:
  q:
    type: values
    rows:
      - {m: Jan, s: a, v: 1}
charts:
  c:
    query: q
    type: bar
    x: m
    y: v
    color: s
    link: "/x?t={{ theta }}"
rows:
  - c
"""
    errors = render_board_svg_result(board).chart_errors
    assert [e.code for e in errors] == ["ERR-LINK-CHANNEL-NOT-ENCODED"]


def test_a_faceted_bar_keeps_a_null_color_category_in_its_panel() -> None:
    rows = "\n".join(
        f"      - {{month: {m}, panel: {p}, orders: {o}, score: {sc}}}"
        for m, p, o, sc in [
            ("Jan", "a", 5, 10),
            ("Feb", "a", 7, "null"),
            ("Mar", "a", 6, 90),
            ("Jan", "b", 5, 20),
            ("Feb", "b", 7, 30),
            ("Mar", "b", 6, 90),
        ]
    )
    svg = _render(
        "    type: bar\n    x: month\n    y: orders\n    color: score\n"
        "    multiples:\n      columns: panel",
        rows,
    )
    assert "ERR-" not in svg
    assert svg.count("3 values: Jan, Feb, Mar") == 2
