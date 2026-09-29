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

    def test_dbt_project_without_local_warehouse_raises(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_project.yml").write_text("name: test\n")
        registry = build_adapter_registry(local_project(tmp_path), read_only=False)
        try:
            with pytest.raises(DbtChartsError) as exc:
                registry.execute(_make_query("SELECT 42 AS x"))
            assert exc.value.code is ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE
        finally:
            registry.close()


class TestDuckDBAdapterAttachConfig:
    """DuckDBSourceConfig's attach/extensions/settings/secrets must reach the
    real DuckDB connection instead of being silently dropped (issue #50)."""

    def test_distinct_memory_sources_do_not_share_a_pooled_connection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Two named sources at the default `path: ':memory:'` must not share
        a pooled connection when they differ in extensions/settings/secrets/
        attach: a cache key that ignores those fields lets the second source
        silently inherit the first source's session, settings from the
        wrong source, and an attach alias (or secret) meant for one source
        visible to another."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        db_a = tmp_path / "a.duckdb"
        db_b = tmp_path / "b.duckdb"
        for db_path, value in ((db_a, 1), (db_b, 2)):
            seed = duckdb.connect(str(db_path))
            seed.execute("CREATE TABLE w (id INTEGER)")
            seed.execute(f"INSERT INTO w VALUES ({value})")
            seed.close()

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  alpha:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    settings:\n"
            "      threads: 2\n"
            "    attach:\n"
            f"      - path: '{db_a}'\n"
            "        alias: dba\n"
            "        read_only: true\n"
            "  beta:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    settings:\n"
            "      threads: 7\n"
            "    attach:\n"
            f"      - path: '{db_b}'\n"
            "        alias: dbb\n"
            "        read_only: true\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            alpha_threads = registry.execute(
                _make_query("SELECT current_setting('threads') AS t", source="alpha")
            )
            beta_threads = registry.execute(
                _make_query("SELECT current_setting('threads') AS t", source="beta")
            )
            assert alpha_threads.error is None, alpha_threads.error
            assert beta_threads.error is None, beta_threads.error
            assert alpha_threads.data == [{"t": 2}]
            assert beta_threads.data == [{"t": 7}]

            alpha_row = registry.execute(
                _make_query("SELECT id FROM dba.w", source="alpha")
            )
            beta_row = registry.execute(
                _make_query("SELECT id FROM dbb.w", source="beta")
            )
            assert alpha_row.error is None, alpha_row.error
            assert beta_row.error is None, beta_row.error
            assert alpha_row.data == [{"id": 1}]
            assert beta_row.data == [{"id": 2}]

            # alpha's session must not see beta's attach alias, and vice versa.
            alpha_dbs = registry.execute(
                _make_query(
                    "SELECT database_name FROM duckdb_databases()", source="alpha"
                )
            )
            beta_dbs = registry.execute(
                _make_query(
                    "SELECT database_name FROM duckdb_databases()", source="beta"
                )
            )
            alpha_names = {row["database_name"] for row in alpha_dbs.data}
            beta_names = {row["database_name"] for row in beta_dbs.data}
            assert "dba" in alpha_names and "dbb" not in alpha_names
            assert "dbb" in beta_names and "dba" not in beta_names
        finally:
            registry.close()

    def test_attach_alias_is_queryable(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        other_db = tmp_path / "other.duckdb"
        seed = duckdb.connect(str(other_db))
        seed.execute("CREATE TABLE widgets (id INTEGER)")
        seed.execute("INSERT INTO widgets VALUES (7)")
        seed.close()

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    attach:\n"
            f"      - path: '{other_db}'\n"
            "        alias: other\n"
            "        read_only: true\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            dbs = registry.execute(
                _make_query(
                    "SELECT database_name FROM duckdb_databases()", source="analytics"
                )
            )
            assert dbs.error is None, dbs.error
            names = {row["database_name"] for row in dbs.data}
            assert "other" in names

            rows = registry.execute(
                _make_query("SELECT id FROM other.widgets", source="analytics")
            )
            assert rows.error is None, rows.error
            assert rows.data == [{"id": 7}]
        finally:
            registry.close()

    def test_attach_works_with_no_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A host with no local filesystem (data_dir=None, e.g. Cloud) must
        still be able to attach: external access being on solely because the
        source authors attach/extensions/secrets does not need file_search_path,
        so a missing data_dir must not raise for it. External access is then
        closed back down (SET enable_external_access = false) once the ATTACH
        itself has run, so a later read_csv() in authored SQL does not
        silently resolve a relative path against the process cwd, while the
        attached database stays queryable."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        other_db = tmp_path / "other.duckdb"
        seed = duckdb.connect(str(other_db))
        seed.execute("CREATE TABLE widgets (id INTEGER)")
        seed.execute("INSERT INTO widgets VALUES (7)")
        seed.close()

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            data_dir=None,
            read_only=True,
        )
        try:
            raw_config = {
                "type": "duckdb",
                "path": ":memory:",
                "attach": [
                    {"path": str(other_db), "alias": "other", "read_only": True}
                ],
            }
            source_config = DuckDBSourceConfig(**raw_config)
            result = adapter._execute(
                _make_query("SELECT id FROM other.widgets"),
                source_config=source_config,
            )
            assert result.error is None, result.error
            assert result.data == [{"id": 7}]

            # The pooled :memory: connection is reused for a second query on
            # the same source, so this exercises the same connection whose
            # external access was closed back down after the ATTACH above.
            (tmp_path / "rel.csv").write_text("x\n1\n")
            monkeypatch.chdir(tmp_path)
            csv_result = adapter._execute(
                _make_query("SELECT * FROM read_csv('rel.csv')"),
                source_config=source_config,
            )
            assert csv_result.error is not None
            assert "disabled by configuration" in str(csv_result.error.detail)

            still_queryable = adapter._execute(
                _make_query("SELECT id FROM other.widgets"),
                source_config=source_config,
            )
            assert still_queryable.error is None, still_queryable.error
            assert still_queryable.data == [{"id": 7}]
        finally:
            adapter.close()

    def test_secrets_with_no_data_dir_still_raise(self, tmp_path: Path) -> None:
        """Unlike attach, a secret is meant to authenticate a later
        read_csv()/httpfs call, so external access genuinely needs a
        data_dir to resolve relative paths against; a source authoring
        secrets with no data_dir configured must still raise, not silently
        close external access back down."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            data_dir=None,
            read_only=True,
        )
        try:
            source_config = DuckDBSourceConfig(
                type="duckdb",
                path=":memory:",
                secrets=[{"type": "s3", "key_id": "k"}],
            )
            result = adapter._execute(
                _make_query("SELECT 1 AS n"), source_config=source_config
            )
            assert result.error is not None
        finally:
            adapter.close()

    def test_memory_source_attach_cannot_write_while_adapter_is_read_only(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A `:memory:` main database is always opened read-write (there is
        no other way to populate it), so without forcing READ_ONLY onto the
        ATTACH itself, an attached database would be silently writable even
        though the adapter's whole point is read-only enforcement. The
        author here does not set `attach[].read_only` at all; the adapter's
        own read_only posture must still force it. Goes around
        validate_select_only (an authored-SQL guard, not what this pins) by
        writing directly on the connection the adapter itself produced."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        other_db = tmp_path / "other.duckdb"
        seed = duckdb.connect(str(other_db))
        seed.execute("CREATE TABLE widgets (id INTEGER)")
        seed.close()

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
            data_dir=tmp_path,
            read_only=True,
        )
        try:
            raw_config = {
                "type": "duckdb",
                "path": ":memory:",
                "attach": [{"path": str(other_db), "alias": "other"}],
            }
            conn = adapter._get_duckdb_connection_for_query(raw_config)
            with pytest.raises(duckdb.Error, match="read-only"):
                conn.execute("INSERT INTO other.widgets VALUES (1)")
        finally:
            adapter.close()

    def test_settings_applied_to_connection(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    settings:\n"
            "      threads: 2\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(
                _make_query(
                    "SELECT current_setting('threads') AS threads", source="analytics"
                )
            )
            assert result.error is None, result.error
            assert result.data == [{"threads": 2}]
        finally:
            registry.close()

    def test_extensions_installed_and_loaded(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    extensions:\n"
            "      - json\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(
                _make_query(
                    "SELECT extension_name FROM duckdb_extensions() "
                    "WHERE extension_name = 'json' AND loaded",
                    source="analytics",
                )
            )
            assert result.error is None, result.error
            assert result.data == [{"extension_name": "json"}]
        finally:
            registry.close()

    def test_secrets_created(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    secrets:\n"
            "      - type: s3\n"
            "        name: my_secret\n"
            "        key_id: fake\n"
            "        secret: fakesecret\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(
                _make_query(
                    "SELECT name FROM duckdb_secrets() WHERE name = 'my_secret'",
                    source="analytics",
                )
            )
            assert result.error is None, result.error
            assert result.data == [{"name": "my_secret"}]
        finally:
            registry.close()

    def test_secret_type_injection_is_rejected_before_any_sql_runs(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A secret `type` is spliced into CREATE SECRET SQL. Before the
        identifier check, a type value shaped like `s3); COPY ... TO
        '<path>'; SELECT (1` would run as extra statements during
        config-apply, on a connection that authoring `secrets` had just
        switched external access on for, before validate_select_only or
        validate_setup_sql ever see anything."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        main_db = tmp_path / "main.duckdb"
        seed = duckdb.connect(str(main_db))
        seed.execute("CREATE TABLE t (x INTEGER)")
        seed.execute("INSERT INTO t VALUES (42)")
        seed.close()

        exfil_path = tmp_path / "EXFIL.csv"
        injected_type = (
            f"s3); COPY (SELECT * FROM t) TO '{exfil_path}' (HEADER); "
            "SELECT * FROM (SELECT 1"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            f"    path: '{main_db}'\n"
            "    secrets:\n"
            f'      - type: "{injected_type}"\n'
        )
        registry = build_adapter_registry(local_project(tmp_path), read_only=True)
        try:
            result = registry.execute(
                _make_query("SELECT x FROM t", source="analytics")
            )
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"
        finally:
            registry.close()
        assert not exfil_path.exists()

    def test_secret_field_key_injection_is_rejected_before_any_sql_runs(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A secret's provider field keys are spliced into CREATE SECRET SQL
        the same way `type` is; an injected key must be rejected the same
        way, before any generated SQL reaches the connection."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        main_db = tmp_path / "main.duckdb"
        seed = duckdb.connect(str(main_db))
        seed.execute("CREATE TABLE t (x INTEGER)")
        seed.close()

        exfil_path = tmp_path / "EXFIL.csv"
        injected_key = (
            f"key_id 'a'); ATTACH '{exfil_path}' AS exfil; "
            "CREATE TABLE exfil.t AS SELECT 1; --"
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            f"    path: '{main_db}'\n"
            "    secrets:\n"
            "      - type: s3\n"
            f'        "{injected_key}": x\n'
        )
        registry = build_adapter_registry(local_project(tmp_path), read_only=True)
        try:
            result = registry.execute(
                _make_query("SELECT x FROM t", source="analytics")
            )
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"
        finally:
            registry.close()
        assert not exfil_path.exists()

    def test_attach_option_value_injection_via_dbt_profile_is_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """DuckDBAttachmentConfig.options is scalar-only, so a directly
        authored source rejects a list-valued option at parse time. A
        dbt_profile-expanded target (extra="allow") carries it through
        untouched, reaching the same splice site: a list-valued option
        value must still be rejected, not formatted."""
        import yaml

        main_db = tmp_path / "main.duckdb"
        seed = duckdb.connect(str(main_db))
        seed.execute("CREATE TABLE t (x INTEGER)")
        seed.close()

        other_db = tmp_path / "other.duckdb"
        duckdb.connect(str(other_db)).close()

        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "test_project": {
                        "target": "dev",
                        "outputs": {
                            "dev": {
                                "type": "duckdb",
                                "path": str(main_db),
                                "threads": 4,
                                "attach": [
                                    {
                                        "path": str(other_db),
                                        "options": {
                                            "foo": [
                                                "x\"'); CREATE TABLE p2 AS SELECT 1; --"
                                            ]
                                        },
                                    }
                                ],
                            }
                        },
                    }
                }
            )
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "name: test_project\n"
            "sources:\n"
            "  prod:\n"
            "    type: dbt_profile\n"
            "    profile: test_project\n"
            "    target: dev\n"
        )
        (tmp_path / "dbt_project.yml").write_text(
            "name: test_project\nprofile: test_project\n"
        )
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)
        try:
            result = registry.execute(_make_query("SELECT x FROM t", source="prod"))
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"
        finally:
            registry.close()

    def test_secret_map_key_injection_is_escaped_not_spliced(self) -> None:
        """A dict-valued secret field (DuckDB `map`) sits inside single
        quotes (`'key': 'value'`), so its keys are values, not identifiers.
        A key containing a quote must be escaped like any other value, not
        rejected (real map keys are hyphenated HTTP header names, which are
        not valid identifiers): pinned as a pure-function test since a map
        value is reachable via a dbt_profile secret dict."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        sql = _secret_to_sql(
            {
                "type": "s3",
                "extra_http_headers": {"x'); DROP TABLE t; --": "v"},
            }
        )
        assert sql == (
            "CREATE SECRET (\n"
            "    type 's3',\n"
            "    extra_http_headers map {'x''); DROP TABLE t; --': 'v'}\n"
            ")"
        )

    @pytest.mark.parametrize(
        "yaml_field",
        [
            "plugins:\n      - module: motherduck\n",
            "filesystems:\n      - fs: memory\n",
            "remote:\n      host: localhost\n      port: 1234\n      user: dev\n",
            "use_credential_provider: aws\n",
        ],
    )
    def test_unsupported_field_returns_typed_query_error(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        yaml_field: str,
    ) -> None:
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            f"    {yaml_field}"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(_make_query("SELECT 1 AS n", source="analytics"))
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-UNSUPPORTED-DUCKDB-FIELD"
        finally:
            registry.close()

    def test_unsupported_field_rejected_for_dbt_profile_expanded_source(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Silently ignoring use_credential_provider would connect with
        different semantics than the profile authors, even though dbt-duckdb
        itself supports the field, so this fails loud even for a
        dbt_profile-expanded duckdb target. The message must not send the
        author to a dbt_profile source."""
        import yaml

        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        db_path = tmp_path / "warehouse.duckdb"
        seed = duckdb.connect(str(db_path))
        seed.execute("CREATE TABLE numbers (n INTEGER)")
        seed.close()

        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "test_project": {
                        "target": "dev",
                        "outputs": {
                            "dev": {
                                "type": "duckdb",
                                "path": str(db_path),
                                "threads": 4,
                                "use_credential_provider": "aws",
                            }
                        },
                    }
                }
            )
        )
        (tmp_path / "dbt_charts.yml").write_text(
            "name: test_project\n"
            "sources:\n"
            "  prod:\n"
            "    type: dbt_profile\n"
            "    profile: test_project\n"
            "    target: dev\n"
        )
        (tmp_path / "dbt_project.yml").write_text(
            "name: test_project\nprofile: test_project\n"
        )

        registry = build_adapter_registry(local_project(tmp_path), read_only=True)
        try:
            # A file-backed source (path != ':memory:') opens per query on the
            # read-only ephemeral path, which converts a typed DbtChartsError
            # into QueryResult.error.
            result = registry.execute(
                _make_query("SELECT n FROM numbers", source="prod")
            )
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-UNSUPPORTED-DUCKDB-FIELD"
            message = str(result.error)
            assert "use_credential_provider" in message
            assert "dbt_profile" not in message
        finally:
            registry.close()

    def test_attach_failure_on_memory_source_is_query_result_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A :memory: source uses the pooled connect path; the failure must
        still come back as a typed QueryResult.error, not a raised exception."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        missing = tmp_path / "does_not_exist.duckdb"
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    attach:\n"
            f"      - path: '{missing}'\n"
            "        alias: other\n"
            "        read_only: true\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(_make_query("SELECT 1 AS n", source="analytics"))
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-APPLY-FAILED"
        finally:
            registry.close()

    def test_attach_failure_on_file_source_is_query_result_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A file-backed (non-:memory:) read-only source opens per query; the
        same attach failure comes back as QueryResult.error too."""
        from dbt_charts.core.execute.adapters.adapter_registry import (
            build_adapter_registry,
        )

        main_db = tmp_path / "main.duckdb"
        seed = duckdb.connect(str(main_db))
        seed.close()
        missing = tmp_path / "does_not_exist.duckdb"
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: duckdb\n"
            f"    path: '{main_db}'\n"
            "    attach:\n"
            f"      - path: '{missing}'\n"
            "        alias: other\n"
            "        read_only: true\n"
        )
        registry = build_adapter_registry(local_project(tmp_path))
        try:
            result = registry.execute(_make_query("SELECT 1 AS n", source="analytics"))
            assert result.error is not None
            assert result.error.code is not None
            assert result.error.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-APPLY-FAILED"
        finally:
            registry.close()

    def test_dbt_profile_expansion_carries_attach_through(self, tmp_path: Path) -> None:
        """DbtTargetSourceConfig (extra='allow') is how a dbt_profile duckdb
        source reaches this adapter; model_dump(by_alias=True) must carry
        `attach` through untouched, same as a directly-authored DuckDBSourceConfig."""
        from dbt_charts.core.compile.models.source import DbtTargetSourceConfig
        from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter

        other_db = tmp_path / "other.duckdb"
        seed = duckdb.connect(str(other_db))
        seed.execute("CREATE TABLE widgets (id INTEGER)")
        seed.execute("INSERT INTO widgets VALUES (9)")
        seed.close()

        target = DbtTargetSourceConfig(
            type="duckdb",
            path=":memory:",
            attach=[{"path": str(other_db), "alias": "other", "read_only": True}],
        )
        raw_config = target.model_dump(by_alias=True)
        assert raw_config["attach"] == [
            {"path": str(other_db), "alias": "other", "read_only": True}
        ]

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"), data_dir=tmp_path
        )
        try:
            result = adapter._execute(
                _make_query("SELECT id FROM other.widgets"),
                source_config=target,
            )
            assert result.error is None, result.error
            assert result.data == [{"id": 9}]
        finally:
            adapter.close()


class TestDuckDBSqlBuilders:
    """Pure-function tests for the SQL builders duckdb_adapter.py uses to turn
    extensions/settings/secrets/attach config into SQL text. These take a
    dict and return a string (or raise), no connection needed, so pinning
    the exact SQL is cheap and catches a formatting regression that a live
    DuckDB call would happily accept as different-but-valid SQL."""

    def test_duckdb_ident_accepts_bare_identifier(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import _duckdb_ident

        assert _duckdb_ident("threads", "setting key") == "threads"

    @pytest.mark.parametrize(
        "bad",
        ["my-db", "1leading", "has space", "semi;colon", ""],
    )
    def test_duckdb_ident_rejects_unsafe_identifiers(self, bad: str) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import _duckdb_ident

        with pytest.raises(DbtChartsError) as excinfo:
            _duckdb_ident(bad, "attach alias")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"
        assert "attach alias" in str(excinfo.value)

    def test_secret_to_sql_missing_type_raises(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        with pytest.raises(DbtChartsError) as excinfo:
            _secret_to_sql({"name": "my_secret"})
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_secret_to_sql_persistent_and_type_only(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        sql = _secret_to_sql({"type": "s3", "persistent": True})
        assert sql == "CREATE PERSISTENT SECRET (\n    type 's3'\n)"

    def test_secret_to_sql_named_secret_uses_or_replace(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        sql = _secret_to_sql({"type": "s3", "name": "my_secret", "key_id": "abc"})
        assert sql == (
            "CREATE OR REPLACE SECRET my_secret (\n    type 's3',\n    key_id 'abc'\n)"
        )

    def test_secret_to_sql_scope_list_emits_one_scope_per_element(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        sql = _secret_to_sql(
            {"type": "s3", "scope": ["s3://bucket-a", "s3://bucket-b"]}
        )
        assert sql == (
            "CREATE SECRET (\n"
            "    type 's3',\n"
            "    scope 's3://bucket-a',\n"
            "    scope 's3://bucket-b'\n"
            ")"
        )

    def test_secret_to_sql_scope_string_emits_single_scope(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        sql = _secret_to_sql({"type": "s3", "scope": "s3://bucket-a"})
        assert sql == "CREATE SECRET (\n    type 's3',\n    scope 's3://bucket-a'\n)"

    def test_secret_to_sql_type_injection_rejected(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import _secret_to_sql

        with pytest.raises(DbtChartsError) as excinfo:
            _secret_to_sql({"type": "s3); DROP TABLE t; SELECT (1"})
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_secret_field_to_sql_dict_value_becomes_map(self) -> None:
        """Map keys are real-world HTTP header names, which are hyphenated
        and so not valid bare identifiers; they must still be accepted,
        escaped as any other value."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _secret_field_to_sql,
        )

        sql = _secret_field_to_sql(
            "extra_http_headers", {"x-api-key": "bar", "x-request-id": "qux"}
        )
        assert sql == (
            "extra_http_headers map {'x-api-key': 'bar', 'x-request-id': 'qux'}"
        )

    def test_secret_field_to_sql_map_key_with_quote_is_escaped(self) -> None:
        """A map key sits inside the quoted literal DuckDB parses it as, so
        a quote in the key must be escaped, not left to break out of it."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _secret_field_to_sql,
        )

        sql = _secret_field_to_sql(
            "extra_http_headers", {"x'); DROP TABLE t; --": "bar"}
        )
        assert sql == ("extra_http_headers map {'x''); DROP TABLE t; --': 'bar'}")

    def test_secret_field_to_sql_list_value_becomes_array(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _secret_field_to_sql,
        )

        assert _secret_field_to_sql("urls", ["a", "b"]) == "urls array ['a', 'b']"

    def test_secret_field_to_sql_scalar_value_is_always_quoted(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _secret_field_to_sql,
        )

        assert _secret_field_to_sql("provider", "credential_chain") == (
            "provider 'credential_chain'"
        )
        assert _secret_field_to_sql("key_id", "abc") == "key_id 'abc'"

    def test_secret_field_to_sql_key_injection_rejected(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _secret_field_to_sql,
        )

        with pytest.raises(DbtChartsError) as excinfo:
            _secret_field_to_sql("key_id); DROP TABLE t; --", "abc")
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_attachment_to_sql_minimal(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        assert (
            _attachment_to_sql({"path": "x.duckdb"}, adapter_read_only=False)
            == "ATTACH IF NOT EXISTS 'x.duckdb'"
        )

    def test_attachment_to_sql_strips_query_parameters(self) -> None:
        """ATTACH does not support query parameters (a MotherDuck-style URL
        with a `?motherduck_token=...` suffix would otherwise fail to
        attach), so they are stripped before the path is quoted."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql(
            {"path": "md:my_db?motherduck_token=abc123"}, adapter_read_only=False
        )
        assert sql == "ATTACH IF NOT EXISTS 'md:my_db'"

    def test_attachment_to_sql_direct_fields(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql(
            {
                "path": "x.duckdb",
                "alias": "other",
                "type": "sqlite",
                "secret": "my_secret",
                "read_only": True,
            },
            adapter_read_only=False,
        )
        assert sql == (
            "ATTACH IF NOT EXISTS 'x.duckdb' AS other "
            "(TYPE sqlite, SECRET my_secret, READ_ONLY)"
        )

    def test_attachment_to_sql_type_and_secret_from_options(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql(
            {
                "path": "x.duckdb",
                "options": {"type": "sqlite", "secret": "my_secret", "read_only": True},
            },
            adapter_read_only=False,
        )
        assert sql == (
            "ATTACH IF NOT EXISTS 'x.duckdb' (TYPE sqlite, SECRET my_secret, READ_ONLY)"
        )

    def test_attachment_to_sql_conflicting_type_raises(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        with pytest.raises(DbtChartsError) as excinfo:
            _attachment_to_sql(
                {"path": "x.duckdb", "type": "sqlite", "options": {"type": "postgres"}},
                adapter_read_only=False,
            )
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_attachment_to_sql_rejects_non_scalar_option_value(self) -> None:
        """A list-valued attach option was spliced raw before quoting
        (`f"{key.upper()} {value}"`), letting an option value shaped like
        `["x\"'); CREATE TABLE p2 AS SELECT 1; --"]` break out of the ATTACH
        statement once Python's repr rendered it. Non-scalar option values
        are rejected instead of formatted."""
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        with pytest.raises(DbtChartsError) as excinfo:
            _attachment_to_sql(
                {
                    "path": "x.duckdb",
                    "options": {"foo": ["x\"'); CREATE TABLE p2 AS SELECT 1; --"]},
                },
                adapter_read_only=False,
            )
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_attachment_to_sql_generic_bool_str_numeric_options(self) -> None:
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql(
            {
                "path": "x.duckdb",
                "options": {
                    "enabled_flag": True,
                    "disabled_flag": False,
                    "some_string": "value",
                    "some_number": 5,
                },
            },
            adapter_read_only=False,
        )
        assert sql == (
            "ATTACH IF NOT EXISTS 'x.duckdb' "
            "(ENABLED_FLAG, SOME_STRING 'value', SOME_NUMBER '5')"
        )

    def test_attachment_to_sql_adapter_read_only_forces_read_only_flag(self) -> None:
        """When the adapter itself is read_only, READ_ONLY is forced onto the
        ATTACH regardless of the authored flag (unset here, defaults False)."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql({"path": "x.duckdb"}, adapter_read_only=True)
        assert sql == "ATTACH IF NOT EXISTS 'x.duckdb' (READ_ONLY)"

    def test_attachment_to_sql_adapter_read_write_honors_authored_flag(self) -> None:
        """When the adapter is read-write, an authored read_only=False (the
        default) is honored as written; no forced READ_ONLY."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _attachment_to_sql,
        )

        sql = _attachment_to_sql(
            {"path": "x.duckdb", "read_only": False}, adapter_read_only=False
        )
        assert sql == "ATTACH IF NOT EXISTS 'x.duckdb'"

    def test_apply_duckdb_extensions_dict_form_missing_name_raises(self) -> None:
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _apply_duckdb_extensions,
        )

        with pytest.raises(DbtChartsError) as excinfo:
            _apply_duckdb_extensions(
                duckdb.connect(":memory:"), [{"repo": "community"}]
            )
        assert excinfo.value.code is not None
        assert excinfo.value.code.code == "ERR-ADAPTER-DUCKDB-CONFIG-INVALID"

    def test_apply_duckdb_extensions_dict_form_no_repo_uses_default_repository(
        self,
    ) -> None:
        """{name} with no repo installs the same way as the plain-string
        form: the driver's install_extension/load_extension API, not SQL."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _apply_duckdb_extensions,
        )

        installed: list[str] = []
        loaded: list[str] = []

        class _FakeConn:
            def install_extension(self, name: str) -> None:
                installed.append(name)

            def load_extension(self, name: str) -> None:
                loaded.append(name)

        _apply_duckdb_extensions(_FakeConn(), [{"name": "json"}])
        assert installed == ["json"]
        assert loaded == ["json"]

    def test_apply_duckdb_extensions_dict_form_installs_from_repo(self) -> None:
        """{name, repo} installs via SQL (INSTALL ... FROM '<repo>') instead of
        the driver's install_extension API; pin the generated SQL by
        intercepting conn.execute rather than hitting the network."""
        from dbt_charts.core.execute.adapters.duckdb_adapter import (
            _apply_duckdb_extensions,
        )

        executed: list[str] = []
        loaded: list[str] = []

        class _FakeConn:
            def execute(self, sql: str) -> None:
                executed.append(sql)

            def load_extension(self, name: str) -> None:
                loaded.append(name)

        _apply_duckdb_extensions(
            _FakeConn(),
            [{"name": "h3", "repo": "community"}],
        )
        assert executed == ["INSTALL h3 FROM 'community'"]
        assert loaded == ["h3"]
