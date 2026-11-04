"""Tests for LOCAL_AUTHORING_REGISTRY_KWARGS (MCP, playground).

`dct serve` used to share this posture but now defaults to strict read-only
(external access off) — see dbt-charts/tests/core/serve/test_serve_read_only_posture.py.
"""

from collections.abc import Callable
from pathlib import Path

import duckdb

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DuckDBSourceConfig
from dbt_charts.core.execute.adapters.adapter_registry import (
    LOCAL_AUTHORING_REGISTRY_KWARGS,
    build_adapter_registry,
)
from dbt_charts.core.execute.backends.duckdb_local import DuckDBBackend

from ..._backend_runner import DuckDBRunner


class TestLocalAuthoringRegistryKwargs:
    def test_resolve_kwargs_use_shared_lock(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.duckdb")
        kwargs = DuckDBBackend._resolve_duckdb_connect_kwargs(
            db_path,
            LOCAL_AUTHORING_REGISTRY_KWARGS["read_only"],
            LOCAL_AUTHORING_REGISTRY_KWARGS["duckdb_config"],
            LOCAL_AUTHORING_REGISTRY_KWARGS["allow_external_access_in_readonly"],
        )
        assert kwargs.get("read_only") is True
        assert kwargs.get("config", {}).get("enable_external_access") is True

    def test_registry_opens_read_only_with_external_access(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        registry = build_adapter_registry(
            local_project(tmp_path), **LOCAL_AUTHORING_REGISTRY_KWARGS
        )
        backend = registry._backends.for_source(  # noqa: SLF001
            DuckDBSourceConfig(type="duckdb")
        )
        try:
            assert backend.read_only is True
            assert backend.allow_external_access_in_readonly is True
            assert backend._duckdb_config == {"enable_external_access": True}
        finally:
            registry.close()

    def test_external_read_works_and_db_write_blocked_on_file_db(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (x INT)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.close()

        json_path = tmp_path / "rows.jsonl"
        json_path.write_text('{"x":2}\n{"x":3}\n', encoding="utf-8")

        adapter = DuckDBRunner(
            data_dir=local_project(tmp_path).data_path("."),
            source_config=DuckDBSourceConfig(type="duckdb", path=db_path),
            **LOCAL_AUTHORING_REGISTRY_KWARGS,
        )
        try:
            result = adapter.execute(
                SqlQuery(
                    sql=f"SELECT SUM(x) AS total FROM read_json_auto('{json_path.as_posix()}')",
                    source="test_db",
                )
            )
            assert result.error is None, result.error
            assert result.data[0]["total"] == 5

            result = adapter.execute(
                SqlQuery(sql="INSERT INTO t VALUES (99)", source="test_db")
            )
            assert result.error is not None
        finally:
            adapter.close()
