"""AdapterRegistry composition for queries that set `dialect:`.

A query without `dialect:` must reach the adapter exactly as it always has; one
with it is translated to the source's dialect, with its variables inlined as
literals and its dbt relations spelled for the warehouse that runs it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import duckdb

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.authored import QueryDialect
from dbt_charts.core.compile.models.query.normalized import AnyQuery, SqlQuery
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.dialects import SQLDialect
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.adapters.base import BaseAdapter, QueryParams, QueryResult
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

_SNOWFLAKE = {"type": "snowflake", "account": "xy12345", "database": "analytics"}


class _RecordingAdapter(BaseAdapter):
    """Claims snowflake SQL, flattens params like SqlAdapter, records what it got."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, QueryParams]] = []

    @property
    def supported_types(self) -> set[str]:
        return {"sql"}

    def param_render_dialect(self, source_config: ResolvedSourceConfig) -> SQLDialect:
        return INLINE_PLACEHOLDERS

    def _execute(
        self,
        query: AnyQuery,
        variables: object = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        assert isinstance(query, SqlQuery)
        self.seen.append((query.sql, params))
        return QueryResult(data=[])


def _snowflake_registry(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> tuple[AdapterRegistry, _RecordingAdapter]:
    registry = AdapterRegistry(
        project=local_project(tmp_path),
        project_sources=ProjectSourcesConfig(sources={"wh": _SNOWFLAKE}),
    )
    adapter = _RecordingAdapter()
    registry.register(adapter)
    return registry, adapter


def _run(
    registry: AdapterRegistry,
    sql: str,
    dialect: QueryDialect | None,
    **variables: object,
) -> QueryResult:
    from dbt_charts.core.compile.compiler import compile

    board = compile(
        "queries:\n  q:\n    sql: select 1\n    source: wh\n"
        "charts:\n  c:\n    type: table\n    query: q\nrows:\n  - c\n",
        project_sources=ProjectSourcesConfig(sources={"wh": _SNOWFLAKE}),
    ).board
    assert board is not None
    query = SqlQuery(sql=sql, source="wh", dialect=dialect)
    return registry.execute(query, variables or None, board=board)


def test_query_without_dialect_reaches_the_adapter_unchanged(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry, adapter = _snowflake_registry(tmp_path, local_project)

    result = _run(registry, "select a from t where b = {{ b }}", None, b="x")

    assert result.error is None
    assert adapter.seen == [("select a from t where b = \x00dct_param_1\x00", ["x"])]


def test_dialect_equal_to_the_source_is_not_translated(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry, adapter = _snowflake_registry(tmp_path, local_project)

    _run(registry, "select a from t where b = {{ b }}", "snowflake", b="x")

    assert adapter.seen == [("select a from t where b = \x00dct_param_1\x00", ["x"])]


def test_dialect_query_is_translated_and_params_stay_params(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry, adapter = _snowflake_registry(tmp_path, local_project)

    result = _run(
        registry,
        "select date_trunc('month', d) as m from t where who = {{ who }}",
        "duckdb",
        who="O'Brien",
    )

    assert result.error is None
    assert adapter.seen == [
        (
            "SELECT DATE_TRUNC('MONTH', d) AS m FROM t WHERE who = \x00dct_param_1\x00",
            ["O'Brien"],
        )
    ]


def test_untranslatable_sql_returns_a_named_error_and_never_runs(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry, adapter = _snowflake_registry(tmp_path, local_project)

    result = _run(registry, "select some_made_up_fn(a) from t", "duckdb")

    assert result.error is not None
    assert result.error.code.code == "ERR-SQL-DIALECT-UNSUPPORTED"
    assert adapter.seen == []


def _duckdb_project_with_orders(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, placed DATE)")
    conn.execute("INSERT INTO orders VALUES (1, '2024-05-17'), (2, '2024-06-02')")
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  wh:\n    type: duckdb\n    path: {db_path}\n"
    )
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.p.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "main",
                        "alias": "orders",
                    }
                },
                "sources": {},
            }
        )
    )


def test_ref_and_variables_run_end_to_end_on_a_different_warehouse(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """BigQuery-written SQL with a ref() and a quote-bearing variable runs on DuckDB."""
    from dbt_charts.core.compile.compiler import compile
    from dbt_charts.core.execute.adapters import build_adapter_registry

    _duckdb_project_with_orders(tmp_path)
    board = compile(
        "variables:\n  cutoff:\n    input: text\n    default: x\n"
        "queries:\n  q:\n    dialect: bigquery\n    source: wh\n    sql: |\n"
        "      select date_trunc(placed, month) as m, {{ cutoff }} as note\n"
        "      from {{ ref('orders') }}\n      order by id\n"
        "charts:\n  c:\n    type: table\n    query: q\nrows:\n  - c\n"
    ).board
    assert board is not None
    registry = build_adapter_registry(local_project(tmp_path), read_only=True)

    result = registry.execute(
        board.queries["q"], {"cutoff": "it's"}, board=board, query_name="q"
    )

    assert result.error is None, result.error
    assert [row["note"] for row in result.data] == ["it's", "it's"]
    assert [str(row["m"])[:10] for row in result.data] == ["2024-05-01", "2024-06-01"]


def test_dialect_query_with_no_resolved_source_is_refused(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """With no source config the target is unknown: never run it untranslated."""
    registry, _ = _snowflake_registry(tmp_path, local_project)
    query = SqlQuery(sql="select 1", source="wh", dialect="duckdb")

    result = registry._compose_query_refs(
        query,
        None,
        None,
        board=None,
        source_config=None,
        render_dialect=INLINE_PLACEHOLDERS,
    )

    assert isinstance(result, QueryResult)
    assert result.error is not None
    assert result.error.code.code == "ERR-SQL-DIALECT-UNSUPPORTED"


def test_variable_value_that_looks_like_a_ref_arrives_verbatim(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.core.compile.compiler import compile
    from dbt_charts.core.execute.adapters import build_adapter_registry

    _duckdb_project_with_orders(tmp_path)
    board = compile(
        "variables:\n  v:\n    input: text\n    default: x\n"
        "queries:\n  q:\n    dialect: bigquery\n    source: wh\n"
        "    sql: select {{ v }} as v\n"
        "charts:\n  c:\n    type: table\n    query: q\nrows:\n  - c\n"
    ).board
    assert board is not None
    registry = build_adapter_registry(local_project(tmp_path), read_only=True)

    result = registry.execute(
        board.queries["q"], {"v": "{{ ref('orders') }}"}, board=board, query_name="q"
    )

    assert result.error is None, result.error
    assert result.data == [{"v": "{{ ref('orders') }}"}]


def test_interval_variable_cannot_inject_through_a_string_literal(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    registry, adapter = _snowflake_registry(tmp_path, local_project)

    result = _run(
        registry,
        "select d + interval {{ n }} day from t",
        "duckdb",
        n="1' || (select secret from creds) || '",
    )

    assert result.error is not None
    assert result.error.code.code == "ERR-SQL-DIALECT-UNSUPPORTED"
    assert adapter.seen == []


def test_file_source_runs_a_duckdb_dialect_query(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    from dbt_charts.agent_api import ProjectSession

    (tmp_path / "data.csv").write_text("a\n1\n2\n")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  files:\n    type: csv\n    files:\n      data: data.csv\n"
    )
    board_path = tmp_path / "board.yml"
    board_path.write_text(
        "title: probe\n"
        "text: probe board\n"
        "queries:\n  q:\n    dialect: duckdb\n    source: files\n"
        "    sql: select sum(a) as total from data\n"
    )
    with ProjectSession.from_project(local_project(tmp_path)) as session:
        result = session.query_board("q", board_path)

    assert result.success is True, result.errors
    assert result.data == [{"total": 3}]
