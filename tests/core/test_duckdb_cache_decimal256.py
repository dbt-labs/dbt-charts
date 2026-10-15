"""Regression test: high-precision decimals must round-trip through the DuckDB
cache exactly, without crashing.

DuckDB's DECIMAL is HUGEINT-backed and capped at precision 38. BigQuery's
NUMERIC type allows up to 76 digits, and certain SUM-of-fan-out-deduplicated
queries can return precision-47/scale-38 results.

History: a typed DECIMAL column crashed on these values, and coercing them to
DOUBLE (15 significant digits) was a second bug: an incremental watermark
column of restated NUMERIC values duplicated on every warm render, because a
float-coerced prior key (``101.2``) never equaled the freshly-queried
``Decimal('101.20')`` tail key. The cache stores a column whose every value is
a ``Decimal`` as exact text and restores it on read. A column that mixes
Decimal with another type keeps the lossy float coercion; exactness is only
promised for a uniform column.

Eval ``20260428-155000`` was the original production trigger: 12 queries on
dashboards 955 + 2223 failed with this exact error before the original fix.
"""

from __future__ import annotations

from decimal import Decimal

from dbt_charts.core.execute.duckdb_cache import (
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


def test_duckdb_cache_put_with_high_precision_decimal_round_trips(tmp_path):
    """End-to-end: cache.put with rows containing precision-47 decimals.

    Must not crash, must be retrievable, and — since this column is uniformly
    Decimal — must come back as the exact original Decimal, not a lossy float
    stand-in (float's 15 significant digits would truncate 47).
    """
    cache = TrivialDuckDBCache(db_path=tmp_path / "test.duckdb")
    source_hash = compute_source_hash("test_source", {})
    query_hash = compute_query_hash("SELECT 1")
    variables_hash = compute_variables_hash({})

    high_precision = Decimal("12345678.901234567890123456789012345678")
    data = [
        {"id": 1, "amount": high_precision},
        {"id": 2, "amount": Decimal("0." + "9" * 38)},
    ]
    cache.put(
        source_hash,
        query_hash,
        variables_hash,
        data,
        board_slug="test_board",
        query_name="test_query",
    )

    hit = cache.get(source_hash, query_hash, variables_hash)
    assert hit is not None
    assert len(hit.rows) == 2
    assert hit.rows[0]["id"] == 1
    assert hit.rows[1]["id"] == 2
    # A uniformly-Decimal column round-trips exactly, full precision intact.
    assert type(hit.rows[0]["amount"]) is Decimal
    assert hit.rows[0]["amount"] == high_precision
    assert hit.rows[1]["amount"] == Decimal("0." + "9" * 38)


def test_duckdb_cache_list_column_round_trips_as_list(tmp_path):
    """JSON/list columns (e.g. an array_agg sparkline series) must come back as
    Python lists, not the JSON string they are stored as.

    Regression: the cache once serialized list/dict columns to JSON text on
    write and returned the raw JSON string on read. A warm cache then fed
    a str into the spark renderer, which crashed with
    "unsupported operand type(s) for -: 'str' and 'str'". A cache miss returned
    the fresh list, so a first (cold) render looked fine.
    """
    cache = TrivialDuckDBCache(db_path=tmp_path / "test.duckdb")
    source_hash = compute_source_hash("s", {})
    query_hash = compute_query_hash("SELECT 1")
    variables_hash = compute_variables_hash({})

    data = [
        {"lead_source": "Download", "monthly_trend": [0.0, 303.0, 204.0]},
        {"lead_source": "Referral", "monthly_trend": [12.0, 0.0, 88.0]},
    ]
    cache.put(
        source_hash,
        query_hash,
        variables_hash,
        data,
        board_slug="f",
        query_name="q",
    )

    hit = cache.get(source_hash, query_hash, variables_hash)
    assert hit is not None
    assert isinstance(hit.rows[0]["monthly_trend"], list)
    assert hit.rows[0]["monthly_trend"] == [0.0, 303.0, 204.0]
    assert hit.rows[1]["monthly_trend"] == [12.0, 0.0, 88.0]


def test_duckdb_cache_dict_column_round_trips_as_dict(tmp_path):
    """dict (JSON object) columns round-trip as dicts, not JSON strings."""
    cache = TrivialDuckDBCache(db_path=tmp_path / "test.duckdb")
    source_hash = compute_source_hash("s", {})
    query_hash = compute_query_hash("SELECT 1")
    variables_hash = compute_variables_hash({})

    data = [{"id": 1, "meta": {"a": 1, "b": [2, 3]}}, {"id": 2, "meta": None}]
    cache.put(
        source_hash,
        query_hash,
        variables_hash,
        data,
        board_slug="f",
        query_name="q",
    )

    hit = cache.get(source_hash, query_hash, variables_hash)
    assert hit is not None
    assert hit.rows[0]["meta"] == {"a": 1, "b": [2, 3]}
    assert hit.rows[1]["meta"] is None


def test_duckdb_cache_put_with_normal_decimal_round_trips(tmp_path):
    """Ordinary (precision <= 38) decimals also round-trip exactly."""
    cache = TrivialDuckDBCache(db_path=tmp_path / "test.duckdb")
    source_hash = compute_source_hash("s", {})
    query_hash = compute_query_hash("SELECT 1")
    variables_hash = compute_variables_hash({})

    data = [{"id": 1, "amount": Decimal("1.50")}, {"id": 2, "amount": Decimal("2.75")}]
    cache.put(
        source_hash,
        query_hash,
        variables_hash,
        data,
        board_slug="f",
        query_name="q",
    )

    hit = cache.get(source_hash, query_hash, variables_hash)
    assert hit is not None
    assert [r["amount"] for r in hit.rows] == [Decimal("1.50"), Decimal("2.75")]
    assert all(type(r["amount"]) is Decimal for r in hit.rows)
