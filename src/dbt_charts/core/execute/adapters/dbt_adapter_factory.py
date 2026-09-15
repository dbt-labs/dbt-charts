"""Single point of truth for source_config → dbt adapter instantiation.

Every consumer that needs a live dbt adapter calls build_adapter().
The adapter lives as long as the caller holds a reference; GC reclaims
it automatically via weakref.finalize, which deletes the temp target dir.
"""

from __future__ import annotations

import importlib
import logging
import multiprocessing
import shutil
import tempfile
import weakref
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from dbt_charts.core.compile.models.source import infer_bq_method
from dbt_charts.core.execute.adapters.native_attribution import (
    native_attribution_credential,
)
from dbt_charts.core.execute.adapters.query_header import QueryHeader

if TYPE_CHECKING:
    from dbt.config import RuntimeConfig
    from dbt.contracts.graph.manifest import Manifest
    from dbt_common.clients.jinja import MacroProtocol

logger = logging.getLogger(__name__)

# Mapping: source_config type → (module_path, AdapterClass, CredentialsClass)
# Add entries here when new warehouse adapters are needed.
# This is the single source of truth — dbt_adapter.py imports from here.
_ADAPTER_TYPE_MAP: dict[str, tuple[str, str, str]] = {
    "duckdb": ("dbt.adapters.duckdb", "DuckDBAdapter", "DuckDBCredentials"),
    "postgres": ("dbt.adapters.postgres", "PostgresAdapter", "PostgresCredentials"),
    "postgresql": ("dbt.adapters.postgres", "PostgresAdapter", "PostgresCredentials"),
    "redshift": ("dbt.adapters.redshift", "RedshiftAdapter", "RedshiftCredentials"),
    "snowflake": ("dbt.adapters.snowflake", "SnowflakeAdapter", "SnowflakeCredentials"),
    "bigquery": ("dbt.adapters.bigquery", "BigQueryAdapter", "BigQueryCredentials"),
    "spark": ("dbt.adapters.spark", "SparkAdapter", "SparkCredentials"),
    "databricks": (
        "dbt.adapters.databricks",
        "DatabricksAdapter",
        "DatabricksCredentials",
    ),
    "trino": ("dbt.adapters.trino", "TrinoAdapter", "TrinoCredentialsFactory"),
    "athena": ("dbt.adapters.athena", "AthenaAdapter", "AthenaCredentials"),
}


# dbt's Credentials base class requires both `database` and `schema` as str.
# dbt charts source config models define warehouse-conventional defaults for `schema`
# (e.g. "PUBLIC" for Snowflake), but the registry stores raw dicts that bypass
# Pydantic.  build_adapter applies these defaults so schema-discovery commands
# work without requiring the user to know a schema upfront.
_SCHEMA_DEFAULTS: dict[str, str] = {
    "snowflake": "PUBLIC",
    "postgres": "public",
    "postgresql": "public",
    "redshift": "public",
}

_duckdb_initialize_db_patched = False
_original_duckdb_initialize_db: Any = None


def _patched_duckdb_initialize_db(
    cls: type[Any],
    creds: Any,
    plugins: dict[str, Any] | None = None,
) -> Any:
    if (
        getattr(
            creds, "_dct_read_only", False
        )  # type-state: silent_fallback — creds is a foreign dbt Credentials instance we stamp an attribute onto conditionally; absence means read-write, the correct default
        and creds.path != ":memory:"
    ):
        import duckdb

        config = dict(creds.config_options or {})
        config.setdefault("enable_external_access", False)
        return duckdb.connect(creds.path, read_only=True, config=config)
    return _original_duckdb_initialize_db(cls, creds, plugins)


def _ensure_duckdb_readonly_initialize_db_patch() -> None:
    """Patch dbt-duckdb's hardcoded read_only=False for schema/introspection paths."""
    global _duckdb_initialize_db_patched, _original_duckdb_initialize_db
    if _duckdb_initialize_db_patched:
        return

    from dbt.adapters.duckdb import environments as duckdb_envs

    _orig_cm = cast(Any, duckdb_envs.Environment.initialize_db)
    _original_duckdb_initialize_db = _orig_cm.__func__
    duckdb_envs.Environment.initialize_db = classmethod(  # type: ignore[method-assign,assignment]
        _patched_duckdb_initialize_db
    )
    _duckdb_initialize_db_patched = True


def build_adapter(
    source_config: dict[str, Any],
    *,
    read_only: bool = False,
    register_macros: bool = True,
) -> Any:
    """Construct a fresh dbt adapter instance.

    Args:
        source_config: Dict with at minimum a 'type' key matching a known
            warehouse adapter. Other keys are translated via the credentials
            class's alias map (e.g. BigQuery project→database) and deserialized
            through from_dict, so profile-level extras like threads are dropped.
        read_only: When True, open DuckDB file sources read-only so pure-metadata
            verbs coexist with other readers (e.g. a running ``dct serve``).
            In-memory DuckDB stays read-write. Other warehouses ignore this
            flag until a driver-specific read-only hook exists.
        register_macros: When True (default), wire dbt-bundled macros onto the
            adapter so introspection (list_schemas, list_relations, …) works. Set
            False for execute-only adapters (the per-worker connection pool): raw
            SQL execution needs no macro context.

    Returns:
        A dbt adapter instance. Callers enter ``open_connection(adapter, name)``
        before calling adapter.execute(...). The temp target directory is
        deleted automatically when the adapter is GC'd.

    Raises:
        ValueError: 'type' key is missing or names an unsupported adapter.
        ImportError: The dbt adapter package for this warehouse is not installed.
    """
    adapter_type = source_config.get("type")
    if not adapter_type:
        raise ValueError(
            f"source_config must have a 'type' field. Got: {list(source_config.keys())}"
        )

    adapter_type_lower = str(adapter_type).lower()
    entry = _ADAPTER_TYPE_MAP.get(adapter_type_lower)
    if entry is None:
        raise ValueError(
            f"Unsupported adapter type '{adapter_type}'. "
            f"Supported: {', '.join(sorted(_ADAPTER_TYPE_MAP))}. "
            f"For other warehouses, install the relevant dbt-<warehouse> package "
            f"and add an entry to _ADAPTER_TYPE_MAP in dbt_adapter_factory.py."
        )

    module_path, adapter_name, creds_name = entry

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"dbt-{adapter_type_lower} is not installed. "
            f"Install it with: pip install dbt-{adapter_type_lower}"
        ) from e

    adapter_cls = getattr(mod, adapter_name)
    creds_cls = getattr(mod, creds_name)

    # Strip 'type' and 'attribution' (dbt charts cost metadata, not a dbt credential),
    # translate profile-style aliases (e.g. BigQuery project→database), then use
    # from_dict so extras like threads are silently dropped.
    creds_kwargs = {
        k: v for k, v in source_config.items() if k not in ("type", "attribution")
    }
    if adapter_type_lower == "duckdb":
        if "duckdb_config" in creds_kwargs:
            # dbt charts' source schema names this field `duckdb_config`; dbt-duckdb's
            # creds class names it `config_options`. Rename so the value flows to
            # the patched read-only initialize_db path, which reads
            # `creds.config_options`. Without this, separate build_adapter() callers
            # (LayeredSchemaResolver, dbt-charts-super-schema) open the same DuckDB
            # file with different config than the playground's SqlAdapter and DuckDB
            # rejects the second connection.
            creds_kwargs["config_options"] = creds_kwargs.pop("duckdb_config")
        # dbt-duckdb defaults keep_open=True, which parks the warehouse handle on
        # its process-global environment for the life of the process — outliving
        # the registry that opened it. DuckDB then rejects every later connection
        # to that file whose config differs, so an authoring session (external
        # access on) poisoned the next strict read-only one. Same posture
        # DuckDBAdapter already takes for read-only file sources: hold the file
        # only while a connection is live. dbt-duckdb keeps :memory: open
        # regardless — closing an in-memory database would destroy it.
        creds_kwargs.setdefault("keep_open", False)
    if adapter_type_lower == "bigquery" and "method" not in creds_kwargs:
        creds_kwargs["method"] = infer_bq_method(
            creds_kwargs.get("keyfile"), creds_kwargs.get("keyfile_json")
        )
    creds_kwargs = creds_cls.translate_aliases(creds_kwargs)
    if "schema" not in creds_kwargs:
        default_schema = _SCHEMA_DEFAULTS.get(adapter_type_lower)
        if default_schema is not None:
            creds_kwargs["schema"] = default_schema
    # After translate_aliases so the field lands under the name the credentials class
    # reads. Fills the warehouse's own identity field so "is this dbt charts" is a
    # column rather than a regex over the query text — but only where the author left
    # it unset, since these fields can drive real monitoring or governance rules. An
    # unset optional arrives as an explicit None, not a missing key.
    for field, value in native_attribution_credential(adapter_type_lower).items():
        if creds_kwargs.get(field) is None:
            creds_kwargs[field] = value
    # Redshift is Postgres's fork and holds an idle read transaction the same way,
    # but it reaches the driver through the credential rather than the psycopg2
    # handle: dbt-redshift sets autocommit only `if credentials.autocommit`. Set
    # unconditionally for the same reason as the Postgres connection manager — it
    # describes how dbt charts uses the connection, not a preference about the
    # author's warehouse, and on a SELECT-only connection an authored `false` can
    # do nothing but pin locks. (Postgres needs the handle instead because
    # dbt-postgres had no such credential before 1.11 and this package supports
    # 1.10, where the key is silently dropped.)
    if adapter_type_lower == "redshift":
        creds_kwargs["autocommit"] = True
    creds = creds_cls.from_dict(creds_kwargs)
    if read_only and adapter_type_lower == "duckdb":
        _ensure_duckdb_readonly_initialize_db_patch()
        creds._dct_read_only = True

    # Build a minimal duck-typed config satisfying AdapterRequiredConfig.
    # No dbt_project.yml, no manifest — but enough for dbt's macro context to
    # construct itself, since list_schemas / list_relations / get_columns route
    # through execute_macro -> generate_runtime_macro_context, which reads
    # quoting / dependencies / args / vars / load_dependencies / project_root /
    # get_macro_search_order off the config.
    profile_name = f"dct_{adapter_type_lower}"
    target_path = tempfile.mkdtemp(  # noqa: TID251 — dbt macro-context scratch dir
        prefix="dct-target-"
    )

    def _get_macro_search_order(
        _namespace: object,  # type-state: object_annotation — dbt callback contract
    ) -> None:
        return None

    def _to_target_dict() -> dict[str, object]:
        return dict(source_config)

    cfg = SimpleNamespace(
        credentials=creds,
        profile_name=profile_name,
        target_name="dev",
        threads=1,
        project_name=profile_name,
        # job_label=True is the gate dbt-bigquery checks before it will read
        # query_header for labels; `comment` stays empty because our query header
        # supplies the payload directly rather than through dbt's macro renderer.
        query_comment=SimpleNamespace(comment="", append=False, job_label=True),
        cli_vars={},
        target_path=target_path,
        project_root=target_path,
        log_cache_events=False,
        quoting={"database": True, "schema": True, "identifier": True},
        dependencies={},
        args=SimpleNamespace(vars={}),
        vars=_NoVars(),
        flags={},
        load_dependencies=dict,
        get_macro_search_order=_get_macro_search_order,
        to_target_dict=_to_target_dict,
    )

    mp_context = multiprocessing.get_context("spawn")
    adapter = adapter_cls(cfg, mp_context)
    if adapter_type_lower in ("postgres", "postgresql"):
        from dbt_charts.core.execute.adapters.postgres_connection_manager import (
            autocommit_connections,
        )

        adapter.connections = autocommit_connections(adapter_cls.ConnectionManager)(
            adapter.config, mp_context
        )
    if adapter_type_lower == "databricks":
        from dbt_charts.core.execute.adapters.databricks_connection_manager import (
            DbtChartsDatabricksConnectionManager,
        )

        adapter.connections = DbtChartsDatabricksConnectionManager(
            adapter.config, mp_context
        )
    # Must follow any connection-manager replacement above: a fresh manager starts
    # with query_header=None, so attaching earlier would silently drop attribution
    # for that warehouse.
    #
    # BigQuery carries the payload as structured job labels, so its SQL text is left
    # byte-identical: a comment would put per-query text into the query string, and
    # BigQuery's free result cache keys on that string. Every other warehouse takes
    # the comment — Snowflake and Databricks also key result reuse on query text, so
    # two *different* boards running identical SQL no longer share a cached result
    # there; repeats of the same board still do, which is the case that matters.
    adapter.connections.query_header = QueryHeader(
        emit_sql_comment=adapter_type_lower != "bigquery"
    )
    # Register cleanup to fire when the adapter is GC'd.
    # shutil.rmtree is called with ignore_errors=True so a missing dir is skipped.
    weakref.finalize(
        adapter,
        shutil.rmtree,  # noqa: TID251 — cleans up its own scratch dir
        target_path,
        True,
    )
    if register_macros:
        _bootstrap_macros(adapter, adapter_type_lower)
    return adapter


class ConnectionSetupFailed(Exception):
    """Building or connecting a dbt adapter failed (bad credentials, unreachable
    host, misconfigured source) before any SQL reached the warehouse.

    Raised instead of the driver's own exception so a caller can route it to
    ``connection_failure`` rather than ``classify_warehouse_error``.
    """

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


@contextmanager
def open_connection(
    adapter: Any,  # type-state: explicit_any — a dbt adapter; dbt ships no Protocol for it
    name: str,
) -> Generator[None]:
    """``adapter.connection_named(name)`` with the warehouse handle forced open first.

    A connect failure raises as ``ConnectionSetupFailed`` before any SQL is
    sent. ``connection_named`` releases on every exit, and dbt-bigquery's
    release dereferences the handle a failed open left as None, so without this
    the AttributeError it raises would replace the credential error — or a
    ``return`` from inside the block — and the caller would see the cleanup
    crash. Anything else the block or the release raises propagates as itself.
    """
    open_error: Exception | None = None
    try:
        with adapter.connection_named(name):
            try:
                _ = adapter.connections.get_thread_connection().handle
            except Exception as e:  # noqa: BLE001 — any driver's connect failure
                open_error = e
                raise
            yield
    except Exception:  # noqa: BLE001 — must see everything leaving the block to consult open_error
        if open_error is None:
            raise
        raise ConnectionSetupFailed(open_error) from open_error


def _bootstrap_macros(adapter: Any, adapter_type: str) -> None:
    """Wire dbt-bundled macros onto the adapter so introspection works.

    Without this, every BaseAdapter introspection method (list_schemas,
    list_relations, get_columns_in_relation, calculate_freshness_from_metadata,
    get_partitions_metadata) raises "Macro resolver was None" because
    execute_macro() needs a populated _macro_resolver.

    The macro context resolves `adapter` via dbt-core's MacroContext.__init__,
    which calls get_adapter(self.config) = FACTORY.lookup_adapter(creds.type) —
    a process-global slot keyed only by warehouse type. Binding that slot per
    build_adapter() call is last-writer-wins: under gthread two same-type
    introspections race it and one tenant's macros run on another tenant's
    connection. So instead of a persistent slot, the macro-context generator is
    bound to *this* adapter: it registers the adapter in the slot only for the
    in-memory context build, under FACTORY.lock so a concurrent build can't
    overwrite it before get_adapter reads it, then pops it. The query itself
    runs after the generator returns (execute_macro opens the connection then),
    so nothing serializes on I/O and no slot persists to clobber or go stale.

    The generator closes over the adapter and is stored back onto it via
    set_macro_context_generator, so the adapter becomes part of a reference
    cycle; weakref.finalize is cycle-safe (PEP 442), so the temp dir is still
    reclaimed, by the cycle collector rather than at the last drop.
    """
    from dbt.adapters.factory import FACTORY
    from dbt.context.providers import generate_runtime_macro_context

    from dbt_charts.core.execute.adapters.dbt_macro_loader import load_macro_manifest

    # Signature mirrors dbt's generate_runtime_macro_context, the function we
    # forward to: config/manifest are the duck-typed cfg + macro resolver dbt
    # hands back through execute_macro. package_name is None because every
    # introspection method (list_schemas/list_relations/get_columns_in_relation)
    # dispatches execute_macro without a project.
    def generate(
        macro: MacroProtocol,
        config: RuntimeConfig,
        manifest: Manifest,
        package_name: None,
    ) -> dict[str, object]:
        with FACTORY.lock:
            FACTORY.adapters[adapter_type] = adapter
            try:
                return generate_runtime_macro_context(
                    macro, config, manifest, package_name
                )
            finally:
                FACTORY.adapters.pop(adapter_type, None)

    adapter.set_macro_resolver(load_macro_manifest(adapter_type))
    adapter.set_macro_context_generator(generate)


class _NoVars:
    """Stub for `RuntimeConfig.vars` accessed during macro context build.

    Macros we care about (list_schemas, list_relations, get_columns_in_relation)
    don't reference any project vars, so an empty `vars_for` is the right shape.
    """

    def vars_for(
        self,
        _lookup: Any,  # type-state: explicit_any — dbt's IsFQNResource, unread here
        _adapter_type: str,
    ) -> dict[str, Any]:  # type-state: explicit_any — mirrors dbt's Mapping[str, Any]
        return {}
