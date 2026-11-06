"""Browsing a TrivialDuckDBCache: ``entries()``."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from pathlib import Path

import pytest
import yaml

from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.cache_backend import (
    BrowsableResultCache,
    CacheUser,
)
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from ..._results import rows_result
from ..._run import Run
from ..._scripted_env import ScriptedBackend, scripted_env

_SH = "s" * 16
_VH = "v" * 16


@pytest.fixture
def cache() -> Iterator[TrivialDuckDBCache]:
    cache = TrivialDuckDBCache()
    try:
        yield cache
    finally:
        cache.close()


def test_the_local_cache_is_browsable(cache: TrivialDuckDBCache) -> None:
    assert isinstance(cache, BrowsableResultCache)


def test_entries_list_every_rows_slot_newest_first(
    cache: TrivialDuckDBCache,
) -> None:
    cache.put(
        _SH, "a" * 16, _VH, rows_result([{"m": 1}]), board_slug="rev", query_name="q"
    )
    # The same board query under other variables: a second key, listed beside the first.
    cache.put(
        _SH,
        "b" * 16,
        _VH,
        rows_result(
            [{"month": "2026-01", "arr": 2.5}, {"month": "2026-02", "arr": 3.0}]
        ),
        board_slug="rev",
        query_name="q",
        variables={"region": "EU"},
    )
    cache.put(
        _SH, "c" * 16, _VH, rows_result([{"n": 1}]), board_slug="ops", query_name="tix"
    )
    cache.put(
        _SH, "d" * 16, _VH, RuntimeError("boom"), board_slug="broken", query_name="q"
    )

    entries = cache.entries()

    assert [e.used_by for e in entries] == [
        [CacheUser("ops", "tix")],
        [CacheUser("rev", "q")],
        [CacheUser("rev", "q")],
    ]
    assert [e.variables for e in entries] == [{}, {"region": "EU"}, {}]
    eu = entries[1]
    assert eu.columns == ["month", "arr"]
    assert eu.row_count == 2
    assert eu.written_at.tzinfo is UTC
    assert eu.truncated_reason is None


def test_entries_name_every_board_that_wrote_or_read_the_key(
    cache: TrivialDuckDBCache,
) -> None:
    rows = rows_result([{"m": 1}])
    cache.put(
        _SH,
        "a" * 16,
        _VH,
        rows,
        board_slug="rev",
        query_name="q",
        variables={"region": "EU"},
    )
    cache.put(
        _SH,
        "a" * 16,
        _VH,
        rows,
        board_slug="exec",
        query_name="arr",
        variables={"region": "EU"},
    )
    cache.note_use(_SH, "a" * 16, _VH, "ops", "q")
    cache.note_use(_SH, "a" * 16, _VH, "rev", "q")

    [entry] = cache.entries()

    assert entry.used_by == [
        CacheUser("rev", "q"),
        CacheUser("exec", "arr"),
        CacheUser("ops", "q"),
    ]
    assert entry.variables == {"region": "EU"}


def test_a_key_only_write_records_no_variables(cache: TrivialDuckDBCache) -> None:
    cache.put(
        _SH, "a" * 16, _VH, rows_result([{"m": 1}]), board_slug="rev", query_name="q"
    )

    [entry] = cache.entries()

    assert entry.used_by == [CacheUser("rev", "q")]
    assert entry.variables == {}


def test_a_repeated_use_does_not_grow_the_cache_file(tmp_path: Path) -> None:
    """A warm hit names its reader once; naming it again writes nothing, so page
    views of a cached board leave the file's size alone."""
    path = tmp_path / "cache.duckdb"
    cache = TrivialDuckDBCache(path)
    try:
        rows = rows_result([{"m": i, "s": "x" * 200} for i in range(2000)])
        cache.put(_SH, "a" * 16, _VH, rows, board_slug="rev", query_name="q")
        cache.note_use(_SH, "a" * 16, _VH, "ops", "q")
        with cache._connection() as conn:
            conn.execute("CHECKPOINT")
        before = path.stat().st_size
        for _ in range(50):
            cache.note_use(_SH, "a" * 16, _VH, "ops", "q")
        with cache._connection() as conn:
            conn.execute("CHECKPOINT")
        assert path.stat().st_size == before
        [entry] = cache.entries()
        assert entry.used_by == [CacheUser("rev", "q"), CacheUser("ops", "q")]
    finally:
        cache.close()


def test_a_file_of_another_layout_is_rebuilt_even_when_only_a_type_changed(
    tmp_path: Path,
) -> None:
    """The drift check reads column types, not names alone: an earlier build's
    `used_by TEXT[]` has this build's column name and would otherwise be kept and
    then fail every write."""
    import duckdb

    from dbt_charts.core.execute import _duckdb_cache_base as base

    path = tmp_path / "cache.duckdb"
    old_columns = {**base._OUTCOME_COLUMNS, "used_by": "TEXT[] NOT NULL"}
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE _query_outcomes ("
        + ", ".join(f"{n} {t}" for n, t in old_columns.items())
        + ", PRIMARY KEY (source_hash, query_hash, variables_hash))"
    )
    conn.execute(
        "INSERT INTO _query_outcomes (source_hash, query_hash, variables_hash, "
        "board_slug, query_name, used_by, variables) VALUES (?, ?, ?, 'rev', 'q', "
        "['rev'], '{}')",
        [_SH, "a" * 16, _VH],
    )
    conn.close()

    cache = TrivialDuckDBCache(path)
    try:
        assert cache.entries() == []
        cache.put(
            _SH,
            "a" * 16,
            _VH,
            rows_result([{"m": 1}]),
            board_slug="rev",
            query_name="q",
        )
        cache.note_use(_SH, "a" * 16, _VH, "ops", "q")
        [entry] = cache.entries()
        assert entry.used_by == [CacheUser("rev", "q"), CacheUser("ops", "q")]
    finally:
        cache.close()


_SOURCE = DuckDBSourceConfig(type="duckdb", path=":memory:")


def _one_result_backend() -> ScriptedBackend:
    """Runs a query once; a second warehouse call is a test failure."""

    def answer(_source: object, _query: object) -> QueryResult:
        assert backend.execute.call_count == 1, (
            "the second board should have read the cache"
        )
        return QueryResult(data=[{"v": 1}], schema=literal_schema([{"v": 1}]))

    backend = ScriptedBackend(side_effect=answer)
    return backend


def _board_executor(
    name: str,
    query: str,
    backend: ScriptedBackend,
    cache: TrivialDuckDBCache,
) -> Run:
    document = {
        "queries": {query: {"sql": "SELECT 1 AS v", "source": "wh"}},
        "charts": {"c": {"query": query, "type": "table"}},
    }
    board = compile_board(yaml.dump(document)).board
    assert board is not None
    return Run(
        board,
        env=scripted_env(backend, source=_SOURCE),
        query_registry={query: board.queries[query]},
        result_cache=cache,
        board_slug=name,
    )


def test_a_board_served_from_the_cache_is_named_on_the_entry(
    cache: TrivialDuckDBCache,
) -> None:
    """Two boards share a query: the second reads the first's rows, and the
    entry lists both, each under its own query name."""
    backend = _one_result_backend()
    _board_executor("rev", "q", backend, cache).execute_query("q")
    _board_executor("exec", "arr", backend, cache).execute_query("arr")

    [entry] = cache.entries()

    assert backend.execute.call_count == 1
    assert entry.used_by == [CacheUser("rev", "q"), CacheUser("exec", "arr")]
