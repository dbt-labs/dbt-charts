"""Tests for core source/database detection functions.

Tests detect_dbt_database_type fallback_type, detect_database_type_from_registry,
and detect_dbt_connection_string — all in dbt_charts.core.compile.sources.detection.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject


class TestDetectDbtDatabaseTypeFallback:
    """Tests for detect_dbt_database_type with fallback_type parameter."""

    def test_fallback_type_used_when_profile_missing(self, tmp_path: Path) -> None:
        """When profile not found but file exists, fallback_type is returned."""
        from dbt_charts.core.compile.sources.detection import detect_dbt_database_type

        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "other_profile:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        )

        result = detect_dbt_database_type(
            profiles, "nonexistent_profile", "dev", fallback_type="duckdb"
        )
        assert result is not None
        assert result["type"] == "duckdb"
        assert result["engine"] == "DuckDB"

    def test_fallback_type_not_used_when_detection_succeeds(
        self, tmp_path: Path
    ) -> None:
        """When detection succeeds, fallback_type is ignored."""
        from dbt_charts.core.compile.sources.detection import detect_dbt_database_type

        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "my_project:\n  target: dev\n  outputs:\n    dev:\n      type: postgresql\n"
        )

        result = detect_dbt_database_type(
            profiles, "my_project", "dev", fallback_type="duckdb"
        )
        assert result is not None
        assert result["type"] == "postgresql"

    def test_fallback_type_none_when_file_missing(self, tmp_path: Path) -> None:
        """When profiles file doesn't exist, returns None even with fallback_type."""
        from dbt_charts.core.compile.sources.detection import detect_dbt_database_type

        result = detect_dbt_database_type(
            tmp_path / "nonexistent.yml", "profile", "dev", fallback_type="duckdb"
        )
        assert result is None

    def test_fallback_type_none_by_default(self, tmp_path: Path) -> None:
        """Without fallback_type, missing profile returns None (backward compat)."""
        from dbt_charts.core.compile.sources.detection import detect_dbt_database_type

        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "other:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        )

        result = detect_dbt_database_type(profiles, "missing", "dev")
        assert result is None

    def test_fallback_type_invalid_returns_basic_info(self, tmp_path: Path) -> None:
        """Fallback with unrecognized type returns basic info dict."""
        from dbt_charts.core.compile.sources.detection import detect_dbt_database_type

        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "other:\n  target: dev\n  outputs:\n    dev:\n      type: x\n"
        )

        result = detect_dbt_database_type(
            profiles, "missing", "dev", fallback_type="exotic_db"
        )
        assert result is not None
        assert result["type"] == "exotic_db"


class TestDetectDatabaseTypeFromRegistry:
    """Tests for detect_database_type_from_registry."""

    def test_dbt_adapter_with_profiles(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Detects database type from DbtAdapter with profiles.yml."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        # Create profiles.yml
        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "my_project:\n  target: dev\n  outputs:\n    dev:\n      type: postgresql\n"
        )

        adapter = MagicMock(spec=DbtAdapter)
        adapter.dbt_project_path = tmp_path
        adapter.profile_name = "my_project"
        adapter.target_name = "dev"
        adapter.supported_types = {"sql"}

        registry = AdapterRegistry(project=local_project(tmp_path))
        registry.register(adapter)

        result = detect_database_type_from_registry(registry)
        assert result["type"] == "postgresql"

    def test_sql_adapter_with_dbt_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Detects database type from SqlAdapter with dbt_project_path."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        profiles = tmp_path / "profiles.yml"
        profiles.write_text(
            "default:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        )

        adapter = MagicMock(spec=SqlAdapter)
        adapter.dbt_project_path = tmp_path
        adapter.supported_types = {"sql"}

        registry = AdapterRegistry(project=local_project(tmp_path))
        registry.register(adapter)

        result = detect_database_type_from_registry(registry)
        assert result["type"] == "duckdb"

    def test_no_sql_adapters(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Returns 'none' type when no SQL adapters registered."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry

        registry = AdapterRegistry(project=local_project(Path()))
        result = detect_database_type_from_registry(registry)
        assert result["type"] == "none"

    def test_sql_adapter_without_dbt_path_unrecognized_profile_type(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Returns 'unknown' type for SqlAdapter with an unrecognized profile_type."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        adapter = MagicMock(spec=SqlAdapter)
        adapter.dbt_project_path = None
        adapter.profile_type = "not_a_real_engine"
        adapter.supported_types = {"sql"}

        registry = AdapterRegistry(project=local_project(Path()))
        registry.register(adapter)

        result = detect_database_type_from_registry(registry)
        assert result["type"] == "unknown"

    def test_sql_adapter_without_dbt_path_falls_back_to_profile_type(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Reports the real engine from adapter.profile_type when there's no dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        adapter = MagicMock(spec=SqlAdapter)
        adapter.dbt_project_path = None
        adapter.profile_type = "duckdb"
        adapter.supported_types = {"sql"}

        registry = AdapterRegistry(project=local_project(Path()))
        registry.register(adapter)

        result = detect_database_type_from_registry(registry)
        assert result["type"] == "duckdb"

    def test_sql_adapter_without_dbt_path_resolves_profile_type_alias(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Resolves profile_type aliases (e.g. 'postgres' -> 'postgresql') via get_database_info."""
        from dbt_charts.core.compile.sources.detection import (
            detect_database_type_from_registry,
        )
        from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
        from dbt_charts.core.execute.adapters.sql_adapter import SqlAdapter

        adapter = MagicMock(spec=SqlAdapter)
        adapter.dbt_project_path = None
        adapter.profile_type = "postgres"
        adapter.supported_types = {"sql"}

        registry = AdapterRegistry(project=local_project(Path()))
        registry.register(adapter)

        result = detect_database_type_from_registry(registry)
        assert result["type"] == "postgresql"


class TestDetectDbtConnectionString:
    """Tests for detect_dbt_connection_string."""

    def test_detects_duckdb(self, tmp_path: Path) -> None:
        """Detects DuckDB connection from dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_proj\n")
        (tmp_path / "profiles.yml").write_text(
            "test_proj:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: data.db\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "duckdb"
        assert conn_str == str(tmp_path / "data.db")

    def test_renders_env_var_in_duckdb_path(self, tmp_path: Path) -> None:
        """dbt env_var() templating in the path must be rendered, not passed literally."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_proj\n")
        (tmp_path / "profiles.yml").write_text(
            "test_proj:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
            "      path: \"{{ env_var('NONEXISTENT_DB_PATH_VAR', 'dundersign.duckdb') }}\"\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "duckdb"
        # env_var default 'dundersign.duckdb' rendered + made absolute — NOT the
        # literal '{{ env_var(...) }}' string.
        assert "{{" not in conn_str
        assert conn_str == str(tmp_path / "dundersign.duckdb")

    def test_missing_env_var_without_default_raises(self, tmp_path: Path) -> None:
        """A profile referencing an unset env_var() with no default fails loud.

        This is deliberate: a misconfigured profile is a real error, not something
        to silently guess around. The ValueError escapes the function's usual
        None-returning contract on purpose.
        """
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_proj\n")
        (tmp_path / "profiles.yml").write_text(
            "test_proj:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
            "      path: \"{{ env_var('DEFINITELY_UNSET_DB_PATH_VAR') }}\"\n"
        )

        with pytest.raises(ValueError, match="DEFINITELY_UNSET_DB_PATH_VAR"):
            detect_dbt_connection_string(tmp_path)

    def test_detects_duckdb_memory(self, tmp_path: Path) -> None:
        """Detects DuckDB :memory: connection."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_proj\n")
        (tmp_path / "profiles.yml").write_text(
            "test_proj:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: ':memory:'\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "duckdb"
        assert conn_str == ":memory:"

    def test_detects_postgres(self, tmp_path: Path) -> None:
        """Detects PostgreSQL connection from dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_pg\n")
        (tmp_path / "profiles.yml").write_text(
            "test_pg:\n  target: dev\n  outputs:\n    dev:\n"
            "      type: postgres\n      host: localhost\n"
            "      port: 5432\n      user: myuser\n"
            "      password: mypass\n      dbname: mydb\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "postgres"
        assert "myuser" in conn_str
        assert "mydb" in conn_str

    def test_detects_bigquery(self, tmp_path: Path) -> None:
        """Detects BigQuery connection from dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_bq\n")
        (tmp_path / "profiles.yml").write_text(
            "test_bq:\n  target: dev\n  outputs:\n    dev:\n"
            "      type: bigquery\n      project: my-gcp-project\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "bigquery"
        assert conn_str == "bigquery://my-gcp-project"

    def test_detects_snowflake(self, tmp_path: Path) -> None:
        """Detects Snowflake connection from dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_sf\n")
        (tmp_path / "profiles.yml").write_text(
            "test_sf:\n  target: dev\n  outputs:\n    dev:\n"
            "      type: snowflake\n      account: myaccount\n"
            "      database: mydb\n      schema: myschema\n"
            "      warehouse: mywh\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "snowflake"
        assert "myaccount" in conn_str
        assert "warehouse=mywh" in conn_str

    def test_detects_databricks(self, tmp_path: Path) -> None:
        """Detects Databricks connection from dbt project."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_db\n")
        (tmp_path / "profiles.yml").write_text(
            "test_db:\n  target: dev\n  outputs:\n    dev:\n"
            "      type: databricks\n      host: myhost.databricks.com\n"
            "      http_path: /sql/1.0/warehouses/abc\n"
            "      token: dapi123\n"
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is not None
        conn_str, dialect = result
        assert dialect == "databricks"
        assert "myhost" in conn_str

    def test_no_dbt_project(self, tmp_path: Path) -> None:
        """Returns None when no dbt_project.yml found."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        result = detect_dbt_connection_string(tmp_path)
        assert result is None

    def test_no_profiles(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when dbt_project.yml exists but no profiles.yml."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test\n")
        # Override $HOME, so that existing ~/.dbt/profiles.yml file does not mess with the test
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        result = detect_dbt_connection_string(tmp_path)
        assert result is None

    def test_walks_up_directories(self, tmp_path: Path) -> None:
        """Finds dbt_project.yml by walking up parent directories."""
        from dbt_charts.core.compile.sources.detection import (
            detect_dbt_connection_string,
        )

        (tmp_path / "dbt_project.yml").write_text("profile: test_proj\n")
        (tmp_path / "profiles.yml").write_text(
            "test_proj:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: ':memory:'\n"
        )
        subdir = tmp_path / "models" / "staging"
        subdir.mkdir(parents=True)

        result = detect_dbt_connection_string(subdir)
        assert result is not None
        _, dialect = result
        assert dialect == "duckdb"


class TestParseDbtProfilesYaml:
    """Tests for parse_dbt_profiles_yaml (content-string → per-target dicts)."""

    def test_bigquery_target_topology_only_no_credentials(self) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        content = (
            "dundersign:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: bigquery\n"
            "      method: service-account\n"
            "      project: internal-dataface-eng\n"
            "      dataset: dundersign\n"
            "      keyfile: /secrets/sa.json\n"
            "      keyfile_json:\n"
            "        type: service_account\n"
            "        private_key: SUPER_SECRET\n"
        )
        result = parse_dbt_profiles_yaml(content)
        assert len(result) == 1
        target = result[0]
        assert target["profile"] == "dundersign"
        assert target["target"] == "dev"
        assert target["label"] == "dundersign / dev"
        assert target["type"] == "bigquery"
        assert target["project"] == "internal-dataface-eng"
        assert target["dataset"] == "dundersign"
        # Credentials never surface.
        assert "keyfile" not in target
        assert "keyfile_json" not in target
        assert "private_key" not in target

    def test_databricks_target_topology_only_no_credentials(self) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        content = (
            "analytics:\n"
            "  outputs:\n"
            "    prod:\n"
            "      type: databricks\n"
            "      host: my-workspace.cloud.databricks.com\n"
            "      http_path: /sql/1.0/warehouses/abc123\n"
            "      catalog: main\n"
            "      schema: analytics\n"
            "      token: hunter2\n"
        )
        target = parse_dbt_profiles_yaml(content)[0]
        assert target["type"] == "databricks"
        assert target["host"] == "my-workspace.cloud.databricks.com"
        assert target["http_path"] == "/sql/1.0/warehouses/abc123"
        assert target["catalog"] == "main"
        assert target["schema"] == "analytics"
        # Credentials never surface.
        assert "token" not in target

    def test_snowflake_target_drops_password(self) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        content = (
            "analytics:\n"
            "  outputs:\n"
            "    prod:\n"
            "      type: snowflake\n"
            "      account: org-acct.us-east-1\n"
            "      warehouse: COMPUTE_WH\n"
            "      database: RAW\n"
            "      schema: PUBLIC\n"
            "      user: svc_dbt\n"
            "      password: hunter2\n"
        )
        target = parse_dbt_profiles_yaml(content)[0]
        assert target["account"] == "org-acct.us-east-1"
        assert target["warehouse"] == "COMPUTE_WH"
        assert target["database"] == "RAW"
        assert target["schema"] == "PUBLIC"
        assert target["username"] == "svc_dbt"
        assert "password" not in target

    def test_env_var_renders_with_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        monkeypatch.delenv("DUNDERSIGN_BQ_PROJECT", raising=False)
        content = (
            "dundersign:\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: bigquery\n"
            "      project: \"{{ env_var('DUNDERSIGN_BQ_PROJECT', 'internal-dataface-eng') }}\"\n"
            "      dataset: dundersign\n"
        )
        target = parse_dbt_profiles_yaml(content)[0]
        assert target["project"] == "internal-dataface-eng"

    def test_env_var_never_reads_process_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SECURITY: a secret set in the process env must not leak via env_var().

        profiles.yml is remote-committed, so env_var() resolves against an empty
        environment — `{{ env_var('X') }}` referencing a real process variable
        yields nothing (the field is omitted), never the secret's value.
        """
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        monkeypatch.setenv("SUPER_SECRET", "leaked-infra-secret")
        content = (
            "p:\n"
            "  outputs:\n"
            "    evil:\n"
            "      type: bigquery\n"
            "      project: \"{{ env_var('SUPER_SECRET') }}\"\n"
            "      dataset: \"{{ env_var('SUPER_SECRET', 'safe-default') }}\"\n"
        )
        target = parse_dbt_profiles_yaml(content)[0]
        # The raw env_var (no default) referencing a live secret is omitted.
        assert "project" not in target
        # Even a defaulted env_var resolves to the *default*, not the real value.
        assert target["dataset"] == "safe-default"
        assert "leaked-infra-secret" not in str(target)

    def test_unresolvable_env_var_field_is_omitted_target_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        monkeypatch.delenv("MISSING_PROJECT", raising=False)
        content = (
            "p:\n"
            "  outputs:\n"
            "    partial:\n"
            "      type: bigquery\n"
            "      project: \"{{ env_var('MISSING_PROJECT') }}\"\n"
            "      dataset: still-here\n"
        )
        target = parse_dbt_profiles_yaml(content)[0]
        # The unresolvable field is dropped, but the literal field survives so
        # the target still prefills what it can.
        assert "project" not in target
        assert target["dataset"] == "still-here"
        assert target["type"] == "bigquery"

    def test_invalid_or_empty_yaml_returns_empty(self) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        assert parse_dbt_profiles_yaml("") == []
        assert parse_dbt_profiles_yaml("\n\n") == []
        assert parse_dbt_profiles_yaml("just: [a, b") == []  # malformed YAML

    def test_deeply_nested_yaml_returns_empty(self) -> None:
        """PyYAML raises a bare RecursionError, not yaml.YAMLError, on deep nesting."""
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        depth = sys.getrecursionlimit() * 5
        assert parse_dbt_profiles_yaml("[" * depth + "]" * depth) == []
        assert parse_dbt_profiles_yaml("{a: " * depth + "1" + "}" * depth) == []

    def test_multiple_profiles_and_targets_each_emit_one_row(self) -> None:
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        content = (
            "analytics:\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: postgres\n"
            "      host: localhost\n"
            "      dbname: dev_db\n"
            "      user: dev\n"
            "    prod:\n"
            "      type: postgres\n"
            "      server: prod.example.com\n"
            "      dbname: prod_db\n"
            "      user: prod\n"
            "warehouse:\n"
            "  outputs:\n"
            "    main:\n"
            "      type: duckdb\n"
            "      path: /data/wh.duckdb\n"
        )
        result = parse_dbt_profiles_yaml(content)
        labels = {t["label"] for t in result}
        assert labels == {
            "analytics / dev",
            "analytics / prod",
            "warehouse / main",
        }
        # host/server and database/dbname aliases both normalize.
        prod = next(t for t in result if t["target"] == "prod")
        assert prod["host"] == "prod.example.com"
        assert prod["database"] == "prod_db"
        duck = next(t for t in result if t["type"] == "duckdb")
        assert duck["path"] == "/data/wh.duckdb"

    def test_non_dict_outputs_does_not_crash(self) -> None:
        """A present-but-non-dict `outputs:` degrades to [] (never AttributeError).

        profiles.yml is org-member-authored and reaches the live connection form,
        so a half-written `outputs:` (null) or an odd shape (list/scalar) must not
        500 the page.
        """
        from dbt_charts.core.compile.sources.detection import parse_dbt_profiles_yaml

        assert parse_dbt_profiles_yaml("p:\n  outputs:\n") == []  # null outputs
        assert parse_dbt_profiles_yaml("p:\n  outputs: not-a-mapping\n") == []
        assert parse_dbt_profiles_yaml("p:\n  outputs:\n    - a\n    - b\n") == []
