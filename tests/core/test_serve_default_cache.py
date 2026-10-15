"""Tests for dct serve's query-result cache defaults.

Covers:
- TrivialDuckDBCache.get(ttl=...) — fresh hit, expired miss, no-ttl always hits
- create_server(no_cache=True) — skips the cache entirely
- create_server() default (no cache_path) — in-memory cache, no file on disk
- create_server(cache_path=...) — persistent DuckDB file, created if absent
- Two create_server() calls with no cache_path never conflict (each gets its own
  in-memory cache) — regression test for the cross-process lock conflict that
  motivated defaulting the cache to in-memory
"""

import time
from datetime import timedelta
from pathlib import Path

import pytest

from dbt_charts.core.execute.duckdb_cache import (
    compute_query_hash,
    compute_source_hash,
    compute_variables_hash,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_SH = compute_source_hash("test_source")
_QH = compute_query_hash("SELECT 1")
_VH = compute_variables_hash({})


def _put(cache: TrivialDuckDBCache, rows: list[dict] | None = None) -> None:
    cache.put(
        _SH,
        _QH,
        _VH,
        rows if rows is not None else [{"v": 1}],
        board_slug="f",
        query_name="q",
    )


class TestResultTTL:
    """TrivialDuckDBCache.get(ttl=...) — per-call ttl, no instance-level config."""

    def test_no_ttl_always_returns_rows(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache)
            assert cache.get(_SH, _QH, _VH) is not None
        finally:
            cache.close()

    def test_fresh_entry_within_ttl_returned(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache)
            assert cache.get(_SH, _QH, _VH, ttl=timedelta(seconds=3600)) is not None
        finally:
            cache.close()

    def test_expired_entry_returns_none(self) -> None:
        cache = TrivialDuckDBCache()
        try:
            _put(cache)
            assert cache.get(_SH, _QH, _VH, ttl=timedelta(seconds=1)) is not None
            time.sleep(1.1)
            assert cache.get(_SH, _QH, _VH, ttl=timedelta(seconds=1)) is None
        finally:
            cache.close()

    def test_stale_put_then_fresh_put_returns_only_fresh_rows(self) -> None:
        """put(v=1) then expire then put(v=2) — get() must return only v=2, not both."""
        cache = TrivialDuckDBCache()
        try:
            _put(cache, [{"v": 1}])
            time.sleep(1.1)
            _put(cache, [{"v": 2}])
            hit = cache.get(_SH, _QH, _VH, ttl=timedelta(seconds=1))
            assert hit is not None
            assert len(hit.rows) == 1
            assert hit.rows[0]["v"] == 2
        finally:
            cache.close()


class TestCreateServerCacheBehavior:
    """create_server cache lifecycle: no_cache, default-in-memory, and --cache path."""

    def test_no_cache_flag_skips_cache_entirely(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(tmp_path), no_cache=True)
        with TestClient(app):
            assert app.state.result_cache is None

    def test_default_opens_the_per_user_cache_file(self, tmp_path: Path) -> None:
        """No cache_path given: the per-user file, never one in the project."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.compile.config import (
            DEFAULT_CACHE_MAX_AGE,
            default_cache_path,
        )
        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app):
            cache = app.state.result_cache
            assert isinstance(cache, TrivialDuckDBCache)
            assert cache.db_path == default_cache_path().resolve()
            assert cache.max_age == DEFAULT_CACHE_MAX_AGE
        assert not any(tmp_path.rglob("*.duckdb"))

    def test_cache_path_opens_persistent_file(self, tmp_path: Path) -> None:
        """cache_path=<file>: cache is a persistent DuckDB file, created if absent."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        explicit_db = tmp_path / "explicit.duckdb"
        assert not explicit_db.exists()

        app = create_server(FilesystemProject(tmp_path), cache_path=explicit_db)
        with TestClient(app):
            cache = app.state.result_cache
            assert cache is not None
            assert isinstance(cache, TrivialDuckDBCache)
            assert cache.db_path == explicit_db
            assert cache.max_age is None
        assert explicit_db.exists()


class TestCreateServerConfigDrivenCache:
    """create_server reads dbt_charts.yml's cache: block when no flag is given."""

    def test_project_config_disabled_still_opens_the_store(
        self, tmp_path: Path
    ) -> None:
        """`cache: false` defaults the cascade to off, so nothing is written —
        but a query below it can opt back in, so serve still opens a store."""
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        (tmp_path / "dbt_charts.yml").write_text("cache: false\n")
        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app):
            cache = app.state.result_cache
            assert cache is not None

    def test_project_config_path_opens_persistent_file(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        (tmp_path / "dbt_charts.yml").write_text("cache:\n  path: .dct/cache.duckdb\n")
        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app):
            cache = app.state.result_cache
            assert cache is not None
            assert cache.db_path == tmp_path / ".dct" / "cache.duckdb"
        assert (tmp_path / ".dct" / "cache.duckdb").exists()

    def test_no_cache_flag_overrides_config_path(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        (tmp_path / "dbt_charts.yml").write_text("cache:\n  path: .dct/cache.duckdb\n")
        app = create_server(FilesystemProject(tmp_path), no_cache=True)
        with TestClient(app):
            assert app.state.result_cache is None


class TestCreateServerWatcherExcludesCacheFile:
    """create_server must wire the resolved cache path into FileWatcher, or
    the live-reload watcher self-triggers on the cache's own writes."""

    def test_config_driven_relative_cache_path_is_excluded_from_the_watch(
        self, tmp_path: Path
    ) -> None:
        """A cache directory name other than .dct/target: .dct is already
        excluded by ignore_dirs regardless of cache_path, so that shape alone
        would not pin the cache_path wiring this test exists to cover."""
        from watchfiles import Change  # noqa: PLC0415

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        (tmp_path / "dbt_charts.yml").write_text(
            "cache:\n  path: mycache/cache.duckdb\n"
        )
        app = create_server(FilesystemProject(tmp_path))

        cache_path = tmp_path / "mycache" / "cache.duckdb"
        watch_filter = app.state.watcher._filter  # noqa: SLF001
        assert watch_filter(Change.modified, str(cache_path)) is False
        assert watch_filter(Change.modified, str(tmp_path / "charts" / "x.yml")) is True

    def test_explicit_relative_cache_path_flag_is_excluded_from_the_watch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI help's own example (--cache cache.duckdb) is a bare relative
        path — it must resolve before reaching the watcher's exclusion."""
        from watchfiles import Change  # noqa: PLC0415

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        monkeypatch.chdir(tmp_path)
        app = create_server(
            FilesystemProject(tmp_path), cache_path=Path("cache.duckdb")
        )

        watch_filter = app.state.watcher._filter  # noqa: SLF001
        assert watch_filter(Change.modified, str(tmp_path / "cache.duckdb")) is False
        assert watch_filter(Change.modified, str(tmp_path / "charts" / "x.yml")) is True


class TestServerLifespansShareTheDefaultCache:
    def test_two_servers_on_the_default_cache_coexist_and_share_rows(
        self, tmp_path: Path
    ) -> None:
        """Two `dct serve` lifespans with no cache_path open the same file.

        Neither holds it between operations, so both stay usable, and a result
        one wrote is a hit in the other.
        """
        from fastapi.testclient import TestClient

        from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
        from dbt_charts.core.serve.server import create_server

        app1 = create_server(FilesystemProject(tmp_path))
        app2 = create_server(FilesystemProject(tmp_path))

        with TestClient(app1), TestClient(app2):
            cache1 = app1.state.result_cache
            cache2 = app2.state.result_cache
            assert cache1 is not cache2
            assert cache1.db_path == cache2.db_path
            _put(cache1)
            assert cache2.get(_SH, _QH, _VH) is not None
