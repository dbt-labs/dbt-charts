"""describe_query verb — return column schema for a SQL string without fetching rows."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters import AdapterRegistry
from dbt_charts.core.execute.warehouse_check import WarehouseCheck, check_ad_hoc_query
from dbt_charts.core.inspect.query_validator import QueryDiagnostic, validate_query


class DescribeQueryArgs(BaseModel):
    """Return the column schema (names + types) for a SQL string without executing it for data.

    Gates on validate_query first — short-circuits on parse errors and missing join
    predicates with actionable diagnostics; otherwise returns columns alongside any
    non-error diagnostics (e.g. WARN-FANOUT-RISK warnings) so the agent can read them
    without being blocked from the column shape.

    Uses a no-execution check where one exists: DuckDB's own DESCRIBE, or a
    BigQuery dry run. A csv/json/parquet file source also answers via DESCRIBE,
    but that is not free — it must materialize its files onto DuckDB first,
    reading and parsing them, though still far cheaper than running the query
    for a full result set. A source with none of these (Postgres, Snowflake,
    Redshift, ...) returns success=False explaining that it cannot list
    columns without running the query, rather than running it for you.
    """

    sql: str = Field(..., description="SQL query to describe.")
    source: str | None = Field(
        None, description="Data source name to describe against."
    )
    dialect: str | None = Field(
        None, description="SQL dialect hint for the validator (duckdb, bigquery, etc.)."
    )


class DescribeQueryColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    type: str


class DescribeQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    columns: list[DescribeQueryColumn] | None = None
    diagnostics: list[QueryDiagnostic] = Field(
        default_factory=list,
        description="Dialect-specific diagnostic messages from the query runner.",
    )
    error: str | None = None


def describe_query(
    sql: str,
    *,
    source: str | None = None,
    dialect: str | None = None,
    adapter_registry: AdapterRegistry,
) -> DescribeQueryResult:
    """Return the column schema for a SQL string, never at full-query price.

    Runs validate_query first; short-circuits on error-severity diagnostics
    (WARN-PARSE-ERROR, WARN-MISSING-JOIN-PREDICATE) without calling the
    warehouse. Non-error diagnostics are surfaced on the result alongside the
    column schema.

    Column lookup is dispatch, never execution: a DuckDB source uses its own
    read-only DESCRIBE, and every other adapter goes through
    ``core.execute.warehouse_check.check_ad_hoc_query`` — a BigQuery dry run;
    a csv/json/parquet file source, which also answers via DESCRIBE but must
    materialize its files onto DuckDB first; an EXPLAIN on
    Postgres/Redshift/Snowflake that proves validity but returns no schema (so
    the refusal below still fires, after a warehouse round-trip that
    validates the SQL); or an explicit refusal for an adapter with no
    mechanism at all. That refusal is the answer for such an adapter, never a
    silent fall-through to running the query in full.
    """
    try:
        diags = validate_query(sql, dialect=dialect)

        error_diags = [d for d in diags if d.severity == "error"]
        if error_diags:
            return DescribeQueryResult(
                success=False,
                columns=None,
                diagnostics=diags,
                error=error_diags[0].message,
            )

        cfg = adapter_registry.resolve_source_config(source)

        if cfg.get("type") == "duckdb":
            # Run DESCRIBE via the registry's existing read-only DuckDBAdapter so
            # we don't open a writable dbt-duckdb connection that would conflict
            # with dct serve.
            cols = _duckdb_describe(sql, source, adapter_registry)
        else:
            check = check_ad_hoc_query(
                sql, source=source, adapter_registry=adapter_registry
            )
            if check.status != "valid" or not check.columns_checked:
                return DescribeQueryResult(
                    success=False,
                    columns=None,
                    diagnostics=diags,
                    error=_describe_refusal(check),
                )
            cols = [
                DescribeQueryColumn(name=c.name, type=c.type) for c in check.columns
            ]

        return DescribeQueryResult(
            success=True,
            columns=cols,
            diagnostics=diags,
        )

    except Exception as e:  # noqa: BLE001 — adapter boundary, mirrors execute_query
        return DescribeQueryResult(
            success=False,
            error=str(e),
        )


def _describe_refusal(check: WarehouseCheck) -> str:
    """Build describe_query's caller-facing message for a non-column outcome.

    ``check.reason``/``check.error`` are worded for ``dct validate
    --warehouse``'s audience (a board author auditing many queries at once —
    "unchecked" is a fine verdict there). describe_query's caller asked for
    columns and got none, so the message has to say what happened and what to
    do about it, not just classify the outcome.
    """
    if check.status == "invalid":
        # The warehouse read this SQL and rejected it — its own message names
        # the defect directly; nothing to add.
        return check.error
    detail = check.reason or check.error
    return (
        f"Cannot list columns without running the query: {detail}. Run it "
        "by executing it in full if you accept that cost."
    )


def _duckdb_describe(
    sql: str, source: str | None, adapter_registry: AdapterRegistry
) -> list[DescribeQueryColumn]:
    """Run DESCRIBE ({sql}) via the registry's read-only DuckDBAdapter."""
    result = adapter_registry.execute(SqlQuery(sql=f"DESCRIBE ({sql})", source=source))
    if result.error:
        raise RuntimeError(str(result.error))
    return [
        DescribeQueryColumn(name=row["column_name"], type=row["column_type"])
        for row in result.data
    ]
