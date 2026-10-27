"""A result with two same-named columns must raise, not silently drop one."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from dbt_charts.core.column_types import reject_duplicate_columns
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig, SQLiteSourceConfig
from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_DUPLICATE_COLUMN
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import RowFetchLimit
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter
from dbt_charts.core.execute.cache_composition import rows_from_result
from dbt_charts.core.execute.result import arrow_to_query_result

_DUPLICATE_SQL = "select 1 as a, 2 as a, 3 as b, 4 as b, 5 as c"


def test_helper_names_every_duplicated_column() -> None:
    with pytest.raises(ExecutionError) as excinfo:
        reject_duplicate_columns(["a", "a", "b", "b", "c"])
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN
    assert "'a'" in str(excinfo.value)
    assert "'b'" in str(excinfo.value)
    assert "'c'" not in str(excinfo.value)


def test_helper_accepts_distinct_columns() -> None:
    reject_duplicate_columns(["a", "b", "A"])


def test_arrow_result_raises() -> None:
    table = pa.Table.from_arrays([pa.array([1]), pa.array([2])], names=["a", "a"])
    limit = RowFetchLimit(fetch_limit=2, effective_limit=1, ceiling_binding=False)
    with pytest.raises(ExecutionError) as excinfo:
        arrow_to_query_result(table, limit)
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN


def test_duckdb_adapter_returns_the_registered_error() -> None:
    adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
    result = adapter.execute(SqlQuery(sql=_DUPLICATE_SQL))
    assert result.error is not None
    assert result.error.code is ERR_RESULT_DUPLICATE_COLUMN
    assert "'a'" in str(result.error)


def test_duckdb_cache_read_raises() -> None:
    result = duckdb.connect(":memory:").execute(_DUPLICATE_SQL)
    with pytest.raises(ExecutionError) as excinfo:
        rows_from_result(result)
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN


def test_sqlite_adapter_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    sqlite3.connect(str(db_path)).close()
    with pytest.raises(ExecutionError) as excinfo:
        SqliteAdapter()._execute(
            SqlQuery(sql=_DUPLICATE_SQL, source="db"),
            source_config=SQLiteSourceConfig(type="sqlite", path=str(db_path)),
        )
    assert excinfo.value.code is ERR_RESULT_DUPLICATE_COLUMN
