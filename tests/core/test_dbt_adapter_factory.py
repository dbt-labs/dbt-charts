"""Tests for the dbt adapter factory and public connections API.

TDD: these tests are written before implementation to drive the shape.
"""

import pytest

from dbt_charts.core.compile.models.source import DuckDBSourceConfig


class TestBuildAdapter:
    """Tests for build_adapter(source_config) factory."""

    def test_duckdb_memory_returns_adapter(self) -> None:
        """build_adapter with duckdb :memory: returns a live adapter."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        with adapter.connection_named("test"):
            _, table = adapter.execute("SELECT 42 AS n", auto_begin=True, fetch=True)
        assert list(table.column_names) == ["n"]
        assert table.rows[0][0] == 42

    def test_unknown_type_raises_value_error(self) -> None:
        """Unknown adapter type raises ValueError immediately."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        with pytest.raises(ValueError, match="Unsupported"):
            build_adapter({"type": "oracle"})

    def test_missing_dbt_package_raises_import_error(self, monkeypatch) -> None:
        """Missing dbt adapter package raises ImportError with install hint."""
        import sys

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        # Force ImportError regardless of what's installed in the test environment
        monkeypatch.setitem(sys.modules, "dbt.adapters.postgres", None)
        monkeypatch.setitem(sys.modules, "dbt.adapters.postgres.connections", None)

        with pytest.raises(ImportError, match="dbt-postgres"):
            build_adapter(
                {
                    "type": "postgres",
                    "host": "localhost",
                    "dbname": "testdb",
                    "user": "u",
                    "password": "p",
                    "port": 5432,
                }
            )


class TestTrinoAdapter:
    """Trino wires through the generic dbt-trino seam (dbt-trino installed).

    Offline: adapter construction and macro load open no socket — a Trino
    connection is lazy (connection_named/execute). So build_adapter with
    register_macros=False constructs the real TrinoAdapter and lets us assert
    on the credentials the factory selected, with no live cluster.
    """

    def test_map_entry_names_dbt_trino(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
            _ADAPTER_TYPE_MAP,
        )

        assert _ADAPTER_TYPE_MAP["trino"] == (
            "dbt.adapters.trino",
            "TrinoAdapter",
            "TrinoCredentialsFactory",
        )

    def test_build_adapter_selects_none_credentials(self) -> None:
        """method absent → TrinoCredentialsFactory dispatches to TrinoNoneCredentials."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "trino",
                "host": "trino.example.com",
                "port": 8080,
                "user": "analyst",
                "database": "hive",
                "schema": "analytics",
            },
            register_macros=False,
        )
        creds = adapter.config.credentials
        assert type(creds).__name__ == "TrinoNoneCredentials"
        assert creds.type == "trino"
        assert creds.database == "hive"
        assert creds.schema == "analytics"

    def test_catalog_alias_addresses_the_trino_catalog(self) -> None:
        """`catalog` and `database` both name the Trino catalog (dbt-trino alias)."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "trino",
                "host": "trino.example.com",
                "port": 8080,
                "user": "analyst",
                "catalog": "hive",
                "schema": "analytics",
            },
            register_macros=False,
        )
        assert adapter.config.credentials.database == "hive"


class TestAthenaAdapter:
    """Athena wires through the generic dbt-athena seam (dbt-athena installed).

    Offline: adapter construction opens no socket — build_adapter with
    register_macros=False constructs the real AthenaAdapter and lets us assert
    on the credentials the factory selected, with no live cluster.
    """

    def test_map_entry_names_dbt_athena(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
            _ADAPTER_TYPE_MAP,
        )

        assert _ADAPTER_TYPE_MAP["athena"] == (
            "dbt.adapters.athena",
            "AthenaAdapter",
            "AthenaCredentials",
        )

    def test_build_adapter_constructs_athena_credentials(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "athena",
                "s3_staging_dir": "s3://my-bucket/staging/",
                "region_name": "us-east-1",
                "database": "awsdatacatalog",
                "schema": "analytics",
            },
            register_macros=False,
        )
        creds = adapter.config.credentials
        assert type(creds).__name__ == "AthenaCredentials"
        assert creds.type == "athena"
        assert creds.database == "awsdatacatalog"
        assert creds.schema == "analytics"

    def test_catalog_alias_addresses_the_athena_database(self) -> None:
        """`catalog` and `database` both name the Athena Glue catalog (dbt-athena alias)."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "athena",
                "s3_staging_dir": "s3://my-bucket/staging/",
                "region_name": "us-east-1",
                "catalog": "awsdatacatalog",
                "schema": "analytics",
            },
            register_macros=False,
        )
        assert adapter.config.credentials.database == "awsdatacatalog"


class TestTestConnection:
    """Tests for the public dct.connections.test_connection() API."""

    def test_duckdb_memory_succeeds(self) -> None:
        """test_connection with DuckDBSourceConfig :memory: returns (True, message)."""
        from dbt_charts.core.connections import test_connection

        ok, msg = test_connection(DuckDBSourceConfig(type="duckdb", path=":memory:"))
        assert ok is True
        assert "successful" in msg.lower() or len(msg) > 0

    def test_bad_path_returns_false(self) -> None:
        """test_connection with an uncreateable path returns (False, error_message)."""
        from dbt_charts.core.connections import test_connection

        ok, msg = test_connection(
            DuckDBSourceConfig(
                type="duckdb",
                path="/nonexistent/path/that/cannot/be/created/db.duckdb",
            )
        )
        assert ok is False
        assert len(msg) > 0


class TestBigQueryMethodInference:
    """build_adapter injects the dbt-bigquery method field when absent."""

    def _make_fake_bq_module(self, received: dict):
        from types import SimpleNamespace

        class FakeCreds:
            _ALIASES: dict = {"project": "database", "dataset": "schema"}

            @classmethod
            def translate_aliases(cls, d: dict) -> dict:
                return {cls._ALIASES.get(k, k): v for k, v in d.items()}

            @classmethod
            def from_dict(cls, d: dict) -> "FakeCreds":
                received.update(d)
                return cls()

        class FakeAdapter:
            def __init__(self, cfg, _mp_ctx) -> None:
                self.config = cfg
                # build_adapter attaches the attribution query header here, the
                # same slot a real dbt adapter exposes.
                self.connections = SimpleNamespace()

        return SimpleNamespace(
            BigQueryAdapter=FakeAdapter, BigQueryCredentials=FakeCreds
        )

    def _patch_bq(self, monkeypatch, mod):
        import sys

        from dbt_charts.core.execute.adapters import dbt_adapter_factory

        monkeypatch.setitem(sys.modules, "dbt.adapters.bigquery", mod)
        monkeypatch.setitem(
            dbt_adapter_factory._ADAPTER_TYPE_MAP,
            "bigquery",
            ("dbt.adapters.bigquery", "BigQueryAdapter", "BigQueryCredentials"),
        )
        monkeypatch.setattr(
            dbt_adapter_factory, "_bootstrap_macros", lambda adapter, t: None
        )

    def test_no_keyfile_injects_oauth(self, monkeypatch) -> None:
        """build_adapter injects method='oauth' when no keyfile fields are present."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_bq(monkeypatch, self._make_fake_bq_module(received))
        build_adapter({"type": "bigquery", "project": "p", "dataset": "ds"})
        assert received.get("method") == "oauth"

    def test_keyfile_json_injects_service_account_json(self, monkeypatch) -> None:
        """build_adapter injects method='service-account-json' when keyfile_json set."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_bq(monkeypatch, self._make_fake_bq_module(received))
        build_adapter(
            {
                "type": "bigquery",
                "project": "p",
                "dataset": "ds",
                "keyfile_json": {"type": "service_account"},
            }
        )
        assert received.get("method") == "service-account-json"

    def test_keyfile_path_injects_service_account(self, monkeypatch) -> None:
        """build_adapter injects method='service-account' when keyfile path set."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_bq(monkeypatch, self._make_fake_bq_module(received))
        build_adapter(
            {
                "type": "bigquery",
                "project": "p",
                "dataset": "ds",
                "keyfile": "/creds/key.json",
            }
        )
        assert received.get("method") == "service-account"

    def test_explicit_method_not_overridden(self, monkeypatch) -> None:
        """build_adapter preserves an explicit method already in source_config."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_bq(monkeypatch, self._make_fake_bq_module(received))
        build_adapter(
            {"type": "bigquery", "project": "p", "dataset": "ds", "method": "oauth"}
        )
        assert received.get("method") == "oauth"


class TestProfileAliases:
    """Profile-style keys are translated to canonical credentials fields."""

    def test_translates_profile_aliases_before_construction(self, monkeypatch) -> None:
        """build_adapter calls translate_aliases then from_dict.

        Credentials classes with _ALIASES (e.g. BigQuery's project→database)
        must be translated before construction; direct __init__ bypasses them.
        """
        import sys
        from types import SimpleNamespace

        from dbt_charts.core.execute.adapters import dbt_adapter_factory
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}

        class FakeCreds:
            _ALIASES: dict = {"project": "database", "dataset": "schema"}

            @classmethod
            def translate_aliases(cls, d: dict) -> dict:
                return {cls._ALIASES.get(k, k): v for k, v in d.items()}

            @classmethod
            def from_dict(cls, d: dict) -> "FakeCreds":
                received.update(d)
                return cls()

        class FakeAdapter:
            def __init__(self, cfg, _mp_ctx) -> None:
                self.config = cfg
                # build_adapter attaches the attribution query header here, the
                # same slot a real dbt adapter exposes.
                self.connections = SimpleNamespace()

        fake_mod = SimpleNamespace(FakeAdapter=FakeAdapter, FakeCreds=FakeCreds)
        monkeypatch.setitem(sys.modules, "dbt.adapters.fakewh", fake_mod)
        monkeypatch.setitem(
            dbt_adapter_factory._ADAPTER_TYPE_MAP,
            "fakewh",
            ("dbt.adapters.fakewh", "FakeAdapter", "FakeCreds"),
        )
        # FakeAdapter doesn't implement dbt-core's macro API; this test only
        # exercises the credentials-translation path before adapter construction.
        monkeypatch.setattr(
            dbt_adapter_factory, "_bootstrap_macros", lambda adapter, adapter_type: None
        )

        build_adapter({"type": "fakewh", "project": "myproject", "dataset": "myds"})

        assert received.get("database") == "myproject"
        assert received.get("schema") == "myds"
        assert "project" not in received
        assert "dataset" not in received

    def test_tolerates_profile_level_keys_like_threads(self) -> None:
        """build_adapter silently drops profile-level keys (e.g. threads).

        threads is not a credentials field; ExtensibleDbtClassMixin.from_dict
        drops it rather than crashing.
        """
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        # Must not raise TypeError for unexpected keyword argument 'threads'
        build_adapter({"type": "duckdb", "path": ":memory:", "threads": 4})


class TestSchemaDefault:
    """build_adapter injects warehouse-conventional schema defaults when absent.

    dbt's Credentials base class requires both `database` and `schema` as str.
    dbt-charts' source config models define defaults (e.g. "PUBLIC" for Snowflake)
    but the registry stores raw dicts that bypass Pydantic.  build_adapter must
    inject the same defaults so schema-discovery commands work without requiring
    the user to know a schema upfront.
    """

    def _make_fake_snowflake_module(self, received: dict):
        from types import SimpleNamespace

        class FakeCreds:
            _ALIASES: dict = {}

            @classmethod
            def translate_aliases(cls, d: dict) -> dict:
                return dict(d)

            @classmethod
            def from_dict(cls, d: dict) -> "FakeCreds":
                received.update(d)
                return cls()

        class FakeAdapter:
            def __init__(self, cfg, _mp_ctx) -> None:
                self.config = cfg
                # build_adapter attaches the attribution query header here, the
                # same slot a real dbt adapter exposes.
                self.connections = SimpleNamespace()

        return SimpleNamespace(
            SnowflakeAdapter=FakeAdapter, SnowflakeCredentials=FakeCreds
        )

    def _patch_snowflake(self, monkeypatch, mod):
        import sys

        from dbt_charts.core.execute.adapters import dbt_adapter_factory

        monkeypatch.setitem(sys.modules, "dbt.adapters.snowflake", mod)
        monkeypatch.setitem(
            dbt_adapter_factory._ADAPTER_TYPE_MAP,
            "snowflake",
            (
                "dbt.adapters.snowflake",
                "SnowflakeAdapter",
                "SnowflakeCredentials",
            ),
        )
        monkeypatch.setattr(
            dbt_adapter_factory, "_bootstrap_macros", lambda adapter, t: None
        )

    def test_snowflake_without_schema_injects_public(self, monkeypatch) -> None:
        """build_adapter injects schema='PUBLIC' for Snowflake when absent.

        Regression: schema introspection of <source> failed with
        'Field "schema" of type str is missing in SnowflakeCredentials instance'
        when the user's dbt_charts.yml omitted schema (reasonable — they are
        trying to discover what schemas exist).
        """
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_snowflake(monkeypatch, self._make_fake_snowflake_module(received))
        build_adapter(
            {
                "type": "snowflake",
                "account": "xy12345",
                "user": "me",
                "database": "MY_DB",
                "warehouse": "MY_WH",
                # schema intentionally absent
            }
        )
        assert received.get("schema") == "PUBLIC"

    def test_snowflake_explicit_schema_not_overridden(self, monkeypatch) -> None:
        """build_adapter preserves an explicit schema in the Snowflake source config."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch_snowflake(monkeypatch, self._make_fake_snowflake_module(received))
        build_adapter(
            {
                "type": "snowflake",
                "account": "xy12345",
                "user": "me",
                "database": "MY_DB",
                "warehouse": "MY_WH",
                "schema": "ANALYTICS",
            }
        )
        assert received.get("schema") == "ANALYTICS"


class TestTempDirCleanup:
    """Temp target directories are cleaned up when the adapter is GC'd."""

    def test_target_dir_removed_after_gc(self, tmp_path) -> None:
        """build_adapter() temp dir is deleted when the adapter is GC'd.

        Regression: the old WeakKeyDictionary tracking only recorded the path
        but never wired up cleanup — the directory was orphaned on GC.
        """
        import gc
        from pathlib import Path

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        db_path = str(tmp_path / "cleanup_test.duckdb")
        adapter = build_adapter({"type": "duckdb", "path": db_path})

        # Grab the target_path before we lose the reference
        target_path = Path(adapter.config.target_path)
        assert target_path.exists(), "target_path should exist immediately after build"

        del adapter
        gc.collect()

        assert not target_path.exists(), (
            f"Temp dir {target_path} was not cleaned up after adapter GC. "
            "weakref.finalize must be wired in build_adapter."
        )


class TestFactorySlotIsolation:
    """Two same-type build_adapter calls must not cross-resolve during introspection.

    build_adapter binds each adapter's macro context to the adapter that built it,
    so a second same-type build_adapter cannot clobber the first's schema
    introspection through dbt's type-keyed global FACTORY slot (the gthread
    cross-tenant leak).
    """

    def test_second_adapter_does_not_clobber_first_introspection(
        self, tmp_path
    ) -> None:
        """adapter_a.list_relations must read tenant A's catalog, not tenant B's.

        Two file-backed DuckDBs with distinct tables. Building B second makes B the
        last writer of the type-keyed FACTORY slot, so before the fix adapter_a's
        macro-dispatched introspection (the path ConnectionService.get_schema uses)
        resolves through that shared slot to adapter_b's connection — reading the
        wrong tenant, or crashing on a connection adapter_a never opened.
        """
        import duckdb
        from dbt.adapters.factory import FACTORY

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        path_a = tmp_path / "a.duckdb"
        path_b = tmp_path / "b.duckdb"
        for path, table in ((path_a, "only_in_a"), (path_b, "only_in_b")):
            setup = duckdb.connect(str(path))
            setup.execute(f"CREATE TABLE {table} (n INTEGER)")
            setup.close()

        adapter_a = build_adapter({"type": "duckdb", "path": str(path_a)})
        adapter_b = build_adapter({"type": "duckdb", "path": str(path_b)})
        try:
            # DuckDB's list_relations filters on catalog name = the file stem.
            with adapter_a.connection_named("introspect_a"):
                rels_a = [r.identifier for r in adapter_a.list_relations("a", "main")]
            with adapter_b.connection_named("introspect_b"):
                rels_b = [r.identifier for r in adapter_b.list_relations("b", "main")]
            assert rels_a == ["only_in_a"]
            assert rels_b == ["only_in_b"]
        finally:
            # Defensive: if the fix regresses to a persistent slot write, drop it
            # so this process-global state does not leak into sibling tests.
            with FACTORY.lock:
                FACTORY.adapters.pop("duckdb", None)


class TestReadOnlyDuckDB:
    """read_only=True opens file-backed DuckDB without an exclusive write lock."""

    def test_read_only_coexists_with_existing_reader(self, tmp_path) -> None:
        """Regression: schema drill-down must not require exclusive lock (2026-05-06)."""
        import duckdb

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        db_path = tmp_path / "warehouse.duckdb"
        setup = duckdb.connect(str(db_path))
        setup.execute("CREATE TABLE t (id INTEGER)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.close()

        ro_conn = duckdb.connect(
            str(db_path),
            read_only=True,
            config={"enable_external_access": False},
        )
        try:
            adapter = build_adapter(
                {"type": "duckdb", "path": str(db_path)}, read_only=True
            )
            with adapter.connection_named("dct_schema_list_relations"):
                _, table = adapter.execute(
                    "SELECT id FROM t", auto_begin=True, fetch=True
                )
            assert list(table.column_names) == ["id"]
            assert table.rows[0][0] == 1
        finally:
            ro_conn.close()

    def test_adapter_config_exposes_flags_dict(self) -> None:
        """Regression: Snowflake's list_relations_without_caching macro calls
        adapter.config.flags.get(...). The cfg SimpleNamespace must expose
        a flags dict so the macro can read optional paging config."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        flags = adapter.config.flags
        assert isinstance(flags, dict)
        # .get() must work and return the default for unknown keys
        assert flags.get("list_relations_per_page", 9999) == 9999
        assert flags.get("list_relations_page_limit", 42) == 42

    def test_duckdb_config_source_field_flows_to_dbt_creds(self, tmp_path) -> None:
        """Regression: LayeredSchemaResolver / dbt-charts-super-schema pass
        dbt-chart-shaped source configs (with `duckdb_config:`) to build_adapter.
        dbt-duckdb's creds class names the same field `config_options`. Without
        the rename, the value is dropped and the patched read-only initialize_db
        opens with the default `enable_external_access=False` — which conflicts
        with a sibling read-only connection opened with `enable_external_access=True`
        on the same file (the playground SqlAdapter posture).
        """
        import duckdb

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        db_path = tmp_path / "warehouse.duckdb"
        setup = duckdb.connect(str(db_path))
        setup.execute("CREATE TABLE t (id INTEGER)")
        setup.execute("INSERT INTO t VALUES (1)")
        setup.close()

        holder = duckdb.connect(
            str(db_path),
            read_only=True,
            config={"enable_external_access": True},
        )
        try:
            adapter = build_adapter(
                {
                    "type": "duckdb",
                    "path": str(db_path),
                    "duckdb_config": {"enable_external_access": True},
                },
                read_only=True,
            )
            with adapter.connection_named("dct_schema_inspect"):
                _, table = adapter.execute(
                    "SELECT id FROM t", auto_begin=True, fetch=True
                )
            assert list(table.column_names) == ["id"]
            assert table.rows[0][0] == 1
        finally:
            holder.close()


class TestQueryHeaderWiring:
    """The seam that makes attribution reach the warehouse at all.

    dbt-bigquery reads labels off `query_header` only when `query_comment.job_label`
    is set, and `_add_query_comment` is a no-op when `query_header` is None — so an
    adapter built without both silently sends unattributed queries.
    """

    def test_job_label_is_enabled(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        assert adapter.config.query_comment.job_label is True

    def test_query_header_is_attached(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        assert adapter.connections.query_header is not None

    def test_non_bigquery_emits_the_comment(self) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        assert adapter.connections.query_header.add("SELECT 1").startswith("/*")

    def test_bigquery_leaves_the_sql_untouched(self, monkeypatch) -> None:
        """BigQuery takes labels only; a comment would change the query text its
        free result cache keys on."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        bq = TestBigQueryMethodInference()
        bq._patch_bq(monkeypatch, bq._make_fake_bq_module({}))
        adapter = build_adapter({"type": "bigquery", "project": "p", "dataset": "ds"})
        header = adapter.connections.query_header
        assert header.add("SELECT 1") == "SELECT 1"
        assert header.comment.query_comment  # labels still populated

    def test_databricks_keeps_its_header_after_the_manager_swap(self) -> None:
        """build_adapter replaces the Databricks connection manager wholesale, and a
        fresh manager starts with query_header=None — so attaching before the swap
        silently drops attribution for every Databricks query."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "databricks",
                "host": "tenant.cloud.databricks.com",
                "http_path": "/sql/1.0/warehouses/abc",
                "schema": "analytics",
                "token": "tok",
            },
            register_macros=False,
        )
        assert adapter.connections.query_header is not None


class TestNativeAttributionCredential:
    """The warehouse's own identity field is filled at connect — but never over an
    authored value, which may drive real monitoring or governance rules."""

    def _fake_snowflake(self, received: dict):
        from types import SimpleNamespace

        class FakeCreds:
            @classmethod
            def translate_aliases(cls, d: dict) -> dict:
                return d

            @classmethod
            def from_dict(cls, d: dict) -> "FakeCreds":
                received.update(d)
                return cls()

        class FakeAdapter:
            def __init__(self, cfg, _mp_ctx) -> None:
                self.config = cfg
                self.connections = SimpleNamespace()

        return SimpleNamespace(
            SnowflakeAdapter=FakeAdapter, SnowflakeCredentials=FakeCreds
        )

    def _patch(self, monkeypatch, mod):
        import sys

        from dbt_charts.core.execute.adapters import dbt_adapter_factory

        monkeypatch.setitem(sys.modules, "dbt.adapters.snowflake", mod)
        monkeypatch.setitem(
            dbt_adapter_factory._ADAPTER_TYPE_MAP,
            "snowflake",
            ("dbt.adapters.snowflake", "SnowflakeAdapter", "SnowflakeCredentials"),
        )
        monkeypatch.setattr(
            dbt_adapter_factory, "_bootstrap_macros", lambda adapter, t: None
        )

    def test_fills_an_unset_query_tag(self, monkeypatch) -> None:
        import json

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch(monkeypatch, self._fake_snowflake(received))
        build_adapter({"type": "snowflake", "account": "xy"})
        assert json.loads(received["query_tag"])["app"] == "dbt-charts"

    def test_leaves_an_authored_query_tag_alone(self, monkeypatch) -> None:
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch(monkeypatch, self._fake_snowflake(received))
        build_adapter(
            {"type": "snowflake", "account": "xy", "query_tag": "cost-center-42"}
        )
        assert received["query_tag"] == "cost-center-42"

    def test_treats_an_explicit_none_as_unset(self, monkeypatch) -> None:
        """model_dump emits unset optional fields as None, not as missing keys."""
        import json

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch(monkeypatch, self._fake_snowflake(received))
        build_adapter({"type": "snowflake", "account": "xy", "query_tag": None})
        assert json.loads(received["query_tag"])["app"] == "dbt-charts"

    def test_a_compiled_postgres_source_reports_dbt_charts(self, monkeypatch) -> None:
        """The compiled path dumps every field, so application_name arrives as an
        explicit None rather than a missing key — this is the path that decides what
        pg_stat_activity actually shows, and it used to say "dbt"."""
        import sys
        from types import SimpleNamespace

        from dbt_charts.core.compile.models.source import PostgresSourceConfig
        from dbt_charts.core.execute.adapters import dbt_adapter_factory
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}

        class FakeCreds:
            @classmethod
            def translate_aliases(cls, d: dict) -> dict:
                return d

            @classmethod
            def from_dict(cls, d: dict) -> "FakeCreds":
                received.update(d)
                return cls()

        class FakeConnectionManager:
            """Postgres adapters get their connection manager swapped for one that
            opens in autocommit, so the stand-in has to expose the class to derive."""

            def __init__(self, _cfg, _mp_ctx) -> None:
                pass

        class FakeAdapter:
            ConnectionManager = FakeConnectionManager

            def __init__(self, cfg, _mp_ctx) -> None:
                self.config = cfg
                self.connections = SimpleNamespace()

        monkeypatch.setitem(
            sys.modules,
            "dbt.adapters.postgres",
            SimpleNamespace(PostgresAdapter=FakeAdapter, PostgresCredentials=FakeCreds),
        )
        monkeypatch.setattr(
            dbt_adapter_factory, "_bootstrap_macros", lambda adapter, t: None
        )
        source = PostgresSourceConfig(
            type="postgres", host="h", dbname="db", user="u", password="p"
        )
        build_adapter(source.model_dump(by_alias=True))
        assert received["application_name"] == "dbt-charts"

    def test_carries_no_authored_attribution(self, monkeypatch) -> None:
        """A pooled connection is shared by sources with different attribution, so a
        team on the credential would mislabel whichever source did not build it."""
        import json

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        received: dict = {}
        self._patch(monkeypatch, self._fake_snowflake(received))
        build_adapter(
            {"type": "snowflake", "account": "xy", "attribution": {"team": "finance"}}
        )
        assert "team" not in json.loads(received["query_tag"])


class TestReadsOutsideATransaction:
    """No connection dbt-charts builds may sit in an open transaction between queries.

    psycopg2 opens one on the first statement unless the handle is in autocommit mode,
    and no read path ever commits — so a pooled connection stayed `idle in transaction`
    holding AccessShareLock on every table it had read, blocking the ACCESS EXCLUSIVE
    that a dbt table rebuild needs until the pool was closed.

    These drive the real dbt-postgres classes, not a stand-in for them: the first cut
    of this fix passed an `autocommit` credential, which every dbt-postgres before 1.11
    drops on the floor, and a mocked credentials class reported that as a pass.
    """

    def test_postgres_adapters_read_in_autocommit(self) -> None:
        """The real PostgresAdapter, so a rename of dbt's own connection-manager
        attribute surfaces here rather than at a customer's warehouse."""
        from dbt.adapters.postgres import PostgresAdapter

        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter
        from dbt_charts.core.execute.adapters.postgres_connection_manager import (
            autocommit_connections,
        )

        adapter = build_adapter(
            {
                "type": "postgres",
                "host": "h",
                "port": 5432,
                "dbname": "db",
                "user": "u",
                "password": "p",
            },
            register_macros=False,
        )
        assert isinstance(
            adapter.connections,
            autocommit_connections(PostgresAdapter.ConnectionManager),
        )

    def test_opening_a_connection_puts_the_handle_in_autocommit(self) -> None:
        """dbt-postgres < 1.11 has no autocommit credential at all, so the guarantee
        has to hold at the driver handle rather than in the profile we hand dbt."""
        from types import SimpleNamespace

        from dbt_charts.core.execute.adapters.postgres_connection_manager import (
            autocommit_connections,
        )

        calls: list[str] = []
        handle = SimpleNamespace(autocommit=False)
        handle.commit = lambda: calls.append("commit")
        handle.rollback = lambda: calls.append("rollback")

        class Base:
            @classmethod
            def open(cls, connection):
                return SimpleNamespace(handle=handle)

        result = autocommit_connections(Base).open(SimpleNamespace())

        assert result.handle.autocommit is True
        # `set role` at connect leaves a transaction psycopg2 will not switch out of,
        # and rolling it back reverts the connection to the login role — verified
        # against a live server, where a rollback here reported current_role as the
        # profile's user instead of its role.
        assert calls == ["commit"]

    def test_redshift_connects_in_autocommit_even_when_a_profile_says_otherwise(
        self,
    ) -> None:
        """Redshift is Postgres's fork and shares its lock model, but it reaches the
        driver through the credential rather than the handle: dbt-redshift only sets
        autocommit `if credentials.autocommit`. An authored `false` would pin
        AccessShareLock exactly the way the Postgres bug did, and on a read-only
        connection that is all it can do."""
        from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

        adapter = build_adapter(
            {
                "type": "redshift",
                "host": "h",
                "port": 5439,
                "dbname": "db",
                "user": "u",
                "password": "p",
                "autocommit": False,
            },
            register_macros=False,
        )
        assert adapter.config.credentials.autocommit is True

    def test_a_redshift_source_cannot_author_autocommit(self) -> None:
        """`autocommit` isn't in the authored surface at all: extra='forbid' rejects
        it outright rather than accepting a value that `build_adapter` then silently
        overrides. It has no `true` spelling either, since the field can't vary, so
        there is nothing for an author to configure."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.source import RedshiftSourceConfig

        with pytest.raises(ValidationError):
            RedshiftSourceConfig(
                type="redshift",
                host="h",
                dbname="db",
                user="u",
                password="p",
                autocommit=False,
            )

        RedshiftSourceConfig(
            type="redshift", host="h", dbname="db", user="u", password="p"
        )
