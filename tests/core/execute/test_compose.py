from unittest.mock import MagicMock

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import CsvSourceConfig, DuckDBSourceConfig
from dbt_charts.core.diagnostics.codes_execute import ERR_MUTATING_SQL
from dbt_charts.core.execute.adapters.base import QueryResult, ResolvedRelation
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.compose import ResolvedQuery, compose
from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS


def _refs() -> MagicMock:
    refs = MagicMock(spec=DbtRefResolver)
    refs.resolve.side_effect = lambda sql, target_path, *args: (
        sql.replace("{{ ref('orders') }}", "main.orders"),
        [],
    )
    return refs


def _compose(query: SqlQuery, refs: MagicMock, **overrides: object) -> ResolvedQuery:
    kwargs: dict[str, object] = {
        "variables": {},
        "params": None,
        "board": None,
        "source_config": DuckDBSourceConfig(type="duckdb"),
        "dialect_name": "postgres",
        "render_dialect": INLINE_PLACEHOLDERS,
        "refs": refs,
        "semantic": None,
    }
    resolved = compose(query, **{**kwargs, **overrides})  # type: ignore[arg-type] — kwargs dict is built untyped so tests can override any parameter
    assert not isinstance(resolved, QueryResult), resolved.error
    return resolved


def test_dbt_refs_resolve_once() -> None:
    refs = _refs()
    resolved = _compose(SqlQuery(sql="SELECT * FROM {{ ref('orders') }}"), refs)

    assert resolved.sql == "SELECT * FROM main.orders"
    assert refs.resolve.call_count == 1


def test_variables_stay_bindings() -> None:
    resolved = _compose(
        SqlQuery(sql="SELECT * FROM t WHERE region = '{{ region }}'"),
        _refs(),
        variables={"region": "North"},
    )

    assert resolved.params == ("North",)
    assert "North" not in resolved.sql
    assert resolved.inline("postgres") == "SELECT * FROM t WHERE region = 'North'"


def test_setup_sql_is_rendered_with_variables() -> None:
    resolved = _compose(
        SqlQuery(sql="SELECT 1", setup_sql="CREATE MACRO {{ name }}(x) AS x + 1"),
        _refs(),
        variables={"name": "inc"},
        dialect_name="duckdb",
    )

    assert resolved.setup_sql == ("CREATE MACRO inc(x) AS x + 1",)


def test_limit_rides_along() -> None:
    resolved = _compose(SqlQuery(sql="SELECT 1", limit=5), _refs())

    assert resolved.limit.effective_limit == 5


def test_relations_include_the_querys_own_refs_when_composing_board_queries() -> None:
    refs = MagicMock(spec=DbtRefResolver)
    own = ResolvedRelation(ref_name="orders", schema="main")
    inlined = ResolvedRelation(ref_name="customers", schema="main")
    refs.resolve.side_effect = [
        ("SELECT * FROM main.orders, {{ queries.base }}", [own]),
        ("SELECT * FROM main.orders, main.customers", [inlined]),
    ]
    board = MagicMock()
    board.queries = {"base": SqlQuery(sql="SELECT * FROM {{ ref('customers') }}")}

    resolved = _compose(
        SqlQuery(sql="SELECT * FROM {{ ref('orders') }}, {{ queries.base }}"),
        refs,
        board=board,
    )

    assert resolved.resolved_relations == (own, inlined)


def test_select_only_guard_refuses_mutating_sql() -> None:
    result = compose(
        SqlQuery(sql="DROP TABLE t"),
        variables={},
        params=None,
        board=None,
        source_config=DuckDBSourceConfig(type="duckdb"),
        dialect_name="postgres",
        render_dialect=INLINE_PLACEHOLDERS,
        refs=_refs(),
        semantic=None,
    )

    assert isinstance(result, QueryResult)
    assert result.error is not None
    assert result.error.code is ERR_MUTATING_SQL


def test_pre_rendered_params_pass_through() -> None:
    resolved = _compose(SqlQuery(sql="SELECT :p1"), _refs(), params=["x"])

    assert resolved.params == ("x",)


def test_setup_sql_guard_refuses_mutating_setup() -> None:
    result = compose(
        SqlQuery(sql="SELECT 1", setup_sql="DROP TABLE t"),
        variables={},
        params=None,
        board=None,
        source_config=DuckDBSourceConfig(type="duckdb"),
        dialect_name="postgres",
        render_dialect=INLINE_PLACEHOLDERS,
        refs=_refs(),
        semantic=None,
    )

    assert isinstance(result, QueryResult)
    assert result.error is not None
    assert result.error.code is ERR_MUTATING_SQL


def test_dbt_refs_are_refused_on_file_sources() -> None:
    refs = _refs()
    result = compose(
        SqlQuery(sql="SELECT * FROM {{ ref('orders') }}", source="marts"),
        variables={},
        params=None,
        board=None,
        source_config=CsvSourceConfig(type="csv", files={"orders": "orders.csv"}),
        dialect_name="duckdb",
        render_dialect=INLINE_PLACEHOLDERS,
        refs=refs,
        semantic=None,
    )

    assert isinstance(result, QueryResult)
    assert "not available on file sources" in str(result.error)
    refs.resolve.assert_not_called()
