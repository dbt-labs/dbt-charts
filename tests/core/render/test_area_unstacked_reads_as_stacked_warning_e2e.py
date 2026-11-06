"""End-to-end: WARN-AREA-UNSTACKED-READS-AS-STACKED surfaces from render()
for an unstacked area chart whose two series never cross.

Proves the full seam -- compile() -> render() -> RenderResult.warnings --
not a hand-built WarningContext. Dropping the pass-through in renderer.py
or the `area_unstacked_reads_as_stacked` entry in
`registry.py`'s `_DATA_DETECTORS` would leave this failing while every unit
test in `tests/render/warnings/test_area_unstacked_reads_as_stacked.py`
still passes, since those tests call `detector.detect()` directly against a
hand-built `WarningContext`.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile
from dbt_charts.core.render import render

from ..._run import Run
from ..._scripted_env import ScriptedBackend, scripted_env

_CODE = "WARN-AREA-UNSTACKED-READS-AS-STACKED"

# Two series, s0 and s1, offset far enough apart at every shared x that
# neither ever crosses the other -- s0 always nests inside s1's band.
_NESTED_ROWS = [
    {"x": x, "series": series, "val": offset + x}
    for x in range(4)
    for series, offset in (("s0", 1000), ("s1", 0))
]

_YAML = """
title: Probe
charts:
  area1:
    query: q
    type: area
    x: x
    y: val
    color: series
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - area1
"""


def _render() -> list[object]:
    result = compile(_YAML)
    assert result.success and result.board is not None, result.errors
    mock_registry = scripted_env(ScriptedBackend(_NESTED_ROWS))
    executor = Run(
        result.board,
        env=mock_registry,
        query_registry=result.query_registry,
    )
    render_result = render(result.board, executor.ctx, executor.ledger, format="svg")
    return list(render_result.warnings)


def test_fires_for_unstacked_area_with_two_never_crossing_series() -> None:
    warnings = _render()
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "area1"
    assert w.fix
