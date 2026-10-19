"""Tests for the ``data`` render format — flat, slug-keyed queries + charts."""

from __future__ import annotations

import datetime
import json
import uuid
from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.board.normalized import Board, Layout, LayoutItem
from dbt_charts.core.compile.models.primitives import BoardLinks
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.data_format import render_board_data
from dbt_charts.core.render.errors import RenderError

from .._board_utils import _default_chart_style_context, _default_resolved_style


def _make_executor(data: list[dict[str, Any]]) -> MagicMock:
    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_chart.return_value = data
    executor.render_with_range_filters.return_value = None
    return executor


def _make_board(
    charts: list[Any],
    queries: dict[str, Any] | None = None,
    nested: Board | None = None,
) -> Board:
    items = [LayoutItem(type="chart", chart=c, width=600, height=300) for c in charts]
    if nested is not None:
        items.append(LayoutItem(type="board", board=nested, width=600, height=300))
    return Board(
        id="test-board",
        title="Test Board",
        queries=queries or {"q": SqlQuery(sql="SELECT 1", source="test_profile")},
        layout=Layout(type="rows", items=items, width=600, height=600),
        resolved_style=_default_resolved_style(),
        chart_style_context=_default_chart_style_context(),
        level=1,
    )


def _render(board: Board, executor: MagicMock, **kwargs: Any) -> dict[str, Any]:
    return json.loads(render_board_data(board, executor, {}, **kwargs))


def test_shared_query_rows_are_emitted_once(make_chart: Callable[..., Any]) -> None:
    """Two charts on one query must not duplicate the rows — the whole point."""
    rows = [{"month": "jan", "revenue": 5}]
    board = _make_board(
        [
            make_chart("bar", id="revenue_bar", x="month", y="revenue"),
            make_chart("line", id="revenue_line", x="month", y="revenue"),
        ]
    )
    result = _render(board, _make_executor(rows))

    assert list(result["queries"]) == ["q"]
    assert result["queries"]["q"]["rows"] == rows
    assert set(result["charts"]) == {"revenue_bar", "revenue_line"}
    # Charts reference the query by name and carry no rows of their own.
    for chart in result["charts"].values():
        assert chart["query"] == "q"
        assert "rows" not in chart
        assert "data" not in chart


def test_chart_entry_carries_identity_and_encoding(
    make_chart: Callable[..., Any],
) -> None:
    board = _make_board(
        [
            make_chart(
                "bar",
                id="revenue_bar",
                x="month",
                y="revenue",
                title="Revenue",
                notes="Monthly revenue",
            )
        ]
    )
    chart = _render(board, _make_executor([{"month": "jan", "revenue": 5}]))["charts"][
        "revenue_bar"
    ]

    assert chart["type"] == "bar"
    assert chart["title"] == "Revenue"
    assert chart["notes"] == "Monthly revenue"
    assert chart["x"] == "month"
    assert chart["y"] == "revenue"


def test_query_entry_carries_sql_and_type(make_chart: Callable[..., Any]) -> None:
    """The SQL reported is the one the chart actually runs."""
    board = _make_board(
        [
            make_chart(
                "bar",
                id="revenue_bar",
                x="month",
                y="revenue",
                query=SqlQuery(
                    sql="SELECT month, revenue FROM t", source="test_profile"
                ),
            )
        ]
    )
    query = _render(board, _make_executor([{"month": "jan", "revenue": 5}]))["queries"][
        "q"
    ]

    assert query["sql"] == "SELECT month, revenue FROM t"
    assert query["type"] == "sql"


def test_nested_board_charts_flatten_into_one_map(
    make_chart: Callable[..., Any],
) -> None:
    """Nesting is a layout concern; the data view is flat and globally keyed."""
    nested = _make_board([make_chart("line", id="nested_line", x="month", y="revenue")])
    board = _make_board(
        [make_chart("bar", id="outer_bar", x="month", y="revenue")], nested=nested
    )
    result = _render(board, _make_executor([{"month": "jan", "revenue": 5}]))

    assert set(result["charts"]) == {"outer_bar", "nested_line"}
    assert list(result["queries"]) == ["q"]


def test_row_cap_truncation_is_recorded_on_the_query(
    make_chart: Callable[..., Any],
) -> None:
    rows = [{"month": f"m{i}", "revenue": i} for i in range(5)]
    board = _make_board([make_chart("bar", id="revenue_bar", x="month", y="revenue")])
    query = _render(board, _make_executor(rows), max_rows_per_query=2)["queries"]["q"]

    assert query["rows"] == [rows[0], rows[4]]
    assert query["rows_truncated"] == {"head": 1, "tail": 1, "total": 5}


def test_board_identity_and_variables_are_preserved(
    make_chart: Callable[..., Any],
) -> None:
    board = _make_board([make_chart("bar", id="revenue_bar", x="month", y="revenue")])
    result = _render(board, _make_executor([{"month": "jan", "revenue": 5}]))

    assert result["id"] == "test-board"
    assert result["title"] == "Test Board"


def test_two_distinct_queries_are_both_emitted(
    make_chart: Callable[..., Any],
) -> None:
    board = _make_board(
        [
            make_chart("bar", id="rev", query_name="q", x="month", y="revenue"),
            make_chart("line", id="cost", query_name="q2", x="month", y="cost"),
        ],
        queries={
            "q": SqlQuery(sql="SELECT 1", source="test_profile"),
            "q2": SqlQuery(sql="SELECT 2", source="test_profile"),
        },
    )
    result = _render(board, _make_executor([{"month": "jan", "revenue": 5}]))

    assert set(result["queries"]) == {"q", "q2"}
    assert result["charts"]["rev"]["query"] == "q"
    assert result["charts"]["cost"]["query"] == "q2"


def test_errored_chart_is_keyed_by_slug_carrying_its_error(
    make_chart: Callable[..., Any],
) -> None:
    """A chart that fails to execute is visible as a failure, not a hole."""
    board = _make_board([make_chart("bar", id="revenue_bar", x="month", y="revenue")])
    executor = MagicMock(spec=Executor, board=SimpleNamespace(links=BoardLinks()))
    executor.execute_chart.side_effect = ExecutionError("boom")
    result = _render(board, executor)

    assert "revenue_bar" in result["charts"]
    assert result["charts"]["revenue_bar"]["error"]
    # A failed chart references no query, so it fabricates no query entry.
    assert "query" not in result["charts"]["revenue_bar"]
    assert result["queries"] == {}


# --- compiled-board cases (inline queries and query-less charts) --------------

_INLINE_AND_CALLOUT_YAML = """
source: test
charts:
  rev:
    query:
      sql: "SELECT month, revenue FROM orders"
    type: bar
    x: month
    y: revenue
  note:
    type: callout
    message: hello
rows: [rev, note]
"""


def _render_compiled(yaml_text: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    compiled = compile_board(yaml_text)
    assert compiled.board is not None, [e.message for e in compiled.errors]
    return _render(compiled.board, _make_executor(rows))


def test_inline_chart_query_reports_its_real_sql_and_type() -> None:
    """An inline query is registered under a synthetic name, not in
    ``board.queries`` — reading that alone would report it as a values query
    with no SQL, which is a fabricated provenance."""
    result = _render_compiled(
        _INLINE_AND_CALLOUT_YAML, [{"month": "jan", "revenue": 5}]
    )
    query_name = result["charts"]["rev"]["query"]
    query = result["queries"][query_name]

    assert query["type"] == "sql"
    assert "SELECT month, revenue FROM orders" in query["sql"]


_VARIABLE_YAML = """
source: test
variables:
  year:
    input: text
    default: 2024
charts:
  rev:
    query:
      sql: "SELECT year FROM t WHERE year = {{ year }}"
    type: bar
    x: year
    y: year
rows: [rev]
"""


def test_variables_are_the_applied_values_not_the_definitions() -> None:
    """The SQL ships uninterpolated, so the applied values are what make it
    readable — emitting the authored definitions instead would report the
    default (2024) for rows that were produced with 2023."""
    compiled = compile_board(_VARIABLE_YAML)
    assert compiled.board is not None, [e.message for e in compiled.errors]
    result = json.loads(
        render_board_data(
            compiled.board, _make_executor([{"year": 2023}]), {"year": 2023}
        )
    )

    assert result["variables"] == {"year": 2023}


def test_query_less_chart_references_no_query_and_fabricates_none() -> None:
    """A callout has no query; it must not invent one keyed by its own slug."""
    result = _render_compiled(
        _INLINE_AND_CALLOUT_YAML, [{"month": "jan", "revenue": 5}]
    )

    assert "note" in result["charts"]
    assert "query" not in result["charts"]["note"]
    assert "note" not in result["queries"]


def test_decimal_precision_contract_across_formats(
    make_chart: Callable[..., Any],
) -> None:
    """`json` keeps Decimal exact; the re-compilable formats keep it numeric.

    These are two deliberate policies, not an oversight: `yaml` must re-compile
    with a numeric column still numeric, while `json` has no such contract and
    keeps full precision — a NUMERIC(38,2) value would lose its last digits
    through float.
    """
    from dbt_charts.core.render.json_format import render_board_json

    exact = Decimal("123456789012345678.90")
    board = _make_board([make_chart("bar", id="revenue_bar", x="month", y="revenue")])
    rows = [{"month": "jan", "revenue": exact}]

    as_json = json.loads(render_board_json(board, _make_executor(rows), {}))
    assert as_json["items"][0]["data"][0]["revenue"] == str(exact)

    as_data = _render(board, _make_executor(rows))
    assert as_data["queries"]["q"]["rows"][0]["revenue"] == float(exact)


def test_exotic_warehouse_scalars_pin_the_data_format_wire_shape(
    make_chart: Callable[..., Any],
) -> None:
    """All four types serialize identically to before `clean_value` grew
    explicit branches for them — pydantic's own JSON encoder already rendered
    a naive `time`/`UUID`/`timedelta` this way (via `to_jsonable_python`,
    which `clean_value` now calls directly for `timedelta` so the published
    wire format doesn't drift). BYTES is the one genuine fix: before,
    non-UTF8 bytes raised `PydanticSerializationError` and UTF8-safe bytes
    silently decoded as text with no sign they were ever bytes; `.hex()`
    always succeeds and is honest about the type. Pin the current shape here
    so any future drift is caught rather than silently reshaping this wire
    format.
    """
    data = [
        {
            "started_at": datetime.time(9, 30, 0),
            "duration": datetime.timedelta(hours=1, minutes=15),
            "row_id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "payload": b"\x00\x01binary",
        }
    ]
    board = _make_board([make_chart("table", id="tbl", query_name="q")])
    row = _render(board, _make_executor(data))["queries"]["q"]["rows"][0]

    assert row["started_at"] == "09:30:00"
    assert row["duration"] == "PT1H15M"
    assert row["row_id"] == "12345678-1234-5678-1234-567812345678"
    assert row["payload"] == "000162696e617279"


def test_duplicate_chart_id_across_nested_boards_raises(
    make_chart: Callable[..., Any],
) -> None:
    """Chart ids are unique per board, not per tree.

    Two imported partials — including one partial imported twice — can share an id.
    A flat map would keep the last and silently drop the rest, so this refuses
    rather than emitting a document that looks complete.
    """
    nested = _make_board([make_chart("line", id="total", x="month", y="revenue")])
    board = _make_board(
        [make_chart("bar", id="total", x="month", y="revenue")], nested=nested
    )

    with pytest.raises(RenderError, match="share the id 'total'"):
        _render(board, _make_executor([{"month": "jan", "revenue": 5}]))


def test_pivot_columns_are_emitted_as_columns() -> None:
    """`rows`/`values` without `columns` would describe a different pivot."""
    result = _render_compiled(
        """
source: test
charts:
  grid:
    query: {sql: "SELECT region, quarter, revenue FROM t"}
    type: table
    rows: [region]
    columns: [quarter]
    values: [revenue]
rows: [grid]
""",
        [{"region": "west", "quarter": "q1", "revenue": 5}],
    )
    chart = result["charts"]["grid"]

    assert chart["rows"] == ["region"]
    assert chart["columns"] == ["quarter"]
    assert chart["values"] == ["revenue"]


def test_variables_include_those_declared_in_a_nested_board() -> None:
    """A partial's variable interpolates into that partial's SQL, so its value
    has to reach the document — otherwise the emitted SQL carries a Jinja
    reference with nothing explaining it."""
    compiled = compile_board(_VARIABLE_YAML)
    assert compiled.board is not None, [e.message for e in compiled.errors]
    # variable_registry is the tree-wide set renderer.render merges from;
    # board.variables is only what the root declared.
    assert compiled.board.variable_registry is not None
    assert "year" in compiled.board.variable_registry

    result = json.loads(
        render_board_data(
            compiled.board, _make_executor([{"year": 2023}]), {"year": 2023}
        )
    )
    assert result["variables"] == {"year": 2023}
