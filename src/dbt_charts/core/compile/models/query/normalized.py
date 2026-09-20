"""Unified query interface types — compiled representation.

Stage: COMPILE (Output) / EXECUTE (Input)
Purpose: Define type-safe query classes with a unified interface.

This module implements the unified query interface pattern where:
- All queries inherit from a common Query base class
- Each query type has its own class with type-specific fields
- Type guards enable type-safe narrowing in adapter code
- The interface makes adapter routing clear and type-safe

Benefits:
- Type safety: Compile-time verification of query fields
- Self-documenting: Each query class shows exactly which fields apply
- Extensibility: Adding new query types is isolated
- IDE support: Autocomplete and refactoring work correctly

Dependencies:
    - pydantic (BaseModel)
    - abc (ABC, abstractmethod)

See also:
    - execute/adapters/base.py: Adapters consume these types
"""

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Annotated, Any, Literal, TypeGuard, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.cache import NEVER_CACHED, CachePolicy
from dbt_charts.core.compile.models.primitives import VariableDependencies
from dbt_charts.core.compile.models.query.authored import (
    AuthoredQuery,
    RestMethod,
)
from dbt_charts.core.diagnostics.execution import SqlErrorPosition


class SqlParseError(BaseModel):
    """A compile-time SQL parse failure the author should see.

    ``position`` is SQL-local (relative to the query's own text), the same
    coordinates ``stamp_diagnostics`` offsets into the board file — so the
    warning can underline the offending token rather than the whole block.
    """

    message: str
    position: SqlErrorPosition

    model_config = ConfigDict(extra="forbid")


# ============================================================================
# BASE QUERY CLASS
# ============================================================================


class Query(BaseModel, ABC):
    """Base query interface - all queries return tabular data.

    This abstract base class defines the common interface for all query types.
    Subclasses declare a ``query_type: Literal[...]`` field (the AnyQuery
    discriminator — see below) and implement the source_description property.

    Common Fields (available on all queries):
        limit: Optional maximum number of rows to return

    Abstract Properties:
        source_description: Human-readable description of data source

    Example:
        >>> class CustomQuery(Query):
        ...     query_type: Literal["custom"] = "custom"
        ...     custom_field: str
        ...
        ...     @property
        ...     def source_description(self) -> str:
        ...         return f"Custom: {self.custom_field}"
    """

    # Common fields all queries can have
    notes: str | None = Field(
        default=None,
        description="Human-readable notes about this query. Never rendered.",
    )
    limit: int | None = Field(
        default=None, description="Maximum number of rows to return."
    )

    # Diagnostic suppression — list of diagnostic codes to suppress
    ignore: list[str] | None = Field(
        default=None,
        description=(
            "Diagnostic codes to suppress (e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION'])."
        ),
    )

    # Resolved cache policy, stamped by normalize_query from the four-scope
    # cascade (project root → source → board → query). Direct constructors
    # that skip the cascade (tests, a lowering path that forgets to stamp)
    # get NEVER_CACHED — fail-safe: a missed stamp silently means "don't
    # cache" (a visible perf cost), never "cache forever" (silently stale
    # data). NEVER_CACHED is frozen, so sharing the one instance as every
    # unstamped query's default is safe. When policy.enabled is False, this
    # query must not be used as a {{ queries.X.cache }} upstream.
    cache: CachePolicy = Field(
        default=NEVER_CACHED,
        description=(
            "Resolved cache policy: whether results may be cached (and "
            "referenced via {{ queries.X.cache }}) and when they expire."
        ),
    )

    # Variable dependencies - computed during normalization
    variable_dependencies: VariableDependencies = Field(
        default_factory=frozenset,
        description="Variable names this query references in SQL (computed during normalization).",
    )

    # Incremental refresh watermark column — stamped by normalize_query from
    # the board→query cascade. None means full refresh on every cache miss.
    incremental: str | None = Field(
        default=None,
        description=(
            "Column used as the monotonic watermark for incremental refresh. "
            "When set, the executor fetches only rows after the prior "
            "watermark and merges them with the cached result set."
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @property
    @abstractmethod
    def source_description(self) -> str:
        """Human-readable description of data source.

        Returns:
            String describing the data source for debugging/logging
        """
        pass

    def apply_limit(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply limit to result data.

        Args:
            data: Query results as list of dicts

        Returns:
            Possibly truncated list of dicts
        """
        if self.limit and self.limit > 0:
            return data[: self.limit]
        return data


# ============================================================================
# TYPE-SPECIFIC QUERY CLASSES
# ============================================================================


class SqlQuery(Query):
    """SQL query against a database.

    Executes raw SQL against the configured database connection.
    Supports Jinja templating for variable substitution.

    Required Fields:
        sql: SQL query string (may contain Jinja templates)
        source: Registry source name for the database connection

    Optional Fields:
        target: dbt target name (defaults to 'dev' if not specified)

    Example:
        >>> query = SqlQuery(
        ...     sql="SELECT * FROM users WHERE id = {{ user_id }}",
        ...     source="my_postgres"
        ... )
        >>> query.query_type  # "sql"
        >>> query.source_description  # "SQL: SELECT * FROM users WHERE id = {{ user_..."
    """

    query_type: Literal["sql"] = Field(
        default="sql",
        description="Discriminator key identifying this as a SQL query.",
    )
    sql: str = Field(
        description="SQL query string. May contain Jinja templates (e.g., {{ variable_name }})."
    )
    setup_sql: str | None = Field(
        default=None,
        description="Non-nestable SQL preamble (e.g., CREATE TEMP FUNCTION). Runs before the main query.",
    )
    # Connection configuration. A *compiled* SqlQuery always names its source:
    # normalize_query injects the inherited board/meta default and raises
    # ERR-SOURCE-REQUIRED / -NOT-FOUND, so a board query that reaches
    # execution carries a registry-validated name. The field stays optional
    # because the ad-hoc runtime-execution boundary (execute_query /
    # describe_query with no `source`) legitimately constructs a sourceless
    # query: locally it runs against the scratch DuckDB, and hosted surfaces
    # reject it in the guarded resolver (AllowlistedSourceResolver) — that is
    # the trust boundary, not this field's type.
    source: str | None = Field(
        default=None,
        description="Source name for the database connection.",
    )
    target: str | None = Field(
        default=None,
        description="dbt target name (defaults to 'dev' if not specified).",
    )
    lenient_variables: bool = Field(
        default=False,
        description="When True, undefined Jinja variables degrade gracefully (empty string) instead of raising. Intended for the iterative chart-editor flow where not all variables are set yet.",
    )
    # Populated by normalize_query when the compile-time SQL guard could not
    # parse this query AND the failure is trustworthy enough to report: the
    # dialect was resolvable and sqlglot gave a concrete position. The
    # compiler harvests it into a WARN-PARSE-ERROR so editors squiggle at
    # compile time; the guard itself still defers to runtime either way.
    # None means "nothing worth reporting", never "not yet checked".
    parse_error: SqlParseError | None = Field(
        default=None,
        description="Compile-time SQL parse failure worth surfacing to the author.",
    )

    @property
    def source_description(self) -> str:
        """Return truncated SQL for description."""
        sql_preview = self.sql[:50] if len(self.sql) > 50 else self.sql
        return f"SQL: {sql_preview}..."


class HttpQuery(Query):
    """Query data from an HTTP API.

    Fetches data from REST API endpoints. Supports various HTTP methods,
    headers, query parameters, and request bodies.

    Required Fields:
        url: Full URL of the API endpoint

    Optional Fields:
        method: HTTP method (default: GET)
        headers: Request headers
        params: Query parameters
        body: Request body (for POST/PUT/PATCH)
        json_path: JSONPath expression to extract data from response

    Example:
        >>> query = HttpQuery(
        ...     url="https://api.example.com/predict",
        ...     method="POST",
        ...     body={"input": "data"}
        ... )
        >>> query.query_type  # "http"
        >>> query.source_description  # "HTTP: POST https://api.example.com/predict"
    """

    query_type: Literal["http"] = Field(
        default="http",
        description="Discriminator key identifying this as an HTTP query.",
    )
    url: str = Field(
        description="Full URL of the HTTP endpoint.",
    )
    method: RestMethod = Field(
        default="GET",
        description="HTTP method (GET, POST, PUT, DELETE, PATCH). Default: GET.",
    )
    headers: dict[str, str] | None = Field(
        default=None, description="Request headers (merged with source-level headers)."
    )
    params: dict[str, Any] | None = Field(
        default=None, description="URL query parameters."
    )
    body: dict[str, Any] | str | None = Field(
        default=None,
        description="Request body for POST/PUT/PATCH (dict or JSON string).",
    )
    json_path: str | None = Field(
        default=None,
        description="JSONPath expression to extract tabular data from the response.",
    )

    @property
    def source_description(self) -> str:
        """Return method and URL for description."""
        return f"HTTP: {self.method} {self.url}"


class ValuesQuery(Query):
    """Inline data query with rows embedded directly in the YAML.

    Provides two syntaxes:
    1. Dict rows (verbose): rows: [{month: "Jan", revenue: 100}, ...]
    2. Columns + values (compact, SQL-style): columns: [month, revenue], values: [["Jan", 100], ...]

    When using columns + values, rows are computed automatically via model_post_init.

    Example (dict rows):
        >>> query = ValuesQuery(rows=[{"month": "Jan", "revenue": 100}])

    Example (columns + values):
        >>> query = ValuesQuery(
        ...     columns=["month", "revenue"],
        ...     values=[["Jan", 100], ["Feb", 140]],
        ... )
        >>> query.rows  # [{"month": "Jan", "revenue": 100}, {"month": "Feb", "revenue": 140}]
    """

    query_type: Literal["values"] = Field(
        default="values",
        description="Discriminator key identifying this as a values query.",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Inline data rows as a list of dicts (e.g., [{month: 'Jan', revenue: 100}]).",
    )
    columns: list[str] | None = Field(
        default=None, description="Column names for compact 'columns + values' syntax."
    )
    values: list[list[Any]] | None = Field(
        default=None,
        description="Row values for compact syntax, parallel to 'columns' (e.g., [['Jan', 100], ['Feb', 140]]).",
    )

    def model_post_init(self, _context: Any) -> None:
        """Build rows from columns + values if provided."""
        if self.columns and self.values is not None and not self.rows:
            ncols = len(self.columns)
            for i, row in enumerate(self.values):
                if len(row) != ncols:
                    raise ValueError(
                        f"Values row {i} has {len(row)} items, "
                        f"expected {ncols} (columns: {self.columns})"
                    )
            self.rows = [
                dict(zip(self.columns, row, strict=True)) for row in self.values
            ]
        n = len(self.rows)
        content_hash = hashlib.sha256(
            json.dumps(self.rows, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        self._source_description = (
            f"Values: {n} {'row' if n == 1 else 'rows'} [{content_hash}]"
        )

    # Filled eagerly by model_post_init, once rows are final. A PrivateAttr is
    # excluded from model_fields and every dump, so the memo cannot leak into
    # serialization the way a bare `object.__setattr__` stash did.
    # `cached_property` would also avoid the leak but cannot satisfy the abstract
    # `property` it overrides (pyright reportIncompatibleMethodOverride).
    # Eager, not lazy: pydantic equality compares private attributes, so a memo
    # filled on first read would make two otherwise-equal queries differ based on
    # whether anything had read `source_description` — which broke round-trip
    # equality for every model embedding a ValuesQuery.
    _source_description: str = PrivateAttr(default="")

    @property
    def source_description(self) -> str:
        """Return content-based description for caching.

        Includes a hash of actual row data so that distinct ValuesQuery
        instances with the same row count produce different cache keys.

        Raises:
            ValueError: If the memo was never filled, meaning this instance
                skipped ``model_post_init`` (e.g. ``model_construct``). Since
                this value keys the query-result cache, an empty description
                would silently collide distinct datasets onto one cache entry —
                a wrong-result bug. Fail loudly instead.
        """
        if not self._source_description:
            raise ValueError(
                f"{type(self).__name__}.source_description was never computed: "
                "this instance skipped model_post_init. Construct ValuesQuery "
                "through validation rather than model_construct."
            )
        return self._source_description


class SchemaQuery(Query):
    """Query the LayeredSchemaResolver in-process.

    Dispatches to list_sources / list_schemas / list_tables / profile_table /
    profile_column based on which optional fields are populated:
    - (no source) → list_sources (returns configured source names)
    - source only → list_schemas(source)
    - source + schema → list_tables(source, schema)
    - source + schema + table → profile_table(source, schema, table) → column rows
    - source + schema + table + column → profile_column(source, schema, table, column)

    Note: The YAML field is ``schema`` but the Python attribute is ``schema_name``
    to avoid shadowing ``BaseModel.schema`` (a Pydantic v2 legacy classmethod).

    Fields must be literal strings — Jinja templates (``{{``, ``{%``, ``{#``)
    are rejected at model construction time.  Field prerequisites are also
    validated at construction time: ``table`` requires ``schema``; ``column``
    requires both ``schema`` and ``table``.
    """

    query_type: Literal["schema"] = Field(
        default="schema",
        description="Discriminator key identifying this as a schema query.",
    )
    source: str | None = Field(
        default=None,
        description="dbt source name to query. Omit to list all configured sources.",
    )
    # alias="schema" so YAML authors write `schema: analytics`; Python code uses
    # query.schema_name to avoid the BaseModel.schema classmethod conflict.
    schema_name: str | None = Field(
        default=None, alias="schema", description="Schema name (omit to list schemas)."
    )
    table: str | None = Field(
        default=None, description="Table name (omit to list tables)."
    )
    column: str | None = Field(
        default=None, description="Column name (omit for full table profile)."
    )
    fields: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Project the result rows to exactly these keys, in this order: "
            "the schema-query counterpart of a SQL SELECT list. A projected "
            "key a row lacks yields null (metadata key sets vary with "
            "profiling depth). Omit to return every key each row carries."
        ),
    )

    # populate_by_name=True: 'schema' is a Pydantic BaseModel class method name
    # (BaseModel.schema() in v1 / model_json_schema() in v2), so using it as a
    # field name directly would shadow the classmethod. The Python field is
    # `schema_name`; YAML authors write `schema:` and the alias bridges the two.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator(
        "source", "schema_name", "table", "column", "fields", mode="before"
    )
    @classmethod
    def _no_jinja(cls, v: Any) -> Any:
        # `fields` arrives as a list — check each element; the scalar fields
        # fall through the isinstance(str) branch unchanged.
        values = v if isinstance(v, (list, tuple)) else [v]
        for item in values:
            if isinstance(item, str) and any(tok in item for tok in ("{{", "{%", "{#")):
                raise ValueError(
                    "schema fields must be literal strings; "
                    "Jinja templates ({{ }}, {% %}, {# #}) are not supported."
                )
        return v

    @model_validator(mode="after")
    def _field_prerequisites(self) -> "SchemaQuery":
        if self.schema_name is not None and self.source is None:
            raise ValueError("schema: 'schema' requires 'source' to be set")
        if self.table is not None and self.schema_name is None:
            raise ValueError("schema: 'table' requires 'schema' to be set")
        if self.column is not None and self.table is None:
            raise ValueError(
                "schema: 'column' requires 'table' (and 'schema') to be set"
            )
        return self

    def project(
        self,
        data: list[dict[str, Any]],  # type-state: explicit_any — raw query rows
    ) -> list[dict[str, Any]]:  # type-state: explicit_any — raw query rows
        """Apply the ``fields:`` projection (no-op when unset)."""
        if self.fields is None:
            return data
        return [{f: row.get(f) for f in self.fields} for row in data]

    @property
    def source_description(self) -> str:
        parts: list[str] = []
        if self.source:
            parts.append(self.source)
        if self.schema_name:
            parts.append(self.schema_name)
        if self.table:
            parts.append(self.table)
        if self.column:
            parts.append(self.column)
        base = f"schema: {'.'.join(parts) if parts else '(all sources)'}"
        # The projection is part of what this query yields — and non-SQL cache
        # identity hashes source_description, so two queries over the same
        # target with different `fields:` must not share a description.
        if self.fields is not None:
            base += f" → {', '.join(self.fields)}"
        return base


# ============================================================================
# TYPE ALIASES
# ============================================================================


# Type alias for any query - discriminated union of all query types.
# Use this for type annotations when you want to accept any query type.
# query_type is a real Literal field on each subclass (not the old abstract
# property), so Pydantic can dispatch validation/serialization by exact type
# match instead of trying every union member in turn.
AnyQuery = Annotated[
    SqlQuery | HttpQuery | ValuesQuery | SchemaQuery,
    Discriminator("query_type"),
]


def _derive_query_types() -> set[str]:
    """Derive valid query type strings from the AuthoredQuery union members' type tags."""
    types: set[str] = set()
    union = get_args(AuthoredQuery)[0]  # strip Annotated → the Union
    for member in get_args(union):
        types.update(get_args(member.model_fields["type"].annotation))
    return types


# Derived from the AuthoredQuery union members' Literal tags — no manual maintenance.
VALID_QUERY_TYPES: set[str] = _derive_query_types()


# ============================================================================
# TYPE GUARDS
# ============================================================================


def is_sql_query(query: AnyQuery) -> TypeGuard[SqlQuery]:
    """Type guard for SQL queries.

    Use this for type-safe narrowing in conditional blocks.
    After this check, the type checker knows the query is SqlQuery.

    Args:
        query: Any compiled query

    Returns:
        True if query is a SqlQuery

    Example:
        >>> if is_sql_query(query):
        ...     # Type checker knows query.sql exists
        ...     print(query.sql)
    """
    return query.query_type == "sql"


def is_http_query(query: AnyQuery) -> TypeGuard[HttpQuery]:
    """Type guard for HTTP queries.

    Use this for type-safe narrowing in conditional blocks.
    After this check, the type checker knows the query is HttpQuery.

    Args:
        query: Any compiled query

    Returns:
        True if query is a HttpQuery

    Example:
        >>> if is_http_query(query):
        ...     # Type checker knows query.url exists
        ...     print(query.url)
    """
    return query.query_type == "http"


def is_values_query(query: AnyQuery) -> TypeGuard[ValuesQuery]:
    """Type guard for values queries.

    Args:
        query: Any compiled query

    Returns:
        True if query is a ValuesQuery
    """
    return query.query_type == "values"


def is_schema_query(query: AnyQuery) -> TypeGuard[SchemaQuery]:
    """Type guard for schema queries.

    Args:
        query: Any compiled query

    Returns:
        True if query is a SchemaQuery
    """
    return query.query_type == "schema"
