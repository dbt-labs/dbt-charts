"""Canary tests for DuckDBAdapter: instantiation, routing, and basic execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig


def _make_query(sql: str, source: str = "db") -> SqlQuery:
    return SqlQuery(sql=sql, source=source)


class TestDuckDBAdapterSourceConfigOnly:
    """DuckDBAdapter takes a typed source_config — the loose connection_string
    and use_example_db params are deleted, not deprecated."""

    def test_rejects_legacy_connection_string_param(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        with pytest.raises(TypeError):
            DuckDBAdapter(connection_string=":memory:")  # type: ignore[call-arg]

    def test_rejects_legacy_use_example_db_param(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        with pytest.raises(TypeError):
            DuckDBAdapter(use_example_db=True)  # type: ignore[call-arg]

    def test_source_config_executes_against_memory(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            result = adapter._execute(_make_query("SELECT 1 AS n"))
            assert result.error is None
            assert result.data == [{"n": 1}]
        finally:
            adapter.close()


class TestDuckDBAdapterMaxRowsCeiling:
    """The execution.max_rows ceiling bounds the DuckDB cursor's own fetch
    (fetchmany()) — the SQL text sent to the driver is never touched, so an
    oversized result never gets pulled into memory to begin with.
    """

    def test_ceiling_bounds_fetch_and_truncates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
        captured_sql: list[str] = []
        original_execute = duckdb.DuckDBPyConnection.execute

        def spy_execute(self: duckdb.DuckDBPyConnection, sql: str, *a, **kw):  # type: ignore[no-untyped-def]
            captured_sql.append(sql)
            return original_execute(self, sql, *a, **kw)

        monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", spy_execute)

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            result = adapter._execute(_make_query("SELECT * FROM range(100)"))
        finally:
            adapter.close()

        # No dialect-specific rewrite runs — the SQL sent to DuckDB is
        # exactly what the author wrote, and the ceiling still bounds it.
        assert captured_sql == ["SELECT * FROM range(100)"]
        assert result.error is None
        assert len(result.data) == 3
        assert result.truncated_reason == "max_rows"

    def test_no_truncation_when_under_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1000")
        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            result = adapter._execute(_make_query("SELECT * FROM range(3)"))
        finally:
            adapter.close()

        assert result.error is None
        assert len(result.data) == 3
        assert result.truncated_reason is None

    def test_author_limit_below_ceiling_bounds_the_fetch_exactly(self) -> None:
        """A statement shape that could never be SQL-LIMIT-wrapped under the
        old mechanism (DESCRIBE) needs no special-casing now — fetchmany()
        bounds any cursor's result uniformly, regardless of statement type."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            adapter._get_duckdb_connection().execute(
                "CREATE TABLE t(a INT, b INT, c INT, d INT, e INT)"
            )
            query = _make_query("DESCRIBE t")
            query.limit = 2
            result = adapter._execute(query)
        finally:
            adapter.close()

        assert result.error is None
        assert len(result.data) == 2
        assert result.truncated_reason is None


class TestDuckDBAdapterFetchmanyNotFetchall:
    """Regression guard: the ceiling must bound the driver's own fetch via
    fetchmany(), not fetch all rows then slice. Reverting to fetchall() would
    make the existing length/truncated_reason assertions pass (apply_row_limit_
    truncation reproduces identical visible output) while silently pulling
    unbounded data into memory — the OOM the feature exists to prevent.
    """

    def test_fetchmany_called_with_ceiling_plus_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "3")
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

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            adapter._execute(_make_query("SELECT * FROM range(100)"))
        finally:
            adapter.close()

        # fetch_limit = ceiling + 1 = 4 (detects truncation without knowing
        # the true total — mirrors the agent_api pattern).
        assert fetchmany_args == [4]
        assert not fetchall_called

    def test_no_fetchall_when_author_limit_below_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An author-set limit below the ceiling must also use fetchmany, never
        fetchall — the mechanism is uniform regardless of which bound is tighter."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1000")
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

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            query = _make_query("SELECT * FROM range(100)")
            query.limit = 5
            adapter._execute(query)
        finally:
            adapter.close()

        # Author limit=5 is the binding value; fetch exactly that many (no +1
        # for truncation detection — author's own limit is not a ceiling breach).
        assert fetchmany_args == [5]
        assert not fetchall_called


class TestDuckDBAdapterCanExecute:
    """DuckDBAdapter claims SQL against a resolved duckdb source, or a source-less
    SQL query (the engine's default connection). A resolved sqlite/warehouse
    source is another adapter's territory."""

    def test_claims_resolved_duckdb_source(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        query = _make_query("SELECT 1 AS x")
        assert adapter.can_execute(query, DuckDBSourceConfig(type="duckdb")) is True
        adapter.close()

    def test_claims_sourceless_sql(self) -> None:
        """Source-less SQL falls to DuckDB as the default engine — including SQL
        that carries dbt jinja (DbtAdapter, registered first, wins that case at
        the registry; the predicate itself still claims it)."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        assert adapter.can_execute(_make_query("SELECT 1 AS x"), None) is True
        # dbt-jinja source-less SQL is also claimed by the predicate; DbtAdapter
        # (registered first) wins it at the registry, not here.
        jinja = _make_query("SELECT * FROM {{ ref('customers') }}")
        assert adapter.can_execute(jinja, None) is True
        adapter.close()

    def test_does_not_claim_sqlite_source(self) -> None:
        from dbt_charts.core.compile.models.source import SQLiteSourceConfig
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        sqlite_cfg = SQLiteSourceConfig(type="sqlite", path="db.sqlite")
        assert adapter.can_execute(_make_query("SELECT 1"), sqlite_cfg) is False
        adapter.close()


class TestDuckDBAdapterSelectOne:
    def test_select_1_works(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            result = adapter._execute(_make_query("SELECT 1 AS answer"))
            assert result.error is None
            assert result.data == [{"answer": 1}]
        finally:
            adapter.close()

    def test_returns_columns(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            result = adapter._execute(_make_query("SELECT 1 AS a, 2 AS b"))
            assert result.error is None
            assert result.columns == ["a", "b"]
        finally:
            adapter.close()


class TestDuckDBAdapterExternalAccessOff:
    """When external file access is off, file_search_path is meaningless and must
    not be set — including the data_dir resolution it requires. An adapter with
    no data_dir (a host with no local filesystem, e.g. Cloud) must still open
    :memory: successfully."""

    def test_memory_query_when_data_dir_is_none(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            data_dir=None,
            read_only=True,
            allow_external_access_in_readonly=False,
        )
        try:
            result = adapter._execute(_make_query("SELECT 1 AS n"))
            assert result.error is None
            assert result.data == [{"n": 1}]
        finally:
            adapter.close()


class TestDuckDBAdapterReadOnlyFileReleasesLock:
    """A read-only file-backed adapter must not hold the DuckDB file locked between
    queries. Holding a read-only connection open still denies a writer its exclusive
    lock — so `just spend serve` blocked `just spend` (refresh). Connections to a real
    file are opened per query and closed after; :memory: stays pooled.
    """

    def _seed(self, db_path: Path) -> None:
        con = duckdb.connect(str(db_path))
        con.execute("CREATE TABLE t (n INTEGER)")
        con.execute("INSERT INTO t VALUES (7)")
        con.close()

    def test_writer_can_open_file_after_read_only_query(self, tmp_path: Path) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        db_path = tmp_path / "spend.duckdb"
        self._seed(db_path)

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            read_only=True,
        )
        try:
            result = adapter._execute(_make_query("SELECT n FROM t"))
            assert result.error is None
            assert result.data == [{"n": 7}]

            # The refresh case: a writer must be able to grab the exclusive lock while
            # the serving adapter is idle. Before the fix this raised duckdb.IOException.
            writer = duckdb.connect(str(db_path), read_only=False)
            writer.execute("INSERT INTO t VALUES (8)")
            writer.close()
        finally:
            adapter.close()

    def test_connect_lock_conflict_is_classified_not_raised(
        self, tmp_path: Path
    ) -> None:
        """A render landing while `refresh` holds the write lock fails at *connect*
        time. That must surface as a classified QueryResult.error (execute-domain
        code), not an escaped exception — the connect is inside the error boundary.

        The code is the connection one, not a query-defect one: nothing read the
        SQL, so a caller that judges queries (`dct validate --warehouse`) must not
        be able to read this as the query being broken."""
        from dbt_charts.core.diagnostics.codes_execute import ERR_WAREHOUSE_CONNECTION
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        db_path = tmp_path / "spend.duckdb"
        self._seed(db_path)
        # Hold the exclusive write lock, as a running dbt build / refresh would.
        writer = duckdb.connect(str(db_path), read_only=False)
        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            read_only=True,
        )
        try:
            result = adapter._execute(_make_query("SELECT n FROM t"))
            assert result.data == []
            assert result.error is not None
            assert result.error.code == ERR_WAREHOUSE_CONNECTION
        finally:
            adapter.close()
            writer.close()

    def test_setup_sql_shares_connection_with_main_query(self, tmp_path: Path) -> None:
        """setup_sql + main query run on the same ephemeral connection within one
        _execute — session state from setup_sql is visible to the query."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        db_path = tmp_path / "spend.duckdb"
        self._seed(db_path)

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            read_only=True,
        )
        query = SqlQuery(
            sql="SELECT v FROM _setup",
            source="db",
            setup_sql="CREATE TEMP TABLE _setup AS SELECT 99 AS v",
        )
        try:
            result = adapter._execute(query)
            assert result.error is None
            assert result.data == [{"v": 99}]
        finally:
            adapter.close()

    def test_named_source_file_releases_lock(self, tmp_path: Path) -> None:
        """The named-source ephemeral branch (resolver-provided source_config, so
        raw_config is non-None) also opens per query and releases the lock."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        db_path = tmp_path / "spend.duckdb"
        self._seed(db_path)

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            read_only=True,
        )
        try:
            result = adapter._execute(
                _make_query("SELECT n FROM t", source="spend"),
                source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            )
            assert result.error is None
            assert result.data == [{"n": 7}]

            writer = duckdb.connect(str(db_path), read_only=False)
            writer.execute("INSERT INTO t VALUES (8)")
            writer.close()
        finally:
            adapter.close()

    def test_memory_connection_stays_pooled(self) -> None:
        """:memory: is opened read-write and destroyed on close — it must NOT be made
        ephemeral, or state created by one query vanishes before the next."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            read_only=True,
        )
        # First execute seeds a temp table via setup_sql; a SECOND execute with no
        # setup_sql must still see it — proving the connection was pooled, not reopened.
        seed = SqlQuery(
            sql="SELECT n FROM mem",
            source="db",
            setup_sql="CREATE TEMP TABLE mem AS SELECT 1 AS n",
        )
        try:
            first = adapter._execute(seed)
            assert first.error is None
            assert first.data == [{"n": 1}]
            result = adapter._execute(_make_query("SELECT n FROM mem"))
            assert result.error is None
            assert result.data == [{"n": 1}]
        finally:
            adapter.close()


class TestDuckDBAdapterDataDir:
    """DuckDBAdapter roots relative paths via an injected data_dir — no
    FilesystemProject required."""

    def test_relative_named_source_path_resolves_against_data_dir(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            data_dir=tmp_path,
        )
        try:
            resolved = adapter._resolved_path(
                {"type": "duckdb", "path": "sub/db.duckdb"}
            )
            assert resolved == str((tmp_path / "sub/db.duckdb").resolve())
        finally:
            adapter.close()

    def test_relative_named_source_path_without_data_dir_raises(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(source_config=DuckDBSourceConfig(type="duckdb"))
        try:
            with pytest.raises(DbtChartsError, match="sub/db.duckdb") as excinfo:
                adapter._resolved_path({"type": "duckdb", "path": "sub/db.duckdb"})

            assert excinfo.value.code is not None
            assert excinfo.value.code.code == "ERR-ADAPTER-RELATIVE-PATH-NO-DATA-DIR"
            assert excinfo.value.to_diagnostic() is not None
        finally:
            adapter.close()


class TestDuckDBAdapterRegisteredInRegistry:
    def test_duckdb_adapter_appears_in_registry(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        registry = build_adapter_registry(local_project(tmp_path))
        sql_adapters = registry.get_adapters_for_type("sql")
        duckdb_adapters = [a for a in sql_adapters if isinstance(a, DuckDBAdapter)]
        assert len(duckdb_adapters) == 1

    def test_registry_routes_named_duckdb_source_to_duckdb_adapter(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A query naming a duckdb-typed registry source still executes via
        DuckDBAdapter — post-resolution rerouting corrects the pre-resolution
        SqlAdapter guess for named sources that resolve to a duckdb config
        (see AdapterRegistry.execute()'s Case 2 rerouting)."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  analytics:\n    type: duckdb\n    path: ':memory:'\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        query = _make_query("SELECT 42 AS val", source="analytics")
        result = registry.execute(query)
        assert result.error is None
        assert result.data == [{"val": 42}]


class TestDuckDBAdapterDbtProjectPath:
    """Regression: dbt_project_path scan for local DuckDB files must not be dropped.

    When a dbt project sits next to dbt_charts.yml and has a data/dev.duckdb file,
    a no-source SQL query via build_adapter_registry must auto-connect to that file
    (the DBT_PROJECT_DB_NAMES scan). This reproduces the CRITICAL regression where
    DuckDBAdapter dropped the dbt_project_path branch entirely.
    """

    def test_dbt_project_duckdb_auto_connected(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        # Set up a minimal dbt project layout so dbt_project_path_for returns tmp_path.
        (tmp_path / "dbt_charts.yml").write_text("project: test\n")
        (tmp_path / "dbt_project.yml").write_text("name: test\n")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        db_path = data_dir / "dev.duckdb"

        # Seed the DuckDB file with a known table+row.
        seed_conn = duckdb.connect(str(db_path))
        seed_conn.execute("CREATE TABLE test_table (id INTEGER, name VARCHAR)")
        seed_conn.execute("INSERT INTO test_table VALUES (42, 'hello')")
        seed_conn.close()

        registry = build_adapter_registry(
            local_project(tmp_path),
            read_only=False,
        )
        try:
            result = registry.execute(_make_query("SELECT id, name FROM test_table"))
            assert result.error is None, f"Query failed: {result.error}"
            assert result.data == [{"id": 42, "name": "hello"}]
        finally:
            registry.close()
