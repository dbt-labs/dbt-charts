"""Chart-local validators judge what the chart authored, never inherited board style."""

from __future__ import annotations

from dbt_charts.core.compile import compile as compile_board

_CHART = """
queries:
  q:
    columns: [m, v]
    values:
      - [A, 100]
      - [B, 120]
charts:
  c:
    query: q
    type: {chart_type}
    x: m
    y: v
rows:
  - c
"""


def _board(chart_type: str, board_style: str) -> str:
    return f"style:\n{board_style}\n" + _CHART.format(chart_type=chart_type)


def test_board_level_zero_false_does_not_raise() -> None:
    style = """  charts:
    axis_y:
      scale:
        continuous:
          zero: false
"""
    result = compile_board(_board("bar", style))
    assert not result.errors
    assert "WARN-AREA-ZERO-BASELINE-CROPPED" not in [w.code for w in result.warnings]


def test_board_format_typo_reports_style_path() -> None:
    style = """  charts:
    axis_y:
      labels:
        format: percent_1
"""
    result = compile_board(_board("line", style))
    assert [e.code for e in result.errors] == ["ERR-FORMAT-INVALID"]
    assert "style.charts.axis_y.labels.format" in result.errors[0].message


def test_theme_align_does_not_warn() -> None:
    style = """  charts:
    axis_y:
      position: right
      labels:
        align: left
"""
    result = compile_board(_board("line", style))
    assert not result.errors
    assert "WARN-AXIS-ALIGN-DISCARDED" not in [w.code for w in result.warnings]


def test_chart_local_align_warns() -> None:
    yaml_text = _CHART.format(chart_type="line").replace(
        "    y: v\n",
        "    y: v\n    style:\n      axis_y:\n        position: right\n"
        "        labels:\n          align: left\n",
    )
    result = compile_board(yaml_text)
    assert [w.code for w in result.warnings] == ["WARN-AXIS-ALIGN-DISCARDED"]


def test_format_error_order_follows_model_field_order() -> None:
    yaml_text = _CHART.format(chart_type="bar").replace(
        "    y: v\n",
        "    y: v\n    style:\n      axis_y:\n        labels:\n"
        "          format: percent_1\n    layers:\n      - type: line\n"
        "        y: v\n        axis_y:\n          labels:\n"
        "            format: bogus_9\n",
    )
    result = compile_board(yaml_text)
    assert "percent_1" in result.errors[0].message
