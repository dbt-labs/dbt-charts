"""Tests for {{ queries.X.cache }} cache-read cross-source composition.

TDD order:
1. _QueryNamespace proxy: .cache sentinel, __str__ backward compat
2. Executor: DuckDB cache-ref detection, demand-execution, rewrite, execute
3. Error paths: no cache, uncacheable upstream, missing upstream
4. Route-separation: {{ queries.X }} vs {{ queries.X.cache }} distinct paths
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.template._helpers import _QueryNamespace
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

# ─────────────────────────────────────────────────────────────────────────────
# 1. _QueryNamespace proxy
# ─────────────────────────────────────────────────────────────────────────────

_SENTINEL_RE = re.compile(r"^__dct_cache_ref__(\w+)__$")


def _make_namespace(queries: dict[str, Any]) -> _QueryNamespace:
    return _QueryNamespace(queries)


class TestQueryProxy:
    """The proxy returned by _QueryNamespace.__getattr__ satisfies both paths."""

    def _queries(self) -> dict[str, Any]:
        # SqlQuery requires source; a bare name is enough — these tests only
        # exercise SQL string rendering, never resolve the source to a connection.
        q = SqlQuery(
            sql="SELECT 1 AS x",
            source="db",
        )
        return {"q1": q}

    def test_str_is_parenthesized_aliased_subquery(self) -> None:
        ns = _make_namespace(self._queries())
        proxy = ns.q1
        # {{ queries.X }} renders as a parenthesized subquery aliased to the query
        # name, so the same authored SQL is valid on DuckDB and Postgres.
        assert str(proxy) == "(SELECT 1 AS x) AS q1"

    def test_cache_property_returns_sentinel(self) -> None:
        ns = _make_namespace(self._queries())
        sentinel = ns.q1.cache
        assert _SENTINEL_RE.match(sentinel), f"sentinel {sentinel!r} did not match"

    def test_cache_sentinel_encodes_query_name(self) -> None:
        q = SqlQuery(
            sql="SELECT 42 AS v",
            source="db",
        )
        ns = _make_namespace({"my_query": q})
        sentinel = ns.my_query.cache
        m = _SENTINEL_RE.match(sentinel)
        assert m and m.group(1) == "my_query"

    def test_missing_query_raises_attribute_error(self) -> None:
        ns = _make_namespace({})
        with pytest.raises(AttributeError, match="not found"):
            _ = ns.nonexistent

    def test_inline_and_cache_are_distinct_strings(self) -> None:
        q = SqlQuery(
            sql="SELECT 1",
            source="db",
        )
        ns = _make_namespace({"q": q})
        inline = str(ns.q)
        cache_ref = ns.q.cache
        assert inline != cache_ref
        # inline is an aliased subquery, cache_ref is the sentinel
        assert inline == "(SELECT 1) AS q"
        assert "__dct_cache_ref__" in cache_ref


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _project_with_db_source(local_project: Callable[..., FilesystemProject]) -> Any:
    """A Project whose sources registry resolves the 'db' name to an in-memory
    DuckDB connection — the execute-time counterpart to `source: db` in the
    board YAML built by `_build_board_yaml` (D-09: boards can no longer define
    sources inline, so the registry lives on the project, not the board)."""
    from dbt_charts.core.compile.config import ProjectSourcesConfig

    project = local_project(Path.cwd())
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={"db": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


def _build_board_yaml(queries: dict[str, str], chart_query: str) -> str:
    """Build a minimal board YAML using 'db' as the default source (resolved
    at execute time via `_project_with_db_source`)."""
    lines = [
        "source: db",
        "queries:",
    ]
    for name, sql in queries.items():
        lines.append(f"  {name}:")
        lines.append("    sql: |")
        for line in sql.splitlines():
            lines.append(f"      {line}")
        lines.append("    source: db")
    lines += [
        "charts:",
        "  dummy:",
        "    type: bar",
        "    x: x",
        "    y: x",
        f"    query: {chart_query}",
    ]
    return "\n".join(lines)


def _build_board(queries: dict[str, str], chart_query: str | None = None) -> Any:
    """Compile a board YAML with DuckDB source and return the normalized Board."""
    from dbt_charts.core.compile.compiler import compile

    cq = chart_query or next(iter(queries))
    yaml = _build_board_yaml(queries, cq)
    result = compile(yaml)
    assert result.success, [str(e) for e in result.errors]
    assert result.board is not None
    return result.board


def _executor_with_cache(
    board: Any,
    cache: TrivialDuckDBCache,
    local_project: Callable[..., FilesystemProject],
) -> Any:
    from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
    from dbt_charts.core.execute.executor import Executor

    registry = build_adapter_registry(_project_with_db_source(local_project))
    return Executor(board, registry, use_cache=True, result_cache=cache)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Executor: DuckDB cache-ref — demand-execute + rewrite + execute
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheRefComposition:
    """Integration: {{ queries.A.cache }} in query B forces A to execute+cache,
    then B runs in DuckDB against A's cached rows."""

    def test_cache_ref_executes_upstream_and_returns_rows(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Query B reads A's cached rows via {{ queries.A.cache }}."""
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "source_a": "SELECT 1 AS x, 2 AS y",
                "composed": "SELECT x, y, x + y AS z FROM {{ queries.source_a.cache }}",
            },
            chart_query="source_a",
        )

        executor = _executor_with_cache(board, cache, local_project)
        rows = executor.execute_query("composed")

        assert len(rows) == 1
        assert rows[0]["x"] == 1
        assert rows[0]["y"] == 2
        assert rows[0]["z"] == 3

    def test_inline_ref_and_cache_ref_yield_same_result(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """{{ queries.A }} and {{ queries.A.cache }} both produce the same rows.

        The inline path inlines SQL as a subquery (requires explicit parens in
        authored SQL); the cache path reads from DuckDB. Both must agree on data.
        """
        cache_inline = TrivialDuckDBCache()
        cache_cache = TrivialDuckDBCache()

        # Inline path: {{ queries.base }} renders as a parenthesized subquery
        # aliased to the query name — no hand-written parens or alias needed.
        board_inline = _build_board(
            {
                "base": "SELECT 10 AS val",
                "via_inline": "SELECT val FROM {{ queries.base }}",
            },
            chart_query="base",
        )
        # Cache path: the sentinel is rewritten to a DuckDB subquery automatically.
        board_cache = _build_board(
            {
                "base": "SELECT 10 AS val",
                "via_cache": "SELECT val FROM {{ queries.base.cache }}",
            },
            chart_query="base",
        )

        exec_inline = _executor_with_cache(board_inline, cache_inline, local_project)
        exec_cache = _executor_with_cache(board_cache, cache_cache, local_project)

        rows_inline = exec_inline.execute_query("via_inline")
        rows_cache = exec_cache.execute_query("via_cache")

        assert rows_inline == [{"val": 10}]
        assert rows_cache == [{"val": 10}]

    def test_same_query_referenced_twice_collides(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two refs to the same query in one FROM clause collide on the alias.

        Both render as ``(sql) AS base`` in the same scope → duplicate alias →
        the engine raises. This pins the accepted trade-off (rename to dedup).
        """
        from dbt_charts.core.diagnostics.execution import QueryError

        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "base": "SELECT 1 AS id, 10 AS val",
                "selfjoin": (
                    "SELECT * FROM {{ queries.base }} "
                    "JOIN {{ queries.base }} USING (id)"
                ),
            },
            chart_query="base",
        )
        executor = _executor_with_cache(board, cache, local_project)
        with pytest.raises(QueryError):
            executor.execute_query("selfjoin")

    def test_upstream_demand_executed_and_cached(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Upstream query is not pre-executed; cache-ref triggers demand execution."""
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "upstream": "SELECT 99 AS n",
                "reader": "SELECT n FROM {{ queries.upstream.cache }}",
            },
            chart_query="upstream",
        )

        executor = _executor_with_cache(board, cache, local_project)

        # upstream has NOT been run yet
        assert not executor.is_cached("upstream")

        rows = executor.execute_query("reader")

        # upstream is now cached (side-effect of demand execution)
        assert executor.is_cached("upstream")
        assert rows == [{"n": 99}]

    def test_cache_ref_aliased_to_query_name(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A cache ref is aliased to the query name; columns read as <name>.col."""
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "nums": "SELECT 7 AS n",
                "doubled": "SELECT nums.n * 2 AS n2 FROM {{ queries.nums.cache }}",
            },
            chart_query="nums",
        )

        executor = _executor_with_cache(board, cache, local_project)
        rows = executor.execute_query("doubled")

        assert rows == [{"n2": 14}]


# ─────────────────────────────────────────────────────────────────────────────
# 2b. Isolation & injection (security regressions)
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheRefIsolation:
    """Composition runs in an isolated engine whose namespace is ONLY the
    referenced upstream rows — never any other table on any connection, and
    runtime variables are parameters, not interpolated text."""

    def test_composing_query_cannot_read_unregistered_table(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A composing query may reference only its cache-ref upstreams.

        Regression for the arbitrary-table-read vulnerability: pre-fix the
        composing SQL ran on the cache backend's own connection, so a table that
        happens to live there (an internal/app table) was reachable. Post-fix the
        engine is a fresh in-process DuckDB holding only the registered upstream
        rows, so any other table reference fails.
        """
        cache = TrivialDuckDBCache()
        # A table the composing query must NOT be able to reach. Pre-fix it lived
        # on the same connection cache-ref ran on and would leak; post-fix the
        # isolated engine never sees it.
        with cache._connection() as conn:
            conn.execute("CREATE TABLE off_limits (secret INTEGER)")
            conn.execute("INSERT INTO off_limits VALUES (42)")

        board = _build_board(
            {
                "base": "SELECT 1 AS x",
                "leak": (
                    "SELECT x FROM {{ queries.base.cache }} "
                    "UNION ALL SELECT secret FROM off_limits"
                ),
            },
            chart_query="base",
        )
        executor = _executor_with_cache(board, cache, local_project)

        with pytest.raises(Exception, match="off_limits|Cache-ref composition"):
            executor.execute_query("leak")

    def test_runtime_variable_is_parameterized_not_interpolated(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A runtime variable in a cache-ref composing query is bound as a
        parameter, so an injection payload is treated as a data value.

        Regression for the variable-injection vector: pre-fix a ``{{ var }}`` in
        the composing SQL was string-interpolated onto the raw connection.
        """
        from dbt_charts.core.compile.compiler import compile

        cache = TrivialDuckDBCache()
        yaml = "\n".join(
            [
                "source: db",
                "variables:",
                "  region:",
                "    input: text",
                "    default: North",
                "queries:",
                "  base:",
                "    sql: SELECT 'North' AS region, 10 AS val",
                "    source: db",
                "  composed:",
                "    sql: |",
                "      SELECT val FROM {{ queries.base.cache }}",
                "      WHERE region = '{{ region }}'",
                "    source: db",
                "charts:",
                "  dummy:",
                "    type: bar",
                "    x: val",
                "    y: val",
                "    query: base",
            ]
        )
        result = compile(yaml)
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None
        board = result.board
        executor = _executor_with_cache(board, cache, local_project)

        # Injection payload: if interpolated, the trailing OR would flip the
        # filter and return the row. Parameterized, it matches no region → 0 rows.
        rows = executor.execute_query("composed", {"region": "North' OR '1'='1"})
        assert rows == []

        # A legitimate value still selects the row.
        rows_ok = executor.execute_query("composed", {"region": "North"})
        assert rows_ok == [{"val": 10}]

    def test_empty_upstream_fails_loudly(self) -> None:
        """An upstream that returned zero rows can't be composed over (no schema to
        infer) — the isolated engine fails loudly rather than returning wrong data.

        Pins the behavior change from the removed json_to_recordset path (which
        raised a ValueError on empty rows): the failure is loud, not silent.
        """
        from dbt_charts.core.execute.cache_composition import compose_over_named_rows

        with pytest.raises(RuntimeError, match="Isolated-engine query failed"):
            compose_over_named_rows("SELECT * FROM base", {"base": []}, [])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Error paths
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheRefErrors:
    def test_error_when_no_cache_backend(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """{{ queries.X.cache }} without a cache backend raises a clear error."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.executor import Executor

        board = _build_board(
            {
                "upstream": "SELECT 1 AS x",
                "consumer": "SELECT x FROM {{ queries.upstream.cache }}",
            },
            chart_query="upstream",
        )

        registry = build_adapter_registry(_project_with_db_source(local_project))
        # No result_cache attached
        executor = Executor(board, registry, use_cache=False, result_cache=None)

        with pytest.raises(Exception, match="cache"):
            executor.execute_query("consumer")

    def test_error_when_upstream_not_in_registry(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """{{ queries.X.cache }} referencing an unknown query raises at runtime.

        Compile accepts an unknown cache-ref target (verified: it does not reject
        it), so the executor's demand-execution loop is the real enforcement.
        """
        from dbt_charts.core.diagnostics.execution import ExecutionError

        cache = TrivialDuckDBCache()
        board = _build_board(
            {"consumer": "SELECT x FROM {{ queries.ghost.cache }}"},
            chart_query="consumer",
        )
        executor = _executor_with_cache(board, cache, local_project)

        with pytest.raises(ExecutionError, match="ghost.*does not exist"):
            executor.execute_query("consumer")

    def test_error_when_upstream_has_cache_false(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """{{ queries.X.cache }} when X has cache: false raises at runtime.

        Compile accepts the reference (verified), so the executor's
        ``not upstream.cache`` check is the real enforcement.
        """
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.diagnostics.execution import ExecutionError

        cache = TrivialDuckDBCache()
        yaml = "\n".join(
            [
                "source: db",
                "queries:",
                "  upstream:",
                "    sql: SELECT 1 AS x",
                "    source: db",
                "    cache: false",
                "  consumer:",
                "    sql: SELECT x FROM {{ queries.upstream.cache }}",
                "    source: db",
                "charts:",
                "  d: {type: bar, x: x, y: x, query: consumer}",
            ]
        )
        result = compile(yaml)
        assert result.success, [str(e) for e in result.errors]
        assert result.board is not None
        executor = _executor_with_cache(result.board, cache, local_project)

        with pytest.raises(ExecutionError, match="cache: false"):
            executor.execute_query("consumer")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Cycle detection
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheRefCycleDetection:
    """Cycles via .cache must be caught — compile-time for authored YAML,
    runtime (re-entrancy guard) as defense-in-depth."""

    def test_self_cycle_rejected_at_compile(self) -> None:
        """A query referencing itself via .cache is rejected at compile."""
        from dbt_charts.core.compile.compiler import compile

        yaml = (
            "source: db\n"
            "queries:\n"
            "  self_ref:\n"
            "    sql: 'SELECT x FROM {{ queries.self_ref.cache }}'\n"
            "charts:\n"
            "  d:\n"
            "    type: bar\n"
            "    x: x\n"
            "    y: x\n"
            "    query: self_ref\n"
        )
        result = compile(yaml)
        assert not result.success
        errors = " ".join(str(e) for e in result.errors)
        assert "self_ref" in errors or "circular" in errors.lower()

    def test_mutual_cache_cycle_rejected_at_compile(self) -> None:
        """A→B→A cycle via .cache is rejected at compile."""
        from dbt_charts.core.compile.compiler import compile

        yaml = (
            "source: db\n"
            "queries:\n"
            "  query_a:\n"
            "    sql: 'SELECT x FROM {{ queries.query_b.cache }}'\n"
            "  query_b:\n"
            "    sql: 'SELECT x FROM {{ queries.query_a.cache }}'\n"
            "charts:\n"
            "  d:\n"
            "    type: bar\n"
            "    x: x\n"
            "    y: x\n"
            "    query: query_a\n"
        )
        result = compile(yaml)
        assert not result.success
        errors = " ".join(str(e) for e in result.errors)
        assert (
            "query_a" in errors or "query_b" in errors or "circular" in errors.lower()
        )

    def test_runtime_reentrancy_guard_triggers_on_registry_cycle(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The runtime re-entrancy guard catches a cache-ref cycle that spans the
        cross-board query_registry — which the per-board compile-time check cannot
        see (the compiler only validates cycles within a single board).
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.diagnostics.execution import ExecutionError
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.executor import Executor

        # looper (in the board) → partner.cache; partner lives only in the
        # cross-board registry and points back to looper.cache — a cycle compile
        # never sees because it validates one board at a time.
        board = _build_board(
            {"looper": "SELECT x FROM {{ queries.partner.cache }}"},
            chart_query="looper",
        )
        partner = SqlQuery(
            sql="SELECT y FROM {{ queries.looper.cache }}",
            source="db",
            cache=CachePolicy(enabled=True),
        )
        registry = build_adapter_registry(_project_with_db_source(local_project))
        executor = Executor(
            board,
            registry,
            use_cache=True,
            result_cache=TrivialDuckDBCache(),
            query_registry={"partner": partner},
        )

        with pytest.raises(ExecutionError, match="[Cc]ycle detected at runtime"):
            executor.execute_query("looper")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Persistent cache write-through for composing queries (HIGH 3)
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheRefPersistentWriteThrough:
    """Composing query results must be written to the persistent backend
    so a fresh Executor can serve them without re-executing."""

    def test_composing_result_survives_new_executor(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "upstream": "SELECT 5 AS n",
                "composed": "SELECT n * 2 AS doubled FROM {{ queries.upstream.cache }}",
            },
            chart_query="upstream",
        )

        exec1 = _executor_with_cache(board, cache, local_project)
        rows1 = exec1.execute_query("composed")
        assert rows1 == [{"doubled": 10}]

        # A second executor on the same persistent cache should serve the
        # composing result from persistent store without re-executing.
        exec2 = _executor_with_cache(board, cache, local_project)
        assert exec2.is_cached("composed"), (
            "Composing query result must be persisted so a new executor can serve it."
        )


class TestCacheRefMaxRowsEnforcement:
    """The execution.max_rows ceiling bounds a composing query's own fetch
    (compose_over_named_rows' fetchmany(), not a SQL rewrite) — a result
    exceeding it is truncated before the composing result reaches the
    persistent cache, not just sliced off afterward.
    """

    def test_composed_result_truncated_before_cache_write(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "source_a": "SELECT 1 AS x",
                "composed": (
                    "SELECT * FROM range(20) AS t(n), {{ queries.source_a.cache }}"
                ),
            },
            chart_query="source_a",
        )

        executor = _executor_with_cache(board, cache, local_project)
        rows = executor.execute_query("composed")
        assert len(rows) == 3

        query = board.queries["composed"]
        key = compute_cache_key(query, {}, board.sources)
        cached = cache.get(*key, ttl=None)
        assert cached is not None
        assert len(cached.rows) == 3, (
            "the persistent cache must never receive the untruncated composed result"
        )

    def test_composing_querys_own_limit_is_honored(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The composing query's own Query.limit bounds its result too, not
        just the DCT_MAX_ROWS_CEILING — the composing path shares
        resolve_effective_row_limit with the four adapters, which resolves
        the author's own Query.limit the same way they do.

        `limit:` is not an authorable YAML field on a `type: sql` query
        today (only http queries carry it in the authored
        schema), so this sets it directly on the compiled query — the same
        way a non-YAML producer (e.g. the Looker migrator's inline-UDF-script
        queries) would."""
        cache = TrivialDuckDBCache()
        board = _build_board(
            {
                "source_a": "SELECT 1 AS x",
                "composed": (
                    "SELECT * FROM range(20) AS t(n), {{ queries.source_a.cache }}"
                ),
            },
            chart_query="source_a",
        )
        board.queries["composed"].limit = 5
        executor = _executor_with_cache(board, cache, local_project)

        rows = executor.execute_query("composed")

        assert len(rows) == 5

    def test_fetchmany_called_not_fetchall(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: the ceiling must bound compose_over_named_rows'
        own fetch via fetchmany(), not fetch all rows then slice. Reverting
        to fetchall() would make the row-count assertions above still pass
        (apply_row_limit_truncation/_enforce_result_limits reproduce
        identical visible output from a full fetch) while silently pulling
        an unbounded composed result into memory — the OOM the driver-bound
        fetch exists to prevent, on the isolated compose connection too.

        Calls compose_over_named_rows directly (not through the full
        Executor/TrivialDuckDBCache pipeline) so the driver-level class-wide
        spy below only ever observes the one isolated compose connection —
        the persistent cache is itself DuckDB-backed and would otherwise
        trip the same spy with unrelated fetchall() calls of its own.
        """
        import duckdb

        from dbt_charts.core.execute.cache_composition import compose_over_named_rows

        fetchmany_args: list[int] = []
        fetchall_called: list[bool] = []

        original_fetchmany = duckdb.DuckDBPyConnection.fetchmany
        original_fetchall = duckdb.DuckDBPyConnection.fetchall

        def spy_fetchmany(self: duckdb.DuckDBPyConnection, n: int) -> object:
            fetchmany_args.append(n)
            return original_fetchmany(self, n)

        def spy_fetchall(self: duckdb.DuckDBPyConnection) -> object:
            fetchall_called.append(True)
            return original_fetchall(self)

        monkeypatch.setattr(duckdb.DuckDBPyConnection, "fetchmany", spy_fetchmany)
        monkeypatch.setattr(duckdb.DuckDBPyConnection, "fetchall", spy_fetchall)

        rows = compose_over_named_rows(
            sql="SELECT * FROM t",
            named_rows={"t": [{"n": i} for i in range(20)]},
            params=[],
            limit=4,
        )

        assert len(rows) == 4
        assert fetchmany_args == [4]
        assert not fetchall_called
