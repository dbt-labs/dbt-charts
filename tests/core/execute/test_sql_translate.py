"""translate_sql: authored-dialect SQL to the warehouse dialect, failing closed."""

import subprocess
import sys

import pytest

from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    DbtTargetSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_SQL_DIALECT_UNSUPPORTED
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute import sql_translate
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.sql_translate import (
    dialect_differs,
    engine_dialect,
    translate_sql,
)

PARAM = "\x00dct_param_1\x00"
REL = "\x00dct_rel_1\x00"


def test_identity_when_dialects_match_after_alias_mapping() -> None:
    sql = "select  foo(1)   from t"
    assert translate_sql(sql, "mssql", "tsql") == sql
    assert translate_sql(sql, "duckdb", "duckdb") == sql


def test_translates_between_dialects_and_keeps_sentinels() -> None:
    sql = f"select date_trunc('month', d) as m from {REL} where a = {PARAM}"
    out = translate_sql(sql, "duckdb", "bigquery")
    assert "TIMESTAMP_TRUNC(d, MONTH)" in out
    assert out.count(PARAM) == 1
    assert out.count(REL) == 1


def test_fabric_uses_its_own_datetime_types() -> None:
    out = translate_sql(
        "select cast(a as timestamp), cast(b as timestamptz) from t",
        "duckdb",
        "fabric",
    )
    assert "DATETIME2(6)" in out
    assert "DATETIMEOFFSET" not in out


def test_integer_division_keeps_source_meaning_on_postgres() -> None:
    out = translate_sql("select x / nullif(y, 0) from t", "duckdb", "postgres")
    assert "DOUBLE PRECISION" in out


@pytest.mark.parametrize(
    "sql",
    [
        "select some_made_up_fn(x) from t",
        "select a from t intersect all select a from t",
        "select 1; select 2",
        "select from where",
        f"select * from t where a = '{PARAM}'",
    ],
)
def test_untranslatable_sql_raises_named_error(sql: str) -> None:
    with pytest.raises(ExecutionError) as exc:
        translate_sql(sql, "duckdb", "bigquery")
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


@pytest.mark.parametrize(
    ("sql", "read", "write"),
    [
        # sqlglot moves the placeholder inside the interval's string literal
        (f"select d + interval {PARAM} day from t", "duckdb", "postgres"),
        (f"select d + interval {PARAM} day from t", "duckdb", "snowflake"),
        # a raw-text Command is passed through verbatim, untranslated
        ("explain select safe_divide(a, b) from t", "bigquery", "postgres"),
        ("select * from t where a in (explain select 1)", "bigquery", "postgres"),
        # sqlglot fails internally on this construct
        (f"select make_interval(days => {PARAM})", "redshift", "mysql"),
    ],
)
def test_translation_that_misplaces_or_skips_is_rejected(
    sql: str, read: str, write: str
) -> None:
    with pytest.raises(ExecutionError) as exc:
        translate_sql(sql, read, write)
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


def test_unknown_sqlglot_dialect_raises_the_named_error() -> None:
    with pytest.raises(ExecutionError) as exc:
        translate_sql("select 1", "duckdb", "synapse")
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


def test_lost_sentinel_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A translation that drops a parameter must never reach the warehouse.
    monkeypatch.setattr(sql_translate, "_emit", lambda tree, write: "SELECT NULL AS a")
    with pytest.raises(ExecutionError) as exc:
        translate_sql(f"select {PARAM} as a", "duckdb", "postgres")
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


def test_module_imports_in_a_fresh_interpreter() -> None:
    # A suite run hides an import cycle when another module loads the adapters
    # package first; a fresh interpreter importing this module alone does not.
    result = subprocess.run(
        [sys.executable, "-c", "import dbt_charts.core.execute.sql_translate"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _config(source_type: str) -> DbtTargetSourceConfig:
    return DbtTargetSourceConfig(type=source_type)


@pytest.mark.parametrize(
    ("source_type", "engine"),
    [("postgresql", "postgres"), ("mssql", "sqlserver"), ("mariadb", "mysql")],
)
def test_engine_dialect_canonicalizes_aliases(source_type: str, engine: str) -> None:
    assert engine_dialect("duckdb", _config(source_type)) == engine
    assert not dialect_differs(engine, _config(source_type))
    assert dialect_differs("duckdb", _config(source_type))


def test_file_sources_run_on_duckdb() -> None:
    csv = CsvSourceConfig(type="csv", files={"data": "data.csv"})
    assert engine_dialect("duckdb", csv) == "duckdb"
    assert not dialect_differs("duckdb", csv)
    assert dialect_differs("bigquery", csv)


def test_source_type_without_a_dialect_raises_the_named_error() -> None:
    with pytest.raises(ExecutionError) as exc:
        engine_dialect("duckdb", _config("synapse"))
    assert exc.value.code is ERR_SQL_DIALECT_UNSUPPORTED


@pytest.mark.parametrize(
    ("write", "expected_sql"),
    [
        ("snowflake", "SELECT DATEADD(DAY, ?, ?) AS x"),
        ("sqlserver", "SELECT DATEADD(DAY, @p1, @p2) AS x"),
    ],
)
def test_bound_params_follow_the_translated_argument_order(
    write: str, expected_sql: str
) -> None:
    # BigQuery DATE_ADD(d, INTERVAL n DAY) puts n before d once translated, so
    # driver-bound values must come back in the output's order, not the input's.
    sql, params = sql_translate.render_in_dialect(
        "select date_add({{ d }}, interval {{ n }} day) as x",
        "bigquery",
        write,
        {"d": "2024-01-01", "n": 3},
        None,
        True,
        DbtRefResolver(None),
        None,
        get_dialect(write),
    )

    assert sql == expected_sql
    assert params == [3, "2024-01-01"]
