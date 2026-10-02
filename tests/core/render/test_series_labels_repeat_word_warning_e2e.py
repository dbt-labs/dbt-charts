"""End-to-end: WARN-SERIES-LABELS-REPEAT-WORD surfaces from render() for a
wide chart whose measure labels both repeat a word.

Proves the full seam -- compile() -> render() -> RenderResult.warnings --
not a hand-built WarningContext. Dropping the pass-through in renderer.py
or the `series_labels_repeat_word` entry in registry.py's `_DATA_DETECTORS`
would leave this failing while every unit test in
`tests/render/warnings/test_series_labels_repeat_word.py` still passes,
since those tests call `detector.detect()` directly against a hand-built
`WarningContext`.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

_CODE = "WARN-SERIES-LABELS-REPEAT-WORD"

_ROWS = [{"month": "Jan", "documents_created": 1, "documents_completed": 2}]

_YAML = """
title: Probe
charts:
  volume:
    query: q
    type: line
    x: month
    y: [documents_created, documents_completed]
    title: Document Volume
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - volume
"""


def _render() -> list[object]:
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
    render_result = render(result.board, executor, format="svg")
    return list(render_result.warnings)


def test_fires_for_wide_measures_repeating_the_titles_word() -> None:
    warnings = _render()
    codes = {w.code for w in warnings}
    assert _CODE in codes, codes
    w = next(w for w in warnings if w.code == _CODE)
    assert w.chart == "volume"
    assert w.fix
