"""What TrivialDuckDBCache hands back for what was put: the payload round trip.

Covers the type-fidelity and column-order clauses of ``QueryResultCache.get``,
and that a write stays one statement however many rows it carries.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from dbt_charts.core.execute.cache_backend import CacheHit
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_SH = "s" * 16
_QH = "q" * 16
_VH = "v" * 16


def _round_trip(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache = TrivialDuckDBCache()
    try:
        cache.put(_SH, _QH, _VH, data, board_slug="f", query_name="q")
        hit = cache.get(_SH, _QH, _VH)
        assert isinstance(hit, CacheHit)
        return hit.rows
    finally:
        cache.close()


class TestPayloadRoundTrip:
    def test_large_put_is_one_write_not_a_write_per_row(self) -> None:
        """A 20k-row put must finish far faster than a per-row insert could.

        Binding row by row costs seconds at this size; one payload costs tens
        of milliseconds. The 3s bound separates the two without timing flake.
        """
        data = [{"region": f"r{i % 7}", "amount": i} for i in range(20_000)]
        start = time.perf_counter()
        rows = _round_trip(data)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"put+get took {elapsed:.2f}s"
        assert rows == data

    def test_scalars_nulls_and_nested_values_come_back_as_themselves(self) -> None:
        data = [
            {
                "i": 7,
                "big": 2**70,
                "f": 1.5,
                "s": "hi",
                "b": True,
                "n": None,
                "arr": [1, 2, 3],
                "obj": {"k": "v", "nested": [None, {"deep": 1.5}]},
            }
        ]
        rows = _round_trip(data)
        assert rows == data
        assert rows[0]["b"] is True
        assert type(rows[0]["i"]) is int

    def test_uniform_temporal_and_decimal_columns_keep_their_type(self) -> None:
        tokyo = timezone(timedelta(hours=9))
        data = [
            {
                "day": date(2024, 1, 2),
                "naive": datetime(2024, 1, 2, 3, 4, 5, 678),
                "aware": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
                "offset": datetime(2024, 1, 2, 3, 4, 5, tzinfo=tokyo),
                "dec": Decimal("3.250"),
            },
            {"day": None, "naive": None, "aware": None, "offset": None, "dec": None},
        ]
        rows = _round_trip(data)
        assert rows == data
        assert type(rows[0]["day"]) is date
        assert rows[0]["naive"].tzinfo is None
        assert rows[0]["offset"].utcoffset() == timedelta(hours=9)
        # Equality ignores trailing zeros; the stored text must not.
        assert str(rows[0]["dec"]) == "3.250"

    def test_a_mixed_date_and_datetime_column_is_not_guessed_back(self) -> None:
        """Mixed columns pass through in the store's own form, per the contract."""
        rows = _round_trip(
            [{"when": date(2024, 1, 2)}, {"when": datetime(2024, 1, 2, 3, 4, 5)}]
        )
        assert rows == [{"when": "2024-01-02"}, {"when": "2024-01-02T03:04:05"}]

    def test_a_mixed_type_column_keeps_each_value(self) -> None:
        """A column of mixed type (an HTTP/JSON source, say) must still cache."""
        data = [{"x": "hi"}, {"x": 5}, {"x": None}, {"x": [1]}]
        assert _round_trip(data) == data

    def test_a_decimal_in_a_mixed_column_stays_a_number(self) -> None:
        assert _round_trip([{"x": Decimal("1.5")}, {"x": 2}]) == [{"x": 1.5}, {"x": 2}]

    def test_column_order_is_the_order_put_stored(self) -> None:
        rows = _round_trip([{"zeta": 1, "alpha": 2, "mid": 3}])
        assert list(rows[0]) == ["zeta", "alpha", "mid"]

    def test_rows_with_different_key_sets_keep_their_own_keys(self) -> None:
        data = [{"a": 1}, {"a": 2, "b": 3}]
        assert _round_trip(data) == data

    def test_re_put_replaces(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            cache.put(
                _SH, _QH, _VH, [{"a": 1}, {"a": 2}], board_slug="f", query_name="q"
            )
            cache.put(_SH, _QH, _VH, [{"a": 9}], board_slug="f", query_name="q")
            hit = cache.get(_SH, _QH, _VH)
            assert isinstance(hit, CacheHit)
            assert hit.rows == [{"a": 9}]
        finally:
            cache.close()


class TestUndecodablePayload:
    def test_a_payload_that_cannot_be_decoded_is_a_miss(self, tmp_path: Path) -> None:
        """Another version's encoding, or a damaged entry, must re-run the query."""
        db_path = tmp_path / "cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            cache.put(_SH, _QH, _VH, [{"a": 1}], board_slug="f", query_name="q")
            conn = duckdb.connect(str(db_path))
            conn.execute("UPDATE _query_outcomes SET payload = 'not a payload'::BLOB")
            conn.close()
            assert cache.get(_SH, _QH, _VH) is None
        finally:
            cache.close()
