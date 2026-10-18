"""ERR-BINDER-UNKNOWN-COLUMN names the relation's real columns."""

from pathlib import Path

import duckdb
import pytest

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_GROUP_BY,
    ERR_BINDER_QUERY_SHAPE,
    ERR_BINDER_UNKNOWN_COLUMN,
)
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "jobs.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        'CREATE TABLE us_employment (year_month DATE, "Total Jobs" INTEGER, sector VARCHAR)'
    )
    con.close()
    return path


def _error_message(db_path: Path, sql: str) -> str:
    adapter = DuckDBAdapter(
        source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path))
    )
    try:
        result = adapter._execute(SqlQuery(sql=sql, source="test_db"))
    finally:
        adapter.close()
    assert result.error is not None
    assert result.error.code is ERR_BINDER_UNKNOWN_COLUMN
    return str(result.error)


def test_underscore_for_space_names_the_real_column(db_path: Path) -> None:
    message = _error_message(db_path, "SELECT total_jobs FROM us_employment")

    assert 'Did you mean "Total Jobs"?' in message


def test_typo_names_the_nearest_column(db_path: Path) -> None:
    message = _error_message(db_path, "SELECT yearmonth FROM us_employment")

    assert 'Did you mean "year_month"?' in message


def test_no_near_miss_lists_the_relations_columns(db_path: Path) -> None:
    message = _error_message(db_path, "SELECT zzz FROM us_employment")

    assert "Did you mean" not in message
    assert 'Columns in us_employment: year_month, "Total Jobs", sector.' in message


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT t.gol FROM us_employment e JOIN targets t ON e.sector = t.sector",
        "WITH c AS (SELECT sector FROM us_employment) SELECT secto FROM c",
        "SELECT secto FROM (SELECT sector FROM us_employment) s",
    ],
)
def test_join_cte_and_subquery_get_no_advice(db_path: Path, sql: str) -> None:
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE targets (sector VARCHAR, goal INTEGER)")
    con.close()

    message = _error_message(db_path, sql)

    assert "Did you mean" not in message
    assert "Columns in" not in message


def _error(db_path: Path, sql: str):  # noqa: ANN202
    adapter = DuckDBAdapter(
        source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path))
    )
    try:
        result = adapter._execute(SqlQuery(sql=sql, source="test_db"))
    finally:
        adapter.close()
    assert result.error is not None
    return result.error


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        (
            "SELECT sector FROM us_employment UNION "
            "SELECT sector, year_month FROM us_employment",
            ERR_BINDER_QUERY_SHAPE,
        ),
        ("SELECT sector FROM us_employment ORDER BY 3", ERR_BINDER_QUERY_SHAPE),
        (
            "SELECT sector, year_month FROM us_employment GROUP BY sector",
            ERR_BINDER_GROUP_BY,
        ),
    ],
)
def test_union_and_order_by_shape_errors_have_their_own_code_group_by_main_s(
    db_path: Path, sql: str, code: object
) -> None:
    assert _error(db_path, sql).code is code
