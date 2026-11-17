"""A window is a bounded slice of a query result: searched, filtered, sorted."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.execute.cache_backend import ResultRows
from dbt_charts.core.execute.window import (
    Window,
    WindowError,
    WindowRows,
    slice_rows,
)

_RESULT = ResultRows(
    [
        {"id": 3, "name": "Carol", "n": 7},
        {"id": 1, "name": "alice", "n": 2},
        {"id": 2, "name": None, "n": 9},
        {"id": 4, "name": "Malcolm", "n": 2},
    ],
    {
        "id": ColumnType(kind="integer"),
        "name": ColumnType(kind="string"),
        "n": ColumnType(kind="integer"),
    },
)


def _window(
    search: str = "",
    search_columns: tuple[str, ...] = (),
    equals: tuple[tuple[str, tuple[str | None, ...]], ...] = (),
    order: tuple[tuple[str, str], ...] = (),
    offset: int = 0,
    limit: int = 10,
) -> Window:
    return Window(search, search_columns, equals, order, offset, limit)


def _slice(result: ResultRows, window: Window) -> WindowRows:
    return slice_rows(result, window, "UTC", truncated=False)


def _ids(window: Window) -> list[int]:
    return [row["id"] for row in _slice(_RESULT, window).rows]


def test_an_unsorted_window_keeps_the_query_order() -> None:
    assert _ids(_window()) == [3, 1, 2, 4]


def test_limit_and_offset_bound_the_rows_and_report_more() -> None:
    first = _slice(_RESULT, _window(limit=2))
    assert [row["id"] for row in first.rows] == [3, 1]
    assert (first.total, first.more) == (4, True)

    last = _slice(_RESULT, _window(offset=2, limit=2))
    assert [row["id"] for row in last.rows] == [2, 4]
    assert (last.total, last.more) == (4, False)


def test_a_window_past_the_end_is_empty_but_still_counts() -> None:
    past = _slice(_RESULT, _window(offset=10))
    assert (past.rows, past.total, past.more) == ([], 4, False)


def test_search_is_a_case_insensitive_substring_over_the_named_columns() -> None:
    assert _ids(_window(search="AL", search_columns=("name",))) == [1, 4]


def test_search_skips_nulls_and_counts_only_matches() -> None:
    found = _slice(_RESULT, _window(search="l", search_columns=("name",)))
    assert (len(found.rows), found.total) == (3, 3)


def test_order_sorts_by_each_column_and_ties_keep_query_order() -> None:
    assert _ids(_window(order=(("n", "asc"),))) == [1, 4, 3, 2]
    assert _ids(_window(order=(("n", "desc"), ("id", "asc")))) == [2, 3, 1, 4]


def test_equals_matches_values_given_as_text() -> None:
    assert _ids(_window(equals=(("id", ("4", "1")),))) == [1, 4]


_TYPED = ResultRows(
    [
        {
            "ratio": 1.0,
            "price": Decimal("1.50"),
            "at": datetime(2024, 1, 2, 3, 4, 5),
            "ok": True,
        },
        {
            "ratio": 2.5,
            "price": Decimal("2.00"),
            "at": datetime(2024, 1, 3),
            "ok": False,
        },
    ],
    {
        "ratio": ColumnType(kind="float"),
        "price": ColumnType(kind="decimal"),
        "at": ColumnType(kind="datetime"),
        "ok": ColumnType(kind="boolean"),
    },
)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ratio", "1"),
        ("ratio", "1.0"),
        ("price", "1.5"),
        ("at", "2024-01-02T03:04:05"),
        ("at", "2024-01-02 03:04:05"),
        ("ok", "true"),
    ],
)
def test_equals_compares_in_the_column_type(column: str, value: str) -> None:
    found = _slice(_TYPED, _window(equals=((column, (value,)),)))
    assert [row["ratio"] for row in found.rows] == [1.0]


def test_equals_refuses_a_value_the_column_type_cannot_hold() -> None:
    with pytest.raises(WindowError, match="abc"):
        _slice(_RESULT, _window(equals=(("id", ("abc",)),)))


def test_a_window_reports_that_its_result_was_truncated() -> None:
    assert _slice(_RESULT, _window()).truncated is False
    assert slice_rows(_RESULT, _window(), "UTC", truncated=True).truncated is True


def test_equals_can_match_a_null() -> None:
    assert _ids(_window(equals=(("name", (None,)),))) == [2]


def test_the_rows_carry_only_the_query_columns() -> None:
    window = _slice(_RESULT, _window(limit=1))
    assert window.rows == [{"id": 3, "name": "Carol", "n": 7}]
    assert window.schema == _RESULT.schema


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"search": "a", "search_columns": ("nope",)}, "nope"),
        ({"search": "a"}, "search_columns"),
        ({"order": (("nope", "asc"),)}, "nope"),
        ({"order": (("id", "sideways"),)}, "sideways"),
        ({"equals": (("nope", ("1",)),)}, "nope"),
        ({"equals": (("id", ()),)}, "lists no values"),
        ({"offset": -1}, "offset"),
        ({"limit": 0}, "limit"),
        ({"limit": 100_000}, "limit"),
    ],
)
def test_a_bad_window_is_refused_by_name(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(WindowError, match=message):
        _slice(_RESULT, _window(**overrides))


def test_a_search_term_is_bound_never_spliced_into_the_sql() -> None:
    quoted = ResultRows(
        [{"name": "o'brien"}, {"name": "obi"}], {"name": ColumnType(kind="string")}
    )
    window = _window(search="o'b", search_columns=("name",))
    assert _slice(quoted, window).rows == [{"name": "o'brien"}]


def test_a_result_with_a_rowid_column_is_refused() -> None:
    shadowed = ResultRows([{"rowid": 1}], {"rowid": ColumnType(kind="integer")})
    with pytest.raises(WindowError, match="rowid"):
        _slice(shadowed, _window())
