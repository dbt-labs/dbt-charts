"""DuckDB adapter for direct DuckDB query execution.

Stage: EXECUTE
Purpose: Execute SQL queries via the raw duckdb driver (parameterized, fast).

All DuckDB execution — file-based or :memory: — goes through this adapter,
driven by a typed DuckDBSourceConfig.  Non-DuckDB warehouses use SqlAdapter
(dbt-adapters path).

Security: DuckDB uses parameterized queries ($1, $2 placeholders). The
read-only flag and enable_external_access config are enforced here. A source
authoring `attach`, `extensions`, or `secrets` is an explicit opt-in that
needs external access to do anything (ATTACH, INSTALL, CREATE SECRET all
require it). External access is not forced off on that connection the way
it is by default, but read_only itself is never relaxed: when the adapter
is read_only, every ATTACH gets READ_ONLY forced onto it regardless of the
authored `attach.read_only` flag, closing the gap where a `:memory:` main
database (always opened read-write; there is no other way to populate it)
would otherwise leave an attached database writable. `extensions` stays in
that opt-in list even for a bare string (LOAD-only, already installed):
`install_extension()` always touches the filesystem, whether or not the
extension needs downloading, so there is no cheap way to tell "this one
needs a network fetch" from "this one is already on disk" before calling
it; the source authoring `extensions` at all is the signal, not the entry
shape. `settings` applies unconditionally (plain SET, no external-access
requirement).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable
from pathlib import Path  # noqa: TID251 — resolves the DuckDB database file on disk
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    import duckdb

    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.project import Project

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import DuckDBSourceConfig, SourceOptionScalar
from dbt_charts.core.compile.sql_guard import validate_select_only, validate_setup_sql
from dbt_charts.core.compile.template.parameterized import render_parameterized
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_ADAPTER_DUCKDB_CONFIG_APPLY_FAILED,
    ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
    ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
    ERR_ADAPTER_UNSUPPORTED_DUCKDB_FIELD,
    ERR_BINDER_TYPE_MISMATCH,
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.dialects import get_dialect
from dbt_charts.core.execute.adapters.base import (
    BaseAdapter,
    QueryParams,
    QueryResult,
    apply_row_limit_truncation,
    connection_failure,
    handle_adapter_error,
    plain_error,
    resolve_effective_row_limit,
    resolve_setup_sql,
)
from dbt_charts.core.execute.adapters.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.duckdb_config import normalize_duckdb_config

# Relative read_csv()/read_parquet() paths resolve against data_dir via a
# per-connection `SET file_search_path` applied at connection creation. This lock
# serializes execute/fetchall because the default connection (`self._connection`)
# is shared across threads; removing it requires thread-local connections and a
# close()-vs-in-flight-query contract (see task
# parallelize-duckdb-execution-remove-the-execute-lock).
_DUCKDB_EXECUTE_LOCK = threading.RLock()

# Identifiers we splice directly into SQL text (SET/ATTACH/CREATE SECRET keys,
# extension names, attach aliases). DuckDB has no way to bind them as
# parameters, so they are validated against this pattern instead of quoted.
_DUCKDB_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Value shapes for one CREATE SECRET field (DuckDBSecretConfig's declared
# fields plus its extra="allow" provider fields) and one ATTACH option
# (DuckDBAttachmentConfig's declared fields plus its `options` dict).
DuckDBSecretValue = (
    SourceOptionScalar | list[SourceOptionScalar] | dict[str, SourceOptionScalar]
)
DuckDBAttachValue = SourceOptionScalar | dict[str, SourceOptionScalar]

# model_dump() of any resolved source config (postgres, snowflake, duckdb,
# ...) routed to this adapter, before the duckdb-specific fields are read out
# of it. Heterogeneous by construction across warehouse types.
RawSourceConfig = dict[str, Any]  # type-state: explicit_any — warehouse dump

# DuckDB source fields the in-process adapter does not implement (they need
# dbt-duckdb's own connection lifecycle: plugin hooks, fsspec registration,
# remote transport). Authoring one is a fail-loud error, not a silent no-op.
_UNSUPPORTED_DUCKDB_FIELDS = (
    "plugins",
    "filesystems",
    "remote",
    "use_credential_provider",
)

# Every source field _connect applies to a pooled connection, beyond path and
# schema. The connection-pool cache key must cover all of them: two sources
# sharing a path but differing in any of these must never share a session.
_CONNECTION_IDENTITY_FIELDS = (
    "duckdb_config",
    "extensions",
    "settings",
    "secrets",
    "attach",
)


def _duckdb_session_cache_key(
    source_config: RawSourceConfig,
    path: str,
    schema: str,
) -> str:
    """Stable cache key for the pooled connection this source config produces.

    A hash, not the raw config, so the key stays a short string regardless of
    how large `attach`/`secrets` is.
    """
    identity = {
        field: source_config.get(field) for field in _CONNECTION_IDENTITY_FIELDS
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"{path}\0{schema}\0{digest}"


def _duckdb_ident(value: str, what: str) -> str:
    """Validate `value` is a safe bare SQL identifier before splicing it in."""
    if not _DUCKDB_IDENT_RE.match(value):
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field=what,
            reason=f"{value!r} must match ^[A-Za-z_][A-Za-z0-9_]*$",
        )
    return value


def _duckdb_literal(value: str) -> str:
    """Escape a string for embedding as a single-quoted SQL literal."""
    return value.replace("'", "''")


def _classify_duckdb_error(
    exc: Exception, operation: str = "DuckDB SQL execution"
) -> QueryResult:
    """Map a DuckDB exception to a typed ERR-* QueryResult.

    DuckDB's exception hierarchy:
    - BinderException (ProgrammingError) — unknown column, ambiguous ref
    - CatalogException (ProgrammingError) — unknown table / schema
    - TypeMismatchException (DataError) — operator type mismatch
    Everything else → ERR-WAREHOUSE-RUNTIME (warehouse rejected the query
    but no fine-grained category applies).

    The classified `QueryError` is built via `from_code` so its message is
    exactly the registered template, not a hand-rolled
    "DuckDB SQL execution failed: {detail}" prefix.

    validate_select_only/validate_setup_sql run inside the same try this
    classifies, so a DbtChartsError (MutatingSqlError, UnparseableSqlError)
    can reach here too — delegate those to handle_adapter_error first so
    they keep their own registered code (and, for UnparseableSqlError, its
    sql_line/sql_start_col/sql_end_col fields) instead of falling through to
    the generic warehouse-runtime classification below. Mirrors
    classify_warehouse_error's identical delegation for the dbt-adapter path.
    """
    if isinstance(exc, DbtChartsError):
        return handle_adapter_error(operation, exc)

    import duckdb

    detail = str(exc)
    if isinstance(exc, (duckdb.BinderException, duckdb.CatalogException)):
        code = ERR_BINDER_UNKNOWN_COLUMN
    elif isinstance(exc, duckdb.TypeMismatchException):
        code = ERR_BINDER_TYPE_MISMATCH
    else:
        code = ERR_WAREHOUSE_RUNTIME
    return QueryResult(data=[], error=QueryError.from_code(code, detail=detail))


def _reject_unsupported_duckdb_fields(source_config: RawSourceConfig) -> None:
    """Fail loud on DuckDB source fields the in-process adapter cannot honor."""
    for field in _UNSUPPORTED_DUCKDB_FIELDS:
        if source_config.get(field):
            raise DbtChartsError.from_code(
                ERR_ADAPTER_UNSUPPORTED_DUCKDB_FIELD, field=field
            )


def _apply_duckdb_extensions(
    conn: duckdb.DuckDBPyConnection, extensions: list[str | dict[str, str]]
) -> None:
    """INSTALL + LOAD each extension. Mirrors dbt-duckdb's Environment.initialize_db:
    a bare string, or a {name} mapping with no repo, uses the driver's
    install_extension/load_extension API (DuckDB's default repository); a
    {name, repo} mapping installs from a custom repository via SQL first.

    DuckDBExtensionConfig (extra="forbid", `name` required) makes a
    malformed dict-form entry unrepresentable for a directly-authored
    DuckDBSourceConfig, rejected at parse time. A dbt_profile-expanded
    target (DbtTargetSourceConfig, extra="allow") is not validated against
    that model, so `name` is re-checked here for that path.
    """
    for extension in extensions:
        if isinstance(extension, str):
            conn.install_extension(extension)
            conn.load_extension(extension)
            continue
        name = extension.get("name")
        if not name:
            raise DbtChartsError.from_code(
                ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
                field="extensions",
                reason=f"entry {extension!r} needs a 'name'",
            )
        repo = extension.get("repo")
        if repo:
            ident_name = _duckdb_ident(name, "extension name")
            conn.execute(f"INSTALL {ident_name} FROM '{_duckdb_literal(repo)}'")
            conn.load_extension(ident_name)
        else:
            conn.install_extension(name)
            conn.load_extension(name)


def _apply_duckdb_settings(
    conn: duckdb.DuckDBPyConnection, settings: dict[str, SourceOptionScalar]
) -> None:
    """SET each session setting/pragma. Values are bound as parameters (DuckDB
    casts the bound value to the setting's real type); only the key, which
    DuckDB has no way to bind, is identifier-validated."""
    for key, value in settings.items():
        _duckdb_ident(key, "setting key")
        conn.execute(f"SET {key} = ?", [value])


def _secret_field_to_sql(key: str, value: DuckDBSecretValue) -> str:
    """Format one CREATE SECRET field. The key is always spliced as a bare
    identifier (DuckDB has no way to bind it), so it is always
    identifier-validated; the value is always quoted and escaped, never
    spliced raw. A dict becomes a DuckDB map: its keys sit inside the quoted
    literal (`'key': 'value'`), not as bare identifiers, so they are only
    escaped, not identifier-restricted, the same as any other value; real
    map keys are HTTP header names like `x-api-key`, which is not a valid
    identifier. A list becomes a DuckDB array, anything else a quoted
    scalar. DuckDB accepts a quoted string for every field DuckDB's own
    to_sql() would otherwise leave bare (e.g. `type 's3'`)."""
    _duckdb_ident(key, "secret field")
    if isinstance(value, dict):
        items = ", ".join(
            f"'{_duckdb_literal(str(k))}': '{_duckdb_literal(str(v))}'"
            for k, v in value.items()
        )
        return f"{key} map {{{items}}}"
    if isinstance(value, list):
        items = ", ".join(f"'{_duckdb_literal(str(v))}'" for v in value)
        return f"{key} array [{items}]"
    return f"{key} '{_duckdb_literal(str(value))}'"


def _secret_to_sql(secret: dict[str, DuckDBSecretValue]) -> str:
    """Build a CREATE SECRET statement, matching dbt-duckdb's Secret.to_sql.

    DuckDBSecretConfig (extra="allow", required `type`) makes a missing
    `type` unrepresentable for a directly-authored DuckDBSourceConfig,
    rejected at parse time. A dbt_profile-expanded target
    (DbtTargetSourceConfig, extra="allow") is not validated against that
    model, so `type` is re-checked here for that path. Provider-specific
    fields (key_id, region, ...) are flat on `secret`, matching both models.
    """
    fields = dict(secret)
    secret_type = fields.pop("type", None)
    if not secret_type or not isinstance(secret_type, str):
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field="secrets",
            reason="secret config requires a 'type' field",
        )
    _duckdb_ident(secret_type, "secret type")
    name = fields.pop("name", None)
    if name is not None and not isinstance(name, str):
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field="secrets",
            reason=f"'name' must be a string, got {type(name).__name__}",
        )
    persistent = fields.pop("persistent", False)
    scope = fields.pop("scope", None)
    if name is not None:
        _duckdb_ident(name, "secret name")

    name_sql = f" {name}" if name else ""
    or_replace = " OR REPLACE" if name else ""
    persistent_sql = " PERSISTENT" if persistent is True else ""

    params: dict[str, DuckDBSecretValue] = {"type": secret_type, **fields}
    parts = [
        _secret_field_to_sql(key, value)
        for key, value in params.items()
        if value is not None
    ]
    if scope is not None:
        if not isinstance(scope, (str, list)):
            raise DbtChartsError.from_code(
                ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
                field="secrets",
                reason=f"'scope' must be a string or list of strings, got {type(scope).__name__}",
            )
        scope_values = scope if isinstance(scope, list) else [scope]
        parts.extend(f"scope '{_duckdb_literal(str(s))}'" for s in scope_values)

    body = ",\n    ".join(parts)
    return f"CREATE{or_replace}{persistent_sql} SECRET{name_sql} (\n    {body}\n)"


def _apply_duckdb_secrets(
    conn: duckdb.DuckDBPyConnection, secrets: list[dict[str, DuckDBSecretValue]]
) -> None:
    for secret in secrets:
        conn.execute(_secret_to_sql(secret))


def _attachment_to_sql(
    attachment: dict[str, DuckDBAttachValue], *, adapter_read_only: bool
) -> str:
    """Build an ATTACH statement, matching dbt-duckdb's Attachment.to_sql.

    ``is_ducklake`` is carried on the config for parity with dbt-duckdb's
    schema; like upstream, it does not change the ATTACH statement itself.
    It is a hint dbt-duckdb's own relation resolution reads, which this
    adapter (no dbt-duckdb materializations) has no use for.

    ``adapter_read_only`` is this adapter's own read_only posture, not the
    attachment's authored ``read_only`` field: see the module docstring for
    why the two differ.
    """
    path = attachment["path"]
    if not isinstance(path, str):
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field="attach",
            reason=f"'path' must be a string, got {type(path).__name__}",
        )
    # Query parameters are not supported in ATTACH; upstream strips them the
    # same way (dbt-duckdb's Attachment.to_sql).
    parsed = urlparse(path)
    if parsed.query:
        path = path.replace(f"?{parsed.query}", "")
    base = f"ATTACH IF NOT EXISTS '{_duckdb_literal(path)}'"
    alias = attachment.get("alias")
    if alias:
        if not isinstance(alias, str):
            raise DbtChartsError.from_code(
                ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
                field="attach",
                reason=f"'alias' must be a string, got {type(alias).__name__}",
            )
        base += f" AS {_duckdb_ident(alias, 'attach alias')}"

    raw_options = attachment.get("options")
    if raw_options is not None and not isinstance(raw_options, dict):
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field="attach",
            reason=f"'options' must be a mapping, got {type(raw_options).__name__}",
        )
    options: dict[str, object] = {}  # type-state: object_annotation — untyped profile
    if raw_options:
        options.update(raw_options)
    atype = attachment.get("type")
    secret = attachment.get("secret")
    read_only = attachment.get("read_only")
    conflicts = [
        key
        for key, direct in (
            ("type", atype),
            ("secret", secret),
            ("read_only", read_only),
        )
        if direct and key in options
    ]
    if conflicts:
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
            field="attach",
            reason=(
                f"option(s) {conflicts} set in both direct fields and options; "
                "set each option in only one location"
            ),
        )

    all_options: list[str] = []
    options_type = options.get("type")
    if atype:
        all_options.append(f"TYPE {_duckdb_ident(str(atype), 'attach type')}")
    elif options_type:
        all_options.append(
            f"TYPE {_duckdb_ident(str(options.pop('type')), 'attach type')}"
        )
    options_secret = options.get("secret")
    if secret:
        all_options.append(f"SECRET {_duckdb_ident(str(secret), 'attach secret')}")
    elif options_secret:
        all_options.append(
            f"SECRET {_duckdb_ident(str(options.pop('secret')), 'attach secret')}"
        )
    authored_read_only = bool(read_only or options.pop("read_only", None))
    if adapter_read_only or authored_read_only:
        all_options.append("READ_ONLY")

    for key, value in options.items():
        if key in ("type", "secret"):
            continue
        _duckdb_ident(key, "attach option key")
        if isinstance(value, bool):
            if value:
                all_options.append(key.upper())
        elif value is not None:
            if not isinstance(value, (str, int, float)):
                raise DbtChartsError.from_code(
                    ERR_ADAPTER_DUCKDB_CONFIG_INVALID,
                    field="attach",
                    reason=(
                        f"option {key!r} must be a string, number, or bool; "
                        f"got {type(value).__name__}"
                    ),
                )
            all_options.append(f"{key.upper()} '{_duckdb_literal(str(value))}'")

    if all_options:
        base += f" ({', '.join(all_options)})"
    return base


def _apply_duckdb_attach(
    conn: duckdb.DuckDBPyConnection,
    attachments: list[dict[str, DuckDBAttachValue]],
    *,
    adapter_read_only: bool,
) -> None:
    for attachment in attachments:
        conn.execute(
            _attachment_to_sql(attachment, adapter_read_only=adapter_read_only)
        )


def _apply_duckdb_config_section(field: str, apply: Callable[[], None]) -> None:
    """Run one config-application step, naming its field on any failure.

    ``apply`` takes no arguments (the caller closes over the connection and
    the section's own values), so this stays a plain ``Callable[[], None]``
    across all four sections instead of a shape that varies per section.

    A DbtChartsError from the SQL builders (bad identifier, missing secret
    type, conflicting attach options) already names its field and is not
    re-classified. Anything else, a live duckdb exception from actually
    running the generated SQL (missing extension, unreachable attach
    target, ...), is a driver failure on an already-open connection, not a
    failed connect, so it gets its own code instead of
    ERR-WAREHOUSE-CONNECTION's "before any SQL is sent" framing.
    """
    try:
        apply()
    except DbtChartsError:
        raise
    except Exception as e:  # noqa: BLE001 — surface the driver's own config-apply error
        raise DbtChartsError.from_code(
            ERR_ADAPTER_DUCKDB_CONFIG_APPLY_FAILED,
            field=field,
            reason=str(e),
        ) from e


class DuckDBAdapter(BaseAdapter):
    """Adapter for executing SQL queries directly via the DuckDB driver.

    Handles all DuckDB sources: file-based, :memory:, and named DuckDB sources
    in dbt_charts.yml.  Non-DuckDB warehouses use SqlAdapter.

    Supported query types: sql
    """

    def __init__(
        self,
        *,
        source_config: DuckDBSourceConfig,
        data_dir: Path | None = None,
        read_only: bool = True,
        duckdb_config: dict[str, Any] | None = None,
        allow_external_access_in_readonly: bool = False,
        dbt_project_path: str | None = None,
        project: Project | None = None,
    ):
        """Initialize DuckDB adapter.

        Args:
            source_config: Typed DuckDB connection descriptor. ``source_config.path`` is
                the DuckDB file path or ":memory:" for an in-memory database.
            data_dir: Directory relative DuckDB paths (named-source `path:` values,
                and read_csv()/read_parquet() when external access is on) resolve
                against. None for hosts with no local filesystem to root against
                (e.g. Cloud) — resolving a relative path or enabling external
                access without one raises ValueError.
            read_only: If True (default), open DuckDB connections in read-only mode.
                File-based DuckDB: opened with read_only=True — the driver refuses all writes.
                In-memory DuckDB (:memory:): always opened read-write regardless of this flag
                because there is no other way to populate an in-memory database; this is a
                known exception. enable_external_access=False is still forced for :memory:
                when read_only=True unless allow_external_access_in_readonly=True, OR the
                connecting source itself authors attach/extensions/secrets (see below).

                When read_only=True (and allow_external_access_in_readonly=False),
                enable_external_access=False is forced on the DuckDB config regardless of
                any user-supplied duckdb_config, UNLESS the per-connection source config
                authors attach/extensions/secrets. Those fields cannot do anything without
                external access (ATTACH/INSTALL/CREATE SECRET all require it), so authoring
                one is itself the opt-in for that connection. read_only is never relaxed by
                this second door; only enable_external_access is.
            duckdb_config: Optional DuckDB config dict for the default connection.
            allow_external_access_in_readonly: Security opt-in. When True AND
                read_only=True AND duckdb_config contains enable_external_access=True,
                the adapter passes enable_external_access=True through to DuckDB for every
                connection. Default False preserves the existing defense: read_only=True
                always implies enable_external_access=False UNLESS the narrower
                attach/extensions/secrets opt-in above applies to that connection.
            dbt_project_path: Optional path to the dbt project root. When set and
                source_config.path == ":memory:" (no explicit file requested), the
                adapter scans dbt_project_path/data/ for DBT_PROJECT_DB_NAMES and
                connects to the first existing file (raising if none found).
            project: Seam the dbt manifest is read through, so `{{ ref() }}` in a
                query against a duckdb source (including a dbt_profile source that
                expanded to one) resolves to its relation. None for a host with no
                project to read — ref()/source() then raises rather than reaching
                the variable renderer as an undefined Jinja global.
        """
        self._data_dir = data_dir
        self.source_config = source_config
        self.read_only = read_only
        self.allow_external_access_in_readonly = allow_external_access_in_readonly
        self._duckdb_config = duckdb_config
        self.dbt_project_path = Path(dbt_project_path) if dbt_project_path else None
        self._dbt_refs = DbtRefResolver(project)
        self._connection: duckdb.DuckDBPyConnection | None = None
        # Thread-local storage for per-source DuckDB connection caches.
        self._tls = threading.local()
        # Track all connections opened across all threads so close() can clean up.
        self._all_conns: list[duckdb.DuckDBPyConnection] = []
        self._all_conns_lock = threading.Lock()

    @property
    def supported_types(self) -> set[str]:
        """Return supported query types."""
        return {"sql"}

    @property
    def profile_type(self) -> str:
        """Warehouse type label used by observability observers."""
        return "duckdb"

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Claim SQL against a DuckDB source, or a source-less SQL query.

        Source-less SQL (source_config is None) falls to DuckDB as the engine's
        default connection; a resolved duckdb source routes here explicitly.
        """
        return is_sql_query(query) and (
            source_config is None or source_config.type == "duckdb"
        )

    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute a SQL query via DuckDB.

        Args:
            query: AnyQuery object (SqlQuery expected)
            variables: Variable values for Jinja resolution
            params: Optional pre-computed parameter values.
            source_config: Typed source config from SourceResolver. When None,
                the adapter uses its default connection.

        Returns:
            QueryResult with data or error
        """
        if not is_sql_query(query):
            return plain_error(f"Expected SQL query, got {query.query_type}")

        sql = query.sql
        try:
            sql, resolved_relations = self._dbt_refs.resolve(sql)
        except DbtChartsError as e:
            return handle_adapter_error("dbt ref resolution", e)

        raw_config: dict[str, Any] | None
        if source_config is not None:
            raw_config = source_config.model_dump(by_alias=True)
            dialect_name = source_config.type
        else:
            raw_config = None
            dialect_name = "duckdb"

        # Resolve setup_sql Jinja before executing.
        resolved_setup_sql: str | None = None
        if query.setup_sql:
            resolved = resolve_setup_sql(query.setup_sql, variables)
            if isinstance(resolved, QueryResult):
                return resolved
            resolved_setup_sql = resolved

        if params is not None:
            resolved_sql = sql
            resolved_params = params
        else:
            try:
                parameterized = render_parameterized(
                    sql,
                    variables=variables or {},
                    dialect=get_dialect(dialect_name),
                    strict=not (is_sql_query(query) and query.lenient_variables),
                )
                resolved_sql = parameterized.sql
                resolved_params = parameterized.params
            except (ValueError, KeyError, TypeError) as e:
                return handle_adapter_error("SQL parameterization", e)

        # Read-only file connections are opened per query and closed after, so an
        # idle server does not hold the DB file locked (a held read-only connection
        # still denies a writer its exclusive lock — that is what blocked `refresh`
        # while `serve` was up). :memory: stays pooled — it is opened read-write and
        # destroyed on close, so it cannot be reopened. The one connection spans
        # setup_sql + the main query.
        # Open inside the try so a connect-time failure (a lock conflict on
        # the ephemeral path, a config-apply failure on either path) is
        # typed instead of escaping untyped. It gets the connection code, not
        # a query one: a connection that never opened has not judged
        # anybody's SQL. Both the ephemeral (read-only file) and pooled
        # (:memory:, or a read-write default connection) paths share this
        # handling so a typed DbtChartsError from _connect keeps its own
        # code and detail either way.
        resolved_path = self._resolved_path(raw_config)
        try:
            if self.read_only and resolved_path != ":memory:":
                conn = self._connect(raw_config, resolved_path)
                close_after = True
            else:
                conn = self._get_duckdb_connection_for_query(raw_config)
                close_after = False
        except DbtChartsError as e:
            # Already typed and field-named (config validation, config-apply
            # failure, unsupported field). Keep its own code instead of
            # reclassifying it as a generic connection failure.
            return handle_adapter_error("DuckDB connect", e)
        except Exception as e:  # noqa: BLE001 — classify driver connect errors
            return connection_failure("duckdb", e)
        try:
            if resolved_setup_sql:
                err = self._execute_setup_sql_duckdb(resolved_setup_sql, conn)
                if err is not None:
                    return err

            result = self._execute_duckdb(resolved_sql, resolved_params, query, conn)
            if resolved_relations:
                result.resolved_relations = resolved_relations
            return result
        finally:
            if close_after:
                conn.close()

    def _execute_setup_sql_duckdb(
        self,
        resolved_setup_sql: str,
        conn: duckdb.DuckDBPyConnection,
    ) -> QueryResult | None:
        """Execute setup_sql on the query's connection. Returns None on success."""
        try:
            validate_setup_sql(resolved_setup_sql, dialect="duckdb")
            with _DUCKDB_EXECUTE_LOCK:
                conn.execute(resolved_setup_sql)
        except Exception as e:  # noqa: BLE001
            return _classify_duckdb_error(e, "setup_sql")
        return None

    def _execute_duckdb(
        self,
        sql: str,
        params: list[Any],
        query: AnyQuery,
        conn: duckdb.DuckDBPyConnection,
    ) -> QueryResult:
        """Execute SQL query using DuckDB with parameterized execution."""
        # Resolved outside the try/except below: a malformed DCT_MAX_ROWS_CEILING
        # raises ValueError here, which must surface as a config error, not get
        # relabeled "DuckDB execution failed" by the driver-error classifier.
        # Bounds the driver's own fetch (cursor.fetchmany()) — sql is sent to
        # DuckDB unmodified, so every statement shape (DESCRIBE/SHOW, an
        # author's own inline LIMIT) behaves exactly as it would without this
        # ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        try:
            validate_select_only(sql, dialect="duckdb")
            with _DUCKDB_EXECUTE_LOCK:
                result = conn.execute(sql, params)
                # Snapshot description INSIDE the lock. conn.execute() returns
                # the connection itself, so result.description reflects whichever
                # query last ran on the (thread-shared) default connection. Read
                # it after the lock releases — as column_descriptions once did —
                # and a concurrent query's execute() overwrites the metadata or
                # closes this result handle ("Invalid Input Error: result closed"
                # / DuckDB bad_weak_ptr). rows is materialized by fetchmany(), so
                # it is safe to consume outside the lock.
                description = result.description
                rows = result.fetchmany(row_fetch_limit.fetch_limit)

            columns = [desc[0] for desc in description] if description else []
            rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)

            data = [dict(zip(columns, row, strict=False)) for row in rows]
            col_descs = (
                {desc[0]: tuple(desc) for desc in description} if description else None
            )

            return QueryResult(
                data=data,
                columns=columns,
                column_descriptions=col_descs,
                truncated_reason=truncated_reason,
            )

        except Exception as e:  # noqa: BLE001
            return _classify_duckdb_error(e)

    def _get_duckdb_connection_for_query(
        self, source_config: dict[str, Any] | None = None
    ) -> duckdb.DuckDBPyConnection:
        """Get DuckDB connection for a query.

        Uses the typed source_config (resolver-provided) when it points at a
        DuckDB source; otherwise falls through to the adapter's default connection.
        The thread-local connection cache is keyed by path, schema, and a hash
        of every other field _connect applies (duckdb_config/extensions/
        settings/secrets/attach): two named DuckDB sources only share a
        connection when all of that agrees, not just path and schema.
        """
        if source_config and source_config.get("type") == "duckdb":
            schema = source_config.get("schema") or ""
            path = str(source_config.get("path") or ":memory:")
            cache_key = _duckdb_session_cache_key(source_config, path, schema)
            return self._create_duckdb_connection_from_config(source_config, cache_key)
        return self._get_duckdb_connection()

    def _resolve_duckdb_config(
        self, source_config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve DuckDB config: source-level overrides adapter-level default."""
        return normalize_duckdb_config(source_config) or self._duckdb_config

    def _resolved_path(self, source_config: dict[str, Any] | None) -> str:
        """Resolve the DuckDB target path for a source (':memory:' or a file path).

        Named duckdb sources resolve a relative path against the project data dir;
        the default source honors the dbt-project data-dir scan (a requested
        ':memory:' resolves to a real file, or raises when none exists). Used both
        to open a connection and to decide whether it is file-backed (ephemeral) or
        in-memory (pooled).
        """
        if source_config and source_config.get("type") == "duckdb":
            db_path = str(source_config.get("path") or ":memory:")
            if db_path != ":memory:" and not Path(db_path).is_absolute():
                if self._data_dir is None:
                    raise DbtChartsError.from_code(
                        ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR,
                        adapter="DuckDB",
                        path=db_path,
                    )
                return str((self._data_dir / db_path).resolve())
            return db_path
        path = self.source_config.path
        if path == ":memory:" and self.dbt_project_path:
            from dbt_charts.core.execute.adapters.dbt_utils import DBT_PROJECT_DB_NAMES

            for db_name in DBT_PROJECT_DB_NAMES:
                candidate = self.dbt_project_path / "data" / db_name
                if candidate.exists():
                    return str(candidate)
            raise DbtChartsError.from_code(
                ERR_DBT_PROJECT_NO_LOCAL_WAREHOUSE,
                dbt_project_path=str(self.dbt_project_path),
                candidates=list(DBT_PROJECT_DB_NAMES),
            )
        return path

    def _connect(
        self, source_config: dict[str, Any] | None, path: str
    ) -> duckdb.DuckDBPyConnection:
        """Open a NEW DuckDB connection at ``path`` for a source (no caching).

        ``path`` is the caller's already-resolved ``_resolved_path(source_config)`` —
        passed in so the ephemeral ``_execute`` path resolves it once (the decision)
        rather than twice. Shared by the pooled getters and the per-query ephemeral
        path so both apply the same security posture and session SETs regardless of
        who owns the connection's lifetime.
        """
        import duckdb

        if source_config is not None and source_config.get("type") == "duckdb":
            if "database" in source_config and "path" not in source_config:
                raise ValueError(
                    "DuckDB source_config uses the unsupported 'database' key. "
                    'Use \'path\' instead (e.g. {"type": "duckdb", "path": "db.duckdb"}).'
                )
            _reject_unsupported_duckdb_fields(source_config)
            duckdb_config = self._resolve_duckdb_config(source_config) or None
            schema = source_config.get("schema")
            extensions = source_config.get("extensions")
            settings = source_config.get("settings")
            secrets = source_config.get("secrets")
            attach = source_config.get("attach")
            needs_external_access = bool(extensions or secrets or attach)
        else:
            duckdb_config = self._duckdb_config or None
            schema = None
            extensions = settings = secrets = attach = None
            needs_external_access = False

        conn = duckdb.connect(
            path,
            **self._resolve_duckdb_connect_kwargs(
                path,
                self.read_only,
                duckdb_config,
                self.allow_external_access_in_readonly,
                needs_external_access,
            ),
        )
        # A post-connect SET (or the schema guard) that raises must not orphan the
        # just-opened connection — close it before propagating so the file lock is
        # released.
        close_external_access_after_attach = False
        try:
            # Session-local: relative read_csv()/read_parquet() paths in queries
            # resolve against the project root instead of the process cwd. Only
            # meaningful when external file access is on (DuckDB rejects relative
            # reads otherwise), so skip it — and its data_path resolution — when off.
            if self._external_access_enabled(
                self.read_only,
                duckdb_config,
                self.allow_external_access_in_readonly,
                needs_external_access,
            ):
                if self._data_dir is not None:
                    conn.execute("SET file_search_path = ?", [str(self._data_dir)])
                else:
                    explicit_file_access_opt_in = (
                        self.allow_external_access_in_readonly
                        and duckdb_config is not None
                        and duckdb_config.get("enable_external_access") is True
                    )
                    # attach needs external access only for the ATTACH statement
                    # itself; extensions/secrets can still be needed at query time
                    # (a secret authenticates a later read_csv/httpfs call), so
                    # only an attach-only connection is safe to close back down.
                    attach_only = bool(attach) and not extensions and not secrets
                    if explicit_file_access_opt_in or not (
                        self.read_only and attach_only
                    ):
                        # A caller that actually wants read_csv()/read_parquet()
                        # (the default in read-write mode, or the explicit
                        # allow_external_access_in_readonly opt-in), or a source
                        # authoring extensions/secrets, with no data_dir is a
                        # real misconfiguration.
                        raise ValueError(
                            "External file access (read_csv()/read_parquet()) "
                            "requires a data_dir to resolve relative paths "
                            "against; none was configured for this adapter."
                        )
                    close_external_access_after_attach = True
            if schema:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
                    raise ValueError(
                        f"Invalid schema name {schema!r}: must match "
                        r"^[A-Za-z_][A-Za-z0-9_]*$"
                    )
                conn.execute("SET search_path = ?", [f"{schema},main"])
            if extensions:
                _apply_duckdb_config_section(
                    "extensions", lambda: _apply_duckdb_extensions(conn, extensions)
                )
            if settings:
                _apply_duckdb_config_section(
                    "settings", lambda: _apply_duckdb_settings(conn, settings)
                )
            if secrets:
                _apply_duckdb_config_section(
                    "secrets", lambda: _apply_duckdb_secrets(conn, secrets)
                )
            if attach:
                adapter_read_only = self.read_only
                _apply_duckdb_config_section(
                    "attach",
                    lambda: _apply_duckdb_attach(
                        conn, attach, adapter_read_only=adapter_read_only
                    ),
                )
            if close_external_access_after_attach:
                # data_dir was None and external access was on solely for the
                # ATTACH statement just applied above; close it back down so a
                # later read_csv()/read_parquet() in authored SQL does not
                # silently resolve against the process cwd.
                conn.execute("SET enable_external_access = false")
        except BaseException:
            conn.close()
            raise
        return conn

    def _create_duckdb_connection_from_config(
        self, source_config: dict[str, Any], cache_key: str
    ) -> duckdb.DuckDBPyConnection:
        """Get or create a pooled connection for a named DuckDB source."""
        sources = getattr(self._tls, "sources", None)
        if sources is None:
            sources = self._tls.sources = {}
        if cache_key not in sources:
            conn = self._connect(source_config, self._resolved_path(source_config))
            sources[cache_key] = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return sources[cache_key]

    @staticmethod
    def _external_access_enabled(
        read_only: bool,
        duckdb_config: dict[str, Any] | None,
        allow_external_access_in_readonly: bool = False,
        source_needs_external_access: bool = False,
    ) -> bool:
        """Whether relative read_csv()/read_parquet() can resolve on this connection.

        file_search_path only affects relative file reads, which DuckDB rejects
        unless external access is on — so the SET (and its data_dir resolution)
        is pure overhead when this returns False. Read-only forces external
        access off unless the caller opted in (allow_external_access_in_readonly)
        AND set enable_external_access=True, OR the source itself authors
        `attach`/`extensions`/`secrets` (source_needs_external_access). Those
        fields need external access to do anything, so authoring one is an
        explicit opt-in and the read-only default is not forced onto it.
        An explicit enable_external_access=False in duckdb_config always wins,
        for either opt-in path.
        """
        config = duckdb_config or {}
        if config.get("enable_external_access") is False:
            return False
        if (
            read_only
            and not source_needs_external_access
            and not (
                allow_external_access_in_readonly
                and config.get("enable_external_access") is True
            )
        ):
            return False
        return config.get("enable_external_access", True) is True

    @staticmethod
    def _resolve_duckdb_connect_kwargs(
        path: str,
        read_only: bool,
        duckdb_config: dict[str, Any] | None,
        allow_external_access_in_readonly: bool = False,
        source_needs_external_access: bool = False,
    ) -> dict[str, Any]:
        """Build duckdb.connect() kwargs for the given path and security posture.

        When read_only=True and allow_external_access_in_readonly=False (default):
        - Adds read_only=True for file-based paths (not :memory: — in-memory DuckDB
          must always be opened read-write; this is a known exception).
        - Forces enable_external_access=False in the config, overriding any
          user-supplied duckdb_config value.

        When read_only=True and allow_external_access_in_readonly=True, or the
        source authors attach/extensions/secrets (source_needs_external_access):
        - Same read_only=True driver flag for file-based paths.
        - enable_external_access is NOT forced to False; caller's duckdb_config
          passes through. Approved callsites: LOCAL_AUTHORING_REGISTRY_KWARGS.

        When read_only=False:
        - No read_only driver flag.
        - duckdb_config is passed through as-is.
        """
        kwargs: dict[str, Any] = {}
        if read_only and path != ":memory:":
            kwargs["read_only"] = True
        if duckdb_config is not None or read_only:
            config = dict(duckdb_config or {})
            if not DuckDBAdapter._external_access_enabled(
                read_only,
                config,
                allow_external_access_in_readonly,
                source_needs_external_access,
            ):
                config["enable_external_access"] = False
            if config:
                kwargs["config"] = config
        return kwargs

    def _get_duckdb_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the pooled default DuckDB connection.

        The dbt-project data-dir scan and session SETs live in ``_connect`` /
        ``_resolved_path`` (shared with the ephemeral path). Reached only for the
        pooled case — read-only file sources open per query via ``_connect``.
        """
        if self._connection is None:
            self._connection = self._connect(None, self._resolved_path(None))
        return self._connection

    def close(self) -> None:
        """Close all DuckDB connections."""
        if self._connection:
            self._connection.close()
            self._connection = None

        with self._all_conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            conn.close()

        if hasattr(self._tls, "sources"):
            self._tls.sources = {}
