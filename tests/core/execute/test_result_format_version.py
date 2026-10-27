"""Bumping RESULT_FORMAT_VERSION turns every cached result into a miss.

Both result caches (the local DuckDB file and Cloud's table) fold it into the
query hash they store. It stays out of `compute_cache_key`: recordings are keyed
by that triple and must not go stale on a bump.
"""

from pathlib import Path

import pytest

from dbt_charts.core.compile import compile as df_compile
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute import Executor, cache_keys
from dbt_charts.core.execute.cache_keys import stored_query_hash
from dbt_charts.core.execute.duckdb_cache import (
    compute_cache_key,
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

from .test_cache_policy_enforcement import _board_yaml, _ok_adapter


def test_the_version_never_reaches_the_recording_key() -> None:
    query = SqlQuery(sql="SELECT 1", source="dw")
    assert compute_cache_key(query) == (
        compute_source_hash("dw", board_sources=None),
        compute_query_hash("SELECT 1"),
        compute_variables_hash({}),
    )


def test_a_new_format_version_changes_the_stored_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = stored_query_hash("abc")
    monkeypatch.setattr(cache_keys, "RESULT_FORMAT_VERSION", 2)
    assert stored_query_hash("abc") != before
    assert len(before) == 16


def test_a_local_cache_entry_written_under_one_version_misses_under_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
    try:
        result = df_compile(_board_yaml(""))
        adapter = _ok_adapter()

        def run() -> None:
            Executor(
                result.board,
                adapter_registry=adapter,
                query_registry=result.query_registry,
                result_cache=cache,
            ).execute_query("q")

        run()
        run()
        assert adapter.execute.call_count == 1  # second run is a hit
        monkeypatch.setattr(
            cache_keys, "RESULT_FORMAT_VERSION", cache_keys.RESULT_FORMAT_VERSION + 1
        )
        run()
        assert adapter.execute.call_count == 2
    finally:
        cache.close()
