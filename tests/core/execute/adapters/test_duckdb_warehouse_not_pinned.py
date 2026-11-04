"""A closed registry must not still hold the DuckDB warehouse file open.

DuckDB treats ``enable_external_access`` as a database-instance option: every
connection to one file must agree on it, or the second opener gets
``Can't open a connection to same database file with a different configuration
than existing connections``.

The dbt path pinned the file past its owner's lifetime. dbt's connection pool
only *releases* a connection on ``connection_named`` exit, and dbt-duckdb hangs
the live handle off a process-global ``DuckDBConnectionManager._ENV`` — so a
schema query (auto-link issues one mid-render) left the warehouse open forever.
A later session with a different posture — e.g. an eval's render assertion at
the default read-only posture after the solver ran under
``LOCAL_AUTHORING_REGISTRY_KWARGS`` — could then not connect at all, which is
how ``render`` failed non-deterministically in the dundersign_chat suite.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SchemaQuery, SqlQuery
from dbt_charts.core.execute.adapters.adapter_registry import (
    LOCAL_AUTHORING_REGISTRY_KWARGS,
    build_adapter_registry,
)
from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

_STRICT_READONLY_CONNECT = {
    "read_only": True,
    "config": {"enable_external_access": False},
}


@pytest.fixture
def warehouse_project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> tuple[FilesystemProject, Path]:
    """A project whose one DuckDB source is a real file with one table."""
    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders AS SELECT 1 AS id, 'a' AS label")
    conn.close()

    (tmp_path / "dbt_charts.yml").write_text(
        yaml.safe_dump({"sources": {"db": {"type": "duckdb", "path": db_path.name}}}),
        encoding="utf-8",
    )
    return local_project(tmp_path), db_path


def _assert_openable_at_strict_posture(db_path: Path) -> None:
    try:
        conn = duckdb.connect(str(db_path), **_STRICT_READONLY_CONNECT)
    except duckdb.Error as exc:
        pytest.fail(f"warehouse still pinned by the closed registry: {exc}")
    conn.close()


def test_schema_query_does_not_pin_warehouse_past_registry_close(
    warehouse_project: tuple[FilesystemProject, Path],
) -> None:
    """A schema query routes through dbt; closing the registry must release it."""
    project, db_path = warehouse_project
    registry = build_adapter_registry(project, **LOCAL_AUTHORING_REGISTRY_KWARGS)
    result = registry.execute(
        SchemaQuery(source="db", schema_name="main", table="orders")
    )
    assert result.error is None, result.error
    assert result.data, "schema query returned no columns"

    registry.close()
    _assert_openable_at_strict_posture(db_path)


def test_authored_keep_open_wins(tmp_path: Path) -> None:
    """The engine default must stay a default — an author's own value survives."""
    cfg = {"type": "duckdb", "path": str(tmp_path / "w.duckdb"), "keep_open": True}
    adapter = build_adapter(cfg, read_only=True, register_macros=False)
    assert adapter.config.credentials.keep_open is True


def test_ref_query_does_not_pin_warehouse_past_registry_close(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``{{ ref() }}`` route runs on the dbt backend, whose dbt connection pool is
    what pinned the file. The release happens at ``connection_named`` exit, not
    at ``registry.close()``; the close call below only marks where a caller is
    done with the session."""
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)

    db_path = tmp_path / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE orders AS SELECT 1 AS id, 100 AS revenue")
    conn.close()

    (tmp_path / "dbt_charts.yml").write_text("name: test_project\n", encoding="utf-8")
    (tmp_path / "dbt_project.yml").write_text(
        "name: test_project\nprofile: test_project\n", encoding="utf-8"
    )
    (tmp_path / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "test_project": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "duckdb",
                            "path": str(db_path),
                            "threads": 4,
                            # The posture this route pins the file at. It has to
                            # differ from the probe's or the assertion cannot
                            # fail: a pinned file is only refused to a *differing*
                            # config, so pinning at the probe's own posture would
                            # pass with the file wide open.
                            "config_options": {"enable_external_access": True},
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.test_project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "main",
                        "alias": "orders",
                    }
                },
                "sources": {},
            }
        ),
        encoding="utf-8",
    )

    registry = build_adapter_registry(
        local_project(tmp_path), **LOCAL_AUTHORING_REGISTRY_KWARGS
    )
    # A name no dbt_charts.yml source claims falls through to the dbt project,
    # which is what puts this query on the dbt target rather than the local DuckDB default.
    result = registry.execute(
        SqlQuery(sql="SELECT revenue FROM {{ ref('orders') }}", source="test_project")
    )
    assert result.error is None, result.error
    assert result.data == [{"revenue": 100}]

    registry.close()
    _assert_openable_at_strict_posture(db_path)


def test_second_registry_can_use_a_stricter_posture(
    warehouse_project: tuple[FilesystemProject, Path],
) -> None:
    """The end-to-end shape the evals hit: authoring session, then a strict one."""
    project, _ = warehouse_project

    authoring = build_adapter_registry(project, **LOCAL_AUTHORING_REGISTRY_KWARGS)
    assert (
        authoring.execute(
            SchemaQuery(source="db", schema_name="main", table="orders")
        ).error
        is None
    )
    authoring.close()

    strict = build_adapter_registry(project, read_only=True)
    try:
        result = strict.execute(SqlQuery(sql="SELECT id FROM orders", source="db"))
        assert result.error is None, result.error
    finally:
        strict.close()
