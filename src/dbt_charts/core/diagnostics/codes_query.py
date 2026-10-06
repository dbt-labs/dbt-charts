"""WARN-* warning codes for the query (SQL-lint) domain.

These codes are emitted by core/inspect/query_validator.py and reach
render output via core/diagnostics/from_query_diagnostic.py adapter.
They are declared here (not in the detector modules) so the registry is
complete on import of core.diagnostics, without importing back out to
core/inspect/.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.registry import REGISTRY, WarningCode

WARN_FANOUT_RISK = REGISTRY.register(
    WarningCode(
        code="WARN-FANOUT-RISK",
        domain="query",
        title="Join may multiply rows beyond chart aggregation",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Add a GROUP BY or aggregation in the query to collapse the "
            "duplicate rows before they reach the chart."
        ),
        doc=(
            "Fires when query validation detects a join that may multiply rows "
            "beyond what the chart's aggregation can recover. When a cached "
            "relationship context (super-schema profiles) is available, severity "
            "is calibrated against known multiplicities. Emitted by "
            "`validate_compiled_queries` for every named query."
        ),
        docs_topic="queries",
    )
)

WARN_MISSING_JOIN_PREDICATE = REGISTRY.register(
    WarningCode(
        code="WARN-MISSING-JOIN-PREDICATE",
        domain="query",
        title="Join is missing a predicate and may produce a cross join",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=("Add an ON clause to the join to specify how the tables relate."),
        doc=(
            "Fires when query validation detects an implicit cross join or an "
            "explicit CROSS JOIN without a predicate. These almost always indicate "
            "a missing ON clause and produce wildly fanned-out result sets. "
            "Emitted by `validate_compiled_queries` via the query validator."
        ),
        docs_topic="queries",
    )
)

WARN_PARSE_ERROR = REGISTRY.register(
    WarningCode(
        code="WARN-PARSE-ERROR",
        domain="query",
        title="SQL query could not be parsed for semantic validation",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Check the SQL syntax. The query may still execute, but "
            "semantic validation (fanout detection, reaggregation) cannot run "
            "on unparseable SQL."
        ),
        doc=(
            "Fires when a SQL query cannot be parsed as a structured AST. The "
            "query may still execute against the warehouse; this warning "
            "surfaces that semantic validation (fanout, reaggregation) cannot "
            "run on unparseable SQL. Emitted by `compile()` for authored "
            "queries whose dialect resolves and whose failure carries a "
            "specific position."
        ),
        docs_topic="queries",
    )
)

WARN_REAGGREGATION = REGISTRY.register(
    WarningCode(
        code="WARN-REAGGREGATION",
        domain="query",
        title="Aggregation applied on top of an already-aggregated input",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Refactor the query to aggregate only once at the correct level, "
            "or verify the double-aggregation is intentional."
        ),
        doc=(
            "Fires when query validation detects aggregation applied on top of an "
            "already-aggregated input (e.g. SUM(SUM(...)) patterns or aggregation "
            "over a query result that itself aggregates). The result is usually not "
            "what the author intended. Emitted by `validate_compiled_queries` via "
            "the query validator.\n\n"
            "Looker's symmetric aggregate is exempt. Migrated Looker queries read a "
            "measure across a fan-out join by packing it with a hash of the dedup "
            "key, `SUM(DISTINCT ...)`-ing so duplicate keys collapse, then "
            "subtracting a second `SUM(DISTINCT hash-only)`: an identity over a "
            "per-key value, not a second aggregation. Both halves of that "
            "subtraction must be present for the exemption to apply, so a plain "
            "`SUM(DISTINCT already_summed_column)` still warns."
        ),
        summary=(
            "Fires when query validation detects aggregation applied on top "
            "of an already-aggregated input."
        ),
        docs_topic="queries",
    )
)

WARN_DATE_AS_TEXT = REGISTRY.register(
    WarningCode(
        code="WARN-DATE-AS-TEXT",
        domain="query",
        title="A charted x column is a date the query turned into text",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Return the date itself (with date_trunc for a grain) instead of "
            "formatting it; the axis labels dates by grain. For a month-of-year "
            "or day-of-week category, return the month or weekday name."
        ),
        doc=(
            "Fires when a column some chart uses as its x is a date or timestamp "
            "wrapped in a formatting call (`strftime`, `to_char`, `to_varchar`, "
            "`format_date`, `format_timestamp`, `format_datetime`, "
            "`date_format`, SQL Server `FORMAT`) or cast to a text type. Text "
            "has no time scale, label thinning, or gaps for missing periods, so "
            "the chart draws it as categories.\n\n"
            "The warning needs evidence that the input is temporal. Functions "
            "that take only dates (`strftime`, `format_date`, `format_timestamp`, "
            "`format_datetime`, `date_format`) count on the format alone. "
            "Functions that also format numbers (`to_char`, `to_varchar`, SQL "
            "Server `FORMAT`) and casts to text count only when the value is "
            "evidently a date: a `date_trunc`/date function, a date literal, a "
            "cast to a date type, or a column named like a date (`created_at`, "
            "`order_date`, `ts`).\n\n"
            "A month or weekday name alone stays quiet (`%b`, `%A`, `Mon`, "
            "`FMDay`, `DATENAME(month, d)`, `monthname(d)`): that is a deliberate "
            "seasonality category. Any other format warns, including a month or "
            "hour number alone.\n\n"
            "When the format or the `date_trunc` unit is not a literal, the "
            "warning still fires but leaves the format and grain out of the "
            "message.\n\n"
            "The detector reads the query's own SELECT list, for columns charted as "
            "x by a chart or one of its layers. A date made text in a CTE, view, "
            "or dbt model is not seen."
        ),
        summary=("Fires when a charted x column is a date the query turned into text."),
        docs_topic="queries",
    )
)
