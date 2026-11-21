"""A `category_colors` pin for a field no chart colors by must warn, not do nothing."""

from __future__ import annotations

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics import WARN_CATEGORY_COLOR_PIN_ORPHAN

_BOARD = """
queries:
  q:
    type: values
    rows:
      - {{month: Jan, gamma: 3, alpha: 1}}
style:
  charts:
    category_colors:
      {pinned}:
        values:
          gamma: "#111111"
charts:
  c:
    type: bar
    query: q
    x: month
    y: [gamma, alpha]
rows:
  - c
"""


def _codes(pinned: str) -> list[str]:
    result = compile(_BOARD.format(pinned=pinned))
    assert result.success, result.errors
    return [w.code for w in result.warnings]


def test_a_pin_aimed_at_a_y_measure_warns() -> None:
    assert WARN_CATEGORY_COLOR_PIN_ORPHAN.code in _codes("gamma")


def test_a_pin_on_a_field_a_chart_colors_by_does_not_warn() -> None:
    text = _BOARD.format(pinned="month").replace(
        "    y: [gamma, alpha]", "    y: gamma\n    color: month"
    )
    result = compile(text)
    assert result.success, result.errors
    assert WARN_CATEGORY_COLOR_PIN_ORPHAN.code not in [w.code for w in result.warnings]
