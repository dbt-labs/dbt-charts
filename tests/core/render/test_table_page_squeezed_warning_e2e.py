"""End-to-end: TABLE_PAGE_SQUEEZED surfaces from render() for a table whose
layout slot is shorter than the page its own pagination settled on.

Proves the full seam — the table renderer records the squeeze at the point the
slot overrides its page size, renderer.py threads that capture into the
WarningContext, and the detector's warning lands on RenderResult.warnings —
and that a table whose slot fits its page stays silent.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render


def _rows(n: int) -> list[dict[str, object]]:
    return [{"name": f"row_{i}", "value": i} for i in range(1, n + 1)]


def _make_executor(
    board: object, query_registry: object, rows: list[dict[str, object]]
):
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


def _board(slot_height: str) -> str:
    return f"""
title: Squeezed table
charts:
  orgs:
    query: q
    type: table
    style:
      pagination:
        enabled: true
        page_rows: 20
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - height: {slot_height}
    rows:
      - orgs
"""


def _warnings(board_yaml: str, n_rows: int):
    result = compile(board_yaml)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(n_rows))
    return render(result.board, executor, format="svg").warnings


def test_a_slot_too_short_for_the_page_warns() -> None:
    """A 220px slot cannot draw the 20 rows the table's page holds."""
    warnings = _warnings(_board("220px"), 40)

    warning = next(w for w in warnings if w.code == "WARN-TABLE-PAGE-SQUEEZED")
    assert warning.chart == "orgs"
    assert warning.fix
    assert "of the 20 rows a page holds" in warning.message
    assert "40 rows total" in warning.message


def test_a_slot_that_fits_the_page_is_silent() -> None:
    """Room for every row the page holds — nothing was squeezed."""
    warnings = _warnings(_board("900px"), 40)

    assert "WARN-TABLE-PAGE-SQUEEZED" not in {w.code for w in warnings}


_AUTO_HEIGHT_WRAPPING = """
title: Auto-sized table
style:
  frame:
    width: 400
charts:
  orgs:
    query: q
    type: table
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - orgs
"""


def _wrapping_rows(n: int) -> list[dict[str, object]]:
    """Notes of every length, so some wrap differently if the widths disagree."""
    return [
        {"name": f"row_{i}", "note": " ".join(["word"] * (i + 1)), "value": i}
        for i in range(n)
    ]


def test_an_auto_sized_table_with_wrapping_rows_is_silent() -> None:
    """The sizer reserves the renderer's wrapped row heights, so nothing squeezes."""
    result = compile(_AUTO_HEIGHT_WRAPPING)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _wrapping_rows(30))
    warnings = render(result.board, executor, format="svg").warnings

    assert "WARN-TABLE-PAGE-SQUEEZED" not in {w.code for w in warnings}


def test_a_render_only_table_failure_leaves_the_board_rendering() -> None:
    """Sizing runs the renderer; its failures must become the chart's error card."""
    board = """
title: One bad table
charts:
  bad:
    query: q
    type: table
    link: /x/{{ name | upper }}
  good:
    query: q
    type: table
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - bad
  - good
"""
    result = compile(board)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(3))
    rendered = render(result.board, executor, format="svg")

    assert rendered.output is not None
    assert "row_3" in rendered.output
    assert any(e.fields.get("chart_id") == "bad" for e in rendered.chart_errors), (
        rendered.chart_errors
    )
