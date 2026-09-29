"""Source configuration types for database connections and data sources.

Stage: COMPILE
Purpose: Define Pydantic models for source configurations.

Sources represent "where data comes from" - databases, CSV files, HTTP APIs, etc.
Field names match dbt's profiles.yml exactly where applicable.

Design Principles:
- Match dbt field names exactly for database types
- Support env_var() syntax like dbt profiles
- Use discriminated union via 'type' field
- Declare each supported dbt credential option with a description
- Reject unknown fields at the source-model boundary

Dependencies:
    - pydantic (BaseModel, Field, field_validator)

Read-only posture per warehouse:
    DuckDB opens connections with read_only=True in-process (see DuckDBAdapter.read_only).
    Every other warehouse lacks a native read-only driver flag. For those, the operator
    must bind SELECT-only credentials at the database/IAM layer:

    | Warehouse       | In-process flag | Operator posture                                      |
    |-----------------|-----------------|-------------------------------------------------------|
    | DuckDB          | read_only=True  | Handled in-process by DuckDBAdapter                   |
    | SQLite          | URI ?mode=ro    | Handled in-process by SqliteAdapter and InspectConnection|
    | Postgres        | none            | Bind a SELECT-only role to the dbt profile credential |
    | MySQL/MariaDB   | none            | Bind a SELECT-only user to the dbt profile credential |
    | Snowflake       | none            | Bind a SELECT-only role to the Snowflake user/key-pair|
    | BigQuery        | none            | Bind credentials to roles/bigquery.dataViewer         |
    | Databricks      | none            | Unity Catalog: bind service principal to SELECT-only  |
    | Redshift        | none            | IAM/grants: bind to a SELECT-only IAM role or DB user |
    | SQL Server      | none            | Login-level SELECT-only grants                        |

    sql_guard (allowlist enforcement) provides the in-process defense for all warehouses.
    SELECT-only credentials are the connection-level defense for non-DuckDB warehouses.

See also:
    - config.py: Project-level configuration
    - models/query/compiled.py: Query interface types
    - normalize/dispatch.py: Resolves source references
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, ClassVar, Literal, get_args, get_type_hints

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from dbt_charts.core.attribution import validate_attribution
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.cache import CachePatch, validate_cache_layer
from dbt_charts.core.compile.sources.dbt_jinja import render_dbt_jinja_in_dict
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CREDENTIAL_LITERAL

# tach-ignore(pre-existing compile<->project coupling — accepted debt, see AGENTS.md)
from dbt_charts.core.project import assert_relpath

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Value-bearing credential fields that must never appear as a raw literal in the
# git-committed registry. Deliberately small and explicit — no guessing at
# "secret-looking" arbitrary keys. Path fields that merely POINT at a secret
# (e.g. ``keyfile``) are excluded: a path is not itself secret material.
#
# Scoped follow-up (needs dbt-profile field-contract owner sign-off before adding,
# per the initiative's open decision): other documented dbt secret fields —
# client_secret, refresh_token, access_token, private_key_passphrase, sslkey, and
# bigquery credentials_base64 — are not yet covered here.
KNOWN_SECRET_FIELDS: frozenset[str] = frozenset(
    {"password", "private_key", "keyfile_json", "token", "secret"}
)


def reject_credential_literals(source_name: str, raw_config: Mapping[str, Any]) -> None:
    """Reject raw secret literals in a git-committed source registry entry.

    Runs over the raw authored mapping before ``env_var()`` rendering, so a
    rendered secret is never mistaken for an authored one. A known-secret field
    is accepted only as an ``env_var()`` reference
    (``{{ env_var(...) }}``); a bare literal, or an inlined Jinja constant like
    ``{{ 'hunter2' }}``, is rejected. ``None``/``""`` (an omitted optional secret,
    e.g. Snowflake under OAuth) is not a leak and passes.

    Raises:
        CompilationError: ERR-SOURCE-CREDENTIAL-LITERAL when a known-secret
            field holds inline secret material rather than an env_var() reference.
    """
    for field in KNOWN_SECRET_FIELDS:
        if field not in raw_config:
            continue
        value = raw_config[field]
        if value is None or value == "":
            continue  # optional secret not provided — nothing to leak
        if isinstance(value, str) and "{{" in value and "env_var(" in value:
            continue  # env_var() reference — the sanctioned in-git form
        raise CompilationError.from_code(
            ERR_SOURCE_CREDENTIAL_LITERAL,
            source_name=source_name,
            field=field,
        )


def _validate_files_mapping(
    class_name: str, files: dict[str, str], expected_extension: str | tuple[str, ...]
) -> None:
    """Validate all keys and values in a file source's ``files:`` mapping.

    Keys must be valid SQL identifiers (letters, digits, underscores; no
    leading digit). Values must be non-empty relative POSIX paths that do not
    escape above the project root after normalization, and must end in one of
    ``expected_extension`` (case-insensitive) — parsing dispatches on the
    declared source ``type``, not the file extension, so this is what
    guarantees every servable data file's extension matches its type (and
    therefore lands in Cloud's git-blob-fetch allowlist, which is keyed on
    extension alone).

    Args:
        class_name: Source config class name (for error messages).
        files: The ``files:`` mapping from the authored YAML.
        expected_extension: The canonical extension or tuple of extensions for
            this source type (e.g. ``".csv"`` or ``(".json", ".jsonl")``),
            including the leading dot.

    Raises:
        ValueError: On an invalid key, path value, or extension mismatch.
    """
    exts = (
        (expected_extension,)
        if isinstance(expected_extension, str)
        else expected_extension
    )
    for table_name, file_path in files.items():
        if not _SQL_IDENTIFIER_RE.match(table_name):
            raise ValueError(
                f"{class_name}: files key {table_name!r} is not a valid SQL identifier. "
                "Table names must start with a letter or underscore and contain only "
                "letters, digits, and underscores."
            )
        try:
            assert_relpath(file_path)
        except ValueError as e:
            raise ValueError(f"{class_name}: files[{table_name!r}] {e}") from e
        if not any(file_path.lower().endswith(ext) for ext in exts):
            ext_msg = repr(exts[0]) if len(exts) == 1 else f"one of {list(exts)}"
            raise ValueError(
                f"{class_name}: files[{table_name!r}] path {file_path!r} must end in "
                f"{ext_msg} to match source type."
            )


# ============================================================================
# BIGQUERY AUTH HELPERS
# ============================================================================


def infer_bq_method(
    keyfile: str | None, keyfile_json: dict[str, Any] | None
) -> Literal["oauth", "service-account", "service-account-json"]:
    """Return the dbt-bigquery method for the given credential shape."""
    if keyfile_json is not None:
        return "service-account-json"
    if keyfile is not None:
        return "service-account"
    return "oauth"


# ============================================================================
# BASE SOURCE CONFIG
# ============================================================================


class BaseSourceConfig(BaseModel):
    """Closed-contract root for all source configurations.

    extra="forbid" — file/HTTP/dbt_profile source configs have defined dbt charts
    contracts; unknown fields are validation errors, not silent pass-throughs.
    Database source configs inherit the same closed contract through
    DatabaseSourceConfig.
    """

    model_config = ConfigDict(extra="forbid")

    # Declared without a value: every concrete variant names its own category,
    # and there is no sensible default to inherit if one forgets. FILE_SOURCE_TYPES
    # is derived from these, so a new file variant needs no list kept in step.
    source_category: ClassVar[str]

    # None = no source-level cache override authored; queries against this
    # source inherit the project default (then board/query refinements).
    cache: CachePatch | None = Field(
        default=None,
        description=(
            "Cache policy default for every query against this source, e.g. "
            "cache: 1h: queries inherit it and may refine it; cache: false "
            "opts them out."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _resolve_all_env_vars(
        cls,
        data: Any,  # type-state: explicit_any — raw YAML before validation
        info: ValidationInfo,
    ) -> Any:  # type-state: explicit_any — passes non-dict input through
        """Render dbt Jinja (env_var etc.) in all fields before Pydantic validates.

        This is the ONE render a source config gets, so it is also where a
        caller seals untrusted input: ``parse_source_config(..., env={})``
        arrives here as ``info.context["env"]``. No context (a model built
        directly, local config) means the live process environment.
        """
        if not isinstance(data, dict):
            return data
        env = info.context.get("env") if info.context else None
        if env is None:
            return render_dbt_jinja_in_dict(data)
        return render_dbt_jinja_in_dict(data, env=env)


class AttributedSourceConfig(BaseModel):
    """Cost attribution, for the source families that actually reach a warehouse.

    Scoped to the dbt-credential-backed warehouse types plus the two dbt-profile
    shapes — the only ones whose queries pass through ``build_adapter`` and so can
    carry labels. Deliberately NOT on the closed-contract root: DuckDB and SQLite
    execute in-process, and file/HTTP sources are materialized, so a source config
    that accepted ``attribution:`` there would validate cleanly and then never emit
    it. Same narrowing rationale as ``max_query_duration_seconds`` below.
    """

    model_config = ConfigDict(extra="forbid")

    attribution: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Cost-attribution pairs sent with every query against this source, e.g. "
            "attribution: {team: analytics}. Emitted as BigQuery job labels and as a "
            "query comment elsewhere. Keys and values must match BigQuery's label "
            "rules ([a-z][a-z0-9_-]{0,62} / [a-z0-9_-]{0,63}). The dbt_charts_ prefix "
            "and the app key are reserved for the engine's own identity."
        ),
    )

    @field_validator("attribution")
    @classmethod
    def _check_attribution(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject malformed pairs rather than sanitizing them.

        Runs after each concrete model's env_var rendering, so an
        ``{{ env_var(...) }}`` result is held to the same rules as a literal. A value
        quietly rewritten to fit the charset would attribute spend under a name the
        author never wrote.
        """
        validate_attribution(value)
        return value


class DatabaseSourceConfig(BaseSourceConfig):
    """Closed-contract base for source types backed by dbt credentials."""

    model_config = ConfigDict(extra="forbid")

    max_query_duration_seconds: Annotated[
        int | None,
        Field(
            gt=0,
            description=(
                "Maximum execution time for one query in seconds. Overrides "
                "execution.max_query_duration_seconds for this source."
            ),
        ),
    ] = None


SourceOptionScalar = str | int | float | bool | None


def _default_retryable_exceptions() -> list[str]:
    """Return dbt-duckdb's default retryable exception names."""
    return ["IOException"]


class DuckDBAttachmentConfig(BaseModel):
    """One dbt-duckdb ATTACH declaration."""

    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(description="DuckDB database path or URL to attach.")]
    type: Annotated[
        str | None,
        Field(
            description="Attached database type supplied by its extension.",
        ),
    ] = None
    alias: Annotated[
        str | None,
        Field(description="Optional name for the attached database."),
    ] = None
    secret: Annotated[
        str | None,
        Field(
            description="Name of the DuckDB secret used for this attachment.",
        ),
    ] = None
    read_only: Annotated[
        bool,
        Field(
            description=(
                "Whether the attached database is read-only. Honored as "
                "written when the adapter itself is read-write; when the "
                "adapter is read_only (the default), the in-process DuckDB "
                "adapter forces every attachment read-only regardless of "
                "this flag."
            ),
        ),
    ] = False
    options: Annotated[
        dict[str, SourceOptionScalar] | None,
        Field(description="Additional scalar DuckDB ATTACH options."),
    ] = None
    is_ducklake: Annotated[
        bool | None,
        Field(description="Whether the attachment is a DuckLake database."),
    ] = None


class DuckDBExtensionConfig(BaseModel):
    """One DuckDB extension installed from a custom repository.

    Only for the {name, repo} form; a bare extension name (installed and
    loaded from DuckDB's default repository) is authored as a plain string,
    not this model. `repo` is optional: {name: json} with no repo installs
    from DuckDB's default repository, same as the plain-string form.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(description="Extension name to install and load.")]
    repo: Annotated[
        str | None,
        Field(
            description="Repository to install the extension from. Omit to use DuckDB's default repository."
        ),
    ] = None


_DUCKDB_SECRET_VALUE_TYPES = (str, int, float, bool)


def _is_duckdb_secret_scalar(
    value: Any,  # type-state: explicit_any — raw pre-validation YAML value; that is the whole point of the check
) -> bool:
    return isinstance(value, _DUCKDB_SECRET_VALUE_TYPES)


def _is_duckdb_secret_value(
    value: Any,  # type-state: explicit_any — raw pre-validation YAML value; that is the whole point of the check
) -> bool:
    """Whether `value` is a shape DuckDB's CREATE SECRET SQL can render:
    a scalar, a list of scalars (DuckDB array), or a flat string-keyed
    mapping of scalars (DuckDB map)."""
    if _is_duckdb_secret_scalar(value):
        return True
    if isinstance(value, list):
        return all(_is_duckdb_secret_scalar(v) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_duckdb_secret_scalar(v) for k, v in value.items()
        )
    return False


class DuckDBSecretConfig(BaseModel):
    """One DuckDB CREATE SECRET declaration.

    `type` is required: a secret with no type cannot become valid CREATE
    SECRET SQL, so leaving it optional would make an invalid config
    representable. Provider-specific fields (key_id, secret, region, ...)
    are authored flat, matching dbt-duckdb's own secret shape, not nested
    under an `options` key.
    """

    model_config = ConfigDict(extra="allow")

    type: Annotated[str, Field(description="DuckDB secret type, e.g. s3, gcs, azure.")]
    name: Annotated[str | None, Field(description="Optional secret name.")] = None
    persistent: Annotated[
        bool,
        Field(
            description="Whether the secret persists to disk (~/.duckdb/stored_secrets)."
        ),
    ] = False
    scope: Annotated[
        str | list[str] | None,
        Field(description="Optional URL scope(s) this secret applies to."),
    ] = None

    @model_validator(mode="after")
    def _validate_provider_field_shapes(self) -> DuckDBSecretConfig:
        extra = self.model_extra
        if extra is None:
            return self
        for key, value in extra.items():
            if not _is_duckdb_secret_value(value):
                raise ValueError(
                    f"DuckDB secret field {key!r} must be a scalar, a list of "
                    f"scalars, or a flat mapping of scalars; got {type(value).__name__}"
                )
        return self


class DuckDBRemoteConfig(BaseModel):
    """dbt-duckdb remote connection settings."""

    model_config = ConfigDict(extra="forbid")

    host: Annotated[str, Field(description="Remote DuckDB host name.")]
    port: Annotated[int, Field(description="Remote DuckDB port number.")]
    user: Annotated[str, Field(description="Remote DuckDB user name.")]
    password: Annotated[
        str | None,
        Field(description="Remote DuckDB password."),
    ] = None


class DuckDBPluginConfig(BaseModel):
    """One dbt-duckdb plugin declaration."""

    model_config = ConfigDict(extra="forbid")

    module: Annotated[
        str, Field(description="Python module that implements the plugin.")
    ]
    alias: Annotated[
        str | None,
        Field(description="Optional import alias for the plugin module."),
    ] = None
    config: Annotated[
        dict[str, SourceOptionScalar] | None,
        Field(description="Plugin-specific scalar configuration values."),
    ] = None


class DuckDBRetriesConfig(BaseModel):
    """dbt-duckdb retry settings."""

    model_config = ConfigDict(extra="forbid")

    connect_attempts: Annotated[
        int,
        Field(description="Number of connection attempts before failure."),
    ] = 1
    query_attempts: Annotated[
        int | None,
        Field(
            description="Number of attempts for a retryable query failure.",
        ),
    ] = None
    retryable_exceptions: Annotated[
        list[str],
        Field(
            description="Exception names that dbt-duckdb may retry.",
        ),
    ] = Field(default_factory=_default_retryable_exceptions)


# ============================================================================
# DATABASE SOURCE CONFIGS
# ============================================================================


class PostgresSourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """Postgres source configuration.

    Field names match dbt profiles.yml exactly.

    Read-only posture: no native driver flag. Bind a SELECT-only role to the
    dbt profile credential at the database level.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["postgres"] = Field(description="Source type identifier.")
    host: str = Field(description="Database host name or IP address.")
    port: int = Field(default=5432, description="Database port number.")
    dbname: str = Field(
        validation_alias=AliasChoices("dbname", "database"),
        description="Database name (dbt accepts database as an input alias).",
    )
    schema_: str = Field(
        alias="schema", default="public", description="Default schema for queries."
    )
    user: str = Field(description="Database user name.")
    password: str = Field(
        validation_alias=AliasChoices("password", "pass"),
        description="Database password (dbt accepts pass as an input alias).",
    )
    connect_timeout: Annotated[
        int,
        Field(description="Connection timeout in seconds."),
    ] = 10
    role: Annotated[
        str | None,
        Field(description="PostgreSQL role to assume after connecting."),
    ] = None
    search_path: Annotated[
        str | None,
        Field(description="PostgreSQL search_path for the connection."),
    ] = None
    keepalives_idle: Annotated[
        int,
        Field(description="Idle seconds before TCP keepalives begin."),
    ] = 0
    sslmode: (
        Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
        | None
    ) = Field(
        default=None,
        description="libpq SSL mode forwarded to psycopg2. None lets libpq decide its default.",
    )
    sslcert: Annotated[
        str | None,
        Field(description="Path to the client SSL certificate."),
    ] = None
    sslkey: Annotated[
        str | None,
        Field(description="Path to the client SSL private key."),
    ] = None
    sslrootcert: Annotated[
        str | None,
        Field(
            description="Path to the trusted SSL certificate authority file.",
        ),
    ] = None
    application_name: Annotated[
        str | None,
        # No default: the engine's own name has exactly one owner
        # (engine_attribution), and native_attribution_credential fills this field
        # when the author leaves it unset. A literal here would be a second copy
        # that silently keeps the old name the day the engine's name changes.
        Field(description="Application name reported to PostgreSQL."),
    ] = None
    retries: Annotated[
        int,
        Field(description="Number of connection retry attempts."),
    ] = 1


class SnowflakeSourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """Snowflake source configuration.

    Field names match dbt profiles.yml exactly.

    Read-only posture: no native driver flag. Bind a SELECT-only role to the
    Snowflake user or key-pair in the dbt profile.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["snowflake"] = Field(description="Source type identifier.")
    account: str = Field(
        description="Snowflake account identifier (e.g. xy12345.us-east-1)."
    )
    user: str | None = Field(
        default=None,
        description="Snowflake user name. Omit for token-based authentication when supported.",
    )
    password: str | None = Field(
        default=None,
        description="Snowflake password. Omit when using OAuth or key-pair auth.",
    )
    database: str = Field(description="Snowflake database name.")
    warehouse: str | None = Field(
        default=None,
        description="Snowflake virtual warehouse name.",
    )
    schema_: str = Field(
        alias="schema", default="PUBLIC", description="Default schema for queries."
    )
    role: str | None = Field(
        default=None, description="Snowflake role to assume for the session."
    )
    authenticator: Annotated[
        str | None,
        Field(
            description="Snowflake authenticator name or external-browser mode.",
        ),
    ] = None
    private_key: Annotated[
        str | None,
        Field(
            description="PEM private key used for key-pair authentication.",
        ),
    ] = None
    private_key_path: Annotated[
        str | None,
        Field(
            description="Path to a PEM private key for key-pair authentication.",
        ),
    ] = None
    private_key_passphrase: Annotated[
        str | None,
        Field(description="Passphrase for the configured private key."),
    ] = None
    token: Annotated[
        str | None,
        Field(description="OAuth access token for token authentication."),
    ] = None
    oauth_client_id: Annotated[
        str | None,
        Field(description="OAuth client identifier."),
    ] = None
    oauth_client_secret: Annotated[
        str | None,
        Field(description="OAuth client secret."),
    ] = None
    query_tag: Annotated[
        str | None,
        Field(description="Snowflake query tag applied to statements."),
    ] = None
    client_session_keep_alive: Annotated[
        bool,
        Field(
            description="Whether Snowflake keeps the client session alive.",
        ),
    ] = False
    host: Annotated[
        str | None,
        Field(description="Snowflake host override."),
    ] = None
    port: Annotated[
        int | None,
        Field(description="Snowflake port override."),
    ] = None
    proxy_host: Annotated[
        str | None,
        Field(description="HTTP proxy host for Snowflake connections."),
    ] = None
    proxy_port: Annotated[
        int | None,
        Field(description="HTTP proxy port for Snowflake connections."),
    ] = None
    protocol: Annotated[
        str | None,
        Field(description="Network protocol for the Snowflake connection."),
    ] = None
    connect_retries: Annotated[
        int,
        Field(description="Number of retries while opening a connection."),
    ] = 1
    connect_timeout: Annotated[
        int | None,
        Field(description="Connection timeout in seconds."),
    ] = None
    retry_on_database_errors: Annotated[
        bool,
        Field(description="Whether database errors are retried."),
    ] = False
    retry_all: Annotated[
        bool,
        Field(description="Whether all connection errors are retried."),
    ] = False
    insecure_mode: Annotated[
        bool | None,
        Field(
            description="Whether TLS certificate verification is disabled.",
        ),
    ] = False
    reuse_connections: Annotated[
        bool | None,
        Field(description="Whether dbt reuses Snowflake connections."),
    ] = None
    s3_stage_vpce_dns_name: Annotated[
        str | None,
        Field(description="Private VPC endpoint DNS name for S3 staging."),
    ] = None
    platform_detection_timeout_seconds: Annotated[
        float,
        Field(description="Timeout for Snowflake platform detection."),
    ] = 0.0


class BigQuerySourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """BigQuery source configuration.

    Field names match dbt profiles.yml exactly.

    Read-only posture: no native driver flag. Bind credentials to
    roles/bigquery.dataViewer (or equivalent) at the IAM layer.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["bigquery"] = Field(description="Source type identifier.")
    project: str = Field(description="GCP project ID.")
    dataset: str = Field(description="BigQuery dataset name (equivalent to schema).")
    keyfile: str | None = Field(
        default=None, description="Path to service account JSON key file."
    )
    keyfile_json: dict[str, SourceOptionScalar] | None = Field(
        default=None, description="Inline service account JSON dict."
    )
    location: str | None = Field(
        default=None, description="Dataset location (e.g. US, EU)."
    )
    method: (
        Literal[
            "oauth",
            "oauth-secrets",
            "service-account",
            "service-account-json",
            "external-oauth-wif",
        ]
        | None
    ) = Field(
        default=None,
        description=(
            "dbt-bigquery authentication method. "
            "Inferred from keyfile/keyfile_json when omitted: "
            "'service-account-json' if keyfile_json set, "
            "'service-account' if keyfile set, "
            "'oauth' (Application Default Credentials) otherwise."
        ),
    )
    execution_project: Annotated[
        str | None,
        Field(description="GCP project billed for BigQuery execution."),
    ] = None
    quota_project: Annotated[
        str | None,
        Field(description="GCP project used for quota attribution."),
    ] = None
    api_endpoint: Annotated[
        str | None,
        Field(description="BigQuery API endpoint override."),
    ] = None
    priority: Annotated[
        Literal["interactive", "batch"] | None,
        Field(description="BigQuery job priority."),
    ] = None
    maximum_bytes_billed: Annotated[
        int | None,
        Field(description="Maximum bytes a BigQuery job may bill."),
    ] = None
    impersonate_service_account: Annotated[
        str | None,
        Field(
            description="Service account to impersonate for BigQuery jobs.",
        ),
    ] = None
    job_retry_deadline_seconds: Annotated[
        int | None,
        Field(description="Deadline in seconds for retrying a BigQuery job."),
    ] = None
    job_retries: Annotated[
        int,
        Field(
            validation_alias=AliasChoices("job_retries", "retries"),
            description="Number of BigQuery job retry attempts (dbt accepts retries as an input alias).",
        ),
    ] = 1
    job_creation_timeout_seconds: Annotated[
        int | None,
        Field(
            description="Timeout in seconds while creating a BigQuery job.",
        ),
    ] = None
    job_execution_timeout_seconds: Annotated[
        int | None,
        Field(
            validation_alias=AliasChoices(
                "job_execution_timeout_seconds", "timeout_seconds"
            ),
            description="Timeout in seconds while executing a BigQuery job.",
        ),
    ] = None
    token: Annotated[
        str | None,
        Field(description="OAuth access token."),
    ] = None
    refresh_token: Annotated[
        str | None,
        Field(description="OAuth refresh token."),
    ] = None
    client_id: Annotated[
        str | None,
        Field(description="OAuth client identifier."),
    ] = None
    client_secret: Annotated[
        str | None,
        Field(description="OAuth client secret."),
    ] = None
    token_uri: Annotated[
        str | None,
        Field(description="OAuth token endpoint URI."),
    ] = None
    workload_pool_provider_path: Annotated[
        str | None,
        Field(description="Workload identity pool provider resource path."),
    ] = None
    service_account_impersonation_url: Annotated[
        str | None,
        Field(description="Service-account impersonation endpoint URL."),
    ] = None
    token_endpoint: Annotated[
        dict[str, str] | None,
        Field(description="OAuth token endpoint configuration."),
    ] = None
    compute_region: Annotated[
        str | None,
        Field(
            validation_alias=AliasChoices("compute_region", "dataproc_region"),
            description="Dataproc compute region.",
        ),
    ] = None
    dataproc_cluster_name: Annotated[
        str | None,
        Field(description="Dataproc cluster name."),
    ] = None
    gcs_bucket: Annotated[
        str | None,
        Field(description="Cloud Storage bucket for Dataproc submission."),
    ] = None
    submission_method: Annotated[
        str | None,
        Field(description="dbt-bigquery Dataproc submission method."),
    ] = None
    dataproc_batch: Annotated[
        dict[str, SourceOptionScalar] | None,
        Field(description="Dataproc batch configuration."),
    ] = None
    scopes: Annotated[
        list[str] | None,
        Field(description="OAuth scopes requested for BigQuery credentials."),
    ] = None

    @model_validator(mode="after")
    def _resolve_auth(self) -> BigQuerySourceConfig:
        """Validate credential fields and infer method when not explicitly set."""
        if self.keyfile is not None and self.keyfile_json is not None:
            raise ValueError(
                "BigQuery credentials must use either 'keyfile' (path to JSON file) "
                "or 'keyfile_json' (inline dict), not both."
            )
        if self.method is None:
            self.method = infer_bq_method(self.keyfile, self.keyfile_json)
        return self


class RedshiftSourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """Redshift source configuration.

    Field names match dbt profiles.yml exactly, except `autocommit`: dbt charts
    only reads on this connection, and a non-autocommit session holds
    AccessShareLock on every table it read until the connection closes,
    blocking any writer that needs ACCESS EXCLUSIVE. `build_adapter` forces
    it on unconditionally, so it isn't part of the authored surface.

    Read-only posture: no native driver flag. Bind to a SELECT-only IAM role
    or database user at the Redshift/IAM layer.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["redshift"] = Field(description="Source type identifier.")
    host: str = Field(description="Redshift cluster host name.")
    port: int = Field(default=5439, description="Redshift port number.")
    dbname: str = Field(
        validation_alias=AliasChoices("dbname", "database"),
        description="Redshift database name (dbt accepts database as an input alias).",
    )
    schema_: str = Field(
        alias="schema", default="public", description="Default schema for queries."
    )
    user: str | None = Field(
        default=None,
        description="Redshift user name. Omit for IAM-based authentication when supported.",
    )
    password: str | None = Field(
        default=None,
        validation_alias=AliasChoices("password", "pass"),
        description="Redshift password. Omit for IAM-based authentication when supported.",
    )
    method: Annotated[
        str,
        Field(description="Redshift authentication method."),
    ] = "database"
    cluster_id: Annotated[
        str | None,
        Field(
            description="Redshift cluster identifier for IAM authentication.",
        ),
    ] = None
    iam_profile: Annotated[
        str | None,
        Field(
            description="IAM profile ARN used for Redshift authentication.",
        ),
    ] = None
    autocreate: Annotated[
        bool,
        Field(description="Whether dbt may create the database user."),
    ] = False
    db_groups: Annotated[
        list[str],
        Field(
            description="Redshift database groups assigned to the user.",
        ),
    ] = Field(default_factory=list)
    ra3_node: Annotated[
        bool | None,
        Field(description="Whether the target uses Redshift RA3 nodes."),
    ] = False
    connect_timeout: Annotated[
        int | None,
        Field(description="Connection timeout in seconds."),
    ] = None
    role: Annotated[
        str | None,
        Field(description="IAM role ARN used for Redshift operations."),
    ] = None
    sslmode: Annotated[
        str | None,
        Field(
            description="SSL verification mode for the Redshift connection.",
        ),
    ] = "prefer"
    retries: Annotated[
        int,
        Field(description="Number of Redshift connection retry attempts."),
    ] = 1
    retry_all: Annotated[
        bool,
        Field(description="Whether all connection errors are retried."),
    ] = False
    region: Annotated[
        str | None,
        Field(description="AWS region containing the Redshift target."),
    ] = None
    access_key_id: Annotated[
        str | None,
        Field(description="AWS access key identifier."),
    ] = None
    secret_access_key: Annotated[
        str | None,
        Field(description="AWS secret access key."),
    ] = None
    idc_region: Annotated[
        str | None,
        Field(description="AWS IAM Identity Center region."),
    ] = None
    issuer_url: Annotated[
        str | None,
        Field(description="Identity-provider issuer URL."),
    ] = None
    idp_listen_port: Annotated[
        int | None,
        Field(description="Local port used for identity-provider callbacks."),
    ] = 7890
    idc_client_display_name: Annotated[
        str | None,
        Field(
            description="IAM Identity Center client display name.",
        ),
    ] = "Amazon Redshift driver"
    idp_response_timeout: Annotated[
        int | None,
        Field(description="Identity-provider response timeout in seconds."),
    ] = None
    token_endpoint: Annotated[
        dict[str, str] | None,
        Field(description="Identity-provider token endpoint configuration."),
    ] = None
    is_serverless: Annotated[
        bool | None,
        Field(description="Whether the target is Redshift Serverless."),
    ] = None
    serverless_work_group: Annotated[
        str | None,
        Field(description="Redshift Serverless workgroup name."),
    ] = None
    serverless_acct_id: Annotated[
        str | None,
        Field(description="AWS account identifier for Redshift Serverless."),
    ] = None
    tcp_keepalive: Annotated[
        bool | None,
        Field(description="Whether TCP keepalives are enabled."),
    ] = True
    tcp_keepalive_idle: Annotated[
        int | None,
        Field(description="Idle seconds before TCP keepalives begin."),
    ] = None
    tcp_keepalive_interval: Annotated[
        int | None,
        Field(description="Seconds between TCP keepalive probes."),
    ] = None
    tcp_keepalive_count: Annotated[
        int | None,
        Field(description="Number of TCP keepalive probes before failure."),
    ] = None


class MySQLSourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """MySQL source configuration.

    Field names match dbt profiles.yml exactly.

    Read-only posture: no native driver flag. Bind a SELECT-only user to the
    dbt profile credential at the database level.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["mysql"] = Field(description="Source type identifier.")
    host: str = Field(description="MySQL host name or IP address.")
    port: int = Field(default=3306, description="MySQL port number.")
    database: str = Field(description="MySQL database name.")
    schema_: str = Field(
        alias="schema", default="", description="Default schema for queries."
    )
    user: str = Field(description="MySQL user name.")
    password: str = Field(description="MySQL password.")


class TrinoSourceConfig(AttributedSourceConfig, DatabaseSourceConfig):
    """Trino / Presto source configuration.

    `database` names the Trino catalog; dbt-trino aliases `catalog` → `database`,
    so authors may write either key (`AliasChoices`). Authentication is
    multi-method: `method` defaults to none (a `user`, no password). The model
    declares the union of fields accepted by dbt-trino's method-specific
    credential classes; each field description names the method that consumes it.

    Read-only posture: no native driver flag. Bind a SELECT-only Trino role
    (or SELECT-only credentials on the backing catalog) at the cluster level.
    """

    source_category: ClassVar[str] = "database"
    type: Literal["trino"] = Field(description="Source type identifier.")
    host: str = Field(description="Trino coordinator host name.")
    port: int = Field(default=8080, description="Trino coordinator port number.")
    database: str = Field(
        validation_alias=AliasChoices("database", "catalog"),
        description="Trino catalog name (dbt-trino's `database`; `catalog` is accepted too).",
    )
    schema_: str = Field(alias="schema", description="Default schema for queries.")
    user: str | None = Field(
        default=None,
        description="Trino user name. Required by the none, LDAP, Kerberos, and GSSAPI methods.",
    )
    method: Annotated[
        Literal[
            "ldap", "certificate", "kerberos", "gssapi", "jwt", "oauth", "oauth_console"
        ]
        | None,
        Field(
            description="Trino authentication method. Omit for unauthenticated mode.",
        ),
    ] = None
    password: Annotated[
        str | None,
        Field(description="LDAP password. Required when method is ldap."),
    ] = None
    impersonation_user: Annotated[
        str | None,
        Field(description="User to impersonate with LDAP authentication."),
    ] = None
    client_tags: Annotated[
        list[str] | None,
        Field(description="Client tags sent with Trino queries."),
    ] = None
    roles: Annotated[
        dict[str, str] | None,
        Field(description="Catalog-to-role mapping for the Trino session."),
    ] = None
    cert: Annotated[
        str | bool | None,
        Field(description="CA certificate path or TLS verification flag."),
    ] = None
    http_scheme: Annotated[
        Literal["http", "https"],
        Field(description="HTTP scheme used to connect to Trino."),
    ] = "http"
    http_headers: Annotated[
        dict[str, str] | None,
        Field(description="Additional HTTP headers sent to Trino."),
    ] = None
    session_properties: Annotated[
        dict[str, SourceOptionScalar],
        Field(description="Trino session properties."),
    ] = Field(default_factory=dict)
    prepared_statements_enabled: Annotated[
        bool,
        Field(description="Whether Trino prepared statements are enabled."),
    ] = True
    retries: Annotated[
        int | None,
        Field(description="Maximum request retry attempts."),
    ] = 3
    timezone: Annotated[
        str | None,
        Field(description="Session time zone."),
    ] = None
    suppress_cert_warning: Annotated[
        bool | None,
        Field(description="Whether TLS certificate warnings are suppressed."),
    ] = None
    client_certificate: Annotated[
        str | None,
        Field(
            description="Client certificate path for certificate authentication.",
        ),
    ] = None
    client_private_key: Annotated[
        str | None,
        Field(
            description="Client private-key path for certificate authentication.",
        ),
    ] = None
    keytab: Annotated[
        str | None,
        Field(description="Kerberos keytab path."),
    ] = None
    principal: Annotated[
        str | None,
        Field(description="Kerberos or GSSAPI principal."),
    ] = None
    krb5_config: Annotated[
        str | None,
        Field(description="Kerberos configuration file path."),
    ] = None
    service_name: Annotated[
        str | None,
        Field(description="Kerberos or GSSAPI service name."),
    ] = None
    mutual_authentication: Annotated[
        bool | str | None,
        Field(
            description="Kerberos or GSSAPI mutual-authentication setting.",
        ),
    ] = None
    force_preemptive: Annotated[
        bool | None,
        Field(
            description="Whether Kerberos or GSSAPI auth is sent preemptively.",
        ),
    ] = None
    hostname_override: Annotated[
        str | None,
        Field(description="Kerberos or GSSAPI host name override."),
    ] = None
    sanitize_mutual_error_response: Annotated[
        bool | None,
        Field(
            description="Whether mutual-authentication errors are sanitized.",
        ),
    ] = None
    delegate: Annotated[
        bool | None,
        Field(
            description="Whether Kerberos or GSSAPI credentials are delegated.",
        ),
    ] = None
    jwt_token: Annotated[
        str | None,
        Field(description="JWT token. Required when method is jwt."),
    ] = None


class DuckDBSourceConfig(DatabaseSourceConfig):
    """DuckDB source configuration.

    Read-only posture: handled in-process. DuckDBAdapter opens file-based DuckDB
    connections with read_only=True and forces enable_external_access=False.
    Callers that need external access (e.g. the playground for local dev) can
    pass allow_external_access_in_readonly=True on
    build_adapter_registry together with duckdb_config={"enable_external_access":
    True} to explicitly opt back in.

    Authoring `attach`, `extensions`, or `secrets` is itself an explicit
    opt-in that needs external access (ATTACH/INSTALL/CREATE SECRET all
    require it): the in-process adapter does not force enable_external_access
    off for that connection, without needing allow_external_access_in_readonly.
    read_only itself is never relaxed: when the adapter is read_only, every
    `attach` entry gets READ_ONLY forced onto its ATTACH statement regardless
    of `attach[].read_only`. `settings` applies as a plain SET and
    has no external-access requirement.  `plugins`, `filesystems`, `remote`,
    and `use_credential_provider` are not supported; remove them from the
    source config.

    Example:
        sources:
          analytics:
            type: duckdb
            path: ./data/analytics.duckdb
    """

    source_category: ClassVar[str] = "database"
    type: Literal["duckdb"] = Field(description="Source type identifier.")
    path: Annotated[
        str,
        Field(
            description="DuckDB file path or ':memory:' for an in-memory database.",
        ),
    ] = ":memory:"
    schema_: Annotated[
        str | None,
        Field(
            alias="schema",
            description="Default schema for unqualified table names (sets search_path).",
        ),
    ] = None
    duckdb_config: Annotated[
        dict[str, SourceOptionScalar] | None,
        Field(
            validation_alias=AliasChoices("duckdb_config", "config_options"),
            description="DuckDB connection configuration values, such as enable_external_access.",
        ),
    ] = None
    extensions: Annotated[
        list[str | DuckDBExtensionConfig] | None,
        Field(description="DuckDB extensions to install and load."),
    ] = None
    settings: Annotated[
        dict[str, SourceOptionScalar] | None,
        Field(description="DuckDB settings and pragma values."),
    ] = None
    secrets: Annotated[
        list[DuckDBSecretConfig] | None,
        Field(description="DuckDB secret definitions for external services."),
    ] = None
    external_root: Annotated[
        str,
        Field(
            description="Root path for dbt-duckdb external materializations.",
        ),
    ] = "."
    use_credential_provider: Annotated[
        str | None,
        Field(description="Not supported; remove this field from the source config."),
    ] = None
    attach: Annotated[
        list[DuckDBAttachmentConfig] | None,
        Field(description="Databases to attach to the DuckDB connection."),
    ] = None
    filesystems: Annotated[
        list[dict[str, SourceOptionScalar]] | None,
        Field(description="Not supported; remove this field from the source config."),
    ] = None
    remote: Annotated[
        DuckDBRemoteConfig | None,
        Field(description="Not supported; remove this field from the source config."),
    ] = None
    plugins: Annotated[
        list[DuckDBPluginConfig] | None,
        Field(description="Not supported; remove this field from the source config."),
    ] = None
    disable_transactions: Annotated[
        bool,
        Field(
            description="Whether dbt-duckdb disables statement transactions.",
        ),
    ] = False
    keep_open: Annotated[
        bool,
        Field(
            description=(
                "Whether dbt-duckdb holds its connection open between queries. "
                "Off by default: a held handle pins the database file at one "
                "DuckDB config, and DuckDB refuses any other connection to a "
                "pinned file whose config differs. Ignored for ':memory:' and "
                "MotherDuck, which dbt-duckdb holds open either way; closing "
                "an in-memory database would destroy it."
            )
        ),
    ] = False
    module_paths: Annotated[
        list[str] | None,
        Field(description="Python module paths dbt-duckdb loads."),
    ] = None
    retries: Annotated[
        DuckDBRetriesConfig | None,
        Field(description="dbt-duckdb connection and query retry configuration."),
    ] = None
    is_ducklake: Annotated[
        bool | None,
        Field(description="Whether this source uses DuckLake."),
    ] = None

    @field_validator("schema_", mode="before")
    @classmethod
    def validate_schema_identifier(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", v):
            raise ValueError(
                f"schema must be a valid SQL identifier (letters, digits, underscores; "
                f"no leading digit): got {v!r}"
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def reject_database_key(cls, data: Any) -> Any:
        """Reject the legacy 'database' key at parse time.

        The runtime already raises on 'database', so reject it here too so
        the two layers agree and the error fires as early as possible.
        """
        if isinstance(data, dict) and "database" in data:
            raise ValueError(
                "DuckDB source config uses 'path', not 'database'. "
                "Replace 'database' with 'path' "
                '(e.g. {"type": "duckdb", "path": "db.duckdb"}).'
            )
        return data


class SQLiteSourceConfig(BaseSourceConfig):
    """SQLite source configuration.

    Read-only posture: handled in-process. SqliteAdapter opens SQLite connections
    in read-only URI mode (``?mode=ro``) so the driver refuses all writes.

    Example:
        sources:
          bird:
            type: sqlite
            path: ./data/bird.sqlite
    """

    source_category: ClassVar[str] = "database"
    type: Literal["sqlite"] = Field(description="Source type identifier.")
    path: str = Field(description="Path to the SQLite database file.")


# ============================================================================
# FILE SOURCE CONFIGS
# ============================================================================


class CsvSourceConfig(BaseSourceConfig):
    """CSV file source configuration.

    A file source is a namespace of relations: each key in `files` is a table
    name; the value is the path to the CSV file (relative to the project root).
    Glob patterns (``*`` and ``?``) are supported: every matched file is loaded
    and its rows are concatenated into one table.  All matched files must share
    the same column schema.  A glob that matches no files is an error.

    Example:
        sources:
          sales:
            type: csv
            files:
              sales: assets/data/sales.csv
              returns: assets/data/returns.csv
              monthly: assets/data/monthly_*.csv
    """

    source_category: ClassVar[str] = "file"
    type: Literal["csv"] = Field(description="Source type identifier.")
    files: dict[str, str] = Field(
        description=(
            "Mapping of table_name → file path or glob (``*``/``?``), relative "
            "to the project root. A glob must match at least one file; matched "
            "files must share one column schema and concatenate into one "
            "table. Limits: max 1000 files per glob, max 500 tables, max 5 GB "
            "per table (``execution.max_glob_file_count`` / "
            "``file_source_max_tables`` / ``file_source_max_bytes``). "
            "Required, non-empty."
        )
    )
    delimiter: str = Field(default=",", description="Field delimiter character.")
    encoding: str = Field(default="utf-8", description="File encoding.")

    @model_validator(mode="after")
    def _require_nonempty_files(self) -> CsvSourceConfig:
        if not self.files:
            raise ValueError(
                "CsvSourceConfig: files must not be empty — "
                "provide at least one table_name → path entry."
            )
        _validate_files_mapping("CsvSourceConfig", self.files, ".csv")
        return self


class ParquetSourceConfig(BaseSourceConfig):
    """Parquet file source configuration.

    A file source is a namespace of relations: each key in `files` is a table
    name; the value is the path to the Parquet file (relative to the project root).
    Glob patterns (``*`` and ``?``) are supported: every matched file is loaded
    and its rows are concatenated into one table.  All matched files must share
    the same column schema.  A glob that matches no files is an error.

    Example:
        sources:
          events:
            type: parquet
            files:
              events: assets/data/events.parquet
              archive: assets/data/archive_*.parquet
    """

    source_category: ClassVar[str] = "file"
    type: Literal["parquet"] = Field(description="Source type identifier.")
    files: dict[str, str] = Field(
        description=(
            "Mapping of table_name → file path or glob (``*``/``?``), relative "
            "to the project root. A glob must match at least one file; matched "
            "files must share one column schema and concatenate into one "
            "table. Limits: max 1000 files per glob, max 500 tables, max 5 GB "
            "per table (``execution.max_glob_file_count`` / "
            "``file_source_max_tables`` / ``file_source_max_bytes``). "
            "Required, non-empty."
        )
    )

    @model_validator(mode="after")
    def _require_nonempty_files(self) -> ParquetSourceConfig:
        if not self.files:
            raise ValueError(
                "ParquetSourceConfig: files must not be empty — "
                "provide at least one table_name → path entry."
            )
        _validate_files_mapping("ParquetSourceConfig", self.files, ".parquet")
        return self


class JsonSourceConfig(BaseSourceConfig):
    """JSON file source configuration.

    A file source is a namespace of relations: each key in `files` is a table
    name; the value is the path to the JSON file (relative to the project root).
    Both ``.json`` and ``.jsonl`` (newline-delimited JSON) extensions are accepted;
    the parser is chosen by extension: ``.json`` → standard JSON array/object,
    ``.jsonl`` → newline-delimited JSON (one object per line).
    Glob patterns (``*`` and ``?``) are supported: every matched file is loaded
    and its rows are concatenated into one table.  A glob that matches no files
    is an error.

    Example:
        sources:
          products:
            type: json
            files:
              products: assets/data/products.json
              events: assets/data/events_*.jsonl
    """

    source_category: ClassVar[str] = "file"
    type: Literal["json"] = Field(description="Source type identifier.")
    files: dict[str, str] = Field(
        description=(
            "Mapping of table_name → file path or glob (``*``/``?``), relative "
            "to the project root. A glob must match at least one file; matched "
            "files concatenate into one table. Limits: max 1000 files per "
            "glob, max 500 tables, max 5 GB per table "
            "(``execution.max_glob_file_count`` / ``file_source_max_tables`` "
            "/ ``file_source_max_bytes``). Required, non-empty."
        )
    )
    union_by_name: bool = Field(
        default=False,
        description=(
            "When True, rows from different files in a glob set are unified by "
            "column name: columns absent in a particular file are filled with "
            "NULL.  When False (default), all files in a glob set must share the "
            "same column schema; a mismatch raises an error."
        ),
    )

    @model_validator(mode="after")
    def _require_nonempty_files(self) -> JsonSourceConfig:
        if not self.files:
            raise ValueError(
                "JsonSourceConfig: files must not be empty — "
                "provide at least one table_name → path entry."
            )
        _validate_files_mapping("JsonSourceConfig", self.files, (".json", ".jsonl"))
        return self


# ============================================================================
# HTTP/API SOURCE CONFIG
# ============================================================================


class HttpSourceConfig(BaseSourceConfig):
    """HTTP/REST API source configuration."""

    source_category: ClassVar[str] = "api"
    type: Literal["http"] = Field(description="Source type identifier.")
    url: str = Field(description="Base URL for HTTP requests.")
    headers: dict[str, str] | None = Field(
        default=None, description="Default HTTP headers (e.g. Authorization)."
    )


# ============================================================================
# DBT PROFILE SOURCE CONFIG
# ============================================================================


class DbtProfileSourceConfig(AttributedSourceConfig, BaseSourceConfig):
    """Reference to a dbt profile.

    Instead of defining connection details inline, references an existing
    dbt profile from profiles.yml.
    """

    # Its own category, not "database": this is a *reference* the resolver
    # expands into a DbtTargetSourceConfig, and only that expansion knows which
    # warehouse it names. is_database_source stays False for it, as it was.
    source_category: ClassVar[str] = "dbt_profile"
    type: Literal["dbt_profile"] = Field(description="Source type identifier.")
    profile: str = Field(description="dbt profile name from profiles.yml.")
    target: str | None = Field(
        default=None,
        description="dbt target to use; defaults to the profile's default target.",
    )
    profiles_dir: str | None = Field(
        default=None,
        description=(
            "Directory containing profiles.yml, relative to the linked dbt project "
            "directory (see --dbt-project-dir). Use when profiles.yml is in a "
            "subdirectory (e.g. services/dbt). "
            "Resolution order: profiles_dir → $DBT_PROFILES_DIR → linked dbt project → ~/.dbt."
        ),
    )


# ============================================================================
# UNION TYPE
# ============================================================================


class DbtTargetSourceConfig(AttributedSourceConfig, BaseSourceConfig):
    """A profiles.yml target, already resolved and validated by dbt.

    Not authorable: a user writes `type: dbt_profile`, and the resolver expands it
    into this by asking dbt for the profile. `extra="allow"` is the point — dbt
    owns profiles.yml and its credentials class is the authority on that schema, so
    declaring fields here would re-create the mirror that rejected valid dbt config
    (`threads`, canonical BigQuery `database`/`schema`). Every connection field dbt
    accepted reaches the adapter untouched; dropping one would connect with
    different semantics than dbt, which is worse than erroring.

    `type` is a plain str rather than a Literal because dbt resolves it from the
    installed adapter — that also keeps this out of `_SOURCE_TYPE_MAP`, so it stays
    unauthorable.
    """

    model_config = ConfigDict(extra="allow")

    source_category: ClassVar[str] = "database"
    type: str = Field(description="Warehouse type dbt resolved for this target.")


# Union of all source config types
SourceConfig = (
    PostgresSourceConfig
    | SnowflakeSourceConfig
    | BigQuerySourceConfig
    | RedshiftSourceConfig
    | MySQLSourceConfig
    | TrinoSourceConfig
    | DuckDBSourceConfig
    | SQLiteSourceConfig
    | CsvSourceConfig
    | ParquetSourceConfig
    | JsonSourceConfig
    | HttpSourceConfig
    | DbtProfileSourceConfig
)

# What the execute layer receives from a SourceResolver. Wider than SourceConfig
# because a dbt_profile expands into a DbtTargetSourceConfig, which is not
# authorable and so deliberately stays out of the union above (that union is the
# set of types a user can write and parse_source_config can build).
ResolvedSourceConfig = SourceConfig | DbtTargetSourceConfig


def _source_type_str(cls: type[SourceConfig]) -> str:
    """Extract the Literal type string from a SourceConfig subclass."""
    return get_args(get_type_hints(cls)["type"])[0]


# Derived from the SourceConfig union — no manual maintenance needed.
# Adding a new SourceConfig variant automatically updates this map.
_SOURCE_TYPE_MAP: dict[str, type[SourceConfig]] = {
    _source_type_str(m): m for m in get_args(SourceConfig)
}

VALID_SOURCE_TYPES: frozenset[str] = frozenset(_SOURCE_TYPE_MAP)

# The type names a host can recognize as a file source from raw YAML alone,
# before anything is parsed — for a host that must decide "does this entry
# need a connection?" while reading a committed dbt_charts.yml (Cloud does).
# Derived from the union, so a new file variant is never a list to update.
FILE_SOURCE_TYPES: frozenset[str] = frozenset(
    type_name
    for type_name, model in _SOURCE_TYPE_MAP.items()
    if model.source_category == "file"
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def parse_source_config(
    data: dict[str, Any],  # type-state: explicit_any — unvalidated YAML mapping
    *,
    env: Mapping[str, str] | None = None,
) -> SourceConfig:
    """Parse a source configuration dictionary into the appropriate type.

    Callers hand this a committed ``dbt_charts.yml`` that nobody validated, so
    the annotation above is a promise, not a fact: ``yaml.safe_load`` turns
    ``2024:`` into an int key and ``type: [csv]`` into a list. The two shape
    checks below are what make the promise true for everything downstream —
    without them a non-string key reaches the model as a bad keyword argument
    and an unhashable ``type`` blows up the lookup, so every caller would have
    to catch a TypeError this contract never named.

    ``env`` is the environment ``env_var()`` renders against. ``None`` — the
    documented default for local, trusted config — is the live process
    environment. A host reading remote-committed YAML passes ``{}`` so only
    author-supplied defaults render; the render happens once, inside the
    model, so there is no second pass for an escaped template to reach.

    Raises:
        ValueError: If any key is not a string, or ``type`` is missing, not a
            string, or unknown.
    """
    # Sequence[object] is the honest view of keys YAML built; the annotation
    # cannot express it, and this line exists to reject the ones that break it.
    keys: Sequence[object] = list(data)  # type-state: object_annotation — YAML keys
    non_string_keys = sorted(repr(key) for key in keys if not isinstance(key, str))
    if non_string_keys:
        raise ValueError(
            f"Source configuration keys must be strings; got "
            f"{', '.join(non_string_keys)}. An unquoted YAML key like `2024:` "
            "or `on:` parses as a number or a bool — quote it."
        )

    if "type" not in data:
        raise ValueError("Source configuration must have a 'type' field")

    source_type = data["type"]
    if not isinstance(source_type, str):
        raise ValueError(
            f"Source configuration 'type' must be a string; got {source_type!r}."
        )
    if source_type not in _SOURCE_TYPE_MAP:
        raise ValueError(
            f"Unknown source type: '{source_type}'. "
            f"Valid types: {', '.join(sorted(_SOURCE_TYPE_MAP.keys()))}"
        )

    return _SOURCE_TYPE_MAP[source_type].model_validate(data, context={"env": env})


def is_database_source(config: ResolvedSourceConfig) -> bool:
    """True if config is a database source (source_category == 'database')."""
    return type(config).source_category == "database"


def is_file_source(config: ResolvedSourceConfig) -> bool:
    """True if config is a file-based source (source_category == 'file')."""
    return type(config).source_category == "file"


def is_api_source(config: ResolvedSourceConfig) -> bool:
    """True if config is an HTTP/API source (source_category == 'api')."""
    return type(config).source_category == "api"


def source_cache_layer(
    sources: dict[str, Any], source_name: str | None, scope: str
) -> CachePatch | None:
    """The cache layer authored on *source_name*, if any.

    The registry holds either validated configs or the raw dicts they were
    authored as, depending on how far normalization has gotten; both spellings
    carry the same ``cache:`` layer. ``scope`` names the consumer for the error
    message a malformed value raises.
    """
    if source_name is None:
        return None
    config = sources.get(source_name)
    if isinstance(config, dict):
        return validate_cache_layer(scope, config.get("cache"))
    if isinstance(config, BaseSourceConfig):
        return validate_cache_layer(scope, config.cache)
    return None
