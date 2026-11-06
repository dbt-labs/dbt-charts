"""Tests for DbtSchemaSource with a file-backed DuckDB adapter.

File-backed DuckDB attaches the database under a non-system name
(e.g. 'dundersign' for dundersign.duckdb).  DbtSchemaSource._get_relations
must pass that name — not None — to adapter.list_relations, otherwise
DuckDB returns an empty list.

These tests use a temp .duckdb file so CI does not depend on the
examples/ fixtures directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.backends.dbt_adapter_factory import build_adapter
from dbt_charts.core.inspect.sources.dbt import DbtSchemaSource


def _make_file_backed_duckdb(tmp_path: Path) -> tuple[Path, str]:
    """Create a temp .duckdb file with known tables; return (path, attach_name).

    The attach name is the file stem (what DuckDB uses when you connect to
    a file).  The PRAGMA database_list entry will carry that name in column 1.
    """
    import duckdb

    db_path = tmp_path / "myproject.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE users (id INTEGER, name VARCHAR)")
    conn.execute("INSERT INTO users VALUES (1, 'alice'), (2, 'bob')")
    conn.execute("CREATE TABLE orders (order_id INTEGER, user_id INTEGER)")
    conn.close()
    # Attach name is the file stem ("myproject")
    return db_path, "myproject"


class TestDbtSchemaSourceFileBacked:
    """DbtSchemaSource correctly resolves file-backed DuckDB attach name."""

    def test_list_tables_returns_non_empty(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """list_tables('main') must return the tables we created, not empty."""
        db_path, _attach = _make_file_backed_duckdb(tmp_path)
        adapter = build_adapter({"type": "duckdb", "path": str(db_path)})
        src = DbtSchemaSource(
            adapter=adapter, project=local_project(tmp_path), db_path=str(db_path)
        )
        result = src.list_tables("main")
        assert result is not None, "list_tables returned None for file-backed DuckDB"
        tables = result["tables"]
        assert "users" in tables, f"Expected 'users' in tables, got: {list(tables)}"
        assert "orders" in tables, f"Expected 'orders' in tables, got: {list(tables)}"

    def test_profile_table_returns_columns(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """profile_table('main', 'users') returns column detail from file-backed DuckDB."""
        db_path, _attach = _make_file_backed_duckdb(tmp_path)
        adapter = build_adapter({"type": "duckdb", "path": str(db_path)})
        src = DbtSchemaSource(
            adapter=adapter, project=local_project(tmp_path), db_path=str(db_path)
        )
        result = src.profile_table("main", "users")
        assert result is not None, "profile_table returned None for file-backed DuckDB"
        cols = result["columns"]
        assert "id" in cols, f"Expected 'id' column, got: {list(cols)}"
        assert "name" in cols, f"Expected 'name' column, got: {list(cols)}"

    def test_duckdb_resolve_database_returns_attach_name(self, tmp_path: Path) -> None:
        """duckdb_resolve_database returns the non-system attach name, not 'memory'."""
        from dbt_charts.core.inspect.sources.duckdb_utils import duckdb_resolve_database

        db_path, expected_name = _make_file_backed_duckdb(tmp_path)
        adapter = build_adapter({"type": "duckdb", "path": str(db_path)})
        with adapter.connection_named("test"):
            resolved = duckdb_resolve_database(adapter, db_path)
        assert resolved == expected_name, (
            f"Expected attach name '{expected_name}', got '{resolved}'"
        )

    def test_duckdb_resolve_database_memory_returns_memory(self) -> None:
        """duckdb_resolve_database for :memory: returns 'memory'."""
        from dbt_charts.core.inspect.sources.duckdb_utils import duckdb_resolve_database

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        with adapter.connection_named("test"):
            resolved = duckdb_resolve_database(adapter, None)
        assert resolved == "memory"

    def test_in_memory_duckdb_resolves_to_memory_database_name(
        self, tmp_path: Path
    ) -> None:
        """For :memory: DuckDB, _resolve_duckdb_database returns 'memory' (not None).

        Regression: after the fix, in-memory DuckDB gets database='memory'
        instead of None in list_relations calls.  This test verifies that the
        real dbt-duckdb adapter accepts 'memory' as the database arg.
        """
        from dbt_charts.core.inspect.sources.duckdb_utils import duckdb_resolve_database

        adapter = build_adapter({"type": "duckdb", "path": ":memory:"})
        # Create a table and call list_relations('memory', 'main') in one connection.
        with adapter.connection_named("test"):
            adapter.execute("CREATE TABLE customers (id INTEGER)", auto_begin=True)
            # Verify the real adapter accepts 'memory' as the database arg.
            rels = list(adapter.list_relations("memory", "main"))
            names = [r.identifier.lower() for r in rels]
        assert "customers" in names, (
            f"list_relations('memory', 'main') did not find 'customers': {names}"
        )
        # Verify duckdb_resolve_database short-circuits for :memory:.
        with adapter.connection_named("resolve"):
            resolved = duckdb_resolve_database(adapter, ":memory:")
        assert resolved == "memory"
