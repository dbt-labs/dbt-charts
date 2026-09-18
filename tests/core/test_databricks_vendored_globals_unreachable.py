"""Regression tests: no dbt Charts Databricks code path reaches dbt-databricks'
process-wide vendored state (GlobalState.__use_managed_iceberg,
DatabricksConnectionManager._dbr_capabilities_cache).

Each test drives the real installed adapter/connection-manager classes through
``build_adapter`` (no live warehouse: the network-touching calls are stubbed),
mirroring the drive style in test_databricks_credential_isolation.py. The
capabilities-cache test goes through the actual production connection funnel
(``open_connection`` -> ``adapter.connection_named`` -> ``acquire_connection`` ->
``set_connection_name`` -> ``_create_fresh_connection``, then the lazy ``.handle``
access that triggers ``open()``) rather than calling ``open()`` directly, since
production reaches the cache write through ``_create_fresh_connection`` before
``open()`` ever runs.
"""

import sys

from dbt.adapters.databricks import connections as dbx_connections
from dbt.adapters.databricks.global_state import GlobalState
from dbt.adapters.databricks.handle import DatabricksHandle, SqlUtils

from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    build_adapter,
    open_connection,
)

_BASE_CONFIG = {
    "type": "databricks",
    "host": "tenant.cloud.databricks.com",
    "http_path": "/sql/1.0/warehouses/abc",
    "schema": "default",
    "catalog": "main",
}


def test_build_adapter_never_writes_global_state_managed_iceberg(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        GlobalState, "set_use_managed_iceberg", staticmethod(calls.append)
    )

    build_adapter({**_BASE_CONFIG, "token": "t1"}, register_macros=False)
    build_adapter({**_BASE_CONFIG, "token": "t2"}, register_macros=False)

    assert calls == []


def test_open_connection_never_touches_shared_dbr_capabilities_cache(monkeypatch):
    class _FakeHandle:
        session_id = "fake"

    monkeypatch.setattr(
        SqlUtils,
        "prepare_connection_arguments",
        staticmethod(lambda *_args, **_kwargs: {}),
    )
    monkeypatch.setattr(
        DatabricksHandle,
        "from_connection_args",
        staticmethod(lambda *_args, **_kwargs: _FakeHandle()),
    )
    dbx_connections.DatabricksConnectionManager._dbr_capabilities_cache.clear()
    try:
        for token in ("token-A", "token-B"):
            adapter = build_adapter(
                {**_BASE_CONFIG, "token": token}, register_macros=False
            )
            adapter.config.credentials.authenticate = lambda: object()
            with open_connection(adapter, f"conn-{token}"):
                conn = adapter.connections.get_thread_connection()
                assert conn.capabilities.is_sql_warehouse is True
                assert conn.capabilities.dbr_version == (sys.maxsize, sys.maxsize)
        assert dbx_connections.DatabricksConnectionManager._dbr_capabilities_cache == {}
    finally:
        dbx_connections.DatabricksConnectionManager._dbr_capabilities_cache.clear()
