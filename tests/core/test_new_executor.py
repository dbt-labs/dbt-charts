"""Tests for the new execute module (dbt_charts.execute).

These tests verify the executor works correctly with the new architecture.
"""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import ExecutionError, Executor, QueryError
from dbt_charts.core.execute.adapters import build_adapter_registry


class TestExecutorBasic:
    """Basic executor tests."""

    def test_executor_requires_adapter_registry(self):
        """Executor must receive an explicit adapter_registry.

        Building one implicitly from cwd silently couples query execution to
        the process working directory; the entry point owns that decision.
        """
        result = compile(
            """
title: Test
queries:
  q:
    sql: SELECT 1 as v
    source: s
charts:
  c:
    query: q
    type: kpi
    value: v
rows:
  - c
"""
        )
        with pytest.raises(TypeError, match="adapter_registry"):
            Executor(result.board, query_registry=result.query_registry)  # type: ignore[call-arg]

    def test_executor_query_lookup(self):
        """Test executor resolves queries via execute_query with a mock adapter."""
        yaml_content = """
title: Test
queries:
  my_query:
    sql: SELECT * FROM users
    source: test_profile
charts:
  c:
    query: my_query
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"id": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        data = executor.execute_query("my_query", use_cache=False)
        assert data == [{"id": 1}]
        mock_registry.execute.assert_called_once()

    def test_executor_strips_queries_prefix(self):
        """Test executor strips 'queries.' prefix from query names in execute_query."""
        yaml_content = """
title: Test
queries:
  sales:
    sql: SELECT * FROM sales
    source: test_profile
charts:
  c:
    query: queries.sales
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        # Create mock adapter registry
        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"id": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        # Both forms should work in execute_query (which strips the prefix)
        data1 = executor.execute_query("sales", use_cache=False)
        data2 = executor.execute_query("queries.sales", use_cache=False)

        # Both should have returned data (prefix stripped)
        assert data1 == data2


class TestExecutorWithMockAdapter:
    """Tests with mocked adapter for query execution."""

    def test_execute_query_with_mock(self):
        """Test executing a query with mocked adapter."""
        yaml_content = """
title: Test
queries:
  users:
    sql: SELECT * FROM users
    source: test_profile
charts:
  c:
    query: users
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        # Create mock adapter registry
        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        data = executor.execute_query("users")

        assert len(data) == 2
        assert data[0]["name"] == "Alice"
        mock_registry.execute.assert_called_once()

    def test_execute_chart_with_mock(self):
        """Test executing a chart's query with mocked adapter."""
        yaml_content = """
title: Test
queries:
  sales:
    sql: SELECT * FROM sales
    source: test_profile
charts:
  revenue:
    query: sales
    type: line
    x: date
    y: amount
rows:
  - revenue
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"date": "2024-01", "amount": 100}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        chart = result.board.charts["revenue"]
        data = executor.execute_chart(chart)

        assert len(data) == 1
        assert data[0]["amount"] == 100


class TestExecutorCaching:
    """Tests for query result caching."""

    def test_query_results_are_cached(self):
        """Test that query results are cached."""
        yaml_content = """
title: Test
queries:
  data:
    sql: SELECT * FROM data
    source: test_profile
charts:
  c:
    query: data
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"value": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        # Execute twice
        data1 = executor.execute_query("data")
        data2 = executor.execute_query("data")

        # Should only call adapter once (cached)
        assert mock_registry.execute.call_count == 1
        assert data1 == data2

    def test_call_level_use_cache_false_keeps_the_render_memo(self):
        """Call-level `use_cache=False` is no-store, not re-execute.

        Why the memo is unconditional: `execute_query`'s Step 3 comment.
        """
        yaml_content = """
title: Test
queries:
  data:
    sql: SELECT * FROM data
    source: test_profile
charts:
  c:
    query: data
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"value": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        executor.execute_query("data", use_cache=False)
        executor.execute_query("data", use_cache=False)
        assert mock_registry.execute.call_count == 1


class TestExecutorErrors:
    """Tests for error handling."""

    def test_missing_query_raises_error(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test that missing query raises ExecutionError."""
        yaml_content = """
title: Test
queries:
  existing:
    sql: SELECT 1
    source: test_profile
charts:
  c:
    query: existing
    type: kpi
    value: v
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with pytest.raises(ExecutionError):
            executor.execute_query("nonexistent")

    def test_query_execution_failure(self):
        """Test that query execution failure raises error."""
        yaml_content = """
title: Test
queries:
  bad_query:
    sql: SELECT * FROM nonexistent_table
    source: test_profile
charts:
  c:
    query: bad_query
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.is_success = False
        mock_result.error = QueryError("Table not found")
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        with pytest.raises(QueryError):
            executor.execute_query("bad_query")


class TestExecutorVariables:
    """Tests for variable handling."""

    def test_variables_passed_to_adapter(self):
        """Test that variables are passed to the adapter."""
        yaml_content = """
title: Test
variables:
  status:
    input: select
    default: active
queries:
  users:
    sql: SELECT * FROM users WHERE status = '{{ status }}'
    source: test_profile
charts:
  c:
    query: users
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = []
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        executor.execute_query("users", variables={"status": "inactive"})

        # Check that execute was called
        mock_registry.execute.assert_called_once()


class TestCacheKeyGeneration:
    """Tests for the executor's use of cache keys.

    `compute_cache_key` (dbt_charts.core.execute.duckdb_cache) is the single
    source of truth for query cache identity, exercised directly by
    `dbt-charts/tests/core/test_cache_keys.py::TestComputeCacheKey` (SQL hashing,
    variable filtering, determinism, source-hash discrimination). The tests
    here cover what's specific to the executor: its in-memory `_cache` dict is
    keyed by that exact tuple, and end-to-end hit/miss behavior through
    `execute_query`.
    """

    def test_in_memory_cache_key_matches_compute_cache_key(self):
        """The in-memory _cache must be keyed by compute_cache_key's own output
        scoped to the query's policy — not a separately derived key."""
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key
        from dbt_charts.core.execute.executor import _memo_key

        yaml_content = """
title: Test
queries:
  my_query:
    sql: SELECT * FROM users
    source: test_profile
charts:
  c:
    query: my_query
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.is_success = True
        mock_result.data = [{"id": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )
        executor.execute_query("my_query")

        query = result.board.queries["my_query"]
        content_key = compute_cache_key(query, {}, result.board.sources)
        # The memo key is the content key plus the asking query's resolved
        # policy: freshness is per-reader, and the slot is warmed from the
        # shared persistent entry, so keying it on content alone would let one
        # query's policy answer a same-SQL sibling's read.
        assert executor._cache == {_memo_key(query, content_key): [{"id": 1}]}
        # The persistent key stays content-only — a ttl edit must not
        # cold-start the stored entry.
        assert len(content_key) == 3

    def test_distinct_sources_same_sql_do_not_collide_in_memory(self):
        """Regression: two queries with identical SQL but different sources must
        not share an in-memory cache entry.

        Before this fix, the in-memory key dropped source_hash (unlike the
        persistent cache), so the second query's execute_query call would
        silently return the first query's (wrong-source) cached rows instead
        of executing against its own source.
        """
        yaml_content = """
title: Test
queries:
  q_a:
    sql: SELECT 1 as v
    source: source_a
  q_b:
    sql: SELECT 1 as v
    source: source_b
charts:
  c:
    query: q_a
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        rows_by_source = {
            "source_a": [{"v": "from_a"}],
            "source_b": [{"v": "from_b"}],
        }

        def execute_side_effect(query, *args, **kwargs):
            mock_result = Mock()
            mock_result.is_success = True
            mock_result.data = rows_by_source[query.source]
            mock_result.truncated_reason = None
            return mock_result

        mock_registry = Mock()
        mock_registry.execute.side_effect = execute_side_effect

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        assert executor.execute_query("q_a") == rows_by_source["source_a"]
        assert executor.execute_query("q_b") == rows_by_source["source_b"]
        assert mock_registry.execute.call_count == 2
        assert len(executor._cache) == 2

    def test_cache_correctly_uses_new_key_format(self):
        """Test that the cache actually uses the new key format correctly.

        Verifies that changing unused variables doesn't cause cache misses.
        """
        yaml_content = """
title: Test
variables:
  region:
    input: select
    default: North
  unused_var:
    input: text
    default: ignored
queries:
  sales:
    sql: SELECT * FROM sales WHERE region = '{{ region }}'
    source: test_profile
charts:
  c:
    query: sales
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"id": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        # First call with region=North, unused_var=A
        executor.execute_query(
            "sales", variables={"region": "North", "unused_var": "A"}
        )
        # Second call with region=North, unused_var=B (different unused var)
        executor.execute_query(
            "sales", variables={"region": "North", "unused_var": "B"}
        )

        # Should only execute once - second call uses cache
        # because 'unused_var' is not in the query's variable_dependencies
        assert mock_registry.execute.call_count == 1

    def test_cache_invalidates_on_sql_change(self):
        """Test that cache misses when SQL changes (even with same vars).

        This was a bug with the old implementation that used query name.
        """
        yaml_content = """
title: Test
variables:
  region:
    input: select
    default: North
queries:
  sales:
    sql: SELECT * FROM sales WHERE region = '{{ region }}'
    source: test_profile
charts:
  c:
    query: sales
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.success = True
        mock_result.data = [{"id": 1}]
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        # First call
        executor.execute_query("sales", variables={"region": "North"})

        # "Modify" the query SQL in-place (simulating a hot reload)
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        modified_query = SqlQuery(
            sql="SELECT * FROM sales_v2 WHERE region = '{{ region }}'",
            source="test_profile",
            variable_dependencies={"region"},
        )
        executor.board.queries["sales"] = modified_query

        # Second call - should NOT use cache because SQL changed
        executor.execute_query("sales", variables={"region": "North"})

        # Should execute twice - different SQL = different cache key
        assert mock_registry.execute.call_count == 2


class TestNestedQueryResolution:
    """Tests that {{ queries.* }} references are resolved recursively.

    Verifies the executor resolves multi-level query composition
    (style -> calc -> base) so that the SQL sent to the adapter has
    no remaining {{ queries.* }} placeholders.
    """

    def test_multi_level_query_references_resolved(self):
        """Nested queries.* references should be fully expanded before execution."""
        yaml_content = """
title: Test Nested
queries:
  base:
    sql: SELECT id, name FROM users
    source: test_profile
  calc:
    sql: "SELECT *, 1 AS flag FROM {{ queries.base }}"
    source: test_profile
  style:
    sql: "SELECT name, flag FROM {{ queries.calc }}"
    source: test_profile
charts:
  c:
    query: style
    type: table
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.is_success = True
        mock_result.data = [{"name": "Alice", "flag": 1}]
        mock_result.truncated_reason = None
        mock_result.column_descriptions = None
        mock_result.resolved_relations = None
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        executor.execute_query("style")

        # Verify the SQL sent to the adapter is fully resolved
        executed_query = mock_registry.execute.call_args[0][0]
        assert "{{ queries." not in executed_query.sql
        assert "SELECT id, name FROM users" in executed_query.sql

    def test_single_level_query_reference_still_works(self):
        """Simple one-level references should continue working."""
        yaml_content = """
title: Test Single Level
queries:
  base:
    sql: SELECT * FROM orders
    source: test_profile
  wrapper:
    sql: "SELECT sum(amount) FROM {{ queries.base }}"
    source: test_profile
charts:
  c:
    query: wrapper
    type: kpi
    value: sum
rows:
  - c
"""
        result = compile(yaml_content)
        assert result.board is not None
        assert result.query_registry is not None

        mock_registry = Mock()
        mock_result = Mock()
        mock_result.is_success = True
        mock_result.data = [{"sum": 1000}]
        mock_result.truncated_reason = None
        mock_result.column_descriptions = None
        mock_result.resolved_relations = None
        mock_result.truncated_reason = None
        mock_registry.execute.return_value = mock_result

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
        )

        executor.execute_query("wrapper")

        executed_query = mock_registry.execute.call_args[0][0]
        assert "SELECT * FROM orders" in executed_query.sql
        assert "{{ queries." not in executed_query.sql

    def test_circular_dependency_raises_error(self):
        """Circular query dependencies should raise a clear error at execution."""
        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        from ._board_utils import make_test_board

        # Build board manually — compile() catches cycles before we get here
        queries = {
            "a": SqlQuery(sql="SELECT * FROM {{ queries.b }}", source="test"),
            "b": SqlQuery(sql="SELECT * FROM {{ queries.a }}", source="test"),
        }
        board = make_test_board(
            id="test",
            title="Test Circular",
            queries=queries,
            charts={},
            variable_defaults={},
        )

        mock_registry = Mock()
        executor = Executor(board, adapter_registry=mock_registry)

        with pytest.raises(Exception, match="[Cc]ircular"):
            executor.execute_query("a")


class TestExecutionIdentityMemo:
    """One Executor = one execution per query, regardless of cache settings."""

    _YAML = """
title: Test
queries:
  q:
    sql: SELECT industry, count(*) AS n FROM accounts GROUP BY 1 ORDER BY 2 DESC
    source: test_profile
charts:
  c:
    query: q
    type: table
rows:
  - c
"""

    def test_execute_query_is_one_execution_per_render_even_with_cache_off(self):
        """Regression: the in-memory memo is the render's identity guarantee.

        `use_cache=False` disables the persistent store, not the per-render
        memo. Without the memo, the resolve pass and the render pass execute
        the same query separately, and a query whose ORDER BY has ties can
        legally return a different row order each time — flaking pie renders
        with ERR-RESOLVED-PIE-DATA-MISMATCH.
        """
        result = compile(self._YAML)
        assert result.success, result.errors

        order_a = [{"industry": "Tech", "n": 2}, {"industry": "Media", "n": 2}]
        order_b = [{"industry": "Media", "n": 2}, {"industry": "Tech", "n": 2}]
        results = iter([order_a, order_b])

        def _execute(*args, **kwargs):
            ok = Mock()
            ok.is_success = True
            ok.data = [dict(row) for row in next(results)]
            ok.column_descriptions = None
            ok.resolved_relations = None
            ok.truncated_reason = None
            return ok

        mock_registry = Mock()
        mock_registry.execute.side_effect = _execute

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
            use_cache=False,
        )

        first = executor.execute_query("q")
        second = executor.execute_query("q")

        assert second == first
        assert mock_registry.execute.call_count == 1

    def test_force_refresh_still_re_executes_with_cache_off(self):
        """`force_refresh` remains the sanctioned way to get fresh rows."""
        result = compile(self._YAML)
        assert result.success, result.errors

        rows_by_call = iter([[{"n": 1}], [{"n": 2}]])

        def _execute(*args, **kwargs):
            ok = Mock()
            ok.is_success = True
            ok.data = next(rows_by_call)
            ok.column_descriptions = None
            ok.resolved_relations = None
            ok.truncated_reason = None
            return ok

        mock_registry = Mock()
        mock_registry.execute.side_effect = _execute

        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
            use_cache=False,
        )

        assert executor.execute_query("q") == [{"n": 1}]
        assert executor.execute_query("q", force_refresh=True) == [{"n": 2}]
        assert mock_registry.execute.call_count == 2
