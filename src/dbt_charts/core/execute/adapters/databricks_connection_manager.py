"""dbt Charts overrides for dbt-databricks' process-wide vendored state.

dbt-databricks keeps three pieces of state at class or module level rather than per
instance: the credential manager (cls.credentials_manager, written inside open() and
read back with no lock), the DBR capabilities cache (_dbr_capabilities_cache, a
class-level dict keyed by http_path alone — no workspace host, no eviction), and
GlobalState.__use_managed_iceberg (rewritten on every adapter construction, last
writer wins). In a process holding several Databricks credential sets, each is a
cross-credential leak or a silent clobber. The two subclasses here reproduce the
vendored bodies minus exactly the offending lines; each class documents its own
deviations.

Drift guard
-----------
If any vendored body below changes in a dbt-databricks version bump, its hash no
longer matches and _assert_vendored_methods_unchanged() raises at import time, so CI
catches the desync before a silent credential mismatch or a resurfaced global-state
leak at runtime.

To update after a version bump: first read the new upstream body and confirm the
override still matches its intent (open(): the authenticated manager goes only to a
call-local, never to cls.credentials_manager; _create_fresh_connection(): skipping
only the two cache lines is still right; DatabricksAdapter.__init__(): omitting only
the GlobalState write is still right) — matching a hash on bytes alone would silently
re-arm the bypassed behavior. Then re-run the hash computation and update the
relevant constant:

  uv run python -c "
  import inspect, hashlib
  from dbt.adapters.databricks.connections import DatabricksConnectionManager as C
  from dbt.adapters.databricks.impl import DatabricksAdapter as A
  print(hashlib.sha256(inspect.getsource(C.open).encode()).hexdigest())
  print(hashlib.sha256(inspect.getsource(C._create_fresh_connection).encode()).hexdigest())
  print(hashlib.sha256(inspect.getsource(A.__init__).encode()).hexdigest())
  "
"""

import inspect
from multiprocessing.context import SpawnContext
from typing import Any, cast

from databricks.sql.exc import Error
from dbt.adapters.contracts.connection import (
    Connection,
    ConnectionState,
    Identifier,
    LazyHandle,
)
from dbt.adapters.databricks import constants
from dbt.adapters.databricks.behaviors.columns import (  # pyright: ignore[reportMissingTypeStubs]
    GetColumnsByDescribe,
)
from dbt.adapters.databricks.connections import (
    DatabricksConnectionManager,
    DatabricksDBTConnection,
    QueryConfigUtils,
    QueryContextWrapper,
    SqlUtils,
)
from dbt.adapters.databricks.credentials import (
    DatabricksCredentials,
)
from dbt.adapters.databricks.dbr_capabilities import DBRCapabilities
from dbt.adapters.databricks.events.connection_events import (  # pyright: ignore[reportMissingTypeStubs]
    ConnectionCreate,
    ConnectionCreateError,
)
from dbt.adapters.databricks.handle import DatabricksHandle
from dbt.adapters.databricks.impl import DatabricksAdapter
from dbt.adapters.databricks.logging import logger
from dbt.adapters.databricks.utils import is_cluster_http_path
from dbt.adapters.events.types import NewConnection
from dbt_common.events.contextvars import get_node_info
from dbt_common.events.functions import fire_event
from dbt_common.exceptions import DbtDatabaseError

# SHA-256 of inspect.getsource(...) for the vendored bodies below, dbt-databricks 1.12.0.
# Update these constants whenever the package version changes and re-verify the override.
_VENDORED_OPEN_HASH = "256b105e3e59ad75b494d4687a770603677a9dce71663535a90aeece425f941c"
_VENDORED_CREATE_FRESH_CONNECTION_HASH = (
    "1a35bd3e2059fa300ec86fc3108ebca80b8036aa5b41e1f9ef7dbbf290911678"
)
_VENDORED_ADAPTER_INIT_HASH = (
    "31722fc539a3cc5421db4f3aa3259ba94faf3d96882782c5382944201771a2b2"
)


def _assert_vendored_methods_unchanged() -> None:
    """Raise if any vendored body this module bypasses has drifted.

    Called at module import time so CI fails fast on a dbt-databricks version bump
    that changes a patched method before a silent credential mismatch or a
    resurfaced global-state leak at runtime.
    """
    import hashlib

    for method, expected in (
        (DatabricksConnectionManager.open, _VENDORED_OPEN_HASH),
        (
            DatabricksConnectionManager._create_fresh_connection,
            _VENDORED_CREATE_FRESH_CONNECTION_HASH,
        ),
        (DatabricksAdapter.__init__, _VENDORED_ADAPTER_INIT_HASH),
    ):
        src = inspect.getsource(method)
        actual = hashlib.sha256(src.encode()).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"dbt-databricks vendored method {method.__qualname__} has changed "
                f"(expected hash {expected}, got {actual}). Update the matching "
                f"override in databricks_connection_manager.py to match the new "
                f"upstream body and update the hash constant."
            )


_assert_vendored_methods_unchanged()


class DbtChartsDatabricksConnectionManager(DatabricksConnectionManager):
    """DatabricksConnectionManager that keeps credentials and capabilities per
    connection.

    open() is the vendored body except the credential manager is authenticated into
    a call-local and handed straight to prepare_connection_arguments, never written
    to cls.credentials_manager — the vendored write-then-read has no lock, so
    concurrent opens with different credentials could cross. It also omits the
    vendored capabilities stamping: production reaches _create_fresh_connection (via
    acquire_connection -> set_connection_name) before the LazyHandle ever schedules
    open(), so conn.capabilities is already correct by then.

    _create_fresh_connection() is the vendored body except conn.capabilities is
    constructed inline rather than via _cache_dbr_capabilities /
    _get_capabilities_for_http_path, which read and write the shared class-level
    cache.

    The inherited _query_dbr_version() never sees another connection's credentials:
    this class never writes cls.credentials_manager, so a cluster lookup finds it
    None and the vendored try/except returns None (the capabilities default), while
    a SQL-warehouse lookup short-circuits before reading it.
    """

    @classmethod
    def open(cls, connection: Connection) -> Connection:
        databricks_connection = cast(DatabricksDBTConnection, connection)

        if connection.state == ConnectionState.OPEN:
            return connection

        creds = cast(DatabricksCredentials, connection.credentials)
        timeout = creds.connect_timeout

        # Deviation from vendored: a call-local, never cls.credentials_manager.
        creds_manager = creds.authenticate()

        query_header_context = databricks_connection._query_header_context
        merged_query_tags: dict[str, str] = {}
        if query_header_context:
            merged_query_tags = QueryConfigUtils.get_merged_query_tags(
                query_header_context, creds
            )

        conn_args = SqlUtils.prepare_connection_arguments(
            creds, creds_manager, databricks_connection.http_path, merged_query_tags
        )

        def connect() -> DatabricksHandle:
            try:
                conn = DatabricksHandle.from_connection_args(
                    conn_args,
                    is_cluster_http_path(
                        databricks_connection.http_path, creds.cluster_id
                    ),
                )
                if conn:
                    databricks_connection.session_id = conn.session_id
                    return conn
                else:
                    raise DbtDatabaseError("Failed to create connection")
            except Error as exc:
                logger.error(ConnectionCreateError(exc))
                raise

        def exponential_backoff(attempt: int) -> int:
            return attempt * attempt

        retryable_exceptions: list[type[Exception]] = []
        if creds.retry_all:
            retryable_exceptions = [Error]

        return cls.retry_connection(
            connection,
            connect=connect,
            logger=logger,
            retryable_exceptions=retryable_exceptions,
            retry_limit=creds.connect_retries,
            retry_timeout=(timeout if timeout is not None else exponential_backoff),
        )

    def _create_fresh_connection(
        self, conn_name: str, query_header_context: QueryContextWrapper
    ) -> DatabricksDBTConnection:
        conn = DatabricksDBTConnection(
            type=Identifier(self.TYPE),
            name=conn_name,
            state=ConnectionState.INIT,
            transaction_open=False,
            handle=None,
            credentials=self.profile.credentials,
        )
        creds = cast(  # type-state: cast — mirrors the vendored body; profile.credentials is untyped on AdapterRequiredConfig
            DatabricksCredentials, self.profile.credentials
        )
        conn.http_path = QueryConfigUtils.get_http_path(query_header_context, creds)
        conn.thread_identifier = cast(  # type-state: cast — mirrors the vendored body; get_thread_identifier() returns the untyped base Hashable
            tuple[int, int], self.get_thread_identifier()
        )
        conn._query_header_context = query_header_context
        # Deviation from vendored: inline construction, no shared-cache read/write.
        # _query_dbr_version's warehouse fast path isn't hash-guarded, but
        # has_capability() ignores dbr_version whenever is_sql_warehouse=True, so a
        # drift there can't silently change observed warehouse capabilities.
        is_cluster = is_cluster_http_path(conn.http_path, creds.cluster_id)
        dbr_version = self._query_dbr_version(creds, conn.http_path)
        conn.capabilities = DBRCapabilities(
            dbr_version=dbr_version, is_sql_warehouse=not is_cluster
        )
        conn.handle = LazyHandle(self.open)

        logger.debug(ConnectionCreate(str(conn)))
        self.set_thread_connection(conn)

        fire_event(
            NewConnection(
                conn_name=conn_name, conn_type=self.TYPE, node_info=get_node_info()
            )
        )

        return conn


class DbtChartsDatabricksAdapter(DatabricksAdapter):
    """DatabricksAdapter that never writes GlobalState.__use_managed_iceberg.

    The vendored __init__ ends with GlobalState.set_use_managed_iceberg(...), a
    process-wide write whose one read site (relation_configs/tblproperties.py,
    materialization DDL) dbt charts never executes. This __init__ reproduces the
    body minus that call; super() cannot skip a statement inside the vendored body,
    so it calls super(DatabricksAdapter, self).__init__() — the same target
    (SparkAdapter and SQLAdapter define no __init__ of their own).

    ConnectionManager is pinned so that call builds the per-connection-isolated
    manager directly — no post-construction swap needed.
    """

    ConnectionManager = DbtChartsDatabricksConnectionManager

    def __init__(
        self,
        config: Any,  # type-state: explicit_any — mirrors the vendored signature; dbt ships no Protocol for adapter config
        mp_context: SpawnContext,
    ) -> None:
        super(DatabricksAdapter, self).__init__(config, mp_context)
        self.get_column_behavior = GetColumnsByDescribe()
        # SimpleNamespace duck-types CatalogIntegrationConfig at runtime (same call as
        # the vendored body); pyright can't see that without a stub for dbt-databricks.
        self.add_catalog_integration(
            constants.DEFAULT_UNITY_CATALOG  # pyright: ignore[reportArgumentType]
        )
        self.add_catalog_integration(
            constants.DEFAULT_HIVE_METASTORE_CATALOG  # pyright: ignore[reportArgumentType]
        )
