"""Lookup stage: plain-data partition of probes into hits, misses and failures."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.cache_backend import (
    CachedQueryFailure,
    CacheHit,
    CacheOutcome,
    QueryResultCache,
    ResultRows,
    TruncatedReason,
)
from dbt_charts.core.execute.lookup import Probe, lookup, memo_key

from ..._results import rows_result

KEY = ("s", "q", "v")
WRITTEN = datetime(2026, 1, 1, tzinfo=UTC)
POLICY = CachePolicy(enabled=True, ttl="1h")


def FakeCache(outcome: CacheOutcome | None) -> Mock:
    cache = Mock(spec=QueryResultCache)
    cache.get.return_value = outcome
    return cache


def _query(incremental: str | None = None, cache: CachePolicy = POLICY) -> SqlQuery:
    return SqlQuery(sql="SELECT 1", source="db", cache=cache, incremental=incremental)


def _probe(query: SqlQuery, use_store: bool = True, tailable: bool = True) -> Probe:
    return Probe("q", query, KEY, use_store, lambda: tailable)


def _hit(rows: list[dict[str, Any]]) -> CacheHit:
    result = rows_result(rows)
    return CacheHit(result.rows, result.schema, WRITTEN)


def _run(
    probe: Probe,
    cache: Mock | None = None,
    memo: dict[tuple[str, str, str, str], ResultRows] | None = None,
    errors: dict[str, Exception] | None = None,
    force_refresh: bool = False,
    resume_incremental: bool = True,
):
    def enforce(r: ResultRows, name: str, seed: TruncatedReason | None) -> ResultRows:
        return r

    return lookup(
        [probe],
        memo=memo or {},
        errors=errors or {},
        result_cache=cache,
        enforce_limits=enforce,
        force_refresh=force_refresh,
        resume_incremental=resume_incremental,
    )


def test_store_hit_carries_written_at_and_reader_ttl() -> None:
    cache = FakeCache(_hit([{"a": 1}]))
    found = _run(_probe(_query()), cache)
    assert found.hits["q"].result.rows == [{"a": 1}]
    assert found.hits["q"].written_at == WRITTEN
    assert not found.misses and not found.failures
    cache.get.assert_called_once_with(*KEY, ttl=timedelta(hours=1))


def test_empty_store_is_a_miss() -> None:
    found = _run(_probe(_query()), FakeCache(None))
    assert [m.name for m in found.misses] == ["q"]
    assert found.misses[0].cache_key == KEY and found.misses[0].tail is None


def test_memo_hit_reads_no_store() -> None:
    query = _query()
    cache = FakeCache(None)
    memo = {memo_key(query, KEY): rows_result([{"a": 2}])}
    found = _run(_probe(query), cache, memo)
    assert found.hits["q"].written_at is None
    assert not cache.get.called


def test_no_store_reads_only_the_memo() -> None:
    cache = FakeCache(_hit([{"a": 1}]))
    found = _run(_probe(_query(), use_store=False), cache)
    assert [m.name for m in found.misses] == ["q"] and not cache.get.called
    disabled = _query(cache=CachePolicy(enabled=False))
    assert _run(_probe(disabled), cache).misses and not cache.get.called


def test_incremental_hit_is_a_miss_carrying_its_tail() -> None:
    cache = FakeCache(_hit([{"d": 1}, {"d": 3}, {"d": 2}]))
    found = _run(_probe(_query(incremental="d")), cache)
    tail = found.misses[0].tail
    assert tail is not None and tail.watermark == 3
    assert tail.prior_rows == [{"d": 1}, {"d": 3}, {"d": 2}]
    assert tail.written_at == WRITTEN and not found.hits


def test_incremental_hit_is_a_plain_hit_when_not_resuming() -> None:
    cache = FakeCache(_hit([{"d": 1}]))
    found = _run(_probe(_query(incremental="d")), cache, resume_incremental=False)
    assert "q" in found.hits and not found.misses


def test_incremental_hit_on_file_source_is_a_plain_hit() -> None:
    cache = FakeCache(_hit([{"d": 1}]))
    found = _run(_probe(_query(incremental="d"), tailable=False), cache)
    assert "q" in found.hits and not found.misses


def test_incremental_watermark_column_missing_is_a_failure() -> None:
    cache = FakeCache(_hit([{"x": 1}]))
    found = _run(_probe(_query(incremental="d")), cache)
    assert isinstance(found.failures["q"].error, QueryError)
    assert not found.hits and not found.misses


def test_cached_failure_replays_with_its_code_and_time() -> None:
    failure = CachedQueryFailure("QueryError", "boom", None, {"k": 1}, "", WRITTEN)
    found = _run(_probe(_query()), FakeCache(failure))
    replay = found.failures["q"]
    assert replay.failed_at == WRITTEN
    assert isinstance(replay.error, QueryError)
    assert replay.error.fields == {"k": 1, "query_name": "q"}
    assert replay.error.__cause__ is failure


def test_error_memo_replays_before_the_store() -> None:
    cache = FakeCache(_hit([{"a": 1}]))
    stored = QueryError("bad", "q")
    found = _run(_probe(_query()), cache, errors={"q": stored})
    assert found.failures["q"].error is stored
    assert found.failures["q"].failed_at is None and not cache.get.called


def test_force_refresh_misses_despite_memo_and_store() -> None:
    query = _query()
    cache = FakeCache(_hit([{"a": 1}]))
    memo = {memo_key(query, KEY): rows_result([{"a": 2}])}
    found = _run(_probe(query), cache, memo, {"q": QueryError("x")}, True)
    assert [m.name for m in found.misses] == ["q"]
    assert not found.hits and not found.failures and not cache.get.called


def test_unregistered_failure_code_reruns() -> None:
    failure = CachedQueryFailure("QueryError", "m", "ERR-GONE-XYZ", {}, "", WRITTEN)
    found = _run(_probe(_query()), FakeCache(failure))
    assert [m.name for m in found.misses] == ["q"] and not found.failures
