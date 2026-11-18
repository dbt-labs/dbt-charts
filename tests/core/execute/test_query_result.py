"""take(): the one cut of a result's rows to the row and byte caps."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.column_types import TruncatedReason
from dbt_charts.core.execute.query_result import (
    RowFetchLimit,
    primary_truncation_reason,
    take,
)


def _limit(
    max_bytes: int, rows: int = 1_000, ceiling_binding: bool = True
) -> RowFetchLimit:
    return RowFetchLimit(
        limit=rows,
        ceiling_binding=ceiling_binding,
        max_bytes=max_bytes,
    )


_RESULTS: list[tuple[list[str], list[tuple[Any, ...]]]] = [
    (["a"], [(1,), (None,), (-2.5,)]),
    ([], [(), ()]),
    (
        ['say "hi"', "naïve", "back\\slash", "tab\tname"],
        [
            ("é ✓ 漢", Decimal("1.10"), date(2024, 1, 2), b"\x00raw"),
            ('quote " and \\', None, datetime(2024, 1, 2, 3, 4, tzinfo=UTC), True),
        ],
    ),
    (
        ["nested", "n"],
        [({"k": [1, {"deep": "ü"}]}, 10**30), ([None, "x", 1.5e-300], float("inf"))],
    ),
]


@pytest.mark.parametrize(("columns", "rows"), _RESULTS)
def test_a_tuple_row_measures_what_its_row_dict_serializes_to(
    columns: list[str],
    rows: list[tuple[Any, ...]],
) -> None:
    total = sum(
        len(json.dumps(dict(zip(columns, row, strict=True)), default=str))
        for row in rows
    )

    assert take(rows, columns, _limit(total)) == (rows, None)
    assert take(rows, columns, _limit(total - 1)) == (rows[:-1], "max_result_bytes")


@pytest.mark.parametrize(("columns", "rows"), _RESULTS)
def test_tuple_and_dict_rows_keep_the_same_rows_at_every_budget(
    columns: list[str],
    rows: list[tuple[Any, ...]],
) -> None:
    dicts = [dict(zip(columns, row, strict=True)) for row in rows]
    for budget in range(1, 400):
        kept_tuples, tuple_reason = take(rows, columns, _limit(budget))
        kept_dicts, dict_reason = take(dicts, columns, _limit(budget))
        assert [dict(zip(columns, row, strict=True)) for row in kept_tuples] == (
            kept_dicts
        )
        assert tuple_reason == dict_reason


class TestRowCap:
    def test_the_ceiling_cuts_one_past_its_limit(self) -> None:
        assert take([(1,), (2,), (3,)], ["a"], _limit(10_000, rows=2)) == (
            [(1,), (2,)],
            "max_rows",
        )

    def test_an_authors_own_limit_cuts_without_a_reason(self) -> None:
        assert take(
            [(1,), (2,), (3,)], ["a"], _limit(10_000, rows=2, ceiling_binding=False)
        ) == ([(1,), (2,)], None)

    def test_a_byte_cut_inside_the_row_limit_wins(self) -> None:
        assert take([("xx",), ("yy",), ("zz",)], ["a"], _limit(12, rows=2)) == (
            [("xx",)],
            "max_result_bytes",
        )

    def test_rows_past_the_row_limit_are_never_measured(self) -> None:
        """Only the rows the row limit keeps count against the budget."""
        wide = "x" * 1_000
        assert take([("a",), (wide,)], ["c"], _limit(20, rows=1)) == (
            [("a",)],
            "max_rows",
        )

    def test_the_input_is_left_alone(self) -> None:
        rows = [(1,), (2,)]
        take(rows, ["a"], _limit(1, rows=1))
        assert rows == [(1,), (2,)]


class TestLazyRows:
    """A stream of rows is read only as far as the cut."""

    @staticmethod
    def _stream(rows: list[tuple[Any, ...]], pulled: list[int]) -> Any:
        for row in rows:
            pulled.append(1)
            yield row

    def test_a_byte_cut_stops_reading_at_the_row_past_the_budget(self) -> None:
        pulled: list[int] = []
        kept, reason = take(self._stream([("xx",)] * 100, pulled), ["a"], _limit(30))
        assert (kept, reason) == ([("xx",)] * 2, "max_result_bytes")
        assert len(pulled) == 3

    def test_a_row_cut_stops_reading_at_the_row_past_the_cap(self) -> None:
        pulled: list[int] = []
        kept, reason = take(
            self._stream([(1,)] * 100, pulled), ["a"], _limit(10_000, rows=5)
        )
        assert (kept, reason) == ([(1,)] * 5, "max_rows")
        assert len(pulled) == 6


@pytest.mark.parametrize(
    ("cut", "upstream", "reported"),
    [
        (None, None, None),
        ("max_rows", None, "max_rows"),
        ("max_result_bytes", None, "max_result_bytes"),
        (None, "max_rows", "max_rows"),
        (None, "max_result_bytes", "max_result_bytes"),
        ("max_rows", "max_rows", "max_rows"),
        ("max_rows", "max_result_bytes", "max_result_bytes"),
        ("max_result_bytes", "max_rows", "max_result_bytes"),
        ("max_result_bytes", "max_result_bytes", "max_result_bytes"),
    ],
)
def test_a_byte_cut_outranks_a_row_cut_from_either_side(
    cut: TruncatedReason | None,
    upstream: TruncatedReason | None,
    reported: TruncatedReason | None,
) -> None:
    assert primary_truncation_reason(cut, upstream) == reported
