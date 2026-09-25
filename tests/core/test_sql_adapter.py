"""Tests for SqlAdapter generic execute path."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_UNPARSEABLE_SQL
from dbt_charts.core.execute.adapters.base import QueryResult
from dbt_charts.core.execute.adapters.duckdb_adapter import DuckDBAdapter
from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter


def _make_query(sql: str, source: str = "db") -> SqlQuery:
    """Helper to create a SqlQuery with minimal required fields."""
    return SqlQuery(sql=sql, source=source)


def _sql(
    local_project: Callable[..., FilesystemProject], **kwargs: object
) -> SqlAdapter:
    """Create a SqlAdapter with required args defaulted to in-memory stubs."""
    kwargs.setdefault("project", local_project(Path("/tmp")))
    kwargs.setdefault("dbt_project_path", None)
    kwargs.setdefault("profile_type", "postgres")
    return SqlAdapter(**kwargs)  # type: ignore[arg-type]


def _ddb(
    local_project: Callable[..., FilesystemProject], **kwargs: object
) -> DuckDBAdapter:
    """Create a DuckDBAdapter with required args defaulted to in-memory stubs."""
    kwargs.setdefault("data_dir", local_project(Path("/tmp")).data_path("."))
    kwargs.setdefault("source_config", DuckDBSourceConfig(type="duckdb"))
    return DuckDBAdapter(**kwargs)  # type: ignore[arg-type]


# Minimum valid source configs per dialect (for tests that only need dialect detection).
_MIN_SOURCE: dict[str, dict] = {
    "postgres": {
        "type": "postgres",
        "host": "h",
        "dbname": "db",
        "user": "u",
        "password": "p",
    },
    "snowflake": {
        "type": "snowflake",
        "account": "xy12345",
        "user": "u",
        "database": "db",
        "warehouse": "wh",
    },
    "bigquery": {"type": "bigquery", "project": "proj", "dataset": "ds"},
    "redshift": {
        "type": "redshift",
        "host": "h",
        "dbname": "db",
        "user": "u",
        "password": "p",
    },
    "duckdb": {"type": "duckdb"},
    "sqlserver": {
        "type": "sqlserver",
        "host": "h",
        "database": "db",
        "user": "u",
        "password": "p",
    },
    "mysql": {
        "type": "mysql",
        "host": "h",
        "database": "db",
        "user": "u",
        "password": "p",
    },
}


class TestGenericExecutePath:
    """Test that SqlAdapter executes queries correctly."""

    def test_static_sql_validation_error_keeps_execute_code(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        query_adapter = _sql(local_project, profile_type="duckdb")

        result = query_adapter._execute(
            _make_query(
                "SELECT COUNT(*) FILTER (WasdfHERE status NOT IN ('solved')) "
                "AS open_tickets FROM tickets"
            ),
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        assert result.error is not None
        assert result.error.code == ERR_UNPARSEABLE_SQL

    def test_tokenizer_failure_keeps_execute_code(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A tokenizer failure must carry the coded error, not fall back to internal.

        An unterminated string literal fails sqlglot's tokenizer rather than its
        parser, and TokenError is a sibling of ParseError — so it used to escape
        the guard's conversion and reach the user as an uncoded ERR-INTERNAL.
        """
        query_adapter = _sql(local_project, profile_type="duckdb")

        result = query_adapter._execute(
            _make_query("SELECT 'abc"),
            source_config=DuckDBSourceConfig(type="duckdb"),
        )

        assert result.error is not None
        assert result.error.code == ERR_UNPARSEABLE_SQL

    def test_duckdb_executes(self) -> None:
        """DuckDB adapter executes queries."""
        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            query = _make_query("SELECT 42 AS answer")
            result = adapter._execute(query)
            assert result.error is None
            assert result.data[0]["answer"] == 42
        finally:
            adapter.close()

    def test_source_config_routes_to_dedicated_duckdb_file(self, tmp_path) -> None:
        """A resolver-provided DuckDB source_config opens that file, not the default."""
        import duckdb

        db_path = tmp_path / "analytics.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE adoption (answer INTEGER)")
        conn.execute("INSERT INTO adoption VALUES (7)")
        conn.close()

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            query = _make_query("SELECT answer FROM adoption")
            result = adapter._execute(
                query,
                source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            )
            assert result.error is None
            assert result.data == [{"answer": 7}]
        finally:
            adapter.close()

    def test_close_propagates_unexpected_exception(self) -> None:
        """close() propagates exceptions from the underlying connection (no suppression)."""
        from unittest.mock import MagicMock

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        mock_conn = MagicMock()
        mock_conn.close.side_effect = RuntimeError("unexpected close failure")
        adapter._connection = mock_conn

        with pytest.raises(RuntimeError, match="unexpected close failure"):
            adapter.close()

    def test_execute_returns_columns(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Execution returns column names."""
        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT 1 AS a, 2 AS b")
            result = adapter._execute(query)
            assert result.columns == ["a", "b"]
        finally:
            adapter.close()

    def test_execute_respects_limit(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Execution respects query limit."""
        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
            query.limit = 2
            result = adapter._execute(query)
            assert len(result.data) == 2
        finally:
            adapter.close()

    def test_close_drains_duckdb_source_connections(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """close() drains connections created via _create_connection_from_config (DuckDB source)."""
        adapter = _ddb(local_project)
        query = _make_query("SELECT 1 AS value")
        adapter._execute(
            query, source_config=DuckDBSourceConfig(type="duckdb", path=":memory:")
        )
        # close() must not raise even when source connections were opened
        adapter.close()


class TestSourcelessQueryPath:
    """source_config=None falls through to the adapter's default DuckDB connection.

    The resolver owns named-source lookup and the dbt-context fallback (see
    DefaultSourceResolver tests); the adapter just consumes a typed SourceConfig
    or None. None means "use the adapter's own default connection."
    """

    def test_none_source_runs_on_default_duckdb(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT 7 AS answer")
            result = adapter.execute(query)
            assert result.error is None
            assert result.data == [{"answer": 7}]
        finally:
            adapter.close()


class TestReadOnlyAdapter:
    """Tests for DuckDBAdapter read_only flag."""

    def test_default_file_based_opens_read_only_and_blocks_writes(
        self, tmp_path
    ) -> None:
        """DuckDBAdapter with no read_only arg and a file DB opens read-only and refuses writes."""
        import duckdb

        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (x INT)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.close()

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb", path=db_path),
        )
        try:
            result = adapter._execute(_make_query("SELECT x FROM t"))
            assert result.error is None
            assert result.data[0]["x"] == 1

            result = adapter._execute(_make_query("INSERT INTO t VALUES (2)"))
            assert result.error is not None
        finally:
            adapter.close()

    def test_memory_in_memory_driver_is_writable(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """:memory: DuckDB connections are always opened read-write at the
        driver level — there's no other way to populate an in-memory database.
        DuckDBAdapter._execute() now refuses all writes via the read-only SQL
        guard regardless, but the underlying driver still permits internal
        bootstrap on :memory:.
        """
        adapter = _ddb(local_project)
        try:
            conn = adapter._get_duckdb_connection()
            conn.execute("CREATE TABLE t (x INT)")
            conn.execute("INSERT INTO t VALUES (7)")
            result = adapter._execute(_make_query("SELECT x FROM t"))
            assert result.error is None
            assert result.data[0]["x"] == 7
        finally:
            adapter.close()

    def test_read_only_false_opens_writable_file_driver(self, tmp_path) -> None:
        """Explicit read_only=False opens the file in read-write mode at the
        driver level — the DuckDBAdapter's read-only SQL guard still refuses
        write SQL via _execute(), but the driver itself permits internal
        writes (cache backends, fixture setup).
        """
        import duckdb

        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (x INT)")
        conn.close()

        adapter = DuckDBAdapter(
            data_dir=tmp_path,
            source_config=DuckDBSourceConfig(type="duckdb", path=db_path),
            read_only=False,
        )
        try:
            driver_conn = adapter._get_duckdb_connection()
            driver_conn.execute("INSERT INTO t VALUES (42)")
            result = adapter._execute(_make_query("SELECT x FROM t WHERE x = 42"))
            assert result.error is None
            assert result.data == [{"x": 42}]
        finally:
            adapter.close()


class TestReadOnlyDuckDB:
    def test_read_only_duckdb_blocks_local_json_reads_by_default(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """read_only adapter keeps local JSON readers blocked by default."""
        json_path = tmp_path / "rows.jsonl"
        json_path.write_text('{"x":1}\n{"x":2}\n', encoding="utf-8")

        adapter = _ddb(local_project, read_only=True)
        try:
            query = _make_query(
                f"SELECT SUM(x) AS total FROM read_json_auto('{json_path.as_posix()}')"
            )
            result = adapter._execute(query)
            assert result.error is not None
        finally:
            adapter.close()

    def test_read_only_duckdb_source_config_cannot_override_external_access(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Source-level duckdb_config cannot re-enable external access when adapter is read_only=True."""
        json_path = tmp_path / "rows.jsonl"
        json_path.write_text('{"x":4}\n{"x":5}\n', encoding="utf-8")

        adapter = _ddb(local_project, read_only=True)
        try:
            query = _make_query(
                f"SELECT SUM(x) AS total FROM read_json_auto('{json_path.as_posix()}')"
            )
            source_config = DuckDBSourceConfig(
                type="duckdb",
                path=":memory:",
                duckdb_config={"enable_external_access": True},
            )
            result = adapter._execute(query, source_config=source_config)
            assert result.error is not None, "Expected external access to be blocked"
        finally:
            adapter.close()


class TestLimitFetchDialects:
    """The row limit bounds the driver's own fetch — dbt-adapters'
    execute(..., limit=...) kwarg, which does cursor.fetchmany(limit)
    internally — for every dialect uniformly, including SQL Server (no LIMIT
    keyword) and BigQuery. The SQL text sent to the warehouse is never
    rewritten, so no dialect-specific rewrite can ever get it wrong.
    """

    def _captured_call_for_dialect(
        self,
        local_project: Callable[..., FilesystemProject],
        dialect: str,
        limit: int | None = 10,
        sql: str = "SELECT 1 AS x",
    ) -> tuple[str, dict]:
        """Run _execute_via_dbt_adapter with a mock build_adapter; return the
        (sql, kwargs) the data query was sent with."""
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.query.normalized import SqlQuery

        captured: list[tuple[str, dict]] = []

        mock_table = MagicMock()
        mock_table.column_names = ["x"]
        mock_table.rows = [(1,)]

        mock_adapter = MagicMock()
        mock_adapter.connection_named.return_value.__enter__ = lambda _mock_conn: None
        mock_adapter.connection_named.return_value.__exit__ = MagicMock(
            return_value=False
        )

        def fake_execute(sent_sql: str, **kwargs: object) -> tuple:
            captured.append((sent_sql, kwargs))
            return (None, mock_table)

        mock_adapter.execute.side_effect = fake_execute

        sql_adapter = _sql(local_project, profile_type=dialect)

        src = _MIN_SOURCE.get(dialect, {"type": dialect})
        q = SqlQuery(sql=sql, source=dialect)
        q.limit = limit

        with patch(
            "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            prepared = sql_adapter.prepare_sql(q, params=[])
            assert not isinstance(prepared, QueryResult), prepared.error
            sql_adapter._execute_via_dbt_adapter(prepared, q, src)

        assert captured, f"No call captured for dialect {dialect!r}"
        # captured[0] is the connection-setup timeout SQL; captured[-1] is
        # the data query (the call we're verifying carries limit=).
        return captured[-1]

    @pytest.mark.parametrize(
        "dialect", ["postgres", "snowflake", "redshift", "bigquery", "sqlserver"]
    )
    def test_author_limit_reaches_the_driver_as_a_kwarg(
        self, local_project: Callable[..., FilesystemProject], dialect: str
    ) -> None:
        sql, kwargs = self._captured_call_for_dialect(local_project, dialect)
        assert kwargs["limit"] == 10
        # No dialect-specific rewrite ever runs — the SQL text is exactly
        # what the author wrote, byte for byte.
        assert sql == "SELECT 1 AS x"


class TestMaxRowsCeilingFetchLimit:
    """The execution.max_rows ceiling bounds the driver's own fetch (the
    limit= kwarg passed to dbt-adapters' execute()), never the SQL text. The
    ceiling binds (and requests ceiling + 1 rows, to detect truncation)
    whenever no author limit is set, or the author's own limit exceeds the
    ceiling. When the author's own limit is already at or below the ceiling,
    the driver is asked for exactly that many rows.
    """

    _captured_call_for_dialect = TestLimitFetchDialects._captured_call_for_dialect

    def test_no_author_limit_requests_ceiling_plus_one(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "50")
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", limit=None
        )
        assert kwargs["limit"] == 51
        assert sql == "SELECT 1 AS x"

    def test_author_limit_above_ceiling_requests_ceiling_plus_one(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "50")
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", limit=1000
        )
        assert kwargs["limit"] == 51
        assert sql == "SELECT 1 AS x"

    def test_author_limit_below_ceiling_requests_exactly_that_many(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "5000")
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", limit=10
        )
        assert kwargs["limit"] == 10
        assert sql == "SELECT 1 AS x"


class TestDriverLimitOmittedForFetchmanyIncapableDialects:
    """dbt-spark's Hive/ODBC cursor wrappers implement fetchall but not
    fetchmany — passing limit= to their execute() raises AttributeError
    inside dbt-adapters' get_result_from_cursor. The dialect's
    cursor_supports_driver_limit flag must gate limit= off for those
    dialects; the ceiling still applies via the post-fetch slice in
    apply_row_limit_truncation, just without a driver-level fetch bound.
    """

    _captured_call_for_dialect = TestLimitFetchDialects._captured_call_for_dialect

    def test_spark_never_receives_a_limit_kwarg(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "50")
        _, kwargs = self._captured_call_for_dialect(local_project, "spark", limit=None)
        assert kwargs["limit"] is None

    def test_databricks_still_receives_the_ceiling_as_a_limit_kwarg(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression guard: databricks (dbt-databricks, fetchmany-capable)
        must keep the driver-bound fetch — only spark (dbt-spark) falls back."""
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "50")
        _, kwargs = self._captured_call_for_dialect(
            local_project, "databricks", limit=None
        )
        assert kwargs["limit"] == 51


class TestSqlTextNeverRewrittenByRowLimit:
    """Every SQL shape that broke under the old SQL-rewrite mechanism —
    SQL Server's own TOP, a join with duplicate output column names, an
    author's own inline LIMIT, EXPLAIN, a trailing semicolon — needs no
    special-casing here, because the row-limit ceiling never parses or
    rewrites SQL text at all. Proof: the SQL sent to the driver equals the
    SQL that went in, unconditionally, across dialects and ceilings.
    """

    _captured_call_for_dialect = TestLimitFetchDialects._captured_call_for_dialect

    def test_sqlserver_own_top_clause_survives_untouched(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "5")
        sql, kwargs = self._captured_call_for_dialect(
            local_project,
            "sqlserver",
            limit=None,
            sql="SELECT TOP 1000000 * FROM orders",
        )
        assert sql == "SELECT TOP 1000000 * FROM orders"
        # The ceiling still bounds the fetch even though the author's own
        # TOP is far above it — the driver, not the SQL text, enforces it.
        assert kwargs["limit"] == 6

    def test_duplicate_output_column_names_survive_untouched(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # HIGH regression under the old mechanism: an outer `SELECT * FROM
        # (...) AS wrap` forced the engine to disambiguate duplicate column
        # names from a join (id, id -> id, id_1), silently changing which
        # value a chart bound to "id" read back. Nothing wraps the SQL now.
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", sql="SELECT a.*, b.* FROM a, b"
        )
        assert sql == "SELECT a.*, b.* FROM a, b"

    def test_explain_survives_untouched(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", sql="EXPLAIN SELECT 1 AS a"
        )
        assert sql == "EXPLAIN SELECT 1 AS a"
        assert kwargs["limit"] == 10

    def test_trailing_semicolon_survives_untouched(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", sql="SELECT * FROM orders;"
        )
        assert sql == "SELECT * FROM orders;"

    def test_authors_own_inline_limit_survives_untouched(
        self,
        local_project: Callable[..., FilesystemProject],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # CRITICAL regression under the old mechanism: an author's own
        # `LIMIT 1` got silently widened to a much larger execution.max_rows
        # ceiling whenever no YAML query.limit was set. There is nothing to
        # widen now — the SQL text is never touched.
        monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1000000")
        sql, kwargs = self._captured_call_for_dialect(
            local_project, "postgres", limit=None, sql="SELECT * FROM orders LIMIT 1"
        )
        assert sql == "SELECT * FROM orders LIMIT 1"
        assert kwargs["limit"] == 1000001


class TestResolveDuckdbConnectKwargs:
    """Unit tests for the DuckDBAdapter._resolve_duckdb_connect_kwargs helper."""

    def test_file_read_only_sets_driver_flag(self) -> None:
        """File-based read_only=True sets the DuckDB driver read_only kwarg."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            "/tmp/test.duckdb", read_only=True, duckdb_config=None
        )
        assert kwargs.get("read_only") is True

    def test_memory_read_only_no_driver_flag(self) -> None:
        """:memory: with read_only=True omits the driver read_only flag (in-memory exception)."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            ":memory:", read_only=True, duckdb_config=None
        )
        assert "read_only" not in kwargs

    def test_read_only_forces_external_access_false_over_user_config(self) -> None:
        """read_only=True forces enable_external_access=False, ignoring user-supplied config."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            ":memory:", read_only=True, duckdb_config={"enable_external_access": True}
        )
        assert kwargs["config"]["enable_external_access"] is False

    def test_read_only_no_config_disables_external_access(self) -> None:
        """read_only=True with no config still sets enable_external_access=False."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            ":memory:", read_only=True, duckdb_config=None
        )
        assert kwargs["config"]["enable_external_access"] is False

    def test_read_write_respects_user_external_access(self) -> None:
        """read_only=False lets user-supplied duckdb_config set enable_external_access."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            ":memory:", read_only=False, duckdb_config={"enable_external_access": True}
        )
        assert kwargs["config"]["enable_external_access"] is True

    def test_read_write_no_config_produces_empty_kwargs(self) -> None:
        """read_only=False with no config produces empty kwargs (no driver changes)."""
        kwargs = DuckDBAdapter._resolve_duckdb_connect_kwargs(
            ":memory:", read_only=False, duckdb_config=None
        )
        assert kwargs == {}


class TestDuckDBSchemaSearchPath:
    """schema: in DuckDBSourceConfig sets search_path so unqualified table names resolve."""

    def test_schema_key_sets_search_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        db_path = tmp_path / "multi.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE SCHEMA myschema")
        conn.execute("CREATE TABLE myschema.widgets (id INTEGER)")
        conn.execute("INSERT INTO myschema.widgets VALUES (42)")
        conn.close()

        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT id FROM widgets")
            result = adapter._execute(
                query,
                source_config=DuckDBSourceConfig(
                    type="duckdb", path=str(db_path), schema="myschema"
                ),
            )
            assert result.error is None
            assert result.data == [{"id": 42}]
        finally:
            adapter.close()

    def test_no_schema_key_leaves_main_default(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        db_path = tmp_path / "plain.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE things (val TEXT)")
        conn.execute("INSERT INTO things VALUES ('hello')")
        conn.close()

        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT val FROM things")
            result = adapter._execute(
                query,
                source_config=DuckDBSourceConfig(type="duckdb", path=str(db_path)),
            )
            assert result.error is None
            assert result.data == [{"val": "hello"}]
        finally:
            adapter.close()

    def test_invalid_schema_name_rejected_at_parse(self) -> None:
        """Schema names with quotes/semicolons/spaces are rejected by the validator."""
        with pytest.raises(ValidationError, match="valid SQL identifier"):
            DuckDBSourceConfig(
                type="duckdb", path=":memory:", schema="foo'; DROP TABLE--"
            )

        with pytest.raises(ValidationError, match="valid SQL identifier"):
            DuckDBSourceConfig(type="duckdb", path=":memory:", schema="123bad")

    def test_unknown_schema_surfaces_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Querying through a non-existent schema gives a clear DuckDB error, not silent empty."""
        db_path = tmp_path / "db.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE main.things (val TEXT)")
        conn.close()

        adapter = _ddb(local_project)
        try:
            query = _make_query("SELECT val FROM things")
            result = adapter._execute(
                query,
                source_config=DuckDBSourceConfig(
                    type="duckdb", path=str(db_path), schema="nonexistent"
                ),
            )
            assert result.error is not None, "Expected error for nonexistent schema"
        finally:
            adapter.close()

    def test_malicious_schema_in_raw_config_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        """Adapter-level defense: a raw config dict with a malicious schema value
        must raise ValueError before issuing SET search_path.

        This regression test covers the case where source_config is constructed
        as a raw dict (bypassing the pydantic field_validator) — the adapter's
        own guard must catch it.
        """
        db_path = tmp_path / "safe.duckdb"
        duckdb.connect(str(db_path)).close()

        adapter = DuckDBAdapter(
            source_config=DuckDBSourceConfig(type="duckdb"),
        )
        try:
            malicious = "myschema'; DROP TABLE x; --"
            with pytest.raises(ValueError, match="Invalid schema name"):
                adapter._create_duckdb_connection_from_config(
                    {
                        "type": "duckdb",
                        "path": str(db_path),
                        "schema": malicious,
                    },
                    cache_key=f"{db_path}\x00{malicious}",
                )
        finally:
            adapter.close()


class TestRuntimeMutatingSqlGuard:
    """The runtime guard is the security boundary — a malicious commit that
    slips a `DROP TABLE` past compile-time must still be refused before the
    warehouse driver is called. Tests pin the contract that mutating SQL never
    reaches `conn.execute` / `adapter.execute`, regardless of which adapter
    path runs.
    """

    def test_duckdb_path_never_calls_driver_for_mutating(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The driver `.execute()` must never see a mutating statement —
        the guard refuses DROP / DELETE / INSERT / UPDATE before the connection
        is touched, regardless of whether the warehouse would have errored too.
        """
        from unittest.mock import MagicMock, patch

        for sql in (
            "DROP TABLE users",
            "DELETE FROM users WHERE 1=1",
            "INSERT INTO logs VALUES (1)",
            "UPDATE users SET role='admin'",
        ):
            adapter = _ddb(local_project)
            try:
                mock_conn = MagicMock()
                with patch.object(
                    adapter, "_get_duckdb_connection_for_query", return_value=mock_conn
                ):
                    result = adapter._execute(_make_query(sql))
                assert result.error is not None, f"expected error for {sql!r}"
                assert "outside the read-only SQL allowlist" in str(result.error), (
                    f"expected guard message for {sql!r}, got {result.error!r}"
                )
                mock_conn.execute.assert_not_called()
            finally:
                adapter.close()

    def test_dbt_adapter_path_rejects_drop(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Mutating SQL routed via the dbt-adapter path is rejected before
        adapter.execute(). Mocks build_adapter() so no real warehouse is touched.
        """
        from unittest.mock import MagicMock, patch

        from dbt_charts.core.compile.models.source import PostgresSourceConfig

        adapter = _sql(local_project, profile_type="postgres")
        try:
            query = _make_query("DROP TABLE users")
            source_config = PostgresSourceConfig(
                type="postgres", host="h", dbname="d", user="u", password="p"
            )
            mock_dbt_adapter = MagicMock()
            with patch(
                "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
                return_value=mock_dbt_adapter,
            ):
                result = adapter._execute(query, source_config=source_config)
            assert result.error is not None
            assert "outside the read-only SQL allowlist" in str(result.error)
            mock_dbt_adapter.execute.assert_not_called()
        finally:
            adapter.close()

    def test_variable_branch_drop_rejected_at_runtime(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Spec's documented compile-time gap: a board whose post-resolution SQL
        is `DROP TABLE x` (compile-time picked the safe else branch) is rejected
        at runtime when the real branch resolves to DROP. The runtime closes
        the compile-time variable-branch gap.
        """
        from unittest.mock import MagicMock, patch

        adapter = _ddb(local_project)
        try:
            query = _make_query(
                "{% if kill %}DROP TABLE x{% else %}SELECT 1 AS a{% endif %}"
            )
            mock_conn = MagicMock()
            with patch.object(
                adapter, "_get_duckdb_connection_for_query", return_value=mock_conn
            ):
                result = adapter._execute(query, variables={"kill": True})
            assert result.error is not None
            assert "outside the read-only SQL allowlist" in str(result.error)
            mock_conn.execute.assert_not_called()
            # In the safe path the query still works end-to-end.
            result2 = adapter._execute(query, variables={"kill": False})
            assert result2.error is None
            assert result2.data == [{"a": 1}]
        finally:
            adapter.close()


class TestSnowflakeQmarkInlining:
    """Regression: Snowflake uses ? placeholders; _execute_via_dbt_adapter must
    inline Jinja variables as SQL literals with correct warehouse escaping.

    Native ?/$N/@paramN params no longer reach SqlAdapter — production params
    always arrive via Jinja {{ variable }} composition, so these tests use that
    path rather than the removed native-placeholder fallback.
    """

    def _render_via_jinja(
        self,
        local_project: Callable[..., FilesystemProject],
        sql: str,
        variables: dict,
        dialect: str = "snowflake",
    ) -> str:
        """Render a Jinja SQL template through prepare_sql; return the inlined SQL."""
        q = SqlQuery(sql=sql, source=dialect)
        adapter = _sql(local_project, profile_type=dialect)
        prepared = adapter.prepare_sql(q, variables=variables)
        assert not isinstance(prepared, QueryResult), prepared.error
        return prepared.sql

    def test_qmark_string_param_inlined(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE region = '{{ region }}'",
            {"region": "North"},
        )
        assert "'North'" in sql

    def test_qmark_int_param_inlined(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE days = {{ days }}",
            {"days": 30},
        )
        assert "30" in sql

    def test_qmark_multiple_params_inlined_in_order(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE region = '{{ region }}' AND days = {{ days }}",
            {"region": "North", "days": 7},
        )
        assert "'North'" in sql
        assert "7" in sql

    def test_qmark_none_becomes_null(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE x = {{ x }}",
            {"x": None},
        )
        assert "NULL" in sql

    def test_qmark_sql_injection_escaped(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE name = '{{ name }}'",
            {"name": "x'; DROP TABLE t; --"},
        )
        assert "DROP TABLE" in sql  # literal, not executed
        # Snowflake escapes a quote with a backslash; `''` there would end the
        # literal and start another one.
        assert r"'x\'; DROP TABLE t; --'" in sql

    def test_the_escaping_follows_the_warehouse_the_query_runs_on(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Warehouse dialect drives escaping, not the placeholder style.

        A test on the escaper alone cannot see an argument the caller never
        passes. Three dialects exercised so each escaping branch is covered
        independently: snowflake and bigquery use backslash escaping, postgres
        uses ANSI doubling.
        """
        payload = "\\' UNION ALL SELECT api_key, 2 FROM secrets --"
        jinja_sql = "SELECT * FROM t WHERE c = '{{ c }}'"

        on_snowflake = self._render_via_jinja(local_project, jinja_sql, {"c": payload})
        on_postgres = self._render_via_jinja(
            local_project, jinja_sql, {"c": payload}, dialect="postgres"
        )
        on_bigquery = self._render_via_jinja(
            local_project, jinja_sql, {"c": payload}, dialect="bigquery"
        )

        # endswith, not just `in` — nothing must dangle after the closing
        # quote. The row-limit ceiling never touches SQL text, so there is
        # nothing appended to strip off.
        assert on_snowflake.endswith(
            r"c = '\\\' UNION ALL SELECT api_key, 2 FROM secrets --'"
        )
        assert on_postgres.endswith(
            r"c = '\'' UNION ALL SELECT api_key, 2 FROM secrets --'"
        )
        assert on_bigquery.endswith(
            r"c = '\\\' UNION ALL SELECT api_key, 2 FROM secrets --'"
        )

    def test_a_quote_is_not_left_concatenating_on_the_named_param_branch(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The silent half, on BigQuery and Databricks.

        `''` on GoogleSQL ends one literal and starts the next, so the doubled
        spelling compares against `OBrien` with nothing raising anywhere. This
        fails if the named-param arm is handed any ANSI-quoting dialect.
        """
        sql = self._render_via_jinja(
            local_project,
            "SELECT * FROM t WHERE c = '{{ c }}'",
            {"c": "O'Brien"},
            dialect="bigquery",
        )

        assert sql.endswith(r"c = 'O\'Brien'")


class TestPlaceholderShapedLiterals:
    """Authored literals shaped like the warehouse's own placeholders must
    survive the render→inline round trip. Rendering on the variables path uses
    the internal collision-free placeholder style (INLINE_PLACEHOLDERS), so a
    `$1` inside a money string or a `?` inside prose is never mistaken for a
    parameter the renderer emitted."""

    def _execute_with_capture(
        self,
        local_project: Callable[..., FilesystemProject],
        sql: str,
        source_config: ResolvedSourceConfig,
        variables: dict | None = None,
        params: list | None = None,
    ) -> tuple:
        """Run SqlAdapter._execute with a mock warehouse; return (result, sent SQL)."""
        from unittest.mock import MagicMock, patch

        captured: list[str] = []

        mock_table = MagicMock()
        mock_table.column_names = ["x"]
        mock_table.rows = [("v",)]

        mock_adapter = MagicMock()
        mock_adapter.connection_named.return_value.__enter__ = lambda _mock_conn: None
        mock_adapter.connection_named.return_value.__exit__ = MagicMock(
            return_value=False
        )
        mock_adapter.execute.side_effect = lambda sql, **kw: (
            captured.append(sql),
            (None, mock_table),
        )[1]

        dialect = source_config.type
        adapter = _sql(local_project, profile_type=dialect)
        query = _make_query(sql, source=dialect)
        try:
            with patch(
                "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
                return_value=mock_adapter,
            ):
                result = adapter._execute(
                    query,
                    variables=variables,
                    params=params,
                    source_config=source_config,
                )
        finally:
            adapter.close()

        # captured[0] may be the connection-setup timeout SQL; captured[-1] is
        # the data query.
        return result, (captured[-1] if captured else "")

    def _postgres(self) -> object:
        from dbt_charts.core.compile.models.source import PostgresSourceConfig

        return PostgresSourceConfig(
            type="postgres", host="h", dbname="db", user="u", password="p"
        )

    def test_dollar_literal_survives_beside_a_variable(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result, sql = self._execute_with_capture(
            local_project,
            "SELECT '$1,000+' AS tier FROM orders WHERE region = '{{ region }}'",
            self._postgres(),
            variables={"region": "North"},
        )
        assert result.error is None, result.error
        assert "'$1,000+'" in sql
        assert "region = 'North'" in sql

    def test_dollar_literal_survives_with_no_variables(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Previously raised IndexError: Parameter index $1 out of range for 0 param(s).
        result, sql = self._execute_with_capture(
            local_project,
            "SELECT '$1,000+' AS tier FROM orders",
            self._postgres(),
        )
        assert result.error is None, result.error
        assert "'$1,000+'" in sql

    def test_qmark_literal_survives_on_snowflake(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.compile.models.source import SnowflakeSourceConfig

        result, sql = self._execute_with_capture(
            local_project,
            "SELECT 'why?' AS q FROM t WHERE region = '{{ region }}'",
            SnowflakeSourceConfig(
                type="snowflake", account="xy12345", user="u", database="db"
            ),
            variables={"region": "North"},
        )
        assert result.error is None, result.error
        assert "'why?'" in sql
        assert "region = 'North'" in sql

    def test_quoted_variable_inlines_as_single_quoted_literal(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # The author's quotes around '{{ region }}' must be stripped for the
        # inline placeholder style too — otherwise the value arrives
        # double-quoted (''North'').
        result, sql = self._execute_with_capture(
            local_project,
            "SELECT * FROM t WHERE region = '{{ region }}'",
            self._postgres(),
            variables={"region": "North"},
        )
        assert result.error is None, result.error
        assert "region = 'North'" in sql
        assert "''North''" not in sql

    def test_explicit_params_inline_in_the_adapters_declared_style(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Callers that pass params (the adapter-registry composition point)
        # pre-render with param_render_dialect — the internal inline style —
        # so a warehouse-shaped $1 in the SQL is an authored literal and
        # survives untouched.
        from dbt_charts.core.execute.sql_literals import INLINE_PLACEHOLDERS

        token = INLINE_PLACEHOLDERS.param(1)
        result, sql = self._execute_with_capture(
            local_project,
            f"SELECT '$1,000+' AS tier FROM t WHERE region = {token}",
            self._postgres(),
            params=["North"],
        )
        assert result.error is None, result.error
        assert "region = 'North'" in sql
        assert "'$1,000+'" in sql

    def test_escaping_follows_the_warehouse_dialect(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # BigQuery treats backslash as an escape inside string literals; the
        # inline round trip must escape for the warehouse, never for the
        # internal placeholder style.
        from dbt_charts.core.compile.models.source import BigQuerySourceConfig

        result, sql = self._execute_with_capture(
            local_project,
            "SELECT * FROM t WHERE path = '{{ p }}'",
            BigQuerySourceConfig(type="bigquery", project="proj", dataset="ds"),
            variables={"p": "a\\b"},
        )
        assert result.error is None, result.error
        assert "'a\\\\b'" in sql

    def test_dollar_literal_survives_with_empty_params_list(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Regression: the compose path sets params=[] (not None) even for
        # queries with no variables. An empty params list must NOT activate the
        # native-placeholder fallback — `$1` inside a string literal on postgres
        # would be mistaken for a placeholder with 0 actual params, raising
        # "Parameter index $1 out of range for 0 param(s)".
        result, sql = self._execute_with_capture(
            local_project,
            "SELECT '$1,000+' AS tier FROM orders",
            self._postgres(),
            params=[],
        )
        assert result.error is None, result.error
        assert "'$1,000+'" in sql

    def test_qmark_literal_survives_with_empty_params_list_on_snowflake(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # Regression: same compose-path empty-params bug on snowflake.
        # `SELECT 'Ready?' AS q` must not raise "1 ? placeholder(s) in SQL
        # but 0 param(s)" when params=[] arrives from composition.
        from dbt_charts.core.compile.models.source import SnowflakeSourceConfig

        result, sql = self._execute_with_capture(
            local_project,
            "SELECT 'Ready?' AS q FROM t",
            SnowflakeSourceConfig(
                type="snowflake", account="xy12345", user="u", database="db"
            ),
            params=[],
        )
        assert result.error is None, result.error
        assert "'Ready?'" in sql


class TestDbtAdapterColumnNameNormalization:
    """Regression: Snowflake returns uppercase column names (CONNECTION_NAME);
    dbt adapter results must normalize to lowercase so style.columns keys match."""

    def _execute_with_mock_columns(
        self,
        local_project: Callable[..., FilesystemProject],
        column_names: list[str],
        dialect: str = "snowflake",
    ):  # type: ignore[return]
        from unittest.mock import MagicMock, patch

        mock_table = MagicMock()
        mock_table.column_names = column_names
        mock_table.rows = [tuple("v" for _ in column_names)]

        mock_adapter = MagicMock()
        mock_adapter.connection_named.return_value.__enter__ = lambda _mock_conn: None
        mock_adapter.connection_named.return_value.__exit__ = MagicMock(
            return_value=False
        )
        mock_adapter.execute.return_value = (None, mock_table)

        src = _MIN_SOURCE.get(dialect, {"type": dialect})
        q = SqlQuery(sql="SELECT 1", source=dialect)
        adapter = _sql(local_project, profile_type=dialect)

        with patch(
            "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
            return_value=mock_adapter,
        ):
            prepared = adapter.prepare_sql(q, params=[])
            assert not isinstance(prepared, QueryResult), prepared.error
            return adapter._execute_via_dbt_adapter(prepared, q, src)

    def test_snowflake_uppercase_columns_normalized(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result = self._execute_with_mock_columns(
            local_project, ["CONNECTION_NAME", "STATUS"]
        )
        assert result.columns == ["connection_name", "status"]
        assert "connection_name" in result.data[0]
        assert "CONNECTION_NAME" not in result.data[0]

    def test_mixed_case_columns_normalized(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result = self._execute_with_mock_columns(
            local_project, ["MyCol", "UPPER", "lower"]
        )
        assert result.columns == ["mycol", "upper", "lower"]

    def test_already_lowercase_unchanged(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result = self._execute_with_mock_columns(local_project, ["region", "revenue"])
        assert result.columns == ["region", "revenue"]


class TestDbtProfileSourceCallsiteUsesDbpProjectPath:
    """DefaultSourceResolver._expand_dbt_profile uses dbt_project_path (not project root).

    The regression: dbt_profile expansion previously passed the dbt-charts project root
    to _read_target_dict. When the dbt project root differs from the dbt-charts project
    root, profiles.yml is only found under the dbt root, so the wrong path produces
    a FileNotFoundError. Expansion now lives in the resolver and is called with the
    DbtContext.dbt_project_path from _derive_dbt_context (set to DbtAdapter.dbt_project_path).
    """

    def test_profiles_resolved_from_dbt_project_path_not_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import textwrap

        from dbt_charts.core.compile.config import ProjectSourcesConfig
        from dbt_charts.core.execute.source_resolver import (  # noqa: I001
            DbtContext,
            DefaultSourceResolver,
        )

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

        # Two DIFFERENT directories: dbt_dir has profiles.yml; dbt_charts_dir does not.
        dbt_dir = tmp_path / "dbt"
        dbt_charts_dir = tmp_path / "dbt-charts"
        dbt_dir.mkdir()
        dbt_charts_dir.mkdir()

        # Minimal DuckDB profiles.yml that _read_target_dict can parse.
        (dbt_dir / "profiles.yml").write_text(
            textwrap.dedent(
                """\
                my_project:
                  target: dev
                  outputs:
                    dev:
                      type: duckdb
                      path: ':memory:'
            """
            )
        )

        resolver = DefaultSourceResolver()
        # DbtContext carries dbt_project_path=dbt_dir, not dbt_charts_dir.
        dbt_context = DbtContext(dbt_project_path=dbt_dir)
        result = resolver.resolve(
            authored={"type": "dbt_profile", "profile": "my_project"},
            board_sources={},
            project_sources=ProjectSourcesConfig(),
            dbt_context=dbt_context,
        )

        # If expansion uses dbt_charts_dir, profiles.yml isn't found and raises.
        # With the fix it resolves to DuckDBSourceConfig.
        assert result is not None
        assert result.type == "duckdb"
