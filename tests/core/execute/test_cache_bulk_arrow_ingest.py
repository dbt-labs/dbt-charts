"""TrivialDuckDBCache ingests rows in bulk via Arrow, not row-by-row.

The file-source cold path (and every result-cache write) funnels through
``TrivialDuckDBCache.put``. Loading via a per-row ``executemany`` is O(rows) of
Python round-trips (measured ~47s for 100k rows); registering a single Arrow
table and doing one bulk INSERT is ~100ms. These tests pin the bulk behavior and
that it preserves the existing value-type/JSON semantics.
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_SH = "s" * 16
_QH = "q" * 16
_VH = "v" * 16


def _put(cache: TrivialDuckDBCache, data: list[dict]) -> None:
    cache.put(_SH, _QH, _VH, data, board_slug="f", query_name="q")


class TestBulkIngest:
    def test_large_put_is_bulk_not_row_by_row(self) -> None:
        """A 20k-row put must finish far faster than a per-row executemany could.

        Row-by-row `executemany` at this size is ~9s (extrapolated from the 100k
        profile); the Arrow bulk path is tens of ms. The 3s bound has a ~50x
        margin over the bulk path and ~3x under the row-by-row path, so it pins
        "bulk, not row-by-row" without being timing-flaky.
        """
        cache = TrivialDuckDBCache()
        data = [{"region": f"r{i % 7}", "amount": i} for i in range(20_000)]
        start = time.perf_counter()
        _put(cache, data)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"put took {elapsed:.2f}s — likely still row-by-row"

        hit = cache.get(_SH, _QH, _VH)
        assert hit is not None
        assert len(hit.rows) == 20_000
        assert hit.rows[0] == {"region": "r0", "amount": 0}

    def test_preserves_value_types(self) -> None:
        """The Arrow path keeps the existing coercion/JSON round-trip semantics."""
        cache = TrivialDuckDBCache()
        ts = datetime(2024, 1, 2, 3, 4, 5)
        data = [
            {
                "i": 7,
                "f": 1.5,
                "s": "hi",
                "b": True,
                "n": None,
                "when": ts,
                "dec": Decimal("3.25"),
                "arr": [1, 2, 3],
                "obj": {"k": "v"},
            }
        ]
        _put(cache, data)
        hit = cache.get(_SH, _QH, _VH)
        assert hit is not None
        row = hit.rows[0]
        assert row["i"] == 7
        assert row["f"] == 1.5
        assert row["s"] == "hi"
        assert row["b"] is True
        assert row["n"] is None
        assert row["when"] == ts
        assert row["dec"] == 3.25  # Decimal coerced to float
        assert row["arr"] == [1, 2, 3]  # list round-trips
        assert row["obj"] == {"k": "v"}  # dict round-trips

    def test_re_put_replaces(self) -> None:
        cache = TrivialDuckDBCache()
        _put(cache, [{"a": 1}, {"a": 2}])
        _put(cache, [{"a": 9}])
        hit = cache.get(_SH, _QH, _VH)
        assert hit is not None
        assert hit.rows == [{"a": 9}]

    def test_heterogeneous_column_matches_pre_bulk_behavior(self) -> None:
        """A column with mixed per-row types (e.g. an HTTP/JSON source) must still
        cache, each cell cast to the column type as a bound parameter would be.
        """
        cache = TrivialDuckDBCache()
        # First row str → VARCHAR column; the int in row 2 is cast to text.
        _put(cache, [{"x": "hi"}, {"x": 5}])
        hit = cache.get(_SH, _QH, _VH)
        assert hit is not None
        assert hit.rows == [{"x": "hi"}, {"x": "5"}]
