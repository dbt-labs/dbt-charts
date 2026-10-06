"""Query-level `dialect:` — authored SQL dialect, compile-time handling."""

from typing import get_args

import sqlglot

from dbt_charts.core.compile.compiler import CompileResult, compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.authored import QueryDialect
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.dialects import DIALECTS

_SOURCES = ProjectSourcesConfig(sources={"db": {"type": "duckdb"}})


def _compile(queries_yaml: str) -> CompileResult:
    board = f"""\
queries:
{queries_yaml}
charts:
  c1:
    type: table
    query: q1
rows:
  - c1
"""
    return compile(board, file="f.yml", project_sources=_SOURCES)


def test_dialect_is_carried_to_the_compiled_query() -> None:
    result = _compile(
        """\
  q1:
    sql: select 1 as a
    source: db
    dialect: bigquery
"""
    )
    assert result.errors == []
    query = result.board.queries["q1"]
    assert isinstance(query, SqlQuery)
    assert query.dialect == "bigquery"


def test_unknown_dialect_is_rejected() -> None:
    result = _compile(
        """\
  q1:
    sql: select 1 as a
    source: db
    dialect: oracle
"""
    )
    assert "oracle" in result.errors[0].message


def test_compile_time_parse_uses_the_authored_dialect() -> None:
    sql = "select * from `p.d.t`"
    without = _compile(f"  q1:\n    sql: {sql}\n    source: db\n")
    with_dialect = _compile(
        f"  q1:\n    sql: {sql}\n    source: db\n    dialect: bigquery\n"
    )
    assert [w.code for w in without.warnings] == ["WARN-PARSE-ERROR"]
    assert with_dialect.warnings == []


def test_composing_queries_with_different_dialects_is_an_error() -> None:
    result = _compile(
        """\
  base:
    sql: select 1 as a
    source: db
    dialect: bigquery
  q1:
    sql: select a from {{ queries.base }}
    source: db
"""
    )
    assert [e.code for e in result.errors] == ["ERR-QUERY-DIALECT-MIXED"]


def test_composing_queries_with_the_same_dialect_compiles() -> None:
    result = _compile(
        """\
  base:
    sql: select 1 as a
    source: db
    dialect: bigquery
  q1:
    sql: select a from {{ queries.base }}
    source: db
    dialect: bigquery
"""
    )
    assert result.errors == []


def test_setup_sql_is_checked_in_the_sources_dialect() -> None:
    result = _compile(
        """\
  q1:
    sql: select 1 as a
    source: db
    dialect: postgres
    setup_sql: CREATE OR REPLACE MACRO double_it(x) AS x * 2
"""
    )
    assert result.errors == []


def test_dialect_vocabulary_matches_source_types() -> None:
    result = _compile(
        """\
  q1:
    sql: select 1 as a
    source: db
    dialect: postgresql
"""
    )
    assert result.errors


def test_cache_reference_requires_a_duckdb_dialect() -> None:
    queries = """\
  base:
    sql: select 1 as a
    source: db
  q1:
    sql: select a from {{ queries.base.cache }}
    source: db
    dialect: %s
"""
    assert [e.code for e in _compile(queries % "bigquery").errors] == [
        "ERR-SQL-DIALECT-UNSUPPORTED"
    ]
    assert _compile(queries % "duckdb").errors == []


def test_cache_reference_is_exempt_from_the_composition_rule() -> None:
    result = _compile(
        """\
  base:
    sql: select 1 as a
    source: db
    dialect: bigquery
  q1:
    sql: select a from {{ queries.base.cache }}
    source: db
"""
    )
    assert result.errors == []


def test_every_warehouse_dialect_is_authorable_with_one_spelling() -> None:
    aliases = {"postgresql", "mssql", "mariadb"}
    authorable = set(get_args(QueryDialect))

    assert authorable == set(DIALECTS) - aliases
    for name in authorable:
        sqlglot.Dialect.get_or_raise(sqlglot_dialect(name))


_INLINE = """\
queries:
  base:
    sql: select 1 as a
    source: db
charts:
  c1:
    type: table
    query:
      sql: %s
      source: db
      dialect: postgres
rows:
  - c1
"""


def test_inline_chart_query_cache_reference_requires_a_duckdb_dialect() -> None:
    result = compile(
        _INLINE % "select a from {{ queries.base.cache }}",
        file="f.yml",
        project_sources=_SOURCES,
    )
    assert [e.code for e in result.errors] == ["ERR-SQL-DIALECT-UNSUPPORTED"]


def test_inline_chart_query_cannot_mix_dialects_through_a_reference() -> None:
    result = compile(
        _INLINE % "select a from {{ queries.base }}",
        file="f.yml",
        project_sources=_SOURCES,
    )
    assert [e.code for e in result.errors] == ["ERR-QUERY-DIALECT-MIXED"]
