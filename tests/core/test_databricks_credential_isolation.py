"""Deterministic repro of the Databricks per-tenant credential race.

``DatabricksConnectionManager.open`` is a ``@classmethod`` that writes the
class-level ``cls.credentials_manager`` (``creds.authenticate()``, connections.py
line 479) and then reads it straight back to build the connection arguments
(``SqlUtils.prepare_connection_arguments(creds, cls.credentials_manager, ...)``,
line 487-488), with no lock between the write and the read. Two tenants opening
connections concurrently in one process interleave between the write and the read,
so one tenant's connection arguments are assembled from the *other* tenant's
credential manager — a cross-tenant credential leak.

This drives the real installed ``open()`` (reached through ``build_adapter`` and
the adapter's connection manager). The interleave is forced deterministically with
``threading.Event``s rather than a sleep race: the only statement between the
write (479) and the read (488) is ``QueryConfigUtils.get_merged_query_tags`` (line
485, reached because each connection carries a query-header context), so that call
is the real preemption point — we patch it to park tenant A there until tenant B
has completed its own write, clobbering the shared class var. No live Databricks
endpoint is touched: the network connect is stubbed and
``prepare_connection_arguments`` is patched only to record which credential-manager
identity each thread actually saw at the read.
"""

import threading

import pytest
from dbt.adapters.contracts.connection import ConnectionState
from dbt.adapters.databricks import connections as dbx_connections
from dbt.adapters.databricks.connections import DatabricksDBTConnection
from dbt_common.exceptions import DbtConfigError

from dbt_charts.core.execute.adapters.dbt_adapter_factory import build_adapter

pytestmark = pytest.mark.usefixtures("no_spog_probe")

_BASE_CONFIG = {
    "type": "databricks",
    "host": "tenant.cloud.databricks.com",
    "http_path": "/sql/1.0/warehouses/abc",
    "schema": "default",
    "catalog": "main",
}


def _make_connection(token: str):
    """Build a real Databricks adapter + a real INIT connection for one tenant."""
    adapter = build_adapter({**_BASE_CONFIG, "token": token}, register_macros=False)
    creds = adapter.config.credentials
    connection = DatabricksDBTConnection(
        type="databricks",
        name=f"conn-{token}",
        credentials=creds,
        state=ConnectionState.INIT,
    )
    connection.http_path = creds.http_path
    # A truthy query-header context makes open() take the line-485 branch
    # (get_merged_query_tags), giving us a real call site to park on between the
    # class-var write (479) and the read (488).
    connection._query_header_context = object()
    return adapter, connection


def test_concurrent_opens_do_not_cross_tenant_credentials():
    # Each tenant authenticates to a recognizable, distinct credential manager;
    # open() assigns the return value to the shared class var at line 479.
    manager_by_token: dict[str, object] = {
        "token-A": object(),
        "token-B": object(),
    }

    a_parked = threading.Event()
    b_wrote = threading.Event()

    def authenticate_for(token: str):
        return lambda: manager_by_token[token]

    # get_merged_query_tags is the lone call between open()'s write of
    # cls.credentials_manager (479) and its read (488). Park tenant A here AFTER it
    # has written manager_A, until tenant B has run its own write of manager_B
    # (clobbering the class var). When A unblocks and reads at 488, the shared var
    # holds B's manager — the real race.
    def fake_get_merged_query_tags(_query_header_context, creds):
        if creds.token == "token-A":
            a_parked.set()
            assert b_wrote.wait(timeout=5.0), "tenant B never completed its write"
        else:
            b_wrote.set()
        return {}

    recorded: dict[str, object] = {}

    def fake_prepare(creds, creds_manager, http_path, _query_tags=None):
        # creds_manager is open()'s read of cls.credentials_manager at line 488.
        recorded[creds.token] = creds_manager
        return {"server_hostname": creds.host, "http_path": http_path}

    class _FakeHandle:
        session_id = "fake"

    def fake_from_connection_args(_conn_args, _is_cluster):
        return _FakeHandle()  # opaque handle; no network

    errors: dict[str, BaseException] = {}

    def run_tenant(token: str):
        try:
            adapter, connection = _make_connection(token)
            connection.credentials.authenticate = authenticate_for(token)
            adapter.connections.open(connection)
        except Exception as exc:  # noqa: BLE001 — surface thread failure to test
            errors[token] = exc

    orig_tags = dbx_connections.QueryConfigUtils.get_merged_query_tags
    orig_prepare = dbx_connections.SqlUtils.prepare_connection_arguments
    orig_from_args = dbx_connections.DatabricksHandle.from_connection_args
    dbx_connections.QueryConfigUtils.get_merged_query_tags = staticmethod(
        fake_get_merged_query_tags
    )
    dbx_connections.SqlUtils.prepare_connection_arguments = staticmethod(fake_prepare)
    dbx_connections.DatabricksHandle.from_connection_args = staticmethod(
        fake_from_connection_args
    )
    try:
        thread_a = threading.Thread(target=run_tenant, args=("token-A",))
        thread_b = threading.Thread(target=run_tenant, args=("token-B",))
        thread_a.start()
        # A has written manager_A (479) and is parked at get_merged_query_tags
        # before A reads at 488; only now release B so its write interleaves.
        assert a_parked.wait(timeout=5.0), "tenant A never reached the seam"
        thread_b.start()
        thread_b.join(timeout=10.0)
        thread_a.join(timeout=10.0)
    finally:
        dbx_connections.QueryConfigUtils.get_merged_query_tags = orig_tags
        dbx_connections.SqlUtils.prepare_connection_arguments = orig_prepare
        dbx_connections.DatabricksHandle.from_connection_args = orig_from_args

    if errors:
        raise AssertionError(f"open() raised in a tenant thread: {errors}")

    # Per-tenant isolation: each thread must have built its connection args from
    # ITS OWN credential manager. Today the shared class var crosses them.
    assert recorded["token-A"] is manager_by_token["token-A"]
    assert recorded["token-B"] is manager_by_token["token-B"]


def test_open_rejects_conflicting_workspace_ids_across_computes():
    adapter = build_adapter(
        {
            **_BASE_CONFIG,
            "token": "t",
            "http_path": "/sql/1.0/warehouses/abc?o=1",
            "compute": {"c": {"http_path": "/sql/1.0/warehouses/def?o=2"}},
        },
        register_macros=False,
    )
    connection = DatabricksDBTConnection(
        type="databricks",
        name="conn",
        credentials=adapter.config.credentials,
        state=ConnectionState.INIT,
    )
    connection.http_path = adapter.config.credentials.http_path

    with pytest.raises(DbtConfigError, match="conflicting workspace-id"):
        adapter.connections.open(connection)


def test_adapter_registers_has_dbr_capability_parse_replacement():
    adapter = build_adapter({**_BASE_CONFIG, "token": "t"}, register_macros=False)

    assert (
        adapter._parse_replacements_["has_dbr_capability"]
        == adapter._has_dbr_capability_parse
    )
