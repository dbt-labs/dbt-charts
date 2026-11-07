"""`filter_date_range(col, range, is_naive_utc=true)` takes the calendar date, in the
board clock's time zone, of a naive timestamp column that stores UTC."""

from collections.abc import Callable
from datetime import date, datetime

import duckdb
import pytest

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.compile.template.parameterized import (
    make_filter_date_range_helper,
    render_parameterized,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_FILTER_DATE_RANGE_TIMESTAMP
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
_PG = f"CAST((ts AT TIME ZONE 'UTC') AT TIME ZONE '{_ZONE}' AS DATE)"


def _helper(dialect: str) -> Callable[..., str]:
    return make_filter_date_range_helper(
        lambda v: f"DATE '{v.isoformat()}'", get_dialect(dialect)
    )


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("bigquery", f"DATE(TIMESTAMP(ts, 'UTC'), '{_ZONE}')"),
        ("snowflake", f"CAST(CONVERT_TIMEZONE('UTC', '{_ZONE}', ts) AS DATE)"),
        ("postgres", _PG),
        ("redshift", _PG),
        ("duckdb", _PG),
        ("databricks", f"CAST(convert_timezone('UTC', '{_ZONE}', ts) AS DATE)"),
    ],
)
def test_reads_the_naive_column_as_utc_then_converts(
    dialect: str, expected: str
) -> None:
    with board_clock(_LA):
        sql = _helper(dialect)("ts", _RANGE, is_naive_utc=True)
    assert sql.startswith(f"{expected} BETWEEN")


def test_without_the_kwarg_the_column_is_unchanged() -> None:
    with board_clock(_LA):
        sql = _helper("bigquery")("d", _RANGE)
    assert sql.startswith("CAST(d AS DATE) BETWEEN")


def test_a_utc_board_takes_the_plain_date() -> None:
    with board_clock(_UTC):
        sql = _helper("bigquery")("ts", _RANGE, is_naive_utc=True)
    assert sql.startswith("CAST(ts AS DATE) BETWEEN")


def test_a_dialect_that_cannot_convert_is_refused() -> None:
    with board_clock(_LA), pytest.raises(ValueError, match="time zone"):
        _helper("mysql")("ts", _RANGE, is_naive_utc=True)


def test_duckdb_naive_utc_column_dates_in_the_board_zone_whatever_the_session() -> None:
    """12:00 UTC on 21 Oct is 08:00 on 21 Oct in New York; a bare CAST in the Tokyo session would read 12:00 as Tokyo time, which is 23:00 on 20 Oct in New York."""
    ny = BoardClock(
        date(2026, 10, 21),
        fiscal_start_month=1,
        week_start=1,
        time_zone="America/New_York",
    )
    with board_clock(ny):
        where = _helper("duckdb")("ts", [date(2026, 10, 21)] * 2, is_naive_utc=True)
    con = duckdb.connect()
    con.execute("SET TimeZone='Asia/Tokyo'")
    con.execute("CREATE TABLE t AS SELECT TIMESTAMP '2026-10-21 12:00:00' ts")
    assert con.execute(f"SELECT count(*) FROM t WHERE {where}").fetchone() == (1,)


def test_datetime_bounds_refuse_is_naive_utc() -> None:
    bounds = [datetime(2026, 9, 30, 9), datetime(2026, 9, 30, 17)]
    with board_clock(_LA), pytest.raises(ValueError, match="time of day"):
        _helper("duckdb")("ts", bounds, is_naive_utc=True)


def test_the_retired_timestamp_kwarg_raises_its_code() -> None:
    with board_clock(_UTC), pytest.raises(CompilationError) as raised:
        render_parameterized(
            "SELECT 1 WHERE {{ filter_date_range('ts', [a, b], timestamp=true) }}",
            variables={"a": date(2026, 9, 30), "b": date(2026, 9, 30)},
            profile_type="duckdb",
        )
    assert raised.value.code is ERR_FILTER_DATE_RANGE_TIMESTAMP
