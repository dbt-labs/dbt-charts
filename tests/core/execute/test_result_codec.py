"""Cells are written and restored by the column's declared type, at any depth."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import CacheHit, ResultRows
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.recording import BoardRecording, load_board_recording
from dbt_charts.core.execute.result_codec import decode_result, encode_result
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from ..._run import Run
from ..._scripted_env import backend_of
from .test_cache_policy_enforcement import _board_yaml, _ok_adapter

_SH, _QH, _VH = "s" * 16, "q" * 16, "v" * 16


def _t(kind: Any, **kwargs: Any) -> ColumnType:
    return ColumnType(kind=kind, **kwargs)


SCHEMA = {
    "amounts": _t("list", element=_t("decimal")),
    "point": _t("struct", fields=(("d", _t("date")), ("ts", _t("datetime_tz")))),
    "naive": _t("datetime"),
    "at": _t("time"),
    "blob": _t("bytes"),
    "attrs": _t("json"),
}
ROWS = [
    {
        "amounts": [Decimal("1.50"), None, Decimal("2")],
        "point": {"d": date(2026, 1, 2), "ts": datetime(2026, 1, 2, 3, tzinfo=UTC)},
        "naive": datetime(2026, 1, 2, 3, 4, 5, 6),
        "at": time(3, 4, 5),
        "blob": b"\x00\xff",
        "attrs": {"k": [1, {"z": None}]},
    },
    dict.fromkeys(SCHEMA),
]
RESULT = ResultRows(rows=ROWS, schema=SCHEMA)


def _assert_restored(restored: ResultRows) -> None:
    assert restored == RESULT
    assert str(restored.rows[0]["amounts"][0]) == "1.50"
    assert restored.rows[0]["point"]["ts"].tzinfo is UTC
    assert restored.rows[0]["naive"].tzinfo is None
    assert list(restored.schema) == list(SCHEMA)


def test_codec_round_trips_nested_cells_by_declared_type() -> None:
    _assert_restored(decode_result(json.loads(json.dumps(encode_result(RESULT)))))


@pytest.mark.parametrize(
    ("declared", "cell"),
    [
        pytest.param(_t("datetime_tz"), datetime(2026, 1, 2), id="naive-in-tz"),
        pytest.param(_t("datetime"), datetime(2026, 1, 2, tzinfo=UTC), id="aware"),
        pytest.param(
            _t("datetime_tz"),
            datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=9))),
            id="non-utc",
        ),
        pytest.param(_t("date"), "2026-01-02", id="date-as-text"),
        pytest.param(_t("integer"), True, id="bool-as-int"),
        pytest.param(
            _t("list", element=_t("decimal")), [1.5], id="float-in-list-decimal"
        ),
    ],
)
def test_a_cell_that_does_not_match_its_type_raises(
    declared: ColumnType, cell: Any
) -> None:
    with pytest.raises(ExecutionError, match="ERR-RESULT-COLUMN-TYPE|declared"):
        encode_result(ResultRows(rows=[{"c": cell}], schema={"c": declared}))


def test_local_cache_hit_returns_the_schema_and_typed_cells() -> None:
    cache = TrivialDuckDBCache()
    try:
        cache.put(_SH, _QH, _VH, RESULT, board_slug="b", query_name="q")
        hit = cache.get(_SH, _QH, _VH)
        assert isinstance(hit, CacheHit)
        _assert_restored(ResultRows(rows=hit.rows, schema=hit.schema))
    finally:
        cache.close()


def test_local_cache_put_rejects_a_mismatched_cell() -> None:
    cache = TrivialDuckDBCache()
    try:
        with pytest.raises(ExecutionError, match="declared date"):
            cache.put(
                _SH,
                _QH,
                _VH,
                ResultRows(rows=[{"d": "2026-01-02"}], schema={"d": _t("date")}),
                board_slug="b",
                query_name="q",
            )
    finally:
        cache.close()


def test_recording_round_trips_nested_cells_and_schema() -> None:
    recording = BoardRecording(
        recorded_at=datetime(2026, 10, 9, tzinfo=UTC),
        results_by_query={"q": RESULT},
    )
    _assert_restored(
        load_board_recording(recording.model_dump_json().encode()).results_by_query["q"]
    )


def test_a_cache_hit_returns_the_schema_the_miss_wrote() -> None:
    cache = TrivialDuckDBCache()
    try:
        compiled = df_compile(_board_yaml(""))
        adapter = _ok_adapter()
        backend_of(adapter).rows = QueryResult(
            data=[{"value": [Decimal("1.50")]}],
            schema={"value": _t("list", element=_t("decimal"))},
        )

        def run() -> ResultRows:
            return Run(
                compiled.board,
                env=adapter,
                query_registry=compiled.query_registry,
                result_cache=cache,
            ).execute_query_result("q")

        miss, hit = run(), run()
        assert backend_of(adapter).execute.call_count == 1
        assert hit == miss
        assert str(hit.rows[0]["value"][0]) == "1.50"
    finally:
        cache.close()
