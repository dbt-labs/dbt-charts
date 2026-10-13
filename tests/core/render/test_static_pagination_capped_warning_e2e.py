"""End-to-end: STATIC_PAGINATION_CAPPED surfaces from render() only when a
static export is capped AND its in-file cap note could not be drawn.

A capped export whose slot has room states the cap inside the file, so it
stays silent; a slot too short for the note would otherwise lose every row
past the cap with no signal at all. The text format states its own row and
page counts and carries no static file, so it never warns.
"""

from __future__ import annotations

from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import RenderResult, render

_STATIC_MULTI_PAGE_MAX_PAGES = 20


def _rows(n: int) -> list[dict[str, object]]:
    return [{"name": f"row_{i}", "value": i} for i in range(1, n + 1)]


def _make_executor(
    board: Board,
    query_registry: dict[str, AnyQuery],
    rows: list[dict[str, object]],
) -> Executor:
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


_BOARD = """
title: Big table
charts:
  orgs:
    query: q
    type: table
    style:
      pagination:
        enabled: true
        page_rows: 5
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - {slot}orgs
"""

# A 150px row cannot hold the cap note's band under the pager; a roomy
# (auto-sized) slot can.
_SHORT_SLOT = _BOARD.replace("{slot}", "height: 150\n    cols:\n      - ")
_ROOMY_SLOT = _BOARD.replace("{slot}", "")
_CODE = "WARN-STATIC-PAGINATION-CAPPED"
_CAPPED_ROWS = (_STATIC_MULTI_PAGE_MAX_PAGES + 5) * 5  # page_rows=5


def _render(yaml_text: str, n_rows: int, fmt: str) -> RenderResult:
    result = compile(yaml_text)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(n_rows))
    return render(result.board, executor, format=fmt)


def test_capped_table_in_a_slot_too_short_for_the_note_warns() -> None:
    render_result = _render(_SHORT_SLOT, _CAPPED_ROWS, "svg")

    assert "in this static export" not in str(render_result.output)
    warning = next(w for w in render_result.warnings if w.code == _CODE)
    assert warning.chart == "orgs"
    assert warning.fix
    assert str(_STATIC_MULTI_PAGE_MAX_PAGES) in warning.message


def test_capped_table_whose_note_is_drawn_is_silent() -> None:
    render_result = _render(_ROOMY_SLOT, _CAPPED_ROWS, "svg")

    assert "in this static export" in str(render_result.output)
    assert _CODE not in {w.code for w in render_result.warnings}


def test_text_render_of_a_capped_table_is_silent() -> None:
    render_result = _render(_SHORT_SLOT, _CAPPED_ROWS, "text")

    assert _CODE not in {w.code for w in render_result.warnings}


def test_table_within_cap_is_silent() -> None:
    render_result = _render(_SHORT_SLOT, 20, "svg")

    assert _CODE not in {w.code for w in render_result.warnings}
