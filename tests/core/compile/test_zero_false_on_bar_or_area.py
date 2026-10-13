"""`axis_y.scale.continuous.zero: false` crops a bar or area away from the zero its
length or fill is measured from: a bar errors at compile, an area warns.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile as compile_board


def _board(chart_type: str, *, y_start: bool = False) -> str:
    return f"""
queries:
  q:
    columns: [m, lo, v]
    values:
      - [A, 90, 100]
      - [B, 95, 120]
charts:
  c:
    query: q
    type: {chart_type}
    x: m
    y: v
{"    y_start: lo" if y_start else ""}
    style:
      axis_y:
        scale:
          continuous:
            zero: false
rows:
  - c
"""


def test_bar_zero_false_errors_naming_the_key() -> None:
    result = compile_board(_board("bar"))
    assert [e.code for e in result.errors] == ["ERR-BAR-ZERO-BASELINE-CROPPED"]
    message = result.errors[0].message
    assert "axis_y.scale.continuous.zero" in message
    assert "type: line" in message


def test_bar_with_y_start_zero_false_is_exempt() -> None:
    result = compile_board(_board("bar", y_start=True))
    assert not result.errors
    assert "WARN-AREA-ZERO-BASELINE-CROPPED" not in [w.code for w in result.warnings]


def test_area_zero_false_warns() -> None:
    result = compile_board(_board("area"))
    assert not result.errors
    assert [w.code for w in result.warnings] == ["WARN-AREA-ZERO-BASELINE-CROPPED"]


def test_line_zero_false_is_silent() -> None:
    result = compile_board(_board("line"))
    assert not result.errors
    assert not result.warnings
