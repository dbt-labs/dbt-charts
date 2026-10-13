"""Regression tests: Executor honors the stamped per-query CachePolicy.

Covers:
- `cache: false` — never reads from nor writes to the persistent cache
  (no-store, not store-and-ignore).
- ttl expiry (lazy) — an expired entry is treated as a miss and replaced on
  the re-run; an unexpired entry serves from cache.
- `ttl: forever` (policy ttl None) — never expires.
- The result cache's `get()` receives the resolved policy's ttl.
- Two queries sharing a cache key keep their own ttls (the sibling case),
  across separate executors *and* within one executor's in-memory memo.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.cache_backend import CachedQueryFailure
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache


def _ok_adapter(value: int = 1) -> Mock:
    ok = Mock()
    ok.is_success = True
    ok.data = [{"value": value}]
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    return registry


def _board_yaml(cache_block: str) -> str:
    return f"""\
title: Test
queries:
  q:
    sql: SELECT 1 as value
    source: test_profile
    {cache_block}
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""


class TestCacheDisabledIsNoStore:
    """`cache: false` on a query — no read, no write to the persistent cache."""

    def test_disabled_query_never_writes_to_persistent_cache(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: false"))
            adapter = _ok_adapter()
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            data = executor.execute_query("q")
            assert data == [{"value": 1}]

            # A fresh Executor sharing the same persistent cache must see
            # nothing — the first run must not have written through.
            executor2 = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor2.execute_query("q")
            assert adapter.execute.call_count == 2, (
                "cache: false must re-run the query on the second executor — "
                "a persistent write on the first run would have served a hit instead"
            )
        finally:
            cache.close()

    def test_disabled_query_never_reads_a_pre_seeded_persistent_entry(
        self, tmp_path
    ) -> None:
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        result = df_compile(_board_yaml("cache: false"))
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            cache.put(
                *key,
                [{"value": 999}],
                board_slug="test",
                query_name="q",
            )

            adapter = _ok_adapter(value=1)
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            data = executor.execute_query("q")
            assert data == [{"value": 1}], "must ignore the pre-seeded cache entry"
            assert adapter.execute.call_count == 1
        finally:
            cache.close()


class TestCacheDisabledKeepsTheInRenderMemo:
    """`cache: false` is no-store, not "execute once per consumer".

    The executor's in-memory ``_cache`` is not a freshness cache — it is the
    render's identity guarantee. Every chart on a query calls
    ``execute_query`` (on top of the parallel prefetch), so gating the memo on
    the policy would fan one live query out to one warehouse round trip per
    consumer, at a different instant each time: two charts off one query could
    then display numbers that disagree.
    """

    def test_one_disabled_query_two_charts_executes_once(self) -> None:
        two_charts = """\
title: Test
queries:
  q:
    sql: SELECT 1 as value
    source: test_profile
    cache: false
charts:
  c1:
    query: q
    type: kpi
    value: value
  c2:
    query: q
    type: kpi
    value: value
rows:
  - c1
  - c2
"""
        result = df_compile(two_charts)
        adapter = _ok_adapter()
        executor = Executor(
            result.board,
            adapter_registry=adapter,
            query_registry=result.query_registry,
            result_cache=None,
        )
        first = executor.execute_query("q")
        second = executor.execute_query("q")
        assert first == second == [{"value": 1}]
        assert adapter.execute.call_count == 1, (
            "cache: false must still memoize within one render — every consumer "
            "of a query has to see the same rows read at the same instant"
        )

    def test_call_level_use_cache_false_keeps_the_memo_too(self) -> None:
        """No opt-out skips the memo — only `force_refresh` re-executes.

        Why the memo is unconditional: `execute_query`'s Step 3 comment.
        """
        result = df_compile(_board_yaml("cache: 1h"))
        adapter = _ok_adapter()
        executor = Executor(
            result.board,
            adapter_registry=adapter,
            query_registry=result.query_registry,
            result_cache=None,
        )
        executor.execute_query("q", use_cache=False)
        executor.execute_query("q", use_cache=False)
        assert adapter.execute.call_count == 1


class TestDisabledRootStillHonorsANearerOptIn:
    """Root `cache: false` is the cascade default, not a project kill switch."""

    def test_query_opt_in_under_a_disabled_root_caches(self, tmp_path) -> None:
        """`cache: false` in dbt_charts.yml + `cache: 1h` on a query must cache.

        Nearest scope wins throughout the cascade (a board's opt-out beats a
        cached source, and the reverse holds too), and Cloud — which wires its
        result store unconditionally — already honors the opt-in. The dct boot
        has to provision the store for the same YAML to mean the same thing.
        """
        from dbt_charts.agent_api.cache import project_cache_ctx
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.compile.config import load_config, reset_config

        reset_config()
        (tmp_path / "dbt_charts.yml").write_text("cache: false\n")
        project = FilesystemProject(tmp_path)
        try:
            load_config(project)
            with project_cache_ctx(project) as cache:
                assert cache is not None, (
                    "a disabled root must still open the store — otherwise the "
                    "query's cache: 1h opt-in has nowhere to land"
                )
                result = df_compile(_board_yaml("cache: 1h"))
                assert result.board.queries["q"].cache.enabled is True
                adapter = _ok_adapter()
                for _ in range(2):
                    Executor(
                        result.board,
                        adapter_registry=adapter,
                        query_registry=result.query_registry,
                        result_cache=cache,
                    ).execute_query("q")
                assert adapter.execute.call_count == 1, (
                    "the second executor must be served from the store"
                )
        finally:
            reset_config()


class TestTtlLazyExpiry:
    """An entry older than the resolved ttl is a miss; the re-run replaces it."""

    def test_expired_entry_re_executes_and_replaces(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache:\n      ttl: 1s"))
            adapter = _ok_adapter(value=1)
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor.execute_query("q")
            assert adapter.execute.call_count == 1

            # Directly age the stored entry past the 1s ttl — no sleep, no
            # timing flake.
            from dbt_charts.core.execute.duckdb_cache import compute_cache_key

            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            cache.conn.execute(
                "UPDATE _query_outcomes SET written_at_utc = TIMESTAMP '2000-01-01' "
                "WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
                list(key),
            )

            adapter.execute.return_value.data = [{"value": 2}]
            # A fresh Executor forces the persistent-cache path (bypasses the
            # first executor's own in-memory _cache memo).
            executor2 = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            data = executor2.execute_query("q")
            assert adapter.execute.call_count == 2, "expired entry must re-execute"
            assert data == [{"value": 2}]
        finally:
            cache.close()

    def test_unexpired_entry_served_from_cache(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache:\n      ttl: 1h"))
            adapter = _ok_adapter(value=1)
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor.execute_query("q")

            executor2 = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            data = executor2.execute_query("q")
            assert adapter.execute.call_count == 1, "unexpired entry must be served"
            assert data == [{"value": 1}]
        finally:
            cache.close()

    def test_ttl_forever_does_not_expire(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache:\n      ttl: forever"))
            query = result.board.queries["q"]
            assert query.cache.ttl_timedelta is None

            adapter = _ok_adapter(value=1)
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor.execute_query("q")

            # Age the stored entry far beyond any realistic ttl — the reader's
            # ttl is None, so no age can evict it.
            from dbt_charts.core.execute.duckdb_cache import compute_cache_key

            key = compute_cache_key(query, {}, result.board.sources)
            cache.conn.execute(
                "UPDATE _query_outcomes SET written_at_utc = TIMESTAMP '2000-01-01' "
                "WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
                list(key),
            )

            executor2 = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            data = executor2.execute_query("q")
            assert adapter.execute.call_count == 1, "ttl: forever must never expire"
            assert data == [{"value": 1}]
        finally:
            cache.close()


class TestUseCacheFalseIsNoStore:
    """Call-level use_cache=False skips the persistent store's read and write."""

    def test_use_cache_false_does_not_write_through(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = _ok_adapter()
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor.execute_query("q", use_cache=False)

            executor2 = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor2.execute_query("q")
            assert adapter.execute.call_count == 2, (
                "use_cache=False must not have written through to the persistent cache"
            )
        finally:
            cache.close()


class TestCacheHitAts:
    """Executor tracks each persistent-cache-hit timestamp this render —
    the "data as of" board-chrome signal."""

    def test_no_cache_hit_stays_empty(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            executor = Executor(
                result.board,
                adapter_registry=_ok_adapter(),
                query_registry=result.query_registry,
                result_cache=cache,
            )
            executor.execute_query("q")
            assert executor.cache_hit_ats == [], (
                "a fresh execution (no cache hit) must not set a data-age stamp"
            )
        finally:
            cache.close()

    def test_persistent_cache_hit_records_the_entry_timestamp(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = _ok_adapter()
            seed_executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            seed_executor.execute_query("q")

            hit_executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )
            assert hit_executor.cache_hit_ats == []
            hit_executor.execute_query("q")
            assert hit_executor.cache_hit_ats != [], (
                "a persistent-cache hit must set the data-age stamp"
            )
        finally:
            cache.close()


# ── shared sibling fixtures ──────────────────────────────────────────────────
# Two queries with byte-identical SQL against one source, at different ttls, so
# they share a cache key but not a policy. Used by both sibling classes below:
# TestSiblingQueriesKeepTheirOwnTtl drives them through a fresh Executor each
# (exercising the persistent store), TestSiblingsDoNotShareOneMemoSlot through
# one shared Executor (exercising the in-memory memo).


def _sibling_yaml(
    left_cache: str, right_cache: str, names: tuple[str, str] = ("patient", "impatient")
) -> str:
    """Two queries with byte-identical SQL against one source, at two policies.

    They share a cache key by construction — that sharing is the point — so the
    only thing varying between the sibling scenarios is each side's `cache:`
    block. Keeping one template makes that the visible difference rather than
    burying it in three near-identical blobs.
    """
    left, right = names
    return f"""\
title: Siblings
queries:
  {left}:
    sql: SELECT 1 as value
    source: test_profile
    {left_cache}
  {right}:
    sql: SELECT 1 as value
    source: test_profile
    {right_cache}
charts:
  c:
    query: {left}
    type: kpi
    value: value
rows:
  - c
"""


_TTL_24H = "cache:\n      ttl: 24h"
_TTL_1H = "cache:\n      ttl: 1h"
_SIBLING_YAML = _sibling_yaml(_TTL_24H, _TTL_1H)


def _sibling_executor(board, registry, adapter, cache) -> Executor:
    return Executor(
        board,
        adapter_registry=adapter,
        query_registry=registry,
        result_cache=cache,
    )


def _age_shared_entry(cache, board, names: tuple[str, str], hours: int) -> None:
    """Back-date the one entry *names* share, in the column's own frame.

    written_at holds a naive UTC wall clock (see `_utc_wall_clock`), so aging it
    with a naive *local* `datetime.now()` would put the entry off by the
    machine's UTC offset — east of Greenwich an "aged" entry would read as
    future-dated and still be served, and the test would fail for a reason that
    has nothing to do with what it is checking.
    """
    from dbt_charts.core.execute.duckdb_cache import compute_cache_key

    left, right = names
    key = compute_cache_key(board.queries[left], {}, board.sources)
    assert key == compute_cache_key(board.queries[right], {}, board.sources), (
        "the two queries must share a key — otherwise this test proves nothing"
    )
    aged = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)
    cache.conn.execute(
        "UPDATE _query_outcomes SET written_at_utc = ? "
        "WHERE source_hash = ? AND query_hash = ? AND variables_hash = ?",
        [aged, *key],
    )


class TestSiblingQueriesKeepTheirOwnTtl:
    """Two queries over identical SQL share a cache entry but not a policy.

    `compute_cache_key` is (source_hash, query_hash, variables_hash) and
    deliberately excludes the cache policy, so byte-identical SQL against one
    source resolves to a single entry — that sharing is the point. But
    `CachePolicy` is per-query and cascaded, so the two can resolve to
    different ttls, and each must get the freshness it authored.

    Regression for: an implementation that stamped expiry on the entry at write
    time. The 24h writer's stamp then governed the 1h reader too, which served
    day-old rows against an authored 1h contract — silently, with a
    correct-looking "data as of" stamp. Nothing else in the suite pins this.
    """

    def test_short_ttl_sibling_re_executes_what_the_long_ttl_one_cached(
        self, tmp_path
    ) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_SIBLING_YAML)
            adapter = _ok_adapter(value=1)

            # The 24h query populates the shared entry.
            _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("patient")
            assert adapter.execute.call_count == 1

            # Two hours pass: past the 1h policy, well inside the 24h one.
            _age_shared_entry(cache, result.board, ("patient", "impatient"), hours=2)

            # A distinguishable value proves a re-run rather than a stale hit.
            adapter.execute.return_value.data = [{"value": 2}]

            impatient = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("impatient")
            assert adapter.execute.call_count == 2, (
                "the 1h query must re-execute: its own ttl has elapsed, even "
                "though the 24h query wrote the entry"
            )
            assert impatient == [{"value": 2}]
        finally:
            cache.close()

    def test_long_ttl_sibling_still_serves_from_the_shared_entry(
        self, tmp_path
    ) -> None:
        """The converse: sharing must still work, or the fix has just broken caching."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_SIBLING_YAML)
            adapter = _ok_adapter(value=1)

            _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("impatient")
            assert adapter.execute.call_count == 1

            _age_shared_entry(cache, result.board, ("patient", "impatient"), hours=2)

            patient = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("patient")
            assert adapter.execute.call_count == 1, (
                "the 24h query is still inside its own ttl and must be served "
                "the entry the 1h query wrote"
            )
            assert patient == [{"value": 1}]
        finally:
            cache.close()


class TestForceRefreshClearsEveryPolicySlot:
    """force_refresh clears all of this content's memo slots, not just the caller's.

    It also clears the *shared* persistent entry, so leaving a sibling's slot
    warm would have it serving pre-refresh rows against a store that no longer
    holds them — a narrowing of what the parameter has always meant, introduced
    by policy-scoping the memo key.
    """

    def test_a_different_policy_siblings_slot_is_cleared_too(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_SIBLING_YAML)
            adapter = _ok_adapter(value=1)
            executor = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            )

            # Both siblings warm their own policy slot off one execution.
            assert executor.execute_query("patient") == [{"value": 1}]
            assert executor.execute_query("impatient") == [{"value": 1}]
            assert len(executor._cache) == 2, "two policies, two slots"

            adapter.execute.return_value.data = [{"value": 2}]
            executor.execute_query("patient", force_refresh=True)

            assert executor.execute_query("impatient") == [{"value": 2}], (
                "the sibling must not keep pre-refresh rows — force_refresh "
                "wiped the shared persistent entry they both read from"
            )
        finally:
            cache.close()


class TestNoStoreIsEnforcedAtTheStoreRead:
    """`cache: false` must be refused where the store is actually read.

    Both production callers gate on the policy before probing — `execute_query`
    via `should_use_cache`, `_lookup_cached` before its probe — so this is
    redundant today, which is exactly why it is worth pinning. A future caller
    of `_warm_from_cache` that forgets would load persisted rows into a disabled
    query's memo slot and `execute_query` would serve them, reinstating the bug
    this module exists to prevent, with every end-to-end test still green.
    """

    def test_warm_from_cache_refuses_a_disabled_policy(self, tmp_path) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            from dbt_charts.core.execute.duckdb_cache import compute_cache_key

            result = df_compile(_board_yaml("cache: false"))
            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            # A fresh entry the disabled query must not be handed.
            cache.put(*key, [{"value": 999}], board_slug="test", query_name="q")

            executor = Executor(
                result.board,
                adapter_registry=_ok_adapter(),
                query_registry=result.query_registry,
                result_cache=cache,
            )
            assert executor._warm_from_cache(key, query, "q") is False, (
                "the store read itself must refuse a disabled policy — not rely "
                "on every caller remembering to check first"
            )
            assert executor._cache == {}, "nothing may reach the memo either"
        finally:
            cache.close()


class TestSiblingsDoNotShareOneMemoSlot:
    """Siblings sharing a cache key must not share the executor's in-memory memo.

    ``_cache`` is the render's identity memo — "one query = one instant" — but
    it is warmed from the persistent store on a hit. Keyed only by content, one
    slot then answers reads for *every* query with the same SQL, whatever policy
    each authored: the first query to render decides what its siblings are
    served, and the ttl machinery never runs for them.

    The two guarantees that breaks are exactly the ones the cache surface sells:
    a short ttl served rows older than it permits, and ``cache: false`` served
    rows that came off disk. Both are silent — no error, and the bypassed read
    records no hit timestamp, so the "data as of" stamp reports the *other*
    query's age.

    Distinct from ``TestSiblingQueriesKeepTheirOwnTtl`` above, which uses a fresh
    Executor per query and so never populates a shared memo slot.
    """

    _DISABLED_SIBLING_YAML = _sibling_yaml(
        _TTL_24H, "cache: false", names=("patient", "live")
    )

    _SAME_POLICY_YAML = _sibling_yaml(_TTL_1H, _TTL_1H, names=("first", "second"))

    def test_short_ttl_sibling_is_not_served_the_long_one_s_memo_slot(
        self, tmp_path
    ) -> None:
        """A 1h query must not be handed 2h-old rows a 24h sibling warmed."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_SIBLING_YAML)
            adapter = _ok_adapter(value=1)

            # Seed the shared entry, then age it past the 1h policy but well
            # inside the 24h one.
            _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("patient")
            assert adapter.execute.call_count == 1
            _age_shared_entry(cache, result.board, ("patient", "impatient"), hours=2)

            # Both queries now run on ONE executor, the 24h one first: it takes a
            # legitimate persistent hit and warms the memo.
            executor = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            )
            assert executor.execute_query("patient") == [{"value": 1}]
            assert adapter.execute.call_count == 1, "the 24h query is inside its ttl"

            # A distinguishable value proves a re-run rather than a memo hit.
            adapter.execute.return_value.data = [{"value": 2}]

            impatient = executor.execute_query("impatient")
            assert adapter.execute.call_count == 2, (
                "the 1h query must re-execute: 2h-old rows are past its own ttl, "
                "even though a 24h sibling warmed the memo slot first"
            )
            assert impatient == [{"value": 2}], (
                "asserting call_count alone is not enough — a memo that returns "
                "stale rows AND re-executes would pass that check"
            )
        finally:
            cache.close()

    def test_disabled_sibling_is_never_served_persisted_rows(self, tmp_path) -> None:
        """`cache: false` must not read a sibling's persistent rows via the memo."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(self._DISABLED_SIBLING_YAML)
            assert result.board.queries["live"].cache.enabled is False
            adapter = _ok_adapter(value=1)

            # Seed a *fresh* entry — no aging, so only the opt-out can save us.
            _sibling_executor(
                result.board, result.query_registry, adapter, cache
            ).execute_query("patient")
            assert adapter.execute.call_count == 1

            executor = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            )
            assert executor.execute_query("patient") == [{"value": 1}]
            assert adapter.execute.call_count == 1

            adapter.execute.return_value.data = [{"value": 2}]

            live = executor.execute_query("live")
            assert adapter.execute.call_count == 2, (
                "cache: false is a real no-store: it must execute rather than be "
                "served rows a sibling read off disk"
            )
            assert live == [{"value": 2}]
        finally:
            cache.close()

    def test_same_policy_siblings_still_share_one_execution_and_one_probe(
        self, tmp_path
    ) -> None:
        """The memoization win must survive the fix, not be traded away for it."""
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(self._SAME_POLICY_YAML)
            adapter = _ok_adapter(value=1)
            executor = _sibling_executor(
                result.board, result.query_registry, adapter, cache
            )
            probes = Mock(wraps=cache.get)
            cache.get = probes  # type: ignore[method-assign]

            assert executor.execute_query("first") == [{"value": 1}]
            assert executor.execute_query("second") == [{"value": 1}]

            assert adapter.execute.call_count == 1, (
                "identical SQL at an identical policy must still execute once — "
                "policy-scoping the memo key must not split same-policy siblings"
            )
            assert probes.call_count == 1, (
                "the second sibling must be answered from the memo, not a second "
                "round trip to the persistent store"
            )
        finally:
            cache.close()


class TestQueryDataAgesOnFailure:
    """A failing query must still contribute to query_data_ages.

    Without a record, compute_snapshot_expires_at([]) returns NEVER_EXPIRES,
    pinning a broken board permanently after a warehouse outage — it is never
    scheduled for re-render because its snapshot shows as perpetually fresh.
    """

    def test_failed_adapter_query_contributes_data_age_record(self) -> None:
        result = df_compile(_board_yaml("cache: false"))
        registry = Mock()
        registry.execute.side_effect = RuntimeError("warehouse down")

        executor = Executor(
            result.board,
            adapter_registry=registry,
            query_registry=result.query_registry,
            result_cache=None,
        )

        with pytest.raises(QueryError):
            executor.execute_query("q")

        names = [name for name, _da, _p in executor.query_data_ages]
        assert "q" in names, (
            "a failed query must contribute a record — without it "
            "compute_snapshot_expires_at returns NEVER_EXPIRES and the board "
            "is never scheduled for re-render"
        )

    def test_failed_cached_query_contributes_data_age_record(self, tmp_path) -> None:
        """A cached failure (within retry window) also contributes a record."""
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache:\n      ttl: 1h"))
            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            # Seed a cached failure still inside the 1h retry window.
            cache.put(
                *key, RuntimeError("seed failure"), board_slug="test", query_name="q"
            )

            executor = Executor(
                result.board,
                adapter_registry=_ok_adapter(),
                query_registry=result.query_registry,
                result_cache=cache,
            )

            with pytest.raises(CachedQueryFailure):
                executor.execute_query("q")

            names = [name for name, _da, _p in executor.query_data_ages]
            assert "q" in names, (
                "a cached failure must also contribute a record — the board must "
                "re-render once the failure's retry window expires"
            )
        finally:
            cache.close()


class TestResultLimitsEnforcedBeforeCacheWrite:
    """A result exceeding execution.max_rows or max_result_bytes is truncated
    at the Executor chokepoint before it ever reaches the persistent cache —
    proven by reading back what the cache backend actually stored, not just
    execute_query()'s return value.
    """

    def test_max_rows_truncates_before_cache_write(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = Mock()
            ok = Mock()
            ok.is_success = True
            ok.data = [{"value": i} for i in range(10)]
            ok.column_descriptions = None
            ok.resolved_relations = None
            ok.truncated_reason = None
            adapter.execute.return_value = ok
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            data = executor.execute_query("q")
            assert len(data) == 3

            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            cached = cache.get(*key, ttl=None)
            assert cached is not None
            assert len(cached.rows) == 3, (
                "the persistent cache must never receive the untruncated result"
            )
        finally:
            cache.close()

    def test_max_result_bytes_truncates_before_cache_write_oversized_row_not_first(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The oversized row sits second, not first — pins that enforcement
        accumulates incrementally rather than sampling the first row and
        extrapolating, which would miss this case."""
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        monkeypatch.setenv("DCT_MAX_RESULT_BYTES_CEILING", "200")
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = Mock()
            ok = Mock()
            ok.is_success = True
            ok.data = [
                {"value": "small"},
                {"value": "x" * 500},
                {"value": "also small"},
            ]
            ok.column_descriptions = None
            ok.resolved_relations = None
            ok.truncated_reason = None
            adapter.execute.return_value = ok
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            data = executor.execute_query("q")
            assert data == [{"value": "small"}]

            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            cached = cache.get(*key, ttl=None)
            assert cached is not None
            assert cached.rows == [{"value": "small"}], (
                "the persistent cache must never receive the untruncated result"
            )
        finally:
            cache.close()

    def test_oversized_first_row_truncates_to_zero_rows(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pins the decision for a single row whose own serialized size alone
        exceeds max_result_bytes: byte-size truncation keeps zero rows rather
        than always keeping at least one. The ceiling exists to bound memory
        and cache size — letting one pathological row through unconditionally
        would defeat it every time that row recurs. WARN_QUERY_RESULT_TRUNCATED
        (kept_row_count=0) is the honest signal for this case, not a silent
        exception that lets an oversized row slip past the budget."""
        from dbt_charts.core.execute.duckdb_cache import compute_cache_key

        monkeypatch.setenv("DCT_MAX_RESULT_BYTES_CEILING", "200")
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = Mock()
            ok = Mock()
            ok.is_success = True
            ok.data = [{"value": "x" * 500}, {"value": "small"}]
            ok.column_descriptions = None
            ok.resolved_relations = None
            ok.truncated_reason = None
            adapter.execute.return_value = ok
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            data = executor.execute_query("q")
            assert data == []
            assert executor.truncations()["q"].reason == "max_result_bytes"
            assert executor.truncations()["q"].kept_row_count == 0

            query = result.board.queries["q"]
            key = compute_cache_key(query, {}, result.board.sources)
            cached = cache.get(*key, ttl=None)
            assert cached is not None
            assert cached.rows == []
        finally:
            cache.close()


class TestTruncatedReasonValidation:
    """QueryResult.truncated_reason reaches _enforce_result_limits unvalidated
    and gets stored straight into TruncationInfo.reason, which the
    render-warnings pass formats into a user-visible message. A Mock adapter
    double that doesn't set truncated_reason explicitly yields a truthy Mock
    object there (Mock() auto-creates attributes), which would otherwise sail
    through as a bogus reason string. Validating at the point of consumption
    turns that into a loud, clear failure instead.
    """

    def test_unset_mock_attribute_raises_instead_of_leaking(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
        try:
            result = df_compile(_board_yaml("cache: true"))
            adapter = Mock()
            ok = Mock()
            ok.is_success = True
            ok.data = [{"value": 1}]
            ok.column_descriptions = None
            ok.resolved_relations = None
            # Deliberately NOT setting ok.truncated_reason — Mock() auto-vivifies
            # it as a truthy Mock object, which must be rejected loudly.
            adapter.execute.return_value = ok
            executor = Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            )

            with pytest.raises(ValueError, match="truncated_reason"):
                executor.execute_query("q")
        finally:
            cache.close()
