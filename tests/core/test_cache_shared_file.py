"""A file-backed TrivialDuckDBCache shared by several processes.

DuckDB allows one read-write process per file, so the cache holds its connection
only for the length of one read or write. These tests use real child processes:
connections inside one process share a DuckDB instance and never conflict.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import CacheHit
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_KEY = ("s" * 16, "q" * 16, "v" * 16)

_CHILD_PUTS_AND_READS = """
import sys
from pathlib import Path
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

cache = TrivialDuckDBCache(db_path=Path(sys.argv[1]))
hit = cache.get("s" * 16, "q" * 16, "v" * 16)
print(hit.rows, flush=True)
cache.put("s" * 16, "child" * 4, "v" * 16, [{"n": 2}], board_slug="b", query_name="q")
cache.close()
"""

_HOLD_LOCK = """
import duckdb, sys, time
conn = duckdb.connect(sys.argv[1])
print("READY", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _put(cache: TrivialDuckDBCache, rows: list[dict[str, int]]) -> None:
    cache.put(*_KEY, rows, board_slug="b", query_name="q")


@pytest.fixture
def lock_holder(tmp_path: Path) -> Iterator[Path]:
    """A cache file another process holds open read-write for 30 seconds."""
    db_path = tmp_path / "held.duckdb"
    TrivialDuckDBCache(db_path=db_path).close()
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(db_path), "30"],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "READY"
        yield db_path
    finally:
        holder.kill()
        holder.wait()


class TestSharedAcrossProcesses:
    def test_a_second_process_opens_the_same_file_and_shares_rows(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "shared.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            _put(cache, [{"n": 1}])
            # The parent cache is still open while the child reads and writes.
            child = subprocess.run(
                [sys.executable, "-c", _CHILD_PUTS_AND_READS, str(db_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            assert child.returncode == 0, child.stderr
            assert child.stdout.strip() == "[{'n': 1}]"
            from_child = cache.get("s" * 16, "child" * 4, "v" * 16)
            assert isinstance(from_child, CacheHit)
            assert from_child.rows == [{"n": 2}]
        finally:
            cache.close()

    def test_waits_for_another_process_to_release_the_file(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "briefly-held.duckdb"
        TrivialDuckDBCache(db_path=db_path).close()
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLD_LOCK, str(db_path), "0.5"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "READY"
            cache = TrivialDuckDBCache(db_path=db_path)
            _put(cache, [{"n": 1}])
            hit = cache.get(*_KEY)
            assert isinstance(hit, CacheHit)
            cache.close()
        finally:
            holder.wait()

    def test_a_file_that_never_frees_raises_naming_the_path(
        self, lock_holder: Path
    ) -> None:
        with pytest.raises(ExecutionError, match="held.duckdb") as excinfo:
            TrivialDuckDBCache(db_path=lock_holder, lock_timeout_seconds=0.2)
        assert excinfo.value.code.code == "ERR-CACHE-FILE-UNAVAILABLE"
        assert isinstance(excinfo.value.__cause__, duckdb.IOException)


class TestSupersededLayoutIsRecreated:
    def test_a_table_per_key_file_is_dropped_and_usable(self, tmp_path: Path) -> None:
        """A file from the one-table-per-key layout is disposable: recreate it."""
        db_path = tmp_path / "old.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE _query_outcomes (
                source_hash TEXT NOT NULL, query_hash TEXT NOT NULL,
                variables_hash TEXT NOT NULL, board_slug TEXT NOT NULL,
                query_name TEXT NOT NULL, row_count BIGINT,
                written_at_utc TIMESTAMP, truncated_reason TEXT,
                decimal_columns TEXT, error_class TEXT, error_message TEXT,
                traceback TEXT, error_written_at_utc TIMESTAMP,
                PRIMARY KEY (source_hash, query_hash, variables_hash)
            )
            """
        )
        conn.execute(
            "INSERT INTO _query_outcomes VALUES "
            "(?, ?, ?, 'b', 'q', 1, now()::TIMESTAMP, NULL, NULL, "
            "NULL, NULL, NULL, NULL)",
            list(_KEY),
        )
        conn.execute('CREATE TABLE "_r_0123456789ab" (n INTEGER)')
        conn.execute('INSERT INTO "_r_0123456789ab" VALUES (99)')
        conn.close()

        cache = TrivialDuckDBCache(db_path=db_path)
        try:
            assert cache.get(*_KEY) is None
            _put(cache, [{"n": 1}])
            hit = cache.get(*_KEY)
            assert isinstance(hit, CacheHit)
            assert hit.rows == [{"n": 1}]
        finally:
            cache.close()
        tables = duckdb.connect(str(db_path)).execute(
            "SELECT table_name FROM information_schema.tables"
        )
        assert [name for (name,) in tables.fetchall()] == ["_query_outcomes"]


class TestMaxAgePrune:
    @staticmethod
    def _age_entries(db_path: Path, days: int, query_hash: str = _KEY[1]) -> None:
        conn = duckdb.connect(str(db_path))
        conn.execute(
            f"UPDATE _query_outcomes "
            f"SET written_at_utc = written_at_utc - INTERVAL {days} DAY "
            "WHERE query_hash = ?",
            [query_hash],
        )
        conn.close()

    def test_entries_past_max_age_are_deleted_on_open(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        _put(cache, [{"n": 1}])
        cache.put("s" * 16, "fresh" * 4, "v" * 16, [], board_slug="b", query_name="q")
        cache.close()
        self._age_entries(db_path, 8)

        reopened = TrivialDuckDBCache(db_path=db_path, max_age=timedelta(days=7))
        try:
            assert reopened.get(*_KEY) is None
            assert isinstance(reopened.get("s" * 16, "fresh" * 4, "v" * 16), CacheHit)
            # The rebuilt table still upserts on its key.
            _put(reopened, [{"n": 2}])
            _put(reopened, [{"n": 3}])
            hit = reopened.get(*_KEY)
            assert isinstance(hit, CacheHit)
            assert hit.rows == [{"n": 3}]
        finally:
            reopened.close()

    def test_entries_inside_max_age_survive(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        _put(cache, [{"n": 1}])
        cache.close()
        self._age_entries(db_path, 6)

        reopened = TrivialDuckDBCache(db_path=db_path, max_age=timedelta(days=7))
        try:
            assert isinstance(reopened.get(*_KEY), CacheHit)
        finally:
            reopened.close()

    def test_no_max_age_keeps_old_entries(self, tmp_path: Path) -> None:
        db_path = tmp_path / "cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        _put(cache, [{"n": 1}])
        cache.close()
        self._age_entries(db_path, 400)

        reopened = TrivialDuckDBCache(db_path=db_path)
        try:
            assert isinstance(reopened.get(*_KEY), CacheHit)
        finally:
            reopened.close()

    def test_an_interrupted_rebuild_loses_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A process that dies between dropping the table and refilling it must
        not leave the cache empty: the rebuild is one transaction."""
        db_path = tmp_path / "cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        _put(cache, [{"n": 1}])
        cache.put("s" * 16, "fresh" * 4, "v" * 16, [], board_slug="b", query_name="q")
        cache.close()
        self._age_entries(db_path, 8)

        create = TrivialDuckDBCache._create_outcomes_table
        calls: list[duckdb.DuckDBPyConnection] = []

        def _die_during_the_rebuild(conn: duckdb.DuckDBPyConnection) -> None:
            calls.append(conn)
            # The first call is the open's own schema check; the second is the
            # rebuild recreating the table it just dropped.
            if len(calls) == 2:
                raise RuntimeError("killed mid-rebuild")
            create(conn)

        monkeypatch.setattr(
            TrivialDuckDBCache,
            "_create_outcomes_table",
            staticmethod(_die_during_the_rebuild),
        )
        with pytest.raises(RuntimeError, match="killed mid-rebuild"):
            TrivialDuckDBCache(db_path=db_path, max_age=timedelta(days=7))
        monkeypatch.undo()

        reopened = TrivialDuckDBCache(db_path=db_path)
        try:
            assert isinstance(reopened.get("s" * 16, "fresh" * 4, "v" * 16), CacheHit)
            assert isinstance(reopened.get(*_KEY), CacheHit)
        finally:
            reopened.close()

    def test_pruned_entries_give_their_disk_space_back(self, tmp_path: Path) -> None:
        """Expired data must not keep the file growing: its space is reused."""
        db_path = tmp_path / "cache.duckdb"
        # Unique strings, so a week's worth of entries is megabytes on disk.
        rows = [{"token": uuid.uuid4().hex} for _ in range(20_000)]

        def write_a_week(generation: int) -> int:
            cache = TrivialDuckDBCache(db_path=db_path, max_age=timedelta(days=7))
            for n in range(10):
                query_hash = f"g{generation}q{n}"
                cache.put("s", query_hash, "v", rows, board_slug="b", query_name="q")
            cache.close()
            for n in range(10):
                self._age_entries(db_path, 8, f"g{generation}q{n}")
            return db_path.stat().st_size

        first = write_a_week(0)
        for generation in range(1, 4):
            last = write_a_week(generation)
        assert last < first * 1.5


class TestUnopenableFile:
    def test_a_file_that_is_not_a_database_says_so_without_blaming_a_lock(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "not-a-database.duckdb"
        db_path.write_text("this is not a DuckDB file, and it is long enough " * 200)
        with pytest.raises(ExecutionError, match="not-a-database.duckdb") as excinfo:
            TrivialDuckDBCache(db_path=db_path, lock_timeout_seconds=0.1)
        message = str(excinfo.value)
        assert "If another process is holding the file" in message
        assert "readable and writable" in message
        assert str(excinfo.value.__cause__).splitlines()[0] in message


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
class TestOnlyTheOwnerCanReadTheCache:
    def test_a_new_cache_file_and_its_directory_are_private(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "com.getdbt" / "charts" / "query-cache.duckdb"
        cache = TrivialDuckDBCache(db_path=db_path)
        _put(cache, [{"n": 1}])
        cache.close()
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700

    def test_an_existing_directory_keeps_its_mode(self, tmp_path: Path) -> None:
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        cache = TrivialDuckDBCache(db_path=shared / "cache.duckdb")
        cache.close()
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
