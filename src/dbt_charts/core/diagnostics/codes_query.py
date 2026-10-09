"""WARN-* warning codes for the query (SQL-lint) domain.

These codes are emitted by core/inspect/query_validator.py and reach
render output via core/diagnostics/from_query_diagnostic.py adapter.
They are declared here (not in the detector modules) so the registry is
complete on import of core.diagnostics, without importing back out to
core/inspect/.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.registry import REGISTRY, WarningCode

WARN_MISSING_JOIN_PREDICATE = REGISTRY.register(
    WarningCode(
        code="WARN-MISSING-JOIN-PREDICATE",
        domain="query",
        title="Join is missing a predicate and may produce a cross join",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Add an ON clause that relates the tables, or join a source that "
            "returns one row (an aggregate with no GROUP BY, or LIMIT 1)."
        ),
        doc=(
            "Fires when query validation detects a CROSS JOIN, a comma join, or a "
            "join on a constant (`ON TRUE`, `ON 1 = 1`) between two sources that "
            "can each return many rows. That multiplies every row of one by every "
            "row of the other; it almost always means a missing ON clause.\n\n"
            "A source that returns one row by construction never multiplies rows, "
            "so a join against it stays quiet: a subquery or CTE that aggregates "
            "with no `GROUP BY`, a `SELECT` with no `FROM`, or a `LIMIT 1`. So does "
            "a table function such as `generate_series`. A filter that merely "
            "happens to match one row does not count. Emitted by "
            "`validate_compiled_queries` via the query validator."
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
            "semantic validation (reaggregation, missing join predicates) cannot run "
            "on unparseable SQL."
        ),
        doc=(
            "Fires when a SQL query cannot be parsed as a structured AST. The "
            "query may still execute against the warehouse; this warning "
            "surfaces that semantic validation (reaggregation, missing join predicates) cannot "
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
        title="Grouping-dependent aggregate applied on top of an aggregated input",
        message_template=("Query {query_name!r}: {message}"),
        fix_template=(
            "Compute the aggregate once over the underlying rows, or carry the "
            "SUM and COUNT up and divide them in the outer query."
        ),
        doc=(
            "Fires when an outer query applies an aggregate whose result depends "
            "on how rows were grouped to a column that a CTE or subquery already "
            "aggregated: `AVG`, `MEDIAN`, or a percentile over any aggregate, and "
            "`SUM` over an `AVG`, `MEDIAN`, or percentile, and any `DISTINCT` "
            "aggregate, which collapses equal pre-aggregated values. An average of "
            "averages weights every group equally, so it differs from the "
            "average of the underlying rows. The message names both functions, "
            "for example `AVG over AVG`. Emitted by `validate_compiled_queries` "
            "via the query validator.\n\n"
            "Pairs that compose exactly stay quiet: `SUM` or `COUNT` over `SUM` "
            "or `COUNT`, `MAX` over `MAX`, and `MIN` over `MIN`. So do window "
            "functions (`LAG`, a running `SUM(...) OVER`), which run per row, "
            "and aggregates with a `FILTER (WHERE ...)` clause, which pick a "
            "subset of rows."
        ),
        summary=(
            "Fires when an AVG, MEDIAN, percentile, or DISTINCT aggregate is applied "
            "to an already-aggregated column, or a SUM to an already-averaged one."
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
