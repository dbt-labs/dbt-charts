"""Tests for dbt macro bootstrap in build_adapter().

These tests assert that adapters built via build_adapter() come back with a
working `_macro_resolver` populated from dbt-core's bundled macros, so the
introspection API (list_schemas / list_relations / get_columns_in_relation)
works without any user files (no manifest.json, no dbt_project.yml).
"""

from pathlib import Path

import pytest

from dbt_charts.core.execute.adapters import dbt_macro_loader
from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter
from dbt_charts.core.execute.adapters.dbt_macro_loader import (
    DbtMacroLoaderError,
    load_macro_manifest,
)


def _duckdb_cfg() -> dict:
    return {"type": "duckdb", "path": ":memory:"}


def test_list_schemas_works_after_build_adapter_duckdb() -> None:
    adapter = build_adapter(_duckdb_cfg())
    with adapter.connection_named("test"):
        adapter.execute("CREATE SCHEMA analytics", auto_begin=True, fetch=False)
        adapter.execute("CREATE SCHEMA raw", auto_begin=True, fetch=False)
        schemas = adapter.list_schemas("memory")
    schemas_lower = {s.lower() for s in schemas}
    assert "analytics" in schemas_lower
    assert "raw" in schemas_lower


def test_list_relations_works_after_build_adapter_duckdb() -> None:
    adapter = build_adapter(_duckdb_cfg())
    with adapter.connection_named("test"):
        adapter.execute("CREATE TABLE t (a INT)", auto_begin=True, fetch=False)
        relations = adapter.list_relations("memory", "main")
    assert any(r.identifier.lower() == "t" for r in relations)


def test_get_columns_in_relation_works_after_build_adapter_duckdb() -> None:
    adapter = build_adapter(_duckdb_cfg())
    with adapter.connection_named("test"):
        adapter.execute(
            "CREATE TABLE t (a INT, b VARCHAR)", auto_begin=True, fetch=False
        )
        relation = adapter.Relation.create(
            database="memory", schema="main", identifier="t"
        )
        cols = adapter.get_columns_in_relation(relation)
    assert {c.name.lower() for c in cols} == {"a", "b"}


def test_macro_manifest_cached_per_adapter_type() -> None:
    load_macro_manifest.cache_clear()
    first = load_macro_manifest("duckdb")
    second = load_macro_manifest("duckdb")
    assert first is second
    assert "macro.dbt.list_schemas" in first.macros
    assert "macro.dbt_duckdb.duckdb__list_schemas" in first.macros


def test_macro_manifest_includes_dependency_adapter_macros() -> None:
    manifest = load_macro_manifest("redshift")
    assert "macro.dbt_redshift.redshift__list_schemas" in manifest.macros
    assert "macro.dbt_postgres.postgres__list_schemas" in manifest.macros


def test_pragma_database_list_resolves_file_attach_name(tmp_path: Path) -> None:
    """PRAGMA database_list resolves the attach name for a file-backed DuckDB.

    Regression: DbtSchemaSource._get_relations() was passing None as the
    database arg to list_relations; file-backed DuckDB returns [] for None.
    The fix reads PRAGMA database_list to get the actual attach name.
    """
    import duckdb

    from dbt_charts.core.inspect.sources.duckdb_utils import duckdb_resolve_database

    db_path = tmp_path / "testdb.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.close()

    adapter = build_adapter({"type": "duckdb", "path": str(db_path)})
    with adapter.connection_named("test"):
        resolved = duckdb_resolve_database(adapter, db_path)
        # attach name for testdb.duckdb is 'testdb'
        assert resolved == "testdb", f"Expected 'testdb', got '{resolved}'"
        # list_relations must work with the resolved name
        relations = list(adapter.list_relations(resolved, "main"))
    assert any(r.identifier.lower() == "foo" for r in relations), (
        f"Expected 'foo' in relations with database='{resolved}', got: {[r.identifier for r in relations]}"
    )


def test_loader_failure_raises_clearly_when_path_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a configured macro path doesn't exist, raise DbtMacroLoaderError.

    Simulates a dbt-core upgrade where the global_project layout moved.
    """
    load_macro_manifest.cache_clear()
    monkeypatch.setattr(
        dbt_macro_loader,
        "_resolve_macro_packages",
        lambda adapter_type: [("dbt", Path("/nonexistent/global_project"))],
    )
    with pytest.raises(DbtMacroLoaderError, match="macros not found"):
        load_macro_manifest("duckdb")
    load_macro_manifest.cache_clear()
