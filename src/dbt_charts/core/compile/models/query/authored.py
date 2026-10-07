"""Authored query types — YAML input representation.

Stage: COMPILE (Input)
Purpose: Types that map directly to the YAML query schema.

AuthoredQuery is a discriminated union over per-type models (the chart-family
pattern): each query type carries only its own fields with ``extra="forbid"``,
so cross-type field misuse (``pivots:`` on a ``type: sql`` query) fails at
parse time. The ``type`` discriminator is stamped before validation by
``normalize_query_value`` (refs.py), which infers it from key presence when
the author omits it.

These live here (separate from board/authored.py) because:
- Discriminated-union models (board/authored.py) import AuthoredQuery via QueryOrRef.
- Keeping AuthoredQuery here avoids the discriminated-union module importing itself.
"""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
)

from dbt_charts.core.compile.models.cache import CachePatch
from dbt_charts.core.compile.models.markers import DisplayText, ExplicitTag, Format
from dbt_charts.core.compile.models.primitives import (
    IncrementalValue,
    validate_incremental_value,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.diagnostics.suppression import validate_suppression_codes

# ============================================================================
# QUERY-LEVEL PRIMITIVES (used by both authored and compiled layers)
# ============================================================================

RestMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]

# Every warehouse dialect, one spelling each: the dialect registry's names
# minus its aliases (postgresql, mssql, mariadb).
QueryDialect = Literal[
    "athena",
    "bigquery",
    "clickhouse",
    "databricks",
    "duckdb",
    "fabric",
    "mysql",
    "postgres",
    "presto",
    "redshift",
    "snowflake",
    "spark",
    "sqlite",
    "sqlserver",
    "trino",
]


# ============================================================================
# AUTHORED QUERY MODELS — one class per query type, discriminated on `type`
# ============================================================================


class _BaseQueryFields(BaseModel):
    """Fields shared by every authored query type.

    Use ``isinstance(obj, _BaseQueryFields)`` to check whether an object is an
    authored query instance — ``AuthoredQuery`` is a union alias, not a class.
    """

    model_config = ConfigDict(extra="forbid")

    # Source name reference or inline file path (e.g. ./data/sales.csv). The dict
    # branch is authored-stage-only: it must NOT reject at the model layer — a
    # dict value has to survive parsing so normalize_query() (normalize/queries.py)
    # can raise the friendly ERR-SOURCE-INLINE-FORBIDDEN, not a generic
    # Pydantic type-mismatch error. Never reaches the normalized SqlQuery.source
    # (str | None) — normalize_query() rejects the dict before constructing it.
    source: str | dict[str, Any] | None = Field(
        default=None,
        description=(
            "Source name reference, or an inline file path (e.g. `./data/sales.csv`). "
            "File paths are detected by `/` or data file extension. An inline "
            "connection-bearing dict (`{type: postgres, ...}`) is rejected at "
            "compile time; reference a named source instead."
        ),
    )
    notes: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Prose summary of what this query returns, passed along with its results to tooling.",
    )
    ignore: list[str] | None = Field(
        default=None,
        description=(
            "Diagnostic codes to suppress for this query "
            "(e.g., ['WARN-FANOUT-RISK', 'WARN-REAGGREGATION'])."
        ),
    )
    cache: CachePatch | None = Field(
        default=None,
        description=(
            "Cache policy override for this query, e.g. cache: 5m: refines "
            "the policy inherited from the source and project scopes; "
            "cache: false opts out of result caching entirely. Queries with "
            "cache: false cannot be used as {{ queries.X.cache }} upstream "
            "references."
        ),
    )
    formats: Annotated[dict[str, FormatAlias | str] | None, Format()] = Field(
        default=None,
        description=(
            "How the query's columns print, as column: format name. A name is "
            "an engine format (`currency`, `percent`, `year`, ...) or one the "
            "board defines in `style.formats`; a d3 spec is rejected. Every "
            "chart showing the column uses the format unless it sets its own."
        ),
    )
    incremental: IncrementalValue = Field(
        default=None,
        description=(
            "Column used as the monotonic watermark for incremental refresh: "
            "the executor fetches only rows after the prior watermark and "
            "merges them with the cached result. `incremental: false` opts "
            "this query out of a board's incremental setting. Inherits from "
            "the board-level incremental setting when omitted."
        ),
    )

    @field_validator("ignore")
    @classmethod
    def _validate_ignore_codes(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            validate_suppression_codes(v, source="query.ignore")
        return v

    @field_validator("incremental", mode="before")
    @classmethod
    def _validate_incremental(
        cls,
        v: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
    ) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
        return validate_incremental_value(v)


class AuthoredSqlQuery(_BaseQueryFields):
    """Raw SQL query: the default query type.

    Example YAML:
        queries:
          sales_by_date:
            sql: SELECT date, SUM(amount) FROM sales GROUP BY date
            source: my_postgres
    """

    type: Literal["sql"] = Field(
        default="sql",
        description="Inferred from the keys present.",
    )
    sql: str | None = Field(
        default=None,
        description="The statement to run, with Jinja2 over board variables, other queries, and the filter helpers.",
    )
    setup_sql: str | None = Field(
        default=None,
        description="Non-nestable SQL preamble executed before the main query (e.g., CREATE TEMP FUNCTION).",
    )
    target: str | None = Field(
        default=None,
        description="dbt target name for queries against a dbt_profile source (defaults to 'dev').",
    )
    dialect: QueryDialect | None = Field(
        default=None,
        description="Warehouse dialect the SQL is written in, translated to the source's.",
    )


class AuthoredHttpQuery(_BaseQueryFields):
    """REST API query.

    Example YAML:
        queries:
          api_data:
            type: http
            url: https://api.example.com/data
    """

    type: Literal["http"] = Field(
        default="http",
        description="Inferred from the keys present.",
    )
    url: str = Field(
        description="HTTP endpoint URL for REST API queries.",
    )
    method: RestMethod | None = Field(
        default=None,
        description="Verb the request is sent with (GET, POST, PUT, DELETE, PATCH).",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Header lines sent with the request, such as auth and content type.",
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description="Values appended to the URL after the '?'.",
    )
    body: dict[str, Any] | str | None = Field(
        default=None,
        description="Payload sent with the request, on POST, PUT, and PATCH.",
    )
    limit: int | None = Field(
        default=None,
        description="Maximum number of rows returned.",
    )
    json_path: str | None = Field(
        default=None,
        description="JSONPath expression to extract tabular data from the HTTP response.",
    )


class _AuthoredValuesQueryFields(_BaseQueryFields):
    """Fields shared by both inline-values authoring shapes."""

    type: Literal["values"] = Field(
        default="values",
        description="Inferred from the keys present.",
    )


class AuthoredValuesQuery(_AuthoredValuesQueryFields):
    """Inline data authored as a list of row mappings."""

    rows: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Inline data rows (list of row dicts). Use the compact "
                "columns-and-values form instead when row values are positional."
            )
        ),
    ]


class AuthoredCompactValuesQuery(_AuthoredValuesQueryFields):
    """Inline data authored as column names plus positional row values."""

    columns: Annotated[
        list[str],
        Field(
            description=(
                "Column names for the compact syntax. Every compact values query "
                "also requires values."
            )
        ),
    ]
    values: Annotated[
        list[list[Any]],
        Field(
            description=(
                "Inline row-oriented data (list of lists) for the compact syntax. "
                "Every compact values query also requires columns."
            )
        ),
    ]


class AuthoredSchemaQuery(_BaseQueryFields):
    """dbt source schema query."""

    # populate_by_name=True: required for schema_name's alias="schema" to accept
    # both the YAML key "schema:" and the Python attribute name "schema_name".
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Annotated[Literal["schema"], ExplicitTag()] = Field(
        default="schema",
        description="Never inferred; write `type: schema` explicitly.",
    )
    # alias="schema" so YAML authors write `schema: analytics`; Python code uses
    # schema_name to avoid shadowing BaseModel.schema (a Pydantic v2 legacy classmethod).
    schema_name: str | None = Field(
        default=None,
        alias="schema",
        description="Which schema to inspect; lists every schema in the source if omitted (YAML key: schema).",
    )
    table: str | None = Field(
        default=None,
        description="Which table to inspect; lists the schema's tables if omitted.",
    )
    column: str | None = Field(
        default=None,
        description="Which column to profile; profiles every column in the table if omitted.",
    )
    fields: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Project the result rows to exactly these keys, in this order: "
            "the schema-query counterpart of a SQL SELECT list. A projected "
            "key a row lacks yields null. Omit to return every key each row "
            "carries."
        ),
    )


def _discriminate_authored_query(v: Any) -> str | None:
    """Custom discriminator: return the type tag for the AuthoredQuery union.

    Returns None for unknown or missing type, which triggers Pydantic's
    union_tag_not_found ValidationError — explicit and loud. The tag is
    stamped by normalize_query_value before validation (inferred from key
    presence when the author omits it).
    """
    if isinstance(v, dict):
        t = v.get("type")
    elif isinstance(v, _BaseQueryFields):
        t = getattr(v, "type", None)
    else:
        return None
    if not isinstance(t, str):
        return None
    if t == "values":
        if isinstance(v, AuthoredValuesQuery):
            return "values_rows"
        if isinstance(v, AuthoredCompactValuesQuery):
            return "values_compact"
        return (
            "values_rows" if isinstance(v, dict) and "rows" in v else "values_compact"
        )
    return t


AuthoredQuery = Annotated[
    Annotated[AuthoredSqlQuery, Tag("sql")]
    | Annotated[AuthoredHttpQuery, Tag("http")]
    | Annotated[AuthoredValuesQuery, Tag("values_rows")]
    | Annotated[AuthoredCompactValuesQuery, Tag("values_compact")]
    | Annotated[AuthoredSchemaQuery, Tag("schema")],
    Discriminator(_discriminate_authored_query),
]
"""Discriminated union over the authored query types.

AuthoredQuery is a type alias, not a BaseModel. Use TypeAdapter(AuthoredQuery)
for validation and isinstance(obj, _BaseQueryFields) for instance checks.
The `type` tag is stamped by normalize_query_value before validation (inferred
from key presence when omitted), so authors rarely write it explicitly.
"""
