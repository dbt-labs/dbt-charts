"""Live-Postgres integration coverage for the EXPLAIN warehouse-check tier.

The EXPLAIN tier's unit tests fake the adapter registry, so nothing there
proves a real engine accepts `EXPLAIN <authored sql>` through the real
dbt-postgres adapter, rejects a dropped column through it, or that a
connect-time fault stays "unchecked". This module runs against the session's
throwaway `pg_cluster` (`tests/conftest.py`) and pins all three against the
real engine.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from pytest_postgresql.executors import PostgreSQLExecutor

from dbt_charts.agent_api.validate import ValidateResult, validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry

_BOARD = """\
title: Orders
source: pg
queries:
  o: SELECT customer_id, amount FROM orders
charts:
  tbl:
    query: o
    type: table
rows:
  - tbl
"""

pytestmark = [
    # Cluster boot runs in fixture setup, which the repo-wide 30s timeout
    # covers; test_slow_family_timeouts.py guards this extension.
    pytest.mark.timeout(180),
    pytest.mark.xdist_group("postgres_cluster"),
]


def _connect(cluster: PostgreSQLExecutor, dbname: str) -> psycopg.Connection:
    return psycopg.connect(
        host=cluster.host,
        port=cluster.port,
        user=cluster.user,
        password=cluster.password,
        dbname=dbname,
        autocommit=True,
    )


@pytest.fixture(scope="module")
def warehouse(pg_cluster: PostgreSQLExecutor) -> Iterator[psycopg.Connection]:
    """`wh.orders(customer_id, amount)` on the session cluster."""
    with _connect(pg_cluster, pg_cluster.maintenance_dbname) as admin:
        admin.execute("DROP DATABASE IF EXISTS wh")
        admin.execute("CREATE DATABASE wh")
    with _connect(pg_cluster, "wh") as con:
        con.execute(
            "CREATE TABLE orders (customer_id INTEGER, amount DOUBLE PRECISION)"
        )
        con.execute("INSERT INTO orders VALUES (1, 10.0)")
        yield con


def _project(tmp_path: Path, port: int, user: str) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  pg:\n"
        "    type: postgres\n"
        "    host: 127.0.0.1\n"
        f"    port: {port}\n"
        "    dbname: wh\n"
        f"    user: {user}\n"
        "    password: \"{{ env_var('DCT_TEST_PG_PASSWORD') }}\"\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yaml").write_text(_BOARD)
    return tmp_path


def _cluster_project(
    tmp_path: Path, cluster: PostgreSQLExecutor, monkeypatch: pytest.MonkeyPatch
) -> Path:
    assert cluster.password is not None
    monkeypatch.setenv("DCT_TEST_PG_PASSWORD", cluster.password)
    return _project(tmp_path, cluster.port, cluster.user)


def _validate(root: Path) -> ValidateResult:
    project = FilesystemProject(root)
    registry = build_adapter_registry(project)
    results = validate_paths(
        [root / "charts" / "board.yaml"],
        project=project,
        adapter_registry=registry,
    )
    assert len(results) == 1
    return results[0]


def test_valid_query_passes_with_the_columns_only_warning(
    pg_cluster: PostgreSQLExecutor,
    warehouse: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _validate(_cluster_project(tmp_path, pg_cluster, monkeypatch))
    assert not result.errors, [e.model_dump() for e in result.errors]
    assert "WARN-COLUMN-CHECK-UNAVAILABLE" in {w.code for w in result.warnings}


def test_dropped_column_fails_through_a_real_explain(
    pg_cluster: PostgreSQLExecutor,
    warehouse: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drift case itself: rename the column in the warehouse only, and the
    board that still references it must fail — through a real Postgres EXPLAIN,
    not a faked registry."""
    root = _cluster_project(tmp_path, pg_cluster, monkeypatch)
    warehouse.execute("ALTER TABLE orders RENAME COLUMN customer_id TO user_id")
    try:
        result = _validate(root)
    finally:
        warehouse.execute("ALTER TABLE orders RENAME COLUMN user_id TO customer_id")
    errors = {e.code for e in result.errors}
    assert "ERR-WAREHOUSE-QUERY-INVALID" in errors, errors
    invalid = next(e for e in result.errors if e.code == "ERR-WAREHOUSE-QUERY-INVALID")
    assert "customer_id" in invalid.message


def test_unreachable_server_is_unchecked_not_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connect-time fault says nothing about the SQL: never 'your query is
    broken', always the unverified warning."""
    # Hold the port bound without listening for the duration: connections are
    # deterministically refused, and no xdist sibling's cluster can claim it.
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        monkeypatch.setenv("DCT_TEST_PG_PASSWORD", "")
        root = _project(tmp_path, blocker.getsockname()[1], "dct")
        result = _validate(root)
    assert not result.errors, [e.model_dump() for e in result.errors]
    assert "WARN-WAREHOUSE-CHECK-UNAVAILABLE" in {w.code for w in result.warnings}
