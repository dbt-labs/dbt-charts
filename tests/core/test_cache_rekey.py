"""Tests for DuckDB cache re-key by (source_hash, query_hash, variables_hash).

Thread 1: Re-key tests — cross-board dedup, source invalidation.
Thread 2: Protocol conformance — QueryResultCache Protocol + fake backend.
Thread 3: Import hygiene — module importable without duckdb installed.
"""

from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.execute.cache_backend import CachedQueryFailure, CacheHit
from dbt_charts.core.execute.duckdb_cache import (
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

BOARD_YAML_A = """
title: BoardA
queries:
  revenue:
    sql: SELECT amount FROM orders
    source: test_profile
charts:
  c:
    query: revenue
    type: kpi
    value: amount
rows:
  - c
"""

BOARD_YAML_B = """
title: BoardB
queries:
  revenue:
    sql: SELECT amount FROM orders
    source: test_profile
charts:
  c:
    query: revenue
    type: kpi
    value: amount
rows:
  - c
"""


@pytest.fixture
def in_memory_cache():
    cache = TrivialDuckDBCache()
    yield cache
    cache.close()


# ─────────────────────────────────────────────────────────────────────────────
# Thread 1: Re-key tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCrossRaceCacheDedup:
    """Canonical: two boards with identical SQL + source share one cache entry.

    Before re-key: (board_slug, query_name) diverge → two entries, two BQ calls.
    After re-key: (source_hash, query_hash, vars_hash) match → one entry, one call.
    """

    def test_board_b_hits_cache_seeded_by_board_a(
        self, in_memory_cache: TrivialDuckDBCache
    ):
        """Key behavior: BoardA seeds cache; BoardB reads it without adapter call."""
        from dbt_charts.core.execute import Executor

        # Compile both boards
        result_a = compile(BOARD_YAML_A)
        result_b = compile(BOARD_YAML_B)

        shared_rows = [{"amount": 500}]

        # Adapter for board A — succeeds once
        ok = Mock()
        ok.is_success = True
        ok.data = shared_rows
        ok.resolved_relations = None
        ok.truncated_reason = None
        registry_a = Mock()
        registry_a.execute.return_value = ok

        # Adapter for board B — must NOT be called
        registry_b = Mock()
        registry_b.execute.side_effect = AssertionError(
            "BoardB adapter must not be called — should hit BoardA's cache entry"
        )

        executor_a = Executor(
            result_a.board,
            adapter_registry=registry_a,
            query_registry=result_a.query_registry,
            result_cache=in_memory_cache,
        )
        executor_b = Executor(
            result_b.board,
            adapter_registry=registry_b,
            query_registry=result_b.query_registry,
            result_cache=in_memory_cache,
        )

        # BoardA executes → seeds cache
        rows_a = executor_a.execute_query("revenue")
        assert rows_a == shared_rows
        assert registry_a.execute.call_count == 1

        # BoardB executes → must read from cache (adapter NOT called)
        rows_b = executor_b.execute_query("revenue")
        assert rows_b == shared_rows
        assert registry_b.execute.call_count == 0


BOARD_YAML_REFS = """
title: Refs
queries:
  upstream:
    sql: SELECT 1 AS n
    source: test_profile
  downstream:
    sql: SELECT n FROM ({{ queries.upstream }}) AS u
    source: test_profile
charts:
  c:
    query: downstream
    type: kpi
    value: n
rows:
  - c
"""


class TestReferenceExpansionKeysCache:
    """Editing an upstream query must bust the downstream query's cache entry."""

    def test_upstream_edit_misses_downstream_cache(
        self, in_memory_cache: TrivialDuckDBCache, monkeypatch: pytest.MonkeyPatch
    ):
        from dbt_charts.core.execute import Executor

        # The file-source path writes the cache under the authored query, which
        # is where a stale downstream entry used to survive.
        monkeypatch.setattr(Executor, "_has_file_support", True)
        monkeypatch.setattr(
            Executor, "_resolve_file_source", lambda self, q: Mock(files={})
        )

        def run(yaml_text: str, rows: list[dict[str, int]]) -> tuple[list, Mock]:
            compiled = compile(yaml_text)
            materializer = Mock()
            materializer.materialize_and_run.return_value = (rows, {})
            executor = Executor(
                compiled.board,
                adapter_registry=Mock(),
                query_registry=compiled.query_registry,
                result_cache=in_memory_cache,
            )
            monkeypatch.setattr(
                Executor, "_get_file_materializer", lambda self: materializer
            )
            return executor.execute_query("downstream"), materializer

        old_rows, _ = run(BOARD_YAML_REFS, [{"n": 1}])
        edited = BOARD_YAML_REFS.replace("SELECT 1 AS n", "SELECT 2 AS n")
        new_rows, materializer = run(edited, [{"n": 2}])

        assert old_rows == [{"n": 1}]
        assert new_rows == [{"n": 2}]
        assert materializer.materialize_and_run.call_count == 1

    def test_upstream_dbt_ref_manifest_change_changes_downstream_key(self) -> None:
        from dbt_charts.core.execute import Executor

        compiled = compile(
            BOARD_YAML_REFS.replace("SELECT 1 AS n", "SELECT n FROM {{ ref('x') }}")
        )

        def key_for(manifest_version: str) -> tuple[str, str, str]:
            registry = Mock()
            registry.resolve_query_source.return_value = {}
            registry.dbt_manifest_version.return_value = manifest_version
            executor = Executor(
                compiled.board,
                adapter_registry=registry,
                query_registry=compiled.query_registry,
            )
            return executor._cache_key(
                "downstream", executor._get_query("downstream"), {}
            )

        assert key_for("v1") != key_for("v2")


class TestSourceHashInvalidation:
    """Different source → different source_hash → cache miss."""

    def test_different_source_is_cache_miss(self, in_memory_cache: TrivialDuckDBCache):
        sql = "SELECT 1 AS x"
        source_a = "bigquery://project-a/dataset-a"
        source_b = "bigquery://project-b/dataset-b"

        query_hash = compute_query_hash(sql)
        vars_hash = compute_variables_hash({})
        source_hash_a = compute_source_hash(source_a)
        source_hash_b = compute_source_hash(source_b)

        assert source_hash_a != source_hash_b

        in_memory_cache.put(
            source_hash_a,
            query_hash,
            vars_hash,
            [{"x": 1}],
            board_slug="board1",
            query_name="q",
        )

        # Same query/vars but different source → miss
        assert in_memory_cache.get(source_hash_b, query_hash, vars_hash) is None
        # Same source → hit
        assert in_memory_cache.get(source_hash_a, query_hash, vars_hash) is not None

    def test_same_source_string_produces_same_hash(self):
        s = "bigquery://my-project/my-dataset"
        assert compute_source_hash(s) == compute_source_hash(s)

    def test_different_source_strings_produce_different_hashes(self):
        assert compute_source_hash("bigquery://p1/d1") != compute_source_hash(
            "bigquery://p2/d2"
        )

    def test_source_dict_produces_stable_hash(self):
        d = {"type": "bigquery", "project": "p1", "dataset": "d1"}
        h1 = compute_source_hash(d)
        h2 = compute_source_hash(d)
        assert h1 == h2

    def test_source_dict_different_project_different_hash(self):
        d1 = {"type": "bigquery", "project": "p1", "dataset": "d1"}
        d2 = {"type": "bigquery", "project": "p2", "dataset": "d1"}
        assert compute_source_hash(d1) != compute_source_hash(d2)


class TestNewKeyRoundtrip:
    """put/get roundtrip with the new (source_hash, query_hash, vars_hash) key."""

    def test_put_then_get_returns_data(self, in_memory_cache: TrivialDuckDBCache):
        sh = compute_source_hash("duckdb://:memory:")
        qh = compute_query_hash("SELECT 1 AS x")
        vh = compute_variables_hash({})
        data = [{"x": 1}]

        in_memory_cache.put(sh, qh, vh, data, board_slug="f", query_name="q")
        result = in_memory_cache.get(sh, qh, vh)
        assert isinstance(result, CacheHit)
        assert result.rows == data

    def test_get_miss_returns_none(self, in_memory_cache: TrivialDuckDBCache):
        assert in_memory_cache.get("no", "such", "key") is None

    def test_failure_roundtrip(self, in_memory_cache: TrivialDuckDBCache):
        sh = compute_source_hash("duckdb://:memory:")
        qh = compute_query_hash("SELECT 1 AS x")
        vh = compute_variables_hash({})
        exc = RuntimeError("boom")

        in_memory_cache.put(sh, qh, vh, exc, board_slug="f", query_name="q")
        failure = in_memory_cache.get(sh, qh, vh)
        assert isinstance(failure, CachedQueryFailure)
        assert failure.error_class == "RuntimeError"
        assert "boom" in failure.error_message

    def test_clear_removes_entry(self, in_memory_cache: TrivialDuckDBCache):
        sh = compute_source_hash("duckdb://:memory:")
        qh = compute_query_hash("SELECT 1 AS x")
        vh = compute_variables_hash({})
        in_memory_cache.put(sh, qh, vh, [{"x": 1}], board_slug="f", query_name="q")
        assert in_memory_cache.get(sh, qh, vh) is not None

        in_memory_cache.clear(sh, qh, vh)
        assert in_memory_cache.get(sh, qh, vh) is None


# ─────────────────────────────────────────────────────────────────────────────
# Thread 2: QueryResultCache Protocol
# ─────────────────────────────────────────────────────────────────────────────


class TestQueryResultCacheProtocol:
    """QueryResultCache is a runtime_checkable Protocol."""

    def test_trivial_cache_is_instance_of_protocol(
        self, in_memory_cache: TrivialDuckDBCache
    ):
        from dbt_charts.core.execute.cache_backend import QueryResultCache

        assert isinstance(in_memory_cache, QueryResultCache)

    def test_protocol_exported_from_execute_init(self):
        from dbt_charts.core.execute import QueryResultCache  # noqa: F401

    def test_fake_backend_satisfies_protocol(self):
        """A pure Python fake that matches the Protocol surface is accepted."""
        from dbt_charts.core.execute.cache_backend import (
            QueryResultCache,
        )

        class FakeCache:
            def get(self, source_hash: str, query_hash: str, variables_hash: str):
                return None

            def put(
                self,
                source_hash,
                query_hash,
                variables_hash,
                outcome,
                *,
                board_slug,
                query_name,
                source_name="",
                truncated_reason=None,
            ):
                pass

            def clear(self, source_hash, query_hash, variables_hash):
                pass

            def close(self):
                pass

            def supports_cache_refs(self):
                return False

        fake = FakeCache()
        assert isinstance(fake, QueryResultCache)

    def test_executor_accepts_protocol_backend(self):
        """Executor constructed with a fake Protocol backend runs without error."""
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.cache_backend import QueryResultCache

        class NoopCache:
            def get(self, source_hash, query_hash, variables_hash, *, ttl=None):
                return None

            def put(
                self,
                source_hash,
                query_hash,
                variables_hash,
                outcome,
                *,
                board_slug,
                query_name,
                source_name="",
                truncated_reason=None,
            ):
                pass

            def clear(self, source_hash, query_hash, variables_hash):
                pass

            def close(self):
                pass

            def supports_cache_refs(self):
                return False

        assert isinstance(NoopCache(), QueryResultCache)

        result = compile(BOARD_YAML_A)
        ok = Mock()
        ok.is_success = True
        ok.data = [{"amount": 1}]
        ok.resolved_relations = None
        ok.truncated_reason = None
        mock_registry = Mock()
        mock_registry.execute.return_value = ok

        noop = NoopCache()
        executor = Executor(
            result.board,
            adapter_registry=mock_registry,
            query_registry=result.query_registry,
            result_cache=noop,
        )
        rows = executor.execute_query("revenue")
        assert rows == [{"amount": 1}]


# ─────────────────────────────────────────────────────────────────────────────
# Thread 3: Import hygiene
# ─────────────────────────────────────────────────────────────────────────────


class TestImportHygiene:
    """duckdb_cache module can be imported without duckdb installed.

    Uses monkeypatch to flip HAS_DUCKDB without reloading the module
    (reloading creates new class objects and breaks isinstance checks elsewhere).
    The canonical guard now lives in _duckdb_cache_base (where the check runs).
    """

    def test_has_duckdb_flag_exists(self):
        """HAS_DUCKDB is a module-level bool in the base module — the guard for optional import."""
        import dbt_charts.core.execute._duckdb_cache_base as base

        assert isinstance(base.HAS_DUCKDB, bool)

    def test_cache_backend_importable_independently(self):
        """cache_backend has no duckdb dependency — importable even without duckdb."""
        # Import directly (no duckdb needed — pure Protocol definition)
        from dbt_charts.core.execute.cache_backend import (
            CachedQueryFailure,  # noqa: F401
            QueryResultCache,  # noqa: F401
        )

    def test_trivial_cache_init_raises_without_duckdb(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """TrivialDuckDBCache.__init__ raises ImportError when HAS_DUCKDB is False."""
        import dbt_charts.core.execute._duckdb_cache_base as base
        import dbt_charts.core.execute.trivial_local_cache as mod

        monkeypatch.setattr(base, "HAS_DUCKDB", False)

        with pytest.raises((ImportError, RuntimeError), match="dbt-charts\\[duckdb\\]"):
            mod.TrivialDuckDBCache()

    def test_query_result_cache_protocol_importable_without_duckdb_in_path(self):
        """Protocol is importable even if HAS_DUCKDB is False (it's in cache_backend, not duckdb_cache)."""
        import dbt_charts.core.execute._duckdb_cache_base as base

        original = base.HAS_DUCKDB
        base.HAS_DUCKDB = False
        try:
            # Protocol lives in cache_backend — no duckdb needed
            from dbt_charts.core.execute.cache_backend import QueryResultCache

            assert QueryResultCache is not None
        finally:
            base.HAS_DUCKDB = original


class TestPutReplacesNotAppends:
    """Re-caching the same key must REPLACE prior rows, not append.

    A cached query result is the *complete* result for its key. The render
    pipeline writes the same key repeatedly — on every re-render, and when two
    charts share byte-identical SQL (identical content-hash → identical key).
    Appending made get() return N×rows, which tripped the pre-aggregated-data
    validator with bogus "duplicate rows" errors on otherwise-clean queries.
    """

    def test_repeated_put_does_not_duplicate_rows(
        self, in_memory_cache: TrivialDuckDBCache
    ) -> None:
        sh = compute_source_hash("duckdb://:memory:")
        qh = compute_query_hash(
            "SELECT region, SUM(revenue) AS revenue FROM t GROUP BY region"
        )
        vh = compute_variables_hash({})
        data = [
            {"region": "North", "revenue": 1},
            {"region": "South", "revenue": 2},
            {"region": "East", "revenue": 3},
            {"region": "West", "revenue": 4},
        ]
        kw = {"board_slug": "f", "query_name": "q"}

        for _ in range(3):
            in_memory_cache.put(sh, qh, vh, data, **kw)
            hit = in_memory_cache.get(sh, qh, vh)
            assert hit is not None
            assert hit.rows == data
