"""Tests for SqliteAdapter — the extracted SQLite execution path.

Covers:
- SqliteAdapter._execute: end-to-end query execution, path resolution, filter injection
- SqlAdapter no longer handles sqlite dialect (structural)
- Registry routing: sqlite source config dispatches to SqliteAdapter
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sqlite_db(tmp_path: Path) -> Path:
    """Create a temp SQLite file with known schema + data."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER, label TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'alpha')")
    conn.execute("INSERT INTO items VALUES (2, 'beta')")
    conn.commit()
    conn.close()
    return db_path


def _make_sqlite_db_with_users(tmp_path: Path) -> Path:
    """Create a temp SQLite file with a users table."""
    db_path = tmp_path / "users.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT, age INTEGER)")
    conn.execute("INSERT INTO users VALUES (1, 'alice', 30), (2, 'bob', 25)")
    conn.execute("CREATE TABLE orders (order_id INTEGER, user_id INTEGER, amount REAL)")
    conn.execute("INSERT INTO orders VALUES (100, 1, 99.99), (101, 2, 49.50)")
    conn.commit()
    conn.close()
    return db_path


def _make_sqlite_db_with_dates(tmp_path: Path) -> Path:
    """Create a temp SQLite file with a date column for temporal-param tests."""
    db_path = tmp_path / "events.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE events (id INTEGER, event_date TEXT, event_ts TEXT)")
    conn.execute(
        "INSERT INTO events VALUES "
        "(1, '2024-01-01', '2024-01-01T00:00:00'), "
        "(2, '2024-06-15', '2024-06-15T12:30:00'), "
        "(3, '2025-03-20', '2025-03-20T08:00:00')"
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# SqliteAdapter: end-to-end execution
# ---------------------------------------------------------------------------


class TestSqliteAdapterExecutes:
    """SqliteAdapter runs sqlite queries end-to-end."""

    def test_sqlite_adapter_direct_execute(self, tmp_path: Path) -> None:
        """SqliteAdapter._execute returns data for a valid sqlite source_config."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db(tmp_path)
        adapter = SqliteAdapter()

        query = SqlQuery(sql="SELECT id FROM items WHERE id = 1", source="db")
        source_config = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_config)

        assert result.error is None
        assert result.data == [{"id": 1}]

    def test_sqlite_adapter_relative_path_resolves_via_data_dir(
        self, tmp_path: Path
    ) -> None:
        """Relative sqlite path is resolved via data_dir, not raw cwd."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        _make_sqlite_db(tmp_path)
        adapter = SqliteAdapter(data_dir=tmp_path)

        query = SqlQuery(sql="SELECT COUNT(*) AS n FROM items", source="db")
        source_config = SQLiteSourceConfig(type="sqlite", path="test.db")
        result = adapter._execute(query, source_config=source_config)

        assert result.error is None
        assert result.data == [{"n": 2}]

    def test_select_from_sqlite_file_returns_rows(self, tmp_path: Path) -> None:
        """SELECT against a .sqlite file returns expected rows."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT id, name FROM users ORDER BY id", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_cfg)
        assert result.error is None, f"Unexpected error: {result.error}"
        assert len(result.data) == 2
        assert result.data[0] == {"id": 1, "name": "alice"}
        assert result.data[1] == {"id": 2, "name": "bob"}

    def test_bad_sqlite_query_surfaces_error(self, tmp_path: Path) -> None:
        """A syntactically invalid SQL query returns a non-None error, not a crash."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT * FROM nonexistent_table_xyz", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_cfg)
        assert result.error is not None
        error = str(result.error)
        assert "nonexistent_table_xyz" in error or "no such table" in error

    def test_sqlite_nonexistent_path_surfaces_error(self, tmp_path: Path) -> None:
        """A path pointing to a nonexistent file surfaces a connection fault.

        The code matters, not just the message: ERR_WAREHOUSE_RUNTIME is a
        query defect under --warehouse, so a missing file carrying it would
        report every query in the board as invalid SQL.
        """
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.diagnostics.codes_execute import ERR_WAREHOUSE_CONNECTION
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        bad_path = tmp_path / "does_not_exist.sqlite"
        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT 1", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(bad_path))
        result = adapter._execute(query, source_config=source_cfg)
        assert result.error is not None
        assert result.error.code == ERR_WAREHOUSE_CONNECTION

    def test_date_param_executes_correctly(self, tmp_path: Path) -> None:
        """A datetime.date parameter executes without DATE literal syntax errors."""
        from datetime import date

        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_dates(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(
            sql="SELECT id FROM events WHERE event_date >= ? ORDER BY id", source="mydb"
        )
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(
            query, params=[date(2024, 6, 1)], source_config=source_cfg
        )
        assert result.error is None, f"Date param execution failed: {result.error}"
        assert [r["id"] for r in result.data] == [2, 3]

    def test_datetime_param_executes_correctly(self, tmp_path: Path) -> None:
        """A datetime.datetime parameter executes without TIMESTAMP literal syntax errors."""
        from datetime import datetime

        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_dates(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(
            sql="SELECT id FROM events WHERE event_ts >= ? ORDER BY id", source="mydb"
        )
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(
            query, params=[datetime(2024, 6, 15, 12, 0, 0)], source_config=source_cfg
        )
        assert result.error is None, f"Datetime param execution failed: {result.error}"
        assert [r["id"] for r in result.data] == [2, 3]

    def test_filter_date_range_executes_on_sqlite(self, tmp_path: Path) -> None:
        """filter_date_range() must not silently exclude every row on SQLite.

        SQLite has no native DATE type: CAST(x AS DATE) takes NUMERIC affinity
        and truncates a date-like string to its leading integer run (e.g.
        '2024-01-15' -> 2024), and SQLite orders every INTEGER below every
        TEXT — so a bare `CAST(col AS DATE) BETWEEN <date> AND <date>` is
        always false there. The helper must spell the "compare as a date"
        comparison differently per dialect (DATE(col) on SQLite).
        """
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_dates(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(
            sql="SELECT id FROM events WHERE {{ filter_date_range('event_date', date_range) }} ORDER BY id",
            source="mydb",
        )
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(
            query,
            variables={"date_range": ["2024-01-01", "2024-12-31"]},
            source_config=source_cfg,
        )
        assert result.error is None, f"filter_date_range query failed: {result.error}"
        assert [r["id"] for r in result.data] == [1, 2]

    def test_sqlite_relative_path_resolves_against_data_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relative sqlite path resolves against data_dir, not process cwd."""
        nested_data_dir = tmp_path / "data"
        nested_data_dir.mkdir()
        db_path = nested_data_dir / "test.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE items (id INTEGER, label TEXT)")
        conn.execute("INSERT INTO items VALUES (1, 'x'), (2, 'y')")
        conn.commit()
        conn.close()

        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        adapter = SqliteAdapter(data_dir=tmp_path)
        source_cfg = SQLiteSourceConfig(type="sqlite", path="data/test.sqlite")
        query = SqlQuery(sql="SELECT COUNT(*) AS n FROM items", source="mydb")

        monkeypatch.chdir(tempfile.gettempdir())
        result = adapter._execute(query, source_config=source_cfg)

        assert result.error is None, f"Unexpected error: {result.error}"
        assert result.data[0]["n"] == 2

    def test_repeated_variable_in_query_executes_correctly(
        self, tmp_path: Path
    ) -> None:
        """A variable referenced more than once executes correctly with ? placeholders."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(
            sql="SELECT id FROM users WHERE id >= {{ x }} OR id <= {{ x }}",
            source="mydb",
        )
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, variables={"x": 1}, source_config=source_cfg)
        assert result.error is None, f"Repeated variable query failed: {result.error}"
        assert len(result.data) == 2

    def test_quoted_variable_in_query_executes_correctly(self, tmp_path: Path) -> None:
        """A quoted string variable '{{ var }}' executes without binding errors."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(
            sql="SELECT id FROM users WHERE name = '{{ name }}'", source="mydb"
        )
        result = adapter._execute(
            query,
            variables={"name": "alice"},
            source_config=SQLiteSourceConfig(type="sqlite", path=str(db_path)),
        )
        assert result.error is None, f"Quoted variable query failed: {result.error}"
        assert len(result.data) == 1
        assert result.data[0]["id"] == 1


class TestSqliteAdapterMaxRowsCeiling:
    """The execution.max_rows ceiling bounds the sqlite3 cursor's own fetch
    (fetchmany()) — the SQL text sent to the driver is never touched.
    """

    @staticmethod
    def _spy_connect(monkeypatch: pytest.MonkeyPatch, captured: list[str]) -> None:
        """Wrap sqlite3.connect so the exact SQL sent to the driver is
        recorded — sqlite3.Connection is a C extension type and cannot be
        monkeypatched directly."""
        from dbt_charts.core.execute.adapters import (
            sqlite_adapter as sqlite_adapter_mod,
        )

        real_connect = sqlite3.connect

        class _SpyConnection:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn

            def execute(self, sql: str, *a: object, **kw: object) -> sqlite3.Cursor:
                captured.append(sql)
                return self._conn.execute(sql, *a, **kw)

            def close(self) -> None:
                self._conn.close()

        def fake_connect(*a: object, **kw: object) -> _SpyConnection:
            return _SpyConnection(real_connect(*a, **kw))  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite_adapter_mod.sqlite3, "connect", fake_connect)

    def test_ceiling_bounds_fetch_and_truncates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")
        captured: list[str] = []
        self._spy_connect(monkeypatch, captured)

        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT id FROM users ORDER BY id", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_cfg)

        # No dialect-specific rewrite runs — the SQL sent to sqlite3 is
        # exactly what the author wrote, and the ceiling still bounds it.
        assert captured == ["SELECT id FROM users ORDER BY id"]
        assert result.error is None
        assert len(result.data) == 1
        assert result.truncated_reason == "max_rows"

    def test_no_truncation_when_under_ceiling(self, tmp_path: Path) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT id FROM users ORDER BY id", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_cfg)

        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason is None

    def test_author_limit_below_ceiling_bounds_the_fetch_exactly(
        self, tmp_path: Path
    ) -> None:
        """A statement shape that could never be SQL-LIMIT-wrapped under the
        old mechanism (EXPLAIN) needs no special-casing now — fetchmany()
        bounds any cursor's result uniformly, regardless of statement type."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        adapter = SqliteAdapter()
        query = SqlQuery(sql="EXPLAIN SELECT id FROM users", source="mydb")
        query.limit = 2
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        result = adapter._execute(query, source_config=source_cfg)

        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason is None


class TestSqliteAdapterFetchmanyNotFetchall:
    """Regression guard: the ceiling must bound the cursor's own fetch via
    fetchmany(), not fetch all rows then slice. Reverting to fetchall() would
    make the existing length/truncated_reason assertions pass (apply_row_limit_
    truncation reproduces identical visible output) while silently pulling
    unbounded data into memory — the OOM the feature exists to prevent.
    """

    @staticmethod
    def _spy_cursor_connect(
        monkeypatch: pytest.MonkeyPatch,
        fetchmany_args: list[int],
        fetchall_called: list[bool],
    ) -> None:
        """Wrap sqlite3.connect so the returned cursor's fetch methods are spied.
        sqlite3.Cursor is a C type that cannot be monkeypatched directly, so the
        spy is threaded in via a wrapper connection that wraps the returned cursor.
        """
        from dbt_charts.core.execute.adapters import (
            sqlite_adapter as sqlite_adapter_mod,
        )

        real_connect = sqlite3.connect

        class _SpyCursor:
            def __init__(self, cursor: sqlite3.Cursor) -> None:
                self._cursor = cursor
                self.description = cursor.description

            def fetchmany(self, n: int) -> list[object]:
                fetchmany_args.append(n)
                return self._cursor.fetchmany(n)

            def fetchall(self) -> list[object]:
                fetchall_called.append(True)
                return self._cursor.fetchall()

        class _SpyConnection:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn

            def execute(self, sql: str, *a: object, **kw: object) -> _SpyCursor:
                return _SpyCursor(self._conn.execute(sql, *a, **kw))

            def close(self) -> None:
                self._conn.close()

        def fake_connect(*a: object, **kw: object) -> _SpyConnection:
            return _SpyConnection(real_connect(*a, **kw))  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite_adapter_mod.sqlite3, "connect", fake_connect)

    def test_fetchmany_called_with_ceiling_plus_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")
        fetchmany_args: list[int] = []
        fetchall_called: list[bool] = []
        self._spy_cursor_connect(monkeypatch, fetchmany_args, fetchall_called)

        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT id FROM users ORDER BY id", source="mydb")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        adapter._execute(query, source_config=source_cfg)

        # fetch_limit = ceiling + 1 = 2 (detects truncation without knowing
        # the true total — mirrors the agent_api pattern).
        assert fetchmany_args == [2]
        assert not fetchall_called

    def test_no_fetchall_when_author_limit_below_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db_with_users(tmp_path)
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1000")
        fetchmany_args: list[int] = []
        fetchall_called: list[bool] = []
        self._spy_cursor_connect(monkeypatch, fetchmany_args, fetchall_called)

        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT id FROM users ORDER BY id", source="mydb")
        query.limit = 1
        source_cfg = SQLiteSourceConfig(type="sqlite", path=str(db_path))
        adapter._execute(query, source_config=source_cfg)

        # Author limit=1 is the binding value; fetch exactly that many.
        assert fetchmany_args == [1]
        assert not fetchall_called


class TestSqliteAdapterDataDir:
    """SqliteAdapter roots relative paths via an injected data_dir — no
    FilesystemProject required."""

    def test_relative_path_resolves_against_data_dir(self, tmp_path: Path) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        db_path = _make_sqlite_db(tmp_path)
        adapter = SqliteAdapter(data_dir=tmp_path)

        query = SqlQuery(sql="SELECT COUNT(*) AS n FROM items", source="db")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=db_path.name)
        result = adapter._execute(query, source_config=source_cfg)

        assert result.error is None
        assert result.data == [{"n": 2}]

    def test_relative_path_without_data_dir_raises(self) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT 1", source="db")
        source_cfg = SQLiteSourceConfig(type="sqlite", path="sub/db.sqlite")

        with pytest.raises(DbtChartsError, match="sub/db.sqlite") as excinfo:
            adapter._execute(query, source_config=source_cfg)

        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-RELATIVE-PATH-NO-DATA-DIR"
        assert excinfo.value.to_diagnostic() is not None

    @pytest.mark.windows
    def test_windows_absolute_path_without_data_dir_does_not_raise(self) -> None:
        """A real absolute Windows path must classify as absolute even with
        no data_dir configured. ``PurePosixPath(str(db_path)).is_absolute()``
        judges a drive-rooted path like ``C:\\...`` relative (no leading
        ``/``), so this raised ERR-ADAPTER-RELATIVE-PATH-NO-DATA-DIR for a
        path that was already absolute. Drive with a literal string, not a
        real ``Path``, so the regression reproduces on any host.
        """
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
        )
        from dbt_charts.core.execute.adapters.sqlite_adapter import SqliteAdapter

        adapter = SqliteAdapter()
        query = SqlQuery(sql="SELECT 1", source="db")
        source_cfg = SQLiteSourceConfig(type="sqlite", path=r"C:\Users\x\bird.sqlite")

        result = adapter._execute(query, source_config=source_cfg)

        assert (
            result.error is None
            or result.error.code != ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR
        )


class TestSqliteRegistryRouting:
    """AdapterRegistry routes sqlite sources to SqliteAdapter."""

    def test_sqlite_query_via_adapter_registry(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A sqlite source query routes to SqliteAdapter and returns data."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        db_path = _make_sqlite_db(tmp_path)
        (tmp_path / "dbt_charts.yml").write_text(
            f"sources:\n  mydb:\n    type: sqlite\n    path: {db_path}\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project)

        query = SqlQuery(sql="SELECT id, label FROM items ORDER BY id", source="mydb")
        result = registry.execute(query)

        assert result.error is None
        assert result.data == [
            {"id": 1, "label": "alpha"},
            {"id": 2, "label": "beta"},
        ]

    def test_sqlite_via_registry_register_source(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """AdapterRegistry.execute routes a sqlite source_config to SqliteAdapter."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        db_path = _make_sqlite_db_with_users(tmp_path)
        registry = build_adapter_registry(local_project(tmp_path))
        registry.register_source("mydb", {"type": "sqlite", "path": str(db_path)})

        query = SqlQuery(sql="SELECT COUNT(*) AS n FROM users", source="mydb")
        result = registry.execute(query)
        assert result.error is None, f"Unexpected error: {result.error}"
        assert result.data[0]["n"] == 2


# ---------------------------------------------------------------------------
# SqlAdapter no longer handles sqlite
# ---------------------------------------------------------------------------


class TestSqlAdapterNoLongerHandlesSqlite:
    """SqlAdapter must not claim or execute sqlite dialect queries after the split."""

    def test_sql_adapter_rejects_sqlite_source_config(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """SqlAdapter._execute with sqlite source_config returns an error, not data."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        _make_sqlite_db(tmp_path)
        adapter = SqlAdapter(
            project=local_project(tmp_path),
            dbt_project_path=None,
            profile_type="postgres",
        )

        query = SqlQuery(sql="SELECT 1 AS n", source="db")
        source_config = SQLiteSourceConfig(type="sqlite", path="test.db")
        result = adapter._execute(query, source_config=source_config)

        assert result.error is not None


# ---------------------------------------------------------------------------
# Dialect registry
# ---------------------------------------------------------------------------


class TestSQLiteDialect:
    def test_get_dialect_returns_sqlite_dialect(self) -> None:
        """get_dialect('sqlite') returns SQLiteDialect, not the Postgres fallback."""
        from dbt_charts.core.dialects import get_dialect

        d = get_dialect("sqlite")
        assert d.name == "sqlite"

    def test_sqlite_dialect_param_is_qmark(self) -> None:
        """SQLiteDialect uses ? placeholders for native sqlite3 positional binding."""
        from dbt_charts.core.dialects import get_dialect

        d = get_dialect("sqlite")
        assert d.param(1) == "?"
        assert d.param(3) == "?"


# ---------------------------------------------------------------------------
# sqlite_ro_uri: URI encoding for paths with special characters
# ---------------------------------------------------------------------------


class TestSqliteRoUri:
    def test_plain_path_produces_read_only_uri(self) -> None:
        """A simple absolute path produces a well-formed read-only URI."""
        from dbt_charts.core.execute.sqlite_utils import sqlite_ro_uri

        uri = sqlite_ro_uri("/tmp/mydb.sqlite")
        assert uri.startswith("file:"), f"Expected file: URI, got {uri!r}"
        assert uri.endswith("?mode=ro"), f"Expected ?mode=ro suffix, got {uri!r}"

    def test_path_with_spaces_is_percent_encoded(self) -> None:
        """A path with spaces is percent-encoded so SQLite's URI parser handles it."""
        from dbt_charts.core.execute.sqlite_utils import sqlite_ro_uri

        uri = sqlite_ro_uri("/tmp/my data/db.sqlite")
        assert "%20" in uri, f"Expected %20 in URI, got {uri!r}"
        assert " " not in uri, f"Found raw space in URI: {uri!r}"

    def test_path_with_spaces_opens_correctly(self, tmp_path: Path) -> None:
        """A SQLite file in a directory with spaces opens without error."""
        from dbt_charts.core.execute.sqlite_utils import sqlite_ro_uri

        spaced_dir = tmp_path / "my data"
        spaced_dir.mkdir()
        db_path = spaced_dir / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()

        uri = sqlite_ro_uri(str(db_path))
        conn2 = sqlite3.connect(uri, uri=True)
        try:
            rows = conn2.execute("SELECT x FROM t").fetchall()
        finally:
            conn2.close()
        assert rows == [(42,)]


# ---------------------------------------------------------------------------
# SQLiteSourceConfig — parse, validate, round-trip
# ---------------------------------------------------------------------------


class TestSQLiteSourceConfig:
    def test_valid_sqlite_config_parses(self) -> None:
        """SQLiteSourceConfig accepts type='sqlite' and a path field."""
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig

        cfg = SQLiteSourceConfig(type="sqlite", path="./data/bird.sqlite")
        assert cfg.type == "sqlite"
        assert cfg.path == "./data/bird.sqlite"

    def test_parse_source_config_returns_sqlite_instance(self) -> None:
        """parse_source_config dispatches to SQLiteSourceConfig for type='sqlite'."""
        from dbt_charts.core.compile.models.source import (
            SQLiteSourceConfig,
            parse_source_config,
        )

        result = parse_source_config({"type": "sqlite", "path": "bird.sqlite"})
        assert isinstance(result, SQLiteSourceConfig)
        assert result.path == "bird.sqlite"

    def test_sqlite_in_valid_source_types(self) -> None:
        """'sqlite' is in VALID_SOURCE_TYPES."""
        from dbt_charts.core.compile.models.source import VALID_SOURCE_TYPES

        assert "sqlite" in VALID_SOURCE_TYPES

    def test_sqlite_is_database_source(self) -> None:
        """is_database_source returns True for sqlite."""
        from dbt_charts.core.compile.models.source import (
            SQLiteSourceConfig,
            is_database_source,
        )

        cfg = SQLiteSourceConfig(type="sqlite", path=":memory:")
        assert is_database_source(cfg) is True

    def test_sqlite_config_requires_path(self) -> None:
        """SQLiteSourceConfig requires the path field (no default)."""
        import pydantic

        from dbt_charts.core.compile.models.source import SQLiteSourceConfig

        with pytest.raises((pydantic.ValidationError, TypeError)):
            SQLiteSourceConfig(type="sqlite")  # type: ignore[call-arg]

    def test_parse_source_config_unknown_type_still_errors(self) -> None:
        """Unknown type still raises; sqlite does not widen the error path."""
        from dbt_charts.core.compile.models.source import parse_source_config

        with pytest.raises(ValueError, match="Unknown source type"):
            parse_source_config({"type": "oracle_nonexistent"})


# ---------------------------------------------------------------------------
# SQLiteSchemaSource — list_tables + profile_table
# ---------------------------------------------------------------------------


class TestSQLiteSchemaSource:
    def test_list_tables_returns_tables(self, tmp_path: Path) -> None:
        """SQLiteSchemaSource.list_tables returns the tables in the db."""
        from dbt_charts.core.inspect.sources.sqlite import SQLiteSchemaSource

        db_path = _make_sqlite_db_with_users(tmp_path)
        src = SQLiteSchemaSource(path=str(db_path))
        result = src.list_tables(schema="main")
        assert result is not None
        tables = result["tables"]
        assert "users" in tables
        assert "orders" in tables

    def test_list_schemas_returns_main(self, tmp_path: Path) -> None:
        """SQLiteSchemaSource.list_schemas returns {'schemas': {'main': {}}}."""
        from dbt_charts.core.inspect.sources.sqlite import SQLiteSchemaSource

        db_path = _make_sqlite_db_with_users(tmp_path)
        src = SQLiteSchemaSource(path=str(db_path))
        result = src.list_schemas()
        assert result is not None
        assert "main" in result["schemas"]

    def test_profile_table_returns_columns(self, tmp_path: Path) -> None:
        """SQLiteSchemaSource.profile_table returns actual column info."""
        from dbt_charts.core.inspect.sources.sqlite import SQLiteSchemaSource

        db_path = _make_sqlite_db_with_users(tmp_path)
        src = SQLiteSchemaSource(path=str(db_path))
        result = src.profile_table(schema="main", table="users")
        assert result is not None
        cols = result["columns"]
        assert "id" in cols
        assert "name" in cols
        assert "age" in cols

    def test_profile_table_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """profile_table returns None for a table that doesn't exist."""
        from dbt_charts.core.inspect.sources.sqlite import SQLiteSchemaSource

        db_path = _make_sqlite_db_with_users(tmp_path)
        src = SQLiteSchemaSource(path=str(db_path))
        result = src.profile_table(schema="main", table="nonexistent_xyz")
        assert result is None


# ---------------------------------------------------------------------------
# LayeredSchemaResolver with SQLite source
# ---------------------------------------------------------------------------


class TestDftSchemaWithSQLite:
    def test_list_tables_via_resolver(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """LayeredSchemaResolver.list_tables works for a SQLite-backed source."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.inspect.resolver import LayeredSchemaResolver

        db_path = _make_sqlite_db_with_users(tmp_path)
        registry = build_adapter_registry(local_project(tmp_path))
        registry.register_source("mydb", {"type": "sqlite", "path": str(db_path)})

        resolver = LayeredSchemaResolver(
            adapter_registry=registry, project=local_project(tmp_path)
        )
        result = resolver.list_tables(source="mydb", schema="main")
        tables = (
            result.get("sources", {})
            .get("mydb", {})
            .get("schemas", {})
            .get("main", {})
            .get("tables", {})
        )
        assert "users" in tables
        assert "orders" in tables

    def test_list_tables_via_resolver_relative_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Resolver resolves relative SQLite path against project_root."""
        import sqlite3 as _sqlite3

        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.inspect.resolver import LayeredSchemaResolver

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        conn = _sqlite3.connect(str(data_dir / "test.sqlite"))
        conn.execute("CREATE TABLE products (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO products VALUES (1, 'widget')")
        conn.commit()
        conn.close()

        registry = build_adapter_registry(local_project(tmp_path))
        registry.register_source("shop", {"type": "sqlite", "path": "data/test.sqlite"})
        resolver = LayeredSchemaResolver(
            adapter_registry=registry, project=local_project(tmp_path)
        )
        result = resolver.list_tables(source="shop", schema="main")
        tables = (
            result.get("sources", {})
            .get("shop", {})
            .get("schemas", {})
            .get("main", {})
            .get("tables", {})
        )
        assert "products" in tables


# The profile → cache → resolver test (SQLite profiled via the private
# dbt_charts_super_schema package, then served through the resolver) is
# intentionally not covered here — it imports dbt_charts_super_schema
# unguarded, so it can only ever run with the private package installed.
