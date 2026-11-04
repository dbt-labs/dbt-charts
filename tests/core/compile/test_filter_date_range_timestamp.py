"""`filter_date_range(col, range, timestamp=true)` takes the column's calendar
date in the board clock's time zone."""

from collections.abc import Callable
from datetime import date, datetime

import duckdb
import pytest

from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.compile.template.parameterized import (
    make_filter_date_range_helper,
)
from dbt_charts.core.dialects import get_dialect

_LA = BoardClock(
    date(2026, 10, 1),
    fiscal_start_month=1,
    week_start=1,
    time_zone="America/Los_Angeles",
)
_UTC = BoardClock(
    date(2026, 10, 1), fiscal_start_month=1, week_start=1, time_zone="UTC"
)
_RANGE = [date(2026, 9, 30), date(2026, 9, 30)]
_ZONE = "America/Los_Angeles"


def _helper(dialect: str) -> Callable[..., str]:
    return make_filter_date_range_helper(
        lambda v: f"DATE '{v.isoformat()}'", get_dialect(dialect)
    )


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("bigquery", f"DATE(ts, '{_ZONE}')"),
        ("snowflake", f"CAST(CONVERT_TIMEZONE('UTC', '{_ZONE}', ts) AS DATE)"),
        ("postgres", f"CAST(CAST(ts AS TIMESTAMPTZ) AT TIME ZONE '{_ZONE}' AS DATE)"),
        ("redshift", f"CAST(CAST(ts AS TIMESTAMPTZ) AT TIME ZONE '{_ZONE}' AS DATE)"),
        ("duckdb", f"CAST(CAST(ts AS TIMESTAMPTZ) AT TIME ZONE '{_ZONE}' AS DATE)"),
        ("databricks", f"CAST(FROM_UTC_TIMESTAMP(ts, '{_ZONE}') AS DATE)"),
    ],
)
def test_converts_to_the_board_zone(dialect: str, expected: str) -> None:
    with board_clock(_LA):
        sql = _helper(dialect)("ts", _RANGE, timestamp=True)
    assert sql.startswith(f"{expected} BETWEEN")


def test_without_the_kwarg_the_column_is_unchanged() -> None:
    with board_clock(_LA):
        sql = _helper("bigquery")("d", _RANGE)
    assert sql.startswith("CAST(d AS DATE) BETWEEN")


def test_a_utc_board_takes_the_plain_date() -> None:
    with board_clock(_UTC):
        sql = _helper("bigquery")("ts", _RANGE, timestamp=True)
    assert sql.startswith("CAST(ts AS DATE) BETWEEN")


def test_a_dialect_that_cannot_convert_is_refused() -> None:
    with board_clock(_LA), pytest.raises(ValueError, match="time zone"):
        _helper("mysql")("ts", _RANGE, timestamp=True)


@pytest.mark.parametrize(
    "column_type",
    ["TIMESTAMPTZ '2026-10-01 03:00:00+00'", "TIMESTAMP '2026-10-01 03:00:00'"],
)
def test_duckdb_dates_a_timestamp_in_the_board_zone(column_type: str) -> None:
    """03:00 UTC on 1 Oct is 20:00 on 30 Sep in Los Angeles; a naive value is UTC."""
    with board_clock(_LA):
        where = _helper("duckdb")("ts", _RANGE, timestamp=True)
    with board_clock(_UTC):
        utc_where = _helper("duckdb")("ts", _RANGE, timestamp=True)
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(f"CREATE TABLE t AS SELECT {column_type} ts")
    assert con.execute(f"SELECT count(*) FROM t WHERE {where}").fetchone() == (1,)
    assert con.execute(f"SELECT count(*) FROM t WHERE {utc_where}").fetchone() == (0,)


def test_datetime_bounds_refuse_timestamp() -> None:
    bounds = [datetime(2026, 9, 30, 9), datetime(2026, 9, 30, 17)]
    with board_clock(_LA), pytest.raises(ValueError, match="time of day"):
        _helper("duckdb")("ts", bounds, timestamp=True)
