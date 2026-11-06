"""A layer label reads queries and variables like the chart title does."""

from dbt_charts.core.compile import compile as compile_board

from .._svg_render import render_board_to_svg

BOARD = """
variables:
  unit:
    options:
      static: [pct, abs]
    default: pct
queries:
  q:
    columns: [m, rate, med]
    values:
      - ["2026-01-01", 31, 39]
      - ["2026-02-01", 44, 39]
  s:
    columns: [med]
    values:
      - [39]
charts:
  c:
    query: q
    type: line
    x: m
    y: rate
    height: 200
    layers:
      - type: line
        y: med
        label: "Median {{ queries.s.one.med }} {{ unit }}"
rows:
  - c
"""


def test_compile_collects_the_queries_and_variables_a_layer_label_reads() -> None:
    compiled = compile_board(BOARD)

    assert compiled.success, compiled.errors
    chart = compiled.board.charts["c"]
    assert chart.text_queries == {"s": "s"}
    assert "unit" in chart.variable_dependencies


def test_a_layer_label_renders_its_query_and_variable() -> None:
    assert "Median 39 pct" in render_board_to_svg(BOARD)
