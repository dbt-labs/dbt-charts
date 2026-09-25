"""Tests for setup_sql support on queries.

setup_sql allows queries to declare non-nestable preamble statements
(e.g. CREATE TEMP FUNCTION) that run before the main query body.
"""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.compile.normalize.queries import normalize_query
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry

from ._board_utils import make_test_board


class TestSetupSqlNormalization:
    """Test that setup_sql is propagated through normalization."""

    def test_normalize_query_with_setup_sql(self):
        """Normalizing a dict with setup_sql produces a SqlQuery with setup_sql."""
        query = normalize_query(
            "my_query",
            {
                "sql": "SELECT add_one(1)",
                "source": "db",
                "setup_sql": "CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);",
            },
            sources={},
        )
        assert isinstance(query, SqlQuery)
        assert (
            query.setup_sql
            == "CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);"
        )

    def test_normalize_query_without_setup_sql(self):
        """Normalizing a dict without setup_sql yields setup_sql=None."""
        query = normalize_query("q", {"sql": "SELECT 1", "source": "db"}, sources={})
        assert isinstance(query, SqlQuery)
        assert query.setup_sql is None

    def test_variable_dependencies_from_setup_sql(self):
        """Variables referenced in setup_sql are tracked as dependencies."""
        query = normalize_query(
            "q",
            {
                "sql": "SELECT my_fn(col) FROM t",
                "source": "db",
                "setup_sql": "CREATE TEMP FUNCTION my_fn(x INT64) RETURNS INT64 AS (x + {{ offset }});",
            },
            sources={},
        )
        assert "offset" in query.variable_dependencies


class TestSetupSqlQueryReferences:
    """Test that {{ queries.X }} still returns only the sql body, not setup_sql."""

    def test_query_namespace_returns_sql_not_setup_sql(self):
        """{{ queries.X }} resolves to X's sql, not its setup_sql."""
        from dbt_charts.core.compile.template._helpers import _QueryNamespace

        queries = {
            "base": SqlQuery(
                sql="SELECT add_one(1) AS result",
                source="db",
                setup_sql="CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);",
            ),
        }
        ns = _QueryNamespace(queries)
        assert ns.base == "SELECT add_one(1) AS result"


class TestSetupSqlPropagation:
    """Test setup_sql propagation through query dependencies."""

    def test_collect_setup_sql_from_dependency(self):
        """When query B depends on query A, B's effective setup includes A's setup."""
        from dbt_charts.core.execute.setup_sql import collect_setup_sql

        queries = {
            "base": SqlQuery(
                sql="SELECT add_one(x) FROM t",
                source="db",
                setup_sql="CREATE TEMP FUNCTION add_one(x INT64) RETURNS INT64 AS (x + 1);",
            ),
            "derived": SqlQuery(
                sql="SELECT * FROM {{ queries.base }} WHERE result > 5", source="db"
            ),
        }
        setup_stmts = collect_setup_sql("derived", queries)
        assert len(setup_stmts) == 1
        assert "add_one" in setup_stmts[0]

    def test_collect_setup_sql_deduplicates(self):
        """Identical setup_sql blocks from multiple dependencies are deduplicated."""
        from dbt_charts.core.execute.setup_sql import collect_setup_sql

        shared_setup = "CREATE TEMP FUNCTION f() RETURNS INT64 AS (1);"
        queries = {
            "a": SqlQuery(sql="SELECT f()", source="db", setup_sql=shared_setup),
            "b": SqlQuery(sql="SELECT f()", source="db", setup_sql=shared_setup),
            "combined": SqlQuery(
                sql="SELECT * FROM {{ queries.a }} UNION ALL SELECT * FROM {{ queries.b }}",
                source="db",
            ),
        }
        setup_stmts = collect_setup_sql("combined", queries)
        assert len(setup_stmts) == 1

    def test_collect_setup_sql_ordering(self):
        """setup_sql from dependencies comes before the query's own setup."""
        from dbt_charts.core.execute.setup_sql import collect_setup_sql

        queries = {
            "base": SqlQuery(
                sql="SELECT base_fn(1)",
                source="db",
                setup_sql="CREATE TEMP FUNCTION base_fn(x INT64) RETURNS INT64 AS (x);",
            ),
            "derived": SqlQuery(
                sql="SELECT derived_fn(base_fn(1)) FROM {{ queries.base }}",
                source="db",
                setup_sql="CREATE TEMP FUNCTION derived_fn(x INT64) RETURNS INT64 AS (x * 2);",
            ),
        }
        setup_stmts = collect_setup_sql("derived", queries)
        assert len(setup_stmts) == 2
        assert "base_fn" in setup_stmts[0]
        assert "derived_fn" in setup_stmts[1]

    def test_collect_setup_sql_no_deps(self):
        """AuthoredQuery with setup_sql but no deps returns its own setup."""
        from dbt_charts.core.execute.setup_sql import collect_setup_sql

        queries = {
            "standalone": SqlQuery(
                sql="SELECT my_fn(1)",
                source="db",
                setup_sql="CREATE TEMP FUNCTION my_fn(x INT64) RETURNS INT64 AS (x);",
            ),
        }
        setup_stmts = collect_setup_sql("standalone", queries)
        assert len(setup_stmts) == 1
        assert "my_fn" in setup_stmts[0]

    def test_collect_setup_sql_no_setup(self):
        """AuthoredQuery without setup_sql and no deps returns empty list."""
        from dbt_charts.core.execute.setup_sql import collect_setup_sql

        queries = {
            "plain": SqlQuery(sql="SELECT 1", source="db"),
        }
        setup_stmts = collect_setup_sql("plain", queries)
        assert setup_stmts == []


class TestSetupSqlExecution:
    """Test that setup_sql is executed before the main query."""

    def test_duckdb_executes_setup_sql_before_main_query(self):
        """setup_sql statements run before the main query on DuckDB."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        query = SqlQuery(
            sql="SELECT add_ten(5) AS result",
            setup_sql="CREATE OR REPLACE MACRO add_ten(x) AS x + 10;",
            source="db",
        )
        result = adapter.execute(query, {})
        assert result.is_success, f"AuthoredQuery failed: {result.error}"
        assert result.data[0]["result"] == 15

    def test_duckdb_setup_sql_with_variables(self):
        """setup_sql can contain Jinja variables that get resolved."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        query = SqlQuery(
            sql="SELECT add_n(5) AS result",
            setup_sql="CREATE OR REPLACE MACRO add_n(x) AS x + {{ n }};",
            source="db",
        )
        result = adapter.execute(query, {"n": 20})
        assert result.is_success, f"AuthoredQuery failed: {result.error}"
        assert result.data[0]["result"] == 25

    def test_setup_sql_propagated_through_dependency(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Dependency setup_sql runs before a derived query."""
        queries = {
            "base": SqlQuery(
                sql="SELECT add_ten(5) AS val",
                setup_sql="CREATE OR REPLACE MACRO add_ten(x) AS x + 10;",
                source="db",
            ),
            "derived": SqlQuery(
                sql="SELECT val * 2 AS doubled FROM {{ queries.base }}",
                source="db",
            ),
        }
        board = make_test_board(
            id="test",
            queries=queries,
            sources={"db": {"type": "duckdb", "path": ":memory:"}},
        )
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            use_cache=False,
        )
        data = executor.execute_query("derived", {})
        assert data[0]["doubled"] == 30


class TestSetupSqlConnectionSharing:
    """setup_sql and the main query must execute in a single adapter call.

    BigQuery rejects standalone CREATE TEMP FUNCTION ("must be followed by an
    actual query in the same script"). Two separate adapter.execute() calls
    also drop session-scoped TEMP entities before the main query runs. The
    adapter concatenates setup_sql and the main inlined SQL into one
    multi-statement script and sends it as a single execute() call.
    """

    def test_non_duckdb_setup_and_main_send_one_combined_execute(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """adapter.execute is called once with setup_sql + main SQL concatenated."""
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.source import PostgresSourceConfig
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        source_config = PostgresSourceConfig(
            type="postgres", host="localhost", dbname="db", user="u", password="p"
        )
        query = SqlQuery(
            sql="SELECT my_fn(1) AS val",
            setup_sql="CREATE TEMP FUNCTION my_fn(x INT64) RETURNS INT64 AS (x + 1);",
            source="db",
        )

        mock_table = MagicMock()
        mock_table.column_names = ["val"]
        mock_table.rows = [(2,)]

        mock_adapter = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_adapter.connection_named.return_value = mock_ctx
        mock_adapter.execute.return_value = (None, mock_table)

        with patch(
            "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            adapter = SqlAdapter(
                project=local_project(Path("/tmp")),
                dbt_project_path=None,
                profile_type="postgres",
            )
            adapter._execute(query, {}, source_config=source_config)

        assert mock_adapter.connection_named.call_count == 1
        # Pool names the connection by thread id; assert it starts with the pool prefix.
        conn_name = mock_adapter.connection_named.call_args[0][0]
        assert conn_name.startswith("dbt_charts_pool_"), conn_name
        # Two execute() calls: one at connection setup (timeout SQL), then one
        # combined SQL with the CREATE TEMP FUNCTION and main SELECT.
        assert mock_adapter.execute.call_count == 2
        sent_sql = mock_adapter.execute.call_args[0][0]  # last call = combined query
        assert "CREATE TEMP FUNCTION my_fn" in sent_sql
        # The row-limit ceiling never rewrites SQL text (bounds the driver's
        # own fetch via a limit= kwarg instead), so the author's casing
        # survives untouched.
        assert "SELECT my_fn(1) AS val" in sent_sql
        # CREATE statement appears before the SELECT (BQ contract).
        assert sent_sql.index("CREATE TEMP FUNCTION") < sent_sql.index("SELECT")
        # fetch=True (we want results from the SELECT, not setup).
        assert mock_adapter.execute.call_args[1]["fetch"] is True


def _mock_adapter_registry():
    """Return a mock registry that captures executed SQL."""
    executed: list[tuple[str, ...]] = []

    def capture(query, variables=None, params=None):
        executed.append((query.sql,))
        result = Mock()
        result.is_success = True
        result.data = [{"val": 1}]
        result.column_descriptions = None
        result.resolved_relations = None
        result.truncated_reason = None
        return result

    registry = Mock()
    registry.execute.side_effect = capture
    return registry, executed


class TestSetupSqlCacheKey:
    """setup_sql participates in the result-cache key."""

    def test_result_cache_hash_includes_setup_sql(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Result cache entries must diverge when setup_sql changes."""
        board = make_test_board(id="test", title="test", queries={}, sources={})
        result_cache = Mock()
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            result_cache=result_cache,
        )

        query_a = SqlQuery(
            sql="SELECT f(1) AS val",
            setup_sql="CREATE TEMP FUNCTION f(x INT64) RETURNS INT64 AS (x + 1);",
            source="s",
        )
        query_b = SqlQuery(
            sql="SELECT f(1) AS val",
            setup_sql="CREATE TEMP FUNCTION f(x INT64) RETURNS INT64 AS (x + 2);",
            source="s",
        )

        executor._cache_outcome("q", query_a, [{"val": 2}], {})
        executor._cache_outcome("q", query_b, [{"val": 3}], {})

        # _cache_outcome calls cache.put(source_hash, query_hash, vars_hash, outcome, ...)
        first_hash = result_cache.put.call_args_list[0].args[1]
        second_hash = result_cache.put.call_args_list[1].args[1]

        assert first_hash != second_hash


class TestSetupSqlValidation:
    def test_query_refs_in_setup_sql_rejected(self):
        """{{ queries.X }} in setup_sql should fail at compile time."""
        yaml_content = """\
title: Test
queries:
  base:
    sql: SELECT 1 AS v
    source: s
  bad:
    setup_sql: |
      CREATE TEMP TABLE t AS {{ queries.base }}
    sql: SELECT * FROM t
    source: s
charts:
  c:
    query: bad
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert not result.success
        assert any("setup_sql" in str(e) for e in result.errors)


class TestSetupSqlMutatingGuard:
    """setup_sql is validated by `validate_setup_sql` immediately before each
    setup statement reaches the warehouse driver. The TEMP carve-out (CREATE
    TEMP FUNCTION / TABLE / VIEW and DuckDB MACRO) is allowed; anything else
    (DROP, INSERT, non-TEMP CREATE) is refused.
    """

    def test_setup_sql_drop_rejected_duckdb(self) -> None:
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        query = SqlQuery(sql="SELECT 1 AS a", setup_sql="DROP TABLE x", source="db")
        mock_conn = MagicMock()
        with patch.object(
            adapter, "_get_duckdb_connection_for_query", return_value=mock_conn
        ):
            result = adapter._execute(query)
        assert result.error is not None
        assert "outside the setup_sql allowlist" in str(result.error)
        # Setup driver never sees mutating SQL.
        mock_conn.execute.assert_not_called()

    def test_setup_sql_drop_rejected_dbt_adapter(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """setup_sql DROP routed via the dbt-adapter path is rejected before
        adapter.execute() — no warehouse contact for mutating setup."""
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.compile.models.source import PostgresSourceConfig
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        adapter = SqlAdapter(
            project=local_project(Path("/tmp")),
            dbt_project_path=None,
            profile_type="postgres",
        )
        query = SqlQuery(sql="SELECT 1 AS a", setup_sql="DROP TABLE x", source="db")
        source_config = PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="p"
        )
        mock_dbt_adapter = MagicMock()
        with patch(
            "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
            return_value=mock_dbt_adapter,
        ):
            result = adapter._execute(query, source_config=source_config)
        assert result.error is not None
        assert "setup_sql" in str(result.error)
        mock_dbt_adapter.execute.assert_not_called()

    def test_setup_sql_valid_temp_function_still_works(self) -> None:
        """CREATE [OR REPLACE] MACRO is allowed for DuckDB end-to-end."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        query = SqlQuery(
            sql="SELECT add_ten(5) AS result",
            setup_sql="CREATE OR REPLACE MACRO add_ten(x) AS x + 10;",
            source="db",
        )
        result = adapter._execute(query)
        assert result.error is None, result.error
        assert result.data[0]["result"] == 15
