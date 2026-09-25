"""Public dbt_charts.core connection API.

Exports test_connection(source_config) -> tuple[bool, WarehouseProbeError | None],
probe_relation_readability, bulk_schema_for_config, plus the BigQuery discovery
helpers hosts use to fill a connection form: build_bigquery_client,
list_datasets, dataset_location — the latter two return real values on success,
so they keep raising on failure rather than a tuple.

All DB connection machinery lives in execute/adapters/dbt_adapter_factory.py.
Cloud and other consumers call this, not dbt.adapters directly — Cloud in
particular must never import google.cloud itself.

Discovery deliberately bypasses build_adapter: dbt's Credentials base requires
`schema` (the BigQuery dataset) as a str, which is the very thing discovery is
looking for, so routing through an adapter would mean inventing a sentinel
dataset just to construct one. The BigQuery client needs no dataset at all.

test_connection, list_datasets, and dataset_location share one classification
boundary: every warehouse-outcome exception they can produce is turned into a
WarehouseProbeError (or a classified subclass), mirroring
apps.cloud.apps.projects.git_providers.GitRemoteError — test_connection
returns it, the discovery pair raises it. Each function carves out an
authored setup/environment error that propagates unclassified instead of
being genericized into warehouse-probe copy it doesn't describe:
test_connection's build_adapter can raise a missing-driver-package
ImportError or an unsupported-adapter-type ValueError; list_datasets and
dataset_location carve out only a missing-optional-dependency ImportError —
a ValueError from the google SDK (e.g. a malformed PEM file) is a real
warehouse outcome there and is classified like any other. See
WarehouseProbeError's docstring for the str(exc)/.detail contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import BigQuerySourceConfig, SourceConfig
    from dbt_charts.core.inspect.bulk_schema import SchemaTree
    from dbt_charts.core.inspect.relations import Relation


class WarehouseProbeError(Exception):
    """A classified failure from a live warehouse probe (test_connection,
    list_datasets, dataset_location).

    ``str(exc)`` is always authored display copy: safe to show a user,
    persist to a database row, or return from an API. The raw driver/SDK
    text — which can carry internal hostnames, service-account emails, or
    project ids — travels only in ``.detail``, for logs. Also the fallback
    bucket when an exception can't be placed in a more specific subclass
    below.

    The safety guarantee covers ``str(exc)`` only, not the whole object: the
    discovery pair raises via ``raise ... from e``, so ``exc.__cause__`` (and
    a traceback of it) still carries the raw text. Log ``.detail``
    explicitly; never format or render this exception's traceback.
    """

    def __init__(self, display: str, *, detail: str = "") -> None:
        self.detail = detail
        super().__init__(display)


class WarehouseAuthError(WarehouseProbeError):
    """The warehouse rejected the credentials."""


class WarehouseUnreachableError(WarehouseProbeError):
    """The warehouse could not be reached at all: refused, unresolvable, or
    timed out. Never split further — for every family whose driver reports
    it, the three causes arrive in the same, undifferentiated text."""


class WarehouseDatabaseNotFoundError(WarehouseProbeError):
    """The named database, dataset, project, or file does not exist."""


class WarehousePermissionError(WarehouseProbeError):
    """The credential is valid but lacks a specific grant. Display names the
    missing permission — a public API constant, never the principal that is
    missing it."""


_GENERIC_PROBE_MESSAGE = (
    "Could not connect to the warehouse. Check your connection details and try again."
)
# Shared by both classifiers below — each buckets a family-specific text or
# type onto the same authored sentence, so one copy avoids the two ever
# drifting apart.
_UNREACHABLE_MESSAGE = "Could not reach the warehouse host."
_BIGQUERY_AUTH_MESSAGE = "The service account credentials could not be used."
_NOT_FOUND_MESSAGE = "The named project or dataset does not exist."
# A local copy, not execute.adapters.base.BIGQUERY_UNKNOWN_REF_SUBSTRINGS: that
# tuple also matches a query's own column/table references, so importing it
# here would let a binder-motivated edit silently change this classification.
_BIGQUERY_NOT_FOUND_SUBSTRING = "Not found: Dataset"


def classify_connection_error(source_type: str, exc: Exception) -> WarehouseProbeError:
    """Classify a test_connection driver failure into a WarehouseProbeError.

    Every dbt-adapter family this project supports flattens its driver
    exception to a bare string before it reaches here: dbt-core's
    ``retry_connection`` re-raises ``FailedToConnectError(str(e))`` with no
    ``from e``, so no driver exception *type* survives — classification is
    per-family substring matching against the server's own emitted text,
    gated on ``source_type`` because the same substring can mean different
    things in a different family's grammar.

    Ship only the rules the evidence supports for a family; anything else —
    including a whole family with no live-verified rules (Redshift's
    auth/not-found rows) — falls to the generic bucket rather than risk a
    confidently wrong classification.
    """
    text = str(exc) or type(exc).__name__

    if source_type == "postgres":
        if "password authentication failed for user" in text:
            return WarehouseAuthError(
                "The warehouse rejected the username or password.", detail=text
            )
        if "does not exist" in text and 'database "' in text:
            return WarehouseDatabaseNotFoundError(
                "The named database does not exist.", detail=text
            )
        if "permission denied for database" in text:
            return WarehousePermissionError(
                "The credential does not have permission to connect to this database.",
                detail=text,
            )
        if (
            "Connection refused" in text
            or "could not translate host name" in text
            or "timeout expired" in text
        ):
            return WarehouseUnreachableError(_UNREACHABLE_MESSAGE, detail=text)
    elif source_type == "redshift":
        # Auth / database-not-found rules are deliberately absent: the
        # evidence for them is LOW confidence (no live Redshift cluster to
        # verify wording against) and a wrong bucket is worse than the
        # generic one.
        if "communication error" in text or "connection time out" in text:
            return WarehouseUnreachableError(_UNREACHABLE_MESSAGE, detail=text)
    elif source_type == "snowflake":
        if "Incorrect username or password" in text:
            return WarehouseAuthError(
                "The warehouse rejected the username or password.", detail=text
            )
        if "/session/v1/login-request" in text and "404 Not Found" in text:
            # Fires before credentials are even checked, so it doesn't fit
            # any of the five buckets — a dedicated authored sentence keyed
            # on the account identifier the user themselves typed.
            return WarehouseProbeError(
                "The Snowflake account identifier was not recognized. Check"
                " your account identifier and try again.",
                detail=text,
            )
        if "Could not connect to Snowflake backend after" in text:
            return WarehouseUnreachableError(_UNREACHABLE_MESSAGE, detail=text)
    elif source_type == "duckdb":
        if "No such file or directory" in text:
            return WarehouseDatabaseNotFoundError(
                "The database file's directory does not exist.", detail=text
            )
        if "Permission denied" in text:
            return WarehousePermissionError(
                "The database file could not be opened: permission denied.",
                detail=text,
            )
        if "Is a directory" in text:
            return WarehouseProbeError(
                "The database path points at a directory, not a file.",
                detail=text,
            )
        if (
            "is not a valid DuckDB database file" in text
            or "Could not read enough bytes" in text
            or "Corrupt database file" in text
        ):
            return WarehouseProbeError(
                "The database path is not a valid DuckDB database file.",
                detail=text,
            )
        if "Could not set lock on file" in text:
            # The raw text embeds the OS username and PID of the other
            # process — never surface it.
            return WarehouseProbeError(
                "The database file is locked by another process.", detail=text
            )
        if "File name too long" in text:
            return WarehouseProbeError("The database path is too long.", detail=text)
        if "Unable to determine target database name" in text:
            # An empty or trailing-slash path (see test_connection's docstring
            # on this arrival shape).
            return WarehouseProbeError(
                "The database path is empty or invalid.", detail=text
            )
    elif source_type == "bigquery":
        if (
            "Unable to load PEM file" in text
            or "Service account info was not in the expected format" in text
        ):
            return WarehouseAuthError(_BIGQUERY_AUTH_MESSAGE, detail=text)
        if "Unable to generate access token" in text or "invalid_grant" in text:
            return WarehouseAuthError(
                "The warehouse rejected these credentials.", detail=text
            )
        if "Access denied while running query" in text:
            return WarehousePermissionError(
                "The credential does not have permission to run this query.",
                detail=text,
            )
        if _BIGQUERY_NOT_FOUND_SUBSTRING in text:
            return WarehouseDatabaseNotFoundError(_NOT_FOUND_MESSAGE, detail=text)

    return WarehouseProbeError(_GENERIC_PROBE_MESSAGE, detail=text)


def _classify_bigquery_discovery_error(
    exc: Exception, *, permission: str
) -> WarehouseProbeError:
    """Classify a list_datasets / dataset_location failure.

    Unlike the test_connection path, this boundary calls the google SDK
    directly, so real google.auth / google.api_core exception types survive
    the round-trip — isinstance is the discriminator here, with one
    substring split on ``Forbidden``, which google also raises for quota
    exhaustion, not only a missing permission.

    ``permission`` is the IAM permission this call actually needs
    (``bigquery.datasets.list`` for ``list_datasets``, ``.get`` for
    ``dataset_location``) — the two calls fail on different grants, so a
    shared classifier must not hard-code either one's name into the other's
    display.
    """
    text = str(exc) or type(exc).__name__
    if isinstance(exc, ValueError) and "Unable to load PEM file" in text:
        return WarehouseAuthError(_BIGQUERY_AUTH_MESSAGE, detail=text)

    from google.api_core.exceptions import Forbidden, NotFound
    from google.auth.exceptions import MalformedError, RefreshError, TransportError

    if isinstance(exc, (MalformedError, RefreshError)):
        return WarehouseAuthError(_BIGQUERY_AUTH_MESSAGE, detail=text)
    if isinstance(exc, TransportError):
        return WarehouseUnreachableError(_UNREACHABLE_MESSAGE, detail=text)
    if isinstance(exc, Forbidden):
        if "quota exceeded" in text.lower():
            return WarehouseProbeError(
                "The warehouse's request quota was exhausted. Try again later.",
                detail=text,
            )
        return WarehousePermissionError(
            f"The credential does not have the {permission} permission needed"
            " for this project.",
            detail=text,
        )
    if isinstance(exc, NotFound):
        return WarehouseDatabaseNotFoundError(_NOT_FOUND_MESSAGE, detail=text)
    return WarehouseProbeError(_GENERIC_PROBE_MESSAGE, detail=text)


def import_bigquery(module_name: str) -> Any:
    """Import an optional google-cloud-bigquery module, naming the extra if absent.

    google-cloud-bigquery ships in the `bigquery` extra, not the runtime deps,
    so a plain ImportError here would read as a bug rather than a missing
    install.
    """
    import importlib

    try:
        return importlib.import_module(module_name)
    except ImportError as err:
        raise ImportError(
            f"{module_name} is required for BigQuery connections. "
            "Install the bigquery extra: pip install 'dbt-charts[bigquery]'"
        ) from err


def build_bigquery_client(
    project: str,
    keyfile_json: dict[str, Any] | None = None,
    keyfile: str | None = None,
) -> Any:
    """Construct a google.cloud.bigquery.Client.

    Takes the three credential inputs rather than a SourceConfig so callers
    holding a raw source-config dict (dbt-charts-super-schema) and callers
    holding a typed model share one implementation.

    Mirrors dbt-bigquery's credential resolution order:
      1. keyfile_json dict → service account credentials
      2. keyfile path     → service account credentials from file
      3. neither          → Application Default Credentials (ADC / metadata server)
    """
    bigquery = import_bigquery("google.cloud.bigquery")
    if keyfile_json is not None:
        service_account = import_bigquery("google.oauth2.service_account")
        creds = service_account.Credentials.from_service_account_info(keyfile_json)
    elif keyfile is not None:
        service_account = import_bigquery("google.oauth2.service_account")
        creds = service_account.Credentials.from_service_account_file(keyfile)
    else:
        creds = None  # ADC / metadata server
    return bigquery.Client(project=project, credentials=creds)


def _client_for(source_config: BigQuerySourceConfig) -> Any:
    return build_bigquery_client(
        source_config.project, source_config.keyfile_json, source_config.keyfile
    )


def list_datasets(source_config: SourceConfig) -> list[str]:
    """Dataset names reachable with these credentials.

    The source_config's own `dataset` is irrelevant here and may be empty —
    this is what the connection form calls before the user can know which
    datasets exist.
    """
    from dbt_charts.core.compile.models.source import BigQuerySourceConfig

    if not isinstance(source_config, BigQuerySourceConfig):
        raise ValueError(
            f"list_datasets is BigQuery-only, got {source_config.type!r}. "
            "Other warehouses list schemas through the dbt adapter."
        )
    try:
        client = _client_for(source_config)
        return [ds.dataset_id for ds in client.list_datasets()]
    except ImportError:
        # A missing optional dep is an environment/setup problem for the
        # operator to fix, not a warehouse outcome to classify.
        raise
    except Exception as e:  # noqa: BLE001 — classify at the SDK boundary
        raise _classify_bigquery_discovery_error(
            e, permission="bigquery.datasets.list"
        ) from e


def dataset_location(source_config: SourceConfig) -> str:
    """Location of the dataset named in source_config.

    BigQuery-only, like list_datasets — no other warehouse has a dataset-level
    location, so a non-BigQuery config is a caller bug rather than a None.

    Resolved one dataset at a time on purpose: list_datasets() returns partial
    resources without a location, so filling it for every listed dataset would
    be an N+1 across potentially hundreds of them.
    """
    from dbt_charts.core.compile.models.source import BigQuerySourceConfig

    if not isinstance(source_config, BigQuerySourceConfig):
        raise ValueError(
            f"dataset_location is BigQuery-only, got {source_config.type!r}."
        )
    if not source_config.dataset:
        raise ValueError("dataset_location requires a dataset to look up.")
    try:
        location: str = (
            _client_for(source_config).get_dataset(source_config.dataset).location
        )
    except ImportError:
        raise
    except Exception as e:  # noqa: BLE001 — classify at the SDK boundary
        raise _classify_bigquery_discovery_error(
            e, permission="bigquery.datasets.get"
        ) from e
    return location


def test_connection(
    source_config: SourceConfig,
) -> tuple[bool, WarehouseProbeError | None]:
    """Verify that source_config can reach the database.

    Constructs a fresh adapter, opens the connection, pings with SELECT 1, and
    lets GC clean up when the local reference drops at function exit. The temp
    target dir is removed by the weakref.finalize registered in build_adapter.

    A connect failure is classified into a WarehouseProbeError rather than
    left as a bare driver string:
    ``open_connection`` raises it typed (``ConnectionSetupFailed``), before
    any SQL, and ahead of whatever the release raises on the way out. Not
    every failure arrives that way, though — some adapters (DuckDB on an
    empty path) raise before ``open_connection`` even runs, and a query-phase
    failure during the ``SELECT 1`` itself lands in the broad except below —
    so both branches classify.

    Args:
        source_config: Typed SourceConfig instance (DuckDBSourceConfig,
            PostgresSourceConfig, etc.).

    Returns:
        (True, None) on success.
        (False, <classified WarehouseProbeError>) on any warehouse-probe
        failure — bad creds, network unreachable, etc. See the class
        docstring for the str(exc)/.detail contract.

    Raises:
        ImportError: The dbt adapter package for this warehouse is not
            installed — an environment/setup problem for the operator to fix,
            not a warehouse outcome, so it propagates unclassified (same
            authored message ``build_adapter`` raises).
        ValueError: ``source_config`` naming an unsupported adapter type.
            Both carve-outs are scoped to ``build_adapter``; a ``ValueError``
            raised deeper in the probe is classified like any other escape,
            since it can carry driver internals.
    """
    from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
        ConnectionSetupFailed,
        build_adapter,
        open_connection,
    )

    creds = source_config.model_dump(
        by_alias=True, exclude_unset=True, exclude_none=True
    )
    try:
        adapter = build_adapter(creds)
    except (ImportError, ValueError):
        # Scoped to build_adapter — a ValueError raised deeper (mashumaro's
        # InvalidFieldValue reprs the offending value) classifies like any
        # other escape rather than propagating its text.
        raise
    except Exception as e:  # noqa: BLE001 — e.g. DuckDB's empty-path DbtRuntimeError
        return False, classify_connection_error(source_config.type, e)

    try:
        with open_connection(adapter, "test"):
            adapter.execute("SELECT 1", auto_begin=False, fetch=True)
    except ConnectionSetupFailed as e:
        return False, classify_connection_error(source_config.type, e.cause)
    except Exception as e:  # noqa: BLE001 — classify every driver-level escape
        return False, classify_connection_error(source_config.type, e)
    return True, None


def bulk_schema_for_config(source_config: SourceConfig) -> SchemaTree:
    """Return source_config's whole schema tree from a single warehouse query.

    Connection-scoped sibling of ``bulk_schema`` — same SQL builder and
    row→tree parser, but keyed by a bare ``SourceConfig`` (via ``build_adapter``,
    the ``test_connection`` pattern) instead of an ``AdapterRegistry``. For
    hosts holding a connection's credentials with no project/registry, such
    as Cloud's per-connection schema profile.

    Fails only through the same narrow contract as ``bulk_schema``:

      * ``NotImplementedError`` — the dialect has no bulk-introspection form
        (e.g. BigQuery without a region qualifier).
      * ``RuntimeError`` — the query returned or raised an error (any driver
        exception is normalized into this), or the result was truncated by the
        ``execution.max_rows``/``DCT_MAX_ROWS_CEILING`` ceiling — a partial
        tree would silently under-count, so it raises instead.
    """
    # Function-local like every other dbt_charts import in this module: keeping
    # the module itself leaf-light is what lets `dbt_charts.cli.main` import
    # without eagerly loading core.compile (pinned by test_lazy_imports).
    from dbt_charts.core.dialects import get_dialect
    from dbt_charts.core.execute.adapters.base import (
        apply_row_limit_truncation,
        resolve_effective_row_limit,
    )
    from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
        build_adapter,
        open_connection,
    )
    from dbt_charts.core.inspect.bulk_schema import (
        bulk_schema_scope,
        parse_bulk_schema_rows,
    )

    creds = source_config.model_dump(
        by_alias=True, exclude_unset=True, exclude_none=True
    )
    source_type = str(creds.get("type"))
    dialect = get_dialect(source_type)
    sql = dialect.bulk_schema_sql(bulk_schema_scope(source_type, creds))

    # Same execution.max_rows/DCT_MAX_ROWS_CEILING guard as the registry path:
    # bound the driver fetch where the cursor supports it, post-slice otherwise,
    # and raise rather than return a silently partial tree.
    row_limit = resolve_effective_row_limit(None)
    driver_limit = (
        row_limit.fetch_limit if dialect.cursor_supports_driver_limit else None
    )

    try:
        # No macro context: this runs plain metadata SQL on every connection
        # save in a long-lived worker, and register_macros' bootstrap closure
        # keeps the adapter (and its warehouse session) alive until a gen-GC
        # sweep.
        adapter = build_adapter(creds, register_macros=False)
        with open_connection(adapter, "bulk_schema_for_config"):
            _response, table = adapter.execute(
                sql, auto_begin=False, fetch=True, limit=driver_limit
            )
        rows, truncated_reason = apply_row_limit_truncation(list(table.rows), row_limit)
        tree = parse_bulk_schema_rows(
            dict(zip(table.column_names, row, strict=True)) for row in rows
        )
    except (
        Exception  # noqa: BLE001 — normalize driver errors to RuntimeError
    ) as e:
        raise RuntimeError(f"bulk_schema_for_config query failed: {e}") from e
    if truncated_reason is not None:
        raise RuntimeError(
            f"bulk_schema_for_config query for {source_type!r} was truncated to "
            f"{row_limit.effective_limit} rows by {truncated_reason!r} — schema "
            "tree is incomplete. Raise execution.max_rows or "
            "DCT_MAX_ROWS_CEILING above the source's column count to fix this."
        )
    return tree


def probe_relation_readability(
    source_config: SourceConfig, relation: Relation
) -> tuple[bool, str]:
    """Verify *relation* is readable with *source_config*'s credential.

    ``SELECT * FROM <relation> LIMIT 0`` — a live probe. Portable across every
    supported dialect and truthful regardless of how the grant was applied (dbt,
    Terraform, or by hand), since it asks the warehouse directly rather than
    reading declared config. Zero rows are ever fetched or returned; only
    whether the statement itself was accepted.

    Args:
        source_config: The connection's own credential.
        relation: One relation from ``inspect.relations.relations_read_by`` —
            ``database``/``schema`` may be None when the query didn't qualify it.

    Returns:
        (True, "") when the relation is readable.
        (False, "<error message>") on any failure — missing grant, missing
        relation, unreachable warehouse. Never raises: a caller probes many
        relations in a loop and one failure must not abort the rest.
    """
    from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
        build_adapter,
        open_connection,
    )

    creds = source_config.model_dump(
        by_alias=True, exclude_unset=True, exclude_none=True
    )
    try:
        adapter = build_adapter(creds)
        qualified = ".".join(
            adapter.quote(part)
            for part in (relation.database, relation.schema, relation.name)
            if part
        )
        with open_connection(adapter, "probe_relation_readability"):
            adapter.execute(
                f"SELECT * FROM {qualified} LIMIT 0", auto_begin=False, fetch=True
            )
        return True, ""
    except (
        Exception  # noqa: BLE001
    ) as e:  # broad on purpose — surfaces driver-level errors as messages
        return False, str(e) or type(e).__name__
