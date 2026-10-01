"""End-to-end: WARN-DUAL-AXIS-COMPETING-SCALES surfaces from render() for a
layer pinned to its own y-axis side.

Dropping the `dual_axis_competing_scales` entry in `registry.py`'s DETECTORS
would leave this failing while the unit tests in
`tests/render/warnings/test_dual_axis_competing_scales.py` still pass, since
those call `detector.detect()` against a hand-built `WarningContext`.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-DUAL-AXIS-COMPETING-SCALES"

_ROWS = [
    {"month": "2026-01", "revenue": 5.0, "headcount": 40},
    {"month": "2026-02", "revenue": 6.0, "headcount": 44},
    {"month": "2026-03", "revenue": 5.5, "headcount": 47},
]

_YAML = """
title: Probe
charts:
  dual:
    query: q
    type: bar
    x: month
    y: revenue
    layers:
      - type: line
        y: headcount
        axis_y:
          position: right
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - dual
"""


def test_fires_for_layer_pinned_to_its_own_axis() -> None:
    result = compile(_YAML)
    assert result.success and result.board is not None, result.errors
    ok = Mock()
    ok.is_success = True
    ok.data = _ROWS
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    executor = Executor(
        result.board,
        adapter_registry=mock_registry,
        query_registry=result.query_registry,
    )

    rendered = render(result.board, executor, format="svg")

    assert rendered.board_error is None and rendered.output
    (w,) = [w for w in rendered.warnings if w.code == _CODE]
    assert w.chart == "dual"
    assert w.path == "charts.dual.layers.0.axis_y.position"
