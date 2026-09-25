"""ERR-* error codes and WARN-* warning codes for the compile domain."""

from __future__ import annotations

from dbt_charts.core.diagnostics.hints import (
    suggest_close_extends,
    suggest_close_format,
    suggest_close_palette,
    suggest_close_source,
    suggest_close_theme,
)
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode, WarningCode

ERR_UNKNOWN_VARIABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-UNKNOWN-VARIABLE",
        domain="compile",
        title="Unknown variable referenced in template",
        message_template=(
            "{surface} {owner_name!r} references unknown variable {var_name!r}. "
            "Declare it under `variables:` or fix the typo."
        ),
        doc=(
            "Fired when a Jinja template references a variable name that is not "
            "declared under `variables:`. Check for typos and ensure the variable "
            "is declared before the template that uses it."
        ),
        docs_topic="variables",
    )
)

ERR_UNKNOWN_QUERY = REGISTRY.register(
    ErrorCode(
        code="ERR-UNKNOWN-QUERY",
        domain="compile",
        title="Chart references an unknown query",
        message_template=(
            "Chart {chart_name!r} references unknown query {query_name!r}. "
            "Declare the query under `queries:` or fix the typo in `query:`."
        ),
        doc=(
            "Fired when a chart's `query:` names a query that is not declared "
            "under `queries:` in the board or any included meta.yml. Check for "
            "typos and ensure the query is declared."
        ),
        summary="Fired when a chart's `query:` names a query that isn't declared under `queries:`.",
        docs_topic="queries",
    )
)

ERR_EXTRA_FIELD = REGISTRY.register(
    ErrorCode(
        code="ERR-EXTRA-FIELD",
        domain="compile",
        title="Unknown field in board YAML",
        message_template=(
            "Unknown field {field_path!r} in board YAML. "
            "Remove it or check the schema for supported keys."
        ),
        doc=(
            "Fired when the board YAML contains a field name that the schema does "
            "not recognize. Remove the unknown field or refer to the YAML reference "
            "for the supported keys."
        ),
        docs_topic="board",
    )
)

ERR_SQL_LITERAL_NEWLINES = REGISTRY.register(
    ErrorCode(
        code="ERR-SQL-LITERAL-NEWLINES",
        domain="compile",
        title="SQL contains literal backslash-n from single-quoted YAML",
        message_template=(
            "Query {query_name!r} {field_label} contains literal \\n (backslash + n). "
            "Use YAML block scalar `{field_label}: |` to write multiline SQL:\n\n"
            "  queries:\n"
            "    my_query:\n"
            "      {field_label}: |\n"
            "        SELECT\n"
            "          col\n"
            "        FROM t"
        ),
        doc=(
            "Fired when a query's SQL or pre-query string contains a literal "
            "backslash followed by 'n', which typically means YAML single-quote "
            "escaping swallowed an intended newline. Use a YAML block scalar "
            "(`field: |`) for multiline SQL to avoid this."
        ),
        docs_topic="queries",
    )
)

ERR_SQL_DATE_LITERAL_VARIABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-SQL-DATE-LITERAL-VARIABLE",
        domain="compile",
        title="Variable quoted as a date/time/timestamp literal will not compile",
        message_template=(
            "Query {query_name!r} {field_label} casts a variable with "
            "{keyword} '{{{{ {variable} }}}}'. This source binds the variable "
            "as a query parameter, so its quotes are stripped and this "
            "compiles to {keyword} $N, which the warehouse does not parse. "
            "Use cast('{{{{ {variable} }}}}' as {keyword}) instead."
        ),
        doc=(
            "Fired when a query casts a Jinja variable to a date/time/timestamp "
            "using the SQL literal syntax (`date '{{{{ var }}}}'`) on a source "
            "whose adapter binds variables as real query parameters (duckdb, "
            "sqlite). The parameterizer strips the quotes around every "
            "placeholder, so `date '{{{{ var }}}}'` compiles to `date $1` -- "
            "syntax the warehouse does not accept. Other sources (postgres, "
            "snowflake, bigquery, dbt_profile, etc.) render variables as "
            "inline literal text instead, so the same SQL is valid there and "
            "this check does not fire. Use `cast('{{{{ var }}}}' as date)` (or "
            "`time`/`timestamp`) instead, which keeps the variable an ordinary "
            "string parameter and works on every source."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_REQUIRED = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-REQUIRED",
        domain="compile",
        title="Query has no source configured",
        message_template=(
            "Query {query_name!r}: SQL queries must have a source. "
            "Set it on the query (`source: my_db`), "
            "or at the board or folder meta.yml level (`source: my_db`)."
        ),
        doc=(
            "Fired when a SQL query has no `source:` set at the query, board, or "
            "folder meta.yml level and no default source is configured. Set "
            "`source: my_db` on the query or as a default at a higher level."
        ),
        summary="Fired when a SQL query has no source configured at any level.",
        docs_topic="queries",
    )
)

ERR_SOURCE_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-NOT-FOUND",
        domain="compile",
        title="Query references an unknown source",
        message_template=(
            "Query {query_name!r} references unknown source {source!r}. "
            "Available sources: {available}. "
            "dct sources are declared under `sources:` in your dbt_charts.yml; "
            "the source name is not the dbt project name."
        ),
        doc=(
            "Fired when a query's `source:` names a source that is not declared "
            "in the sources registry. Check for typos and ensure the source is "
            "declared under `sources:` in your dbt_charts.yml. Also includes the "
            "execute-side failure of the same name (source lookup at query time)."
        ),
        docs_topic="queries",
        hint_generator=suggest_close_source,
    )
)

ERR_SOURCE_INLINE_FORBIDDEN = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-INLINE-FORBIDDEN",
        domain="compile",
        title="Inline source definition is not allowed",
        message_template=(
            "Query {query_name!r}: inline source definitions are not allowed. "
            "Reference a source by name (`source: my_db`) instead. Got: {offending_value}"
        ),
        fix_template=(
            "Use a named source declared under `sources:` in your dbt_charts.yml "
            "instead of inline connection parameters."
        ),
        doc=(
            "Fired when a query's `source:` is set to an inline dictionary instead "
            "of a named source reference. Inline source definitions are forbidden "
            "for security reasons: connection parameters in the committed YAML would "
            "leak credentials. Use a named source declared under `sources:`."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_AMBIGUOUS = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-AMBIGUOUS",
        domain="compile",
        title="Inline file source path exists at both candidate locations",
        message_template=(
            "Query {query_name!r}: inline file source {ref!r} exists at both "
            "{candidates}. Rename or remove one so only a single candidate exists."
        ),
        doc=(
            "Fired when a query's inline `source: <path>` ref resolves to a real "
            "file at both of its two candidate locations, the board's own "
            "directory and the project root (a bare path is tried against "
            "both anchors so it works from any board depth). dbt charts never "
            "silently prefers one anchor; move or rename one of the two files so "
            "only a single candidate remains."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_CREDENTIAL_LITERAL = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-CREDENTIAL-LITERAL",
        domain="compile",
        title="Source contains a raw credential literal",
        message_template=(
            "Source {source_name!r}: field {field!r} holds a raw credential literal. "
            "dbt_charts.yml is committed to git, so inline secrets leak on push. "
            "Reference the secret instead (e.g. {field}: {{{{ env_var('SECRET_NAME') }}}}), "
            "or use a `type: dbt_profile` source that delegates to an out-of-repo profiles.yml."
        ),
        doc=(
            "Fired when a `sources:` entry in the committed dbt_charts.yml contains a "
            "raw secret literal (password, API key, etc.). Since dbt_charts.yml is "
            "committed to git, inline secrets would be leaked on push. Reference the "
            "secret via `env_var()` or use a `dbt_profile` source type that delegates "
            "to an out-of-repo profiles.yml."
        ),
        summary="Fired when a `sources:` entry in the committed dbt_charts.yml contains a raw secret literal.",
        docs_topic="queries",
    )
)

ERR_SOURCE_CONFIG_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-CONFIG-INVALID",
        domain="compile",
        title="Source configuration fails validation",
        message_template="Source {source_name!r} in {filename}: {detail}",
        doc=(
            "Fired when a `sources:` entry in dbt_charts.yml fails typed SourceConfig "
            "validation. Check the source definition for missing required fields or "
            "invalid values, and refer to the sources reference for the expected schema."
        ),
        summary="Fired when a `sources:` entry fails typed SourceConfig validation.",
        docs_topic="queries",
    )
)

ERR_META_SCHEMA = REGISTRY.register(
    ErrorCode(
        code="ERR-META-SCHEMA",
        domain="compile",
        title="meta.yml contains an unknown or invalid field",
        message_template=(
            "meta.yml schema error: {message}. "
            "Check that all keys are valid board fields."
        ),
        doc=(
            "Fired when a meta.yml file contains a field that is not recognized "
            "by the board schema, or a field with an invalid value. Check that all "
            "keys match the supported board fields and remove any extras."
        ),
        summary="Fired when a meta.yml file contains a field the board schema doesn't recognize.",
        docs_topic="board",
    )
)

ERR_WRONG_SHAPE = REGISTRY.register(
    ErrorCode(
        code="ERR-WRONG-SHAPE",
        domain="compile",
        title="Field expects a mapping but got a scalar",
        message_template=(
            "Field {field_path!r} expects a mapping, not a scalar. "
            "Provide a YAML block with keys: {available_keys}."
        ),
        doc=(
            "Fired when a board YAML field that expects a mapping (nested object) "
            "receives a plain scalar value instead. Provide a YAML block with the "
            "appropriate keys."
        ),
        docs_topic="board",
    )
)

ERR_UNRESOLVED_REFERENCE = REGISTRY.register(
    ErrorCode(
        code="ERR-UNRESOLVED-REFERENCE",
        domain="compile",
        title="Reference points to an unknown name",
        message_template="Reference {ref!r} not found{context}.",
        doc=(
            "Fired when a chart or layout reference names a target that cannot be "
            "found in the current board or any included files. Check for typos and "
            "ensure the referenced chart, query, or layout item is declared."
        ),
        docs_topic="queries",
    )
)

ERR_JINJA_ERROR = REGISTRY.register(
    ErrorCode(
        code="ERR-JINJA-ERROR",
        domain="compile",
        title="Jinja template in a query failed to render",
        message_template="Jinja template error: {message}",
        doc=(
            "Fired when a Jinja template in a query or pre-query raises a rendering "
            "error. Check the template syntax and ensure all referenced values and "
            "filters are available."
        ),
        docs_topic="queries",
    )
)

ERR_TEMPLATE_OUTPUT_TOO_LARGE = REGISTRY.register(
    ErrorCode(
        code="ERR-TEMPLATE-OUTPUT-TOO-LARGE",
        domain="compile",
        title="Board render's cumulative template output exceeded the cap",
        message_template=(
            "This board render's templated fields (queries, titles, markdown, "
            "…) emitted more than {ceiling} bytes combined, stopping after "
            "{emitted_bytes} bytes. This is a render-wide cumulative cap, not "
            "a per-field one — a nested loop, a runaway variable expansion, "
            "or genuinely oversized content anywhere in the board can trip "
            "it. Reduce the amount of text a templated field (or their "
            "combination) produces."
        ),
        summary=(
            "Fired when one board render's Jinja-emitted output, summed "
            "across every templated field, exceeds the effective cap."
        ),
        doc=(
            "Fired when the cumulative bytes emitted by every templated "
            "field (queries, titles, markdown, chart labels) in one board "
            "render exceed the effective `execution.max_template_output_bytes` "
            "limit — a hard error, not a truncation-with-warning like "
            "`WARN-QUERY-RESULT-TRUNCATED`: a truncated SVG or SQL string is "
            "a corrupt document, never a usable-with-a-caveat result. The "
            "message names no config key deliberately: the effective limit "
            "is the lower of the project's own config value and any "
            "deployment ceiling (`DCT_MAX_TEMPLATE_OUTPUT_BYTES_CEILING`), "
            "so it cannot be raised past the ceiling."
        ),
        docs_topic="queries",
    )
)

ERR_VALIDATION_FIELD = REGISTRY.register(
    ErrorCode(
        code="ERR-VALIDATION-FIELD",
        domain="compile",
        title="Board YAML field failed Pydantic validation",
        message_template="Field {field_path!r}: {pydantic_msg}",
        doc=(
            "Fired when a board YAML field fails Pydantic's type or constraint "
            "validation, or a normalize-stage rule on one field that Pydantic "
            "cannot express (a cross-field exclusion, a root-only constraint). "
            "The message carries the specific error. Check the field value "
            "against the schema."
        ),
        docs_topic="board",
    )
)

ERR_BAR_Y_NOT_NUMERIC = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-Y-NOT-NUMERIC",
        domain="compile",
        title="Bar chart y column is not numeric",
        message_template=(
            "Chart {chart_id!r} (bar): y column {y_field!r} is not numeric. "
            "Bar charts always plot x as the category and y as the measure, "
            "regardless of orientation; use a numeric column for y."
        ),
        doc=(
            "Fired when a bar chart's `y:` column contains non-numeric data. Bar "
            "charts always use y as the measure axis regardless of orientation; "
            "use a numeric column for y."
        ),
        docs_topic="charts",
    )
)

ERR_BAR_Y_START_NULL = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-Y-START-NULL",
        domain="compile",
        title="Bar chart y_start column has an empty cell",
        message_template=(
            "Chart {chart_id!r} (bar): y_start column {y_start_field!r} is empty "
            "in row {row}. Every row needs a start; write 0 for a bar that "
            "starts at zero."
        ),
        doc=(
            "Fired when a bar chart's `y_start:` column holds a NULL. A missing "
            "start is never read as zero: have the query write an explicit 0 for "
            "a bar that starts at zero."
        ),
        docs_topic="charts",
    )
)

ERR_BAR_Y_START_KIND = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-Y-START-KIND",
        domain="compile",
        title="Bar chart y and y_start are different kinds",
        message_template=(
            "Chart {chart_id!r} (bar): column {y_field!r} is {y_kind} but "
            "column {y_start_field!r} is {y_start_kind}. A bar's start and end, "
            "and every bar on the chart, sit on one value axis: make them all "
            "numeric, or all dates."
        ),
        doc=(
            "Fired when a bar's `y:` and `y_start:` columns (or a bar layer's, "
            "against the chart's own) are not the same kind. Both must be "
            "numeric, or both dates."
        ),
        docs_topic="charts",
    )
)

ERR_LINE_Y_NOT_NUMERIC = REGISTRY.register(
    ErrorCode(
        code="ERR-LINE-Y-NOT-NUMERIC",
        domain="compile",
        title="Line chart y column is not numeric",
        message_template=(
            "Chart {chart_id!r} (line): y column {y_field!r} is not numeric. "
            "Line charts always plot x as the dimension and y as the value; "
            "use a numeric column for y."
        ),
        doc=(
            "Fired when a line chart's `y:` column contains non-numeric data. Line "
            "charts always use y as the value axis; use a numeric column for y."
        ),
        docs_topic="charts",
    )
)

ERR_SCATTER_MULTI_Y_NOT_NUMERIC = REGISTRY.register(
    ErrorCode(
        code="ERR-SCATTER-MULTI-Y-NOT-NUMERIC",
        domain="compile",
        title="Scatter chart multi-metric y column is not numeric",
        message_template=(
            "Chart {chart_id!r} (scatter): y: [...] column {y_field!r} is not "
            "numeric. A single y: column may be categorical (a dot plot), but "
            "every measure in a y: [...] list is folded onto one numeric axis; "
            "use numeric columns."
        ),
        doc=(
            "Fired when a scatter chart's y: [a, b] list contains a non-numeric "
            "column. A single y: column may be categorical (a dot plot), but a "
            "list y: folds every measure onto one shared numeric axis, so each "
            "measure must be numeric."
        ),
        docs_topic="charts",
    )
)

ERR_AREA_ENCODING_SWAPPED = REGISTRY.register(
    ErrorCode(
        code="ERR-AREA-ENCODING-SWAPPED",
        domain="compile",
        title="Area chart x/y encoding looks swapped",
        message_template=(
            "Chart {chart_id!r} (area): {reason} "
            "Area charts always plot x as the dimension and y as the value; "
            "there is no orientation knob to rotate an area chart, so a "
            "swapped x/y silently bakes a broken axis."
        ),
        doc=(
            "Fired when an area chart's encoding looks incorrect: either y is "
            "non-numeric (should be the measure), or x is a numeric measure with "
            "stack enabled. Area charts always plot x as the dimension and y as "
            "the value; there is no orientation knob."
        ),
        docs_topic="charts",
    )
)

ERR_LAYERS_AMBIGUOUS_Y_DOMAIN = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYERS-AMBIGUOUS-Y-DOMAIN",
        domain="compile",
        title="Chart-level y domain is ambiguous with independent layer scales",
        message_template=(
            "Chart {chart_id!r} sets axis_y.scale.domain={domain!r} but the "
            "layers use independent y scales (left and right sides differ). "
            "A chart-level domain is ambiguous when each side has its own scale; "
            "set axis_y.scale.domain on the individual layer instead."
        ),
        doc=(
            "Fired when a chart sets `axis_y.scale.domain` at the chart level but "
            "the layers use independent (split) y scales. A chart-level domain is "
            "ambiguous when left and right sides have different scales. Set "
            "`axis_y.scale.domain` on the individual layer instead."
        ),
        summary="Fired when a chart sets a shared y-axis domain but its layers use independent y scales.",
        docs_topic="charts",
    )
)

ERR_SUPPORT_TABLE_POSITION_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-SUPPORT-TABLE-POSITION-INVALID",
        domain="compile",
        title="style.support_table.position is invalid for the chart's orientation",
        message_template="{message}",
        summary=(
            "Fired when `style.support_table.position` names a side the "
            "chart's category-axis orientation cannot place."
        ),
        doc=(
            "Fired when `style.support_table.position` names a side the chart's "
            "own category-axis orientation can't place. A horizontal category "
            "axis (vertical bar, line, area) only accepts `top`/`bottom`; a "
            "vertical one (a horizontal bar) only accepts `left`/`right`. The "
            "message carries the specific value and orientation."
        ),
        docs_topic="charts",
    )
)

ERR_BAR_LOG_SCALE_NOT_SUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-LOG-SCALE-NOT-SUPPORTED",
        domain="compile",
        title="Log scale is not supported on bar charts",
        message_template=(
            "Chart {chart_id!r} (bar): axis_y.scale.continuous.type: log is not "
            "supported: a bar's length encodes magnitude from zero, which a "
            "log scale makes meaningless. Use axis_y.scale.continuous.type: symlog "
            "on the bar chart, or switch to a line or area chart for a log scale."
        ),
        doc=(
            "Fired when a bar chart's y axis is set to `axis_y.scale.continuous.type: log`. "
            "A bar's length encodes magnitude from zero, which a log scale makes meaningless. "
            "Use `axis_y.scale.continuous.type: symlog` on the bar chart, or switch to a "
            "line or area chart for a log scale."
        ),
        summary="Fired when a bar chart's y-axis uses a log scale, which magnitude-from-zero bars can't represent.",
        docs_topic="charts",
    )
)

ERR_TICKS_COUNT_REQUIRES_NON_LOG_SCALE = REGISTRY.register(
    ErrorCode(
        code="ERR-TICKS-COUNT-REQUIRES-NON-LOG-SCALE",
        domain="compile",
        title="ticks.count is not supported with log scale",
        message_template=(
            "Chart {chart_id!r}: axis_y.ticks.count is not supported with "
            "scale.type: log: a target tick count on a log axis is "
            "nonsense; Vega-Lite computes log-decade ticks natively. Remove "
            "ticks.count."
        ),
        doc=(
            "Fired when `axis_y.ticks.count` is combined with "
            "`axis_y.scale.type: log`. A target tick count on a log axis is "
            "meaningless because Vega-Lite computes log-decade ticks natively. "
            "Remove `ticks.count`."
        ),
        summary="Fired when a target tick count is combined with a log y-axis scale.",
        docs_topic="charts",
    )
)

ERR_LOG_SCALE_REQUIRES_POSITIVE_DATA = REGISTRY.register(
    ErrorCode(
        code="ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA",
        domain="compile",
        title="Log scale requires strictly positive data",
        message_template=(
            "Chart {chart_id!r}: column {y_field!r} has a value <= 0, but "
            "axis_y.scale.type: log requires strictly positive data; a log "
            "domain is undefined at and below zero. Filter out the "
            "non-positive rows, or drop the log scale."
        ),
        doc=(
            "Fired when `axis_y.scale.type: log` is used but the y column "
            "contains a value ≤ 0. A log domain is undefined at and below zero. "
            "Filter out the non-positive rows, or drop the log scale."
        ),
        summary="Fired when a log y-axis scale is used but the data contains a value at or below zero.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_FIELD_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-FIELD-NOT-FOUND",
        domain="compile",
        title="multiples partition field not found in the query result",
        message_template=(
            "multiples field(s) {fields} not found in the query result. "
            "Available columns: {available}."
        ),
        doc=(
            "Fired when `multiples.rows` or `multiples.columns` names a "
            "column that is not present in the chart's query result. Check "
            "for a typo, or add the column to the query."
        ),
        summary="Fired when a multiples partition field is not a column of the query result.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_VALUE_COLLISION = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-VALUE-COLLISION",
        domain="compile",
        title="two distinct multiples values stringify to the same panel key",
        message_template=(
            "multiples field {field!r}: values {previous!r} and {value!r} "
            "both stringify to {canonical!r}; rename one to disambiguate."
        ),
        doc=(
            "Fired when `multiples.rows` or `multiples.columns` names a "
            "column whose distinct values collide once canonicalized to a "
            "string panel key (e.g. the string `'1'` and the integer `1`). "
            "Merging them into one panel would silently combine two "
            "genuinely distinct groups. Rename one value so the two panel "
            "keys stay distinct."
        ),
        summary="Fired when two distinct multiples partition values canonicalize to the same panel key.",
        docs_topic="charts",
    )
)

ERR_MULTIPLES_SELF_CROSSED = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTIPLES-SELF-CROSSED",
        domain="compile",
        title="multiples.rows and multiples.columns name the same field",
        message_template=(
            "multiples.rows and multiples.columns both name {field!r}. Only the "
            "diagonal of the resulting grid can ever hold a row; the rest are "
            "structurally empty, whatever the data says."
        ),
        doc=(
            "Fired when `multiples.rows` and `multiples.columns` name the same "
            "column. Crossing a field with itself builds a panel grid where a "
            "row of data can only ever land on the diagonal (the row's value "
            "matches itself); every other cell in the grid is guaranteed "
            "empty. Name a different column for `rows` or `columns`, or drop "
            "one of them and keep a single-direction partition."
        ),
        summary="Fired when multiples.rows and multiples.columns name the same field.",
        docs_topic="charts",
    )
)

ERR_AREA_STACKED_LOG_SCALE_NOT_SUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED",
        domain="compile",
        title="Stacked area and log scale are incompatible",
        message_template=(
            "Chart {chart_id!r} (area): `style.stack: {stack}` with "
            "axis_y.scale.type: log is not supported: a stacked band's top "
            "encodes a cumulative sum, which a log scale can't represent "
            "(the baked domain would be computed from unstacked values and "
            "clip the real stacked extent). Use `stack: none`, or drop the "
            "log scale."
        ),
        doc=(
            "Fired when a stacked area chart (`style.stack: zero/normalize/center`) "
            "is combined with `axis_y.scale.type: log`. A cumulative stack top is "
            "meaningless on a log scale. Use `stack: none`, or drop the log scale."
        ),
        summary="Fired when a stacked area chart is combined with a log y-axis scale.",
        docs_topic="charts",
    )
)

ERR_AREA_LOG_SCALE_INDEPENDENT_MULTIPLES = REGISTRY.register(
    ErrorCode(
        code="ERR-AREA-LOG-SCALE-INDEPENDENT-MULTIPLES",
        domain="compile",
        title="Log-scale area and independent-scale multiples are incompatible",
        message_template=(
            "Chart {chart_id!r} (area): axis_y.scale.type: log with "
            "multiples scale: independent is not supported: the explicit "
            "domain area bakes on a log scale (to avoid Vega-Lite's "
            "degenerate log-area rendering) is computed once from every "
            "panel's data combined, so it would apply the same domain to "
            "every panel regardless of scale: independent. Use "
            "multiples scale: shared, or drop the log scale."
        ),
        doc=(
            "Fired when an area chart authors both `axis_y.scale.type: log` "
            "and `multiples: {scale: independent}`. Area on a log scale "
            "needs an explicit baked domain to avoid Vega-Lite's degenerate "
            "rendering (see ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED's "
            "sibling note), but that domain is necessarily one shared "
            "value; baking it would contradict `scale: independent`'s "
            "promise of a per-panel domain, and suppressing it would "
            "silently reintroduce the degenerate rendering. Use `scale: "
            "shared`, or drop the log scale."
        ),
        summary=(
            "Fired when a log-scale area chart also authors "
            "multiples: {scale: independent}."
        ),
        docs_topic="charts",
    )
)

ERR_AREA_STACKED_MARK_STYLE_CLEARED = REGISTRY.register(
    ErrorCode(
        code="ERR-AREA-STACKED-MARK-STYLE-CLEARED",
        domain="compile",
        title="marks.area.stacked was cleared to null",
        message_template=(
            "Chart {chart_id!r} (area): `marks.area.stacked` resolved to "
            "null. Stacked and single-series area charts both need this "
            "recipe -- a single-series area takes it in full even when the "
            "chart itself isn't stacked -- but a board or chart in this "
            "chart's style cascade authored `marks.area.stacked: null` "
            "explicitly, which clears the inherited theme default instead "
            "of leaving it alone. Remove that null override."
        ),
        doc=(
            "Fired when `marks.area.stacked` resolves to null after the "
            "style cascade. Every built-in theme declares this key, so the "
            "only way to reach null is a board or chart explicitly "
            "authoring `marks.area.stacked: null` -- which clears the "
            "inherited value rather than leaving it untouched. Remove the "
            "null override."
        ),
        summary="Fired when a board or chart clears the area stacked recipe to null.",
        docs_topic="charts",
    )
)

ERR_AREA_STACKED_STROKE_INCOMPLETE = REGISTRY.register(
    ErrorCode(
        code="ERR-AREA-STACKED-STROKE-INCOMPLETE",
        domain="compile",
        title="marks.area.stacked.stroke is missing cap or join",
        message_template=(
            "Chart {chart_id!r} (area): `marks.area.stacked.stroke` must "
            "declare both `cap` and `join`. Stacked and single-series area "
            "charts replace `marks.line.stroke` with it wholesale, so a "
            "property it omits is dropped rather than inherited and the edge "
            "falls back to SVG butt/miter, spiking each vertex. Set both "
            "explicitly (`cap: butt` for the renderer's own default)."
        ),
        doc=(
            "Fired when the area stacked recipe's stroke is missing a cap or "
            "a join, usually because a board or chart authored one of them as "
            "null. The recipe's stroke replaces the top-edge line stroke "
            "wholesale rather than merging into it, so an omitted property is "
            "not inherited. Set both cap and join explicitly."
        ),
        summary="Fired when the area stacked recipe's stroke omits cap or join.",
        docs_topic="charts",
    )
)

ERR_FILE_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-NOT-FOUND",
        domain="compile",
        title="File not found",
        message_template="File not found: {path}",
        doc=(
            "Fired when a file path given to a dbt charts verb does not exist on "
            "the filesystem. Check for typos in the path and ensure the file exists."
        ),
        docs_topic="errors",
    )
)

ERR_TICKS_INTERVAL_MEASURE_AXIS = REGISTRY.register(
    ErrorCode(
        code="ERR-TICKS-INTERVAL-MEASURE-AXIS",
        domain="compile",
        title="ticks.time_unit is not supported on the measure axis",
        message_template=(
            "style.axis_y.ticks.time_unit/step is not supported: the measure "
            "axis (axis_y) is never temporal in dbt charts' cartesian model, "
            "and its tick ladder is computed from ticks.count. Use "
            "axis_y.ticks.count here; ticks.time_unit/step apply to axis_x."
        ),
        doc=(
            "Fired when `style.axis_y.ticks.time_unit` or `step` is set. The "
            "measure axis (axis_y) is never temporal in dbt charts' cartesian "
            "model, and its tick ladder is computed from `ticks.count`. Use "
            "`axis_y.ticks.count` here; `ticks.time_unit/step` apply to "
            "`axis_x`."
        ),
        summary="Fired when `ticks.time_unit` or `step` is set on the measure axis, which is never temporal.",
        docs_topic="charts",
    )
)

ERR_PALETTE_UNKNOWN = REGISTRY.register(
    ErrorCode(
        code="ERR-PALETTE-UNKNOWN",
        domain="compile",
        title="Palette name is not a theme palette role or a shipped palette",
        message_template=(
            "Unknown palette {name!r} at {field_path}. Palette roles are "
            "theme-scoped, resolved only from a theme's `palettes:` block; "
            "this name is not a role the active theme binds, and not a "
            "shipped dbt charts palette."
        ),
        doc=(
            "Fired when an authored `palette:` string names neither a role in "
            "the theme's `palettes:` block (`category`, `sequence`) nor a "
            "shipped dbt charts palette (`editorial-10`). Roles are theme-"
            "scoped; board- and chart-level `style:` cannot author one, only "
            "a palette name. Checked in two places: once the theme cascade is "
            "complete (a theme's own `palettes:` binding), and once each "
            "board/chart is normalized (every other palette field); both are "
            "the first point a role name can be told apart from a typo at "
            "that scope."
        ),
        summary="Fired when `palette:` names no known theme role or shipped palette.",
        docs_topic="charts",
        hint_generator=suggest_close_palette,
    )
)

ERR_UNKNOWN_THEME = REGISTRY.register(
    ErrorCode(
        code="ERR-UNKNOWN-THEME",
        domain="compile",
        title="Theme name is not a built-in theme",
        message_template=(
            "Unknown theme {theme!r}. Available built-in themes: {available}."
        ),
        doc=(
            "Fired during board validation when `theme:`, or a plain (non-path) "
            "`extends:` entry, names something that is not a built-in theme. "
            "Without it the name is dropped and the board renders on the default "
            "theme with no sign the request was ignored. Raised at positions "
            "where nothing resolves the entry as a board: a standalone or "
            "in-memory compile, and any nested board. A project board compiled "
            "through its file gets ERR-EXTENDS-UNRESOLVED from the extends layer "
            "instead, which can also report the board lookup it tried."
        ),
        summary="Fired when `theme:` names something that is not a built-in theme.",
        docs_topic="board",
        hint_generator=suggest_close_theme,
    )
)

ERR_EXTENDS_UNRESOLVED = REGISTRY.register(
    ErrorCode(
        code="ERR-EXTENDS-UNRESOLVED",
        domain="compile",
        title="extends entry names neither a theme nor a board",
        message_template=(
            "Unresolvable `theme:`/`extends:` value {entry!r}: not a built-in "
            "theme, and no board by that name at the project root. Use a "
            "built-in theme, a project-root board name, or a relative path such "
            "as `./base.yaml`. Available built-in themes: {available}."
        ),
        doc=(
            "Fired by the extends layer when a board's `extends:` entry resolves "
            "to nothing: not a built-in theme, and no matching `.yaml`/`.yml` at "
            "the project root. This is the project lane's counterpart to "
            "ERR-UNKNOWN-THEME; it fires only where the board lookup was "
            "actually attempted, so it can offer that lookup as a fix."
        ),
        summary="Fired when an `extends:` entry names neither a theme nor a board.",
        docs_topic="board",
        hint_generator=suggest_close_extends,
    )
)

ERR_FORMAT_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-INVALID",
        domain="compile",
        title="Format spec is not a predefined name, a style.formats alias, or a valid d3-format spec",
        message_template=(
            "Unknown number format {spec!r} at {field_path}. It is not an "
            "engine-predefined format name, not a key in `style.formats`, "
            "and is not a valid d3-format spec "
            "({reason} at position {position})."
        ),
        doc=(
            "Fired when an authored `format:` string is not one of the engine's "
            "predefined format names (e.g. `currency`, `number`, `percent_number`), "
            "not a key defined in `style.formats`, and fails to parse as a d3-format "
            "spec. Check for typos against the predefined names or your `style.formats` "
            "keys, or use a valid d3-format spec (https://d3js.org/d3-format)."
        ),
        summary="Fired when `format:` is not a predefined name, alias, or valid d3 spec.",
        docs_topic="charts",
        hint_generator=suggest_close_format,
    )
)

ERR_FORMAT_NATIVE_IN_VEGA_SLOT = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-NATIVE-IN-VEGA-SLOT",
        domain="compile",
        title="Native formatter used in a Vega-rendered format slot",
        message_template=(
            "Format {spec!r} at {field_path} is a Python-only native formatter "
            "and cannot be used in Vega-rendered slots (axis labels, mark value "
            "labels, number_format, time_format, support_table). Use it only in KPI "
            "or table format fields. Valid alternatives: {available}."
        ),
        doc=(
            "Fired when `percent_number`, `percent_number_delta`, or "
            "`percentage_points_delta` appears in a Vega-rendered format slot "
            "such as an axis label, mark value-label format, number_format, "
            "time_format, or support_table format. These names bypass d3 entirely "
            "and are only valid in Python-rendered slots (KPI headline and table "
            "cells). For Vega-rendered slots, use a d3 percent spec (e.g. `.1%`) "
            "or another predefined name."
        ),
        summary="Python-only native formatter used in a Vega-rendered slot.",
        docs_topic="charts",
    )
)

ERR_FORMAT_KIND_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-KIND-MISMATCH",
        domain="compile",
        title="Predefined format name is the wrong kind for this slot",
        message_template=(
            "Format {spec!r} at {field_path} is not a {kind} format. This slot "
            "takes only the {kind} half of the engine's vocabulary: {available}. "
            "{escape_hatch}"
        ),
        doc=(
            "Fired when a predefined format name from one half of the vocabulary "
            "lands in a slot that takes the other. `number_format` feeds a "
            "quantitative axis and `time_format` a temporal one, so each accepts "
            "only its own names; `time_format: currency` resolves to the d3 "
            "number spec `$.3~s`, which Vega bakes onto a date axis as garbage "
            "tick labels rather than failing. Use `date_short` or a strftime "
            "spec (`%b %Y`) for `time_format`; use a number name (`currency`, "
            "`number`, `percent`) or a d3 spec for `number_format`. A plain "
            "`format:` slot is judged by the column it paints and takes either."
        ),
        summary="Number format name in a time slot, or a time name in a number slot.",
        docs_topic="charts",
        # No hint_generator: only an exact predefined name from the other half
        # reaches this raise, so the input domain is the vocabulary itself and no
        # member of it is a near-miss for the other half. The message names the
        # legal values outright instead.
    )
)

ERR_FORMAT_PREDEFINED_SHADOW = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-PREDEFINED-SHADOW",
        domain="compile",
        title="style.formats key shadows an engine-predefined format name",
        message_template=(
            "Cannot define {spec!r} in style.formats at {field_path}: "
            "this name is engine-predefined and cannot be overridden. "
            "Choose a project-specific name (e.g. 'revenue', 'arr') for "
            "custom format aliases."
        ),
        doc=(
            "Fired when a `style.formats` key collides with an engine-owned "
            "predefined format name such as `number`, `currency`, or "
            "`date_short`. Predefined names resolve via engine rules and cannot "
            "be shadowed. Define your custom alias under a different name."
        ),
        summary="style.formats key shadows an engine-predefined format name.",
        docs_topic="charts",
    )
)


# ──────────────────────────────── Warning codes ───────────────────────────────
# Compile-time authoring warnings declared here in the leaf so the registry
# is complete on import of core.diagnostics. Emitters import their
# constants back rather than declaring codes in-module.

WARN_UNREFERENCED_CHART = REGISTRY.register(
    WarningCode(
        code="WARN-UNREFERENCED-CHART",
        domain="compile",
        redundant=True,
        title="Chart is defined but not placed in any layout",
        message_template=(
            "Chart {chart_id!r} is defined but not referenced in any layout "
            "(rows/cols/grid/tabs). It will not appear in the rendered dashboard."
        ),
        fix_template=(
            "Add the chart to a layout block, or delete it if it is no longer needed."
        ),
        doc=(
            "Fires at compile time for charts that are defined somewhere in the "
            "board tree but never placed in any layout (rows/cols/grid/tabs). The "
            "board still compiles because content-only boards are valid; this warning "
            "surfaces lazy authoring: an author defined a chart and forgot to "
            "display it. Emitted by `compile/validate/board_warnings.py`."
        ),
        docs_topic="charts",
    )
)

WARN_REDUNDANT_AUTHORED_LABEL = REGISTRY.register(
    WarningCode(
        code="WARN-REDUNDANT-AUTHORED-LABEL",
        domain="compile",
        redundant=True,
        title="Variable label is the same as the inferred display name",
        message_template=(
            "Variable {var_name!r} sets label to {authored_label!r}, "
            "which is already inferred from the variable name."
        ),
        fix_template=(
            "Omit `label:` unless the display label should differ "
            "from the inferred name."
        ),
        doc=(
            "Fires when a variable's authored `label:` is exactly the same as "
            "the label dbt charts would infer from the variable name (title-cased, "
            "underscores to spaces). The explicit label is redundant and can be "
            "omitted. Emitted by `compile/validate/authoring_warnings.py`."
        ),
        docs_topic="variables",
    )
)

WARN_REDUNDANT_AUTHORED_DEFAULT = REGISTRY.register(
    WarningCode(
        code="WARN-REDUNDANT-AUTHORED-DEFAULT",
        domain="compile",
        redundant=True,
        title="Variable field is explicitly set to its default value",
        message_template=(
            "Variable {var_name!r} explicitly sets {field_name} "
            "to the default value {default_value!r}."
        ),
        fix_template="Omit `{field_name}:` when the default is intended.",
        doc=(
            "Fires when a variable explicitly sets a field to its default value. "
            "The explicit setting is redundant and adds noise without changing "
            "behavior. Emitted by `compile/validate/authoring_warnings.py`."
        ),
        docs_topic="variables",
    )
)

WARN_FLAT_COLS_UNSIZED_OVERFLOW = REGISTRY.register(
    WarningCode(
        code="WARN-FLAT-COLS-UNSIZED-OVERFLOW",
        domain="compile",
        title="Flat cols: row has too many unsized non-KPI cells",
        message_template=(
            "cols: has {growable_count} unsized non-KPI cells "
            "(max recommended {max_unsized}). Each inherits "
            "the row's full vertical budget, so a wide flat row makes "
            "row height unpredictable. KPI cards are height-stable and "
            "don't count."
        ),
        fix_template=(
            "Nest cells into sized rows-in-cols groups instead, e.g.:\n"
            "  cols:\n"
            '  - width: "17%"\n'
            "    rows:\n"
            "      - kpi_a\n"
            "      - kpi_b\n"
            '  - width: "55%"\n'
            "    rows:\n"
            "      - hero"
        ),
        doc=(
            "Fires when a flat `cols:` row has more unsized non-KPI cells than "
            "the recommended limit. Each unsized cell inherits the row's full "
            "vertical budget, making row height unpredictable with many columns. "
            "KPI cards are carved out because they are height-stable. "
            "Emitted by `compile/validate/authoring_warnings.py`."
        ),
        docs_topic="layout",
    )
)

WARN_DOUBLE_HEADER = REGISTRY.register(
    WarningCode(
        code="WARN-DOUBLE-HEADER",
        domain="compile",
        redundant=True,
        title="Board title is repeated by a heading at the top of the body",
        message_template=(
            "Board title {title!r} is followed immediately by a level-{level} "
            "markdown heading {heading!r}; the board renders both, so the "
            "dashboard headers itself twice."
        ),
        fix_template=(
            # Location-neutral on purpose: the mark sits on the heading when the
            # prose is the board's own `text:`, but on `title:` when it came from
            # a layout slot, and the CLI shows no mark at all. "Delete this line"
            # would be wrong in two of those three.
            "Delete the heading line from the body; `title:` already renders as "
            "the board header. If you would rather keep the heading, drop "
            "`title:` instead and let it be the header."
        ),
        doc=(
            "Fires when a board sets `title:` and the first block of its body "
            "markdown is a heading that either is level 1 (a document has one "
            "document title) or repeats the board title. The board prints the board "
            "title as its header and the markdown heading directly beneath it. A "
            "heading further down the body, or a lower-level heading with different "
            "text (`## Overview` under `title: Sales`), is ordinary section "
            "structure and does not fire. Heading detection uses the same markdown "
            "parser that renders the text, so a fenced code block opening with a "
            "`#` comment is not mistaken for a heading. Checked on the board itself, "
            "not on nested boards; a section card pairing its title with a styled "
            "body heading is a deliberate pattern. "
            "Emitted by `compile/validate/board_warnings.py`."
        ),
        docs_topic="board",
    )
)

WARN_H1_BODY_NO_TITLE = REGISTRY.register(
    WarningCode(
        code="WARN-H1-BODY-NO-TITLE",
        domain="compile",
        title="Body opens with a level-1 heading but the board has no title",
        message_template=(
            "Body opens with a level-1 markdown heading {heading!r} and the board "
            "has no `title:` set; the heading renders as plain body text instead "
            "of the styled board header."
        ),
        fix_template=(
            "Set `title: {heading}` and delete the `# {heading}` line from the "
            "body text."
        ),
        doc=(
            "Fires when a board has no `title:` and the first block of its body "
            "markdown is a level-1 heading; the author likely reached for a "
            "markdown heading instead of the purpose-built `title:` field, so the "
            "board renders as unstyled prose with no header. Only a literal level-1 "
            "heading counts; a lower-level heading (`## Overview`) with no title is "
            "ordinary section structure and does not fire, since there is no title "
            "text to compare it against. Heading detection uses the same markdown "
            "parser that renders the text, so a fenced code block opening with a "
            "`#` comment is not mistaken for a heading. Checked on the board itself, "
            "not on nested boards; a section card's own opening heading is not "
            "inspected. Emitted by `compile/validate/board_warnings.py`."
        ),
        docs_topic="board",
    )
)

ERR_AXIS_COLUMN_REQUIRES_TABULAR_FONT = REGISTRY.register(
    ErrorCode(
        code="ERR-AXIS-COLUMN-REQUIRES-TABULAR-FONT",
        domain="compile",
        title="A column-forming quantitative axis needs a tabular label font",
        message_template=(
            "Chart {chart_id!r}: axis label font {family!r} does not guarantee "
            "tabular figures, but its tick ladder compacts to a shared magnitude "
            "and forms a column; the digits only stack into a column when every "
            "digit shares one advance, which a proportional board does not give, "
            "so the column misaligns silently. Set a tabular family (e.g. 'dbt Sans "
            "Tabular') on style.axis_y.labels.font.family for this chart, or on "
            "charts.axis_quantitative.labels.font.family to cover every chart."
        ),
        doc=(
            "Fired at resolve when a column-forming quantitative axis (a vertical "
            "ruler) resolves a shared magnitude scale but its label font is not "
            "vendor-registered as tabular. Ticks that carry no magnitude suffix "
            "are padded to the width of the one that does, so their digits stack "
            "under it; that column only holds when every digit shares a single "
            "advance, which is what a tabular board guarantees and a proportional "
            "one does not. A horizontal ruler is exempt (its ticks form no "
            "column) regardless of font."
        ),
        summary="Fired when a compacting quantitative axis that forms a column resolves to a non-tabular label font.",
        docs_topic="charts",
    )
)

WARN_SINGLE_CHART_REDUNDANT_TITLE = REGISTRY.register(
    WarningCode(
        code="WARN-SINGLE-CHART-REDUNDANT-TITLE",
        domain="compile",
        redundant=True,
        title="Single-chart dashboard has both a board title and a chart title",
        message_template=(
            "Board title {title!r} sits above a single chart that carries its own "
            "title {chart_title!r}; two headers for one chart."
        ),
        fix_template=(
            "Give the board one header: drop the chart's `title:` (the board title "
            "already heads the board, and it is what listings, search, and nav "
            "show), or drop the board `title:` and let the chart title stand."
        ),
        doc=(
            "Fires when a board's whole content is one chart and both the board and "
            "the chart carry a title, so the board stacks two headers over a single "
            "piece of content. A single-chart dashboard does not need a separate "
            "dashboard title. Does not fire when only one of the two is titled, when "
            "the board holds more than one chart, or when body prose sits between the "
            "two titles. KPI and callout charts are excluded: a KPI labels itself "
            "with `label:` and a callout's title is a prose lead-in, so neither "
            "stacks a chart header under the board's. Checked on the board itself, "
            "not on nested section boards. Emitted by `compile/validate/board_warnings.py`."
        ),
        docs_topic="board",
    )
)

WARN_ADJACENT_TEXT_ROWS = REGISTRY.register(
    WarningCode(
        code="WARN-ADJACENT-TEXT-ROWS",
        domain="compile",
        title="Consecutive text-only rows each flow into their own columns",
        message_template=(
            "{count} text-only rows in a row: each is measured and flowed on its "
            "own, so the column grid restarts at every one; the column edges do "
            "not line up, and whichever block half-fills its last column leaves a "
            "gap mid-page."
        ),
        fix_template=(
            "Merge them into one `- text:` block. Prose in a single block flows "
            "through one set of columns and fills them evenly; the headings that "
            "separated the rows still separate the sections inside it."
        ),
        doc=(
            "Fires when two or more consecutive `rows:` items are body prose and "
            "nothing else. Every prose block picks its own column count from its "
            "own line count and balances its own lines, with no flow between "
            "blocks, so stacking them renders unrelated grids rather than one "
            "continuous passage. One diagnostic per run, marking the second row "
            "of it; a run of four rows is one authoring decision, not three. "
            "A `title:` on the row does not exempt it: `- title:` + `text:` is "
            "how a section of prose is written, and two of those fragment the "
            "same way, so the fix is to merge them and let the later titles "
            "become headings inside the merged block. Four shapes do not "
            "participate, and each of them also ends a run rather than being "
            "skipped over; a row between two passages is something the author "
            "put there. They are: a row holding a layout of its own (a section, "
            "not a block of prose); a row carrying anything only a slot can "
            "carry: `style:`, `visible:`, a `details:` disclosure, an authored "
            "height, since a merged block has one of each and merging would "
            "have to discard one; a row with no flowing prose in it, a markdown "
            "table or a code block on its own, because merging one of those "
            "into a prose column squeezes it to the measure; and prose too "
            "short to reach a second column, which is one column wide at any "
            "board width and so cannot misalign against anything; a caption "
            "rather than a passage. A `cols:` layout never fires: prose side by "
            "side is an authored spread. A row imported from another file or "
            "generated by a `foreach` is not reported either; it has no "
            "authored coordinates to mark, and the merge it would ask for is "
            "not the author's to make here. "
            "Emitted by `compile/validate/board_warnings.py`."
        ),
        docs_topic="layout",
    )
)

WARN_HTML_POLICY_CAPPED = REGISTRY.register(
    WarningCode(
        code="WARN-HTML-POLICY-CAPPED",
        domain="compile",
        redundant=False,
        title="Board html_policy downgraded by deployment ceiling",
        message_template=(
            "Board requested html_policy={requested!r} but the {ceiling_source} "
            "ceiling is {ceiling!r}; effective policy is {effective!r}. "
            "Raw HTML in this board will be treated as {effective!r}."
        ),
        fix_template=(
            "Either lower the board's `html_policy:` to {effective!r} to match "
            "what is actually rendered, or raise the deployment ceiling "
            "(DCT_HTML_POLICY_CEILING or markdown.html_policy_ceiling in "
            "dbt_charts.yml) if the deployment operator permits it."
        ),
        doc=(
            "Fires when a board's authored `html_policy` is above the effective "
            "deployment ceiling (set by DCT_HTML_POLICY_CEILING env var or "
            "`markdown.html_policy_ceiling` in dbt_charts.yml). The board compiles "
            "successfully but the policy is downgraded at normalize time; this "
            "warning makes the downgrade visible so the author knows their "
            "html_policy setting is not being honored. "
            "Emitted by `compile/compiler.py`."
        ),
        summary=(
            "Fired when the board's html_policy exceeds the deployment ceiling "
            "and is silently downgraded to the ceiling value."
        ),
        docs_topic="board",
    )
)

ERR_MULTI_Y_COLOR_CONFLICT = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTI-Y-COLOR-CONFLICT",
        domain="compile",
        title="Multi-y chart's color: must name a column",
        message_template=(
            "Chart {chart_id!r} ({chart_type}): y: [...] folds measures into a "
            "color series, and color: {color_field!r} is bound to a gradient or "
            "conditional scale, which names no series to cross them with. Bind "
            "color: to a plain column (each of its values x each measure becomes "
            "a series) or drop it."
        ),
        doc=(
            "Fired when a bar, area, line, or scatter chart authors y: [a, b] "
            "together with a color: that is not a plain series column -- a "
            "gradient or a conditional scale. A column composes with the "
            "fold: the series become `<value> - <measure>` composites, one "
            "per dimension value per measure."
        ),
        docs_topic="charts",
    )
)

ERR_MULTI_Y_LAYERS_CONFLICT = REGISTRY.register(
    ErrorCode(
        code="ERR-MULTI-Y-LAYERS-CONFLICT",
        domain="compile",
        title="Multi-y chart cannot also have layers:",
        message_template=(
            "Chart {chart_id!r} ({chart_type}): y: [...] folds measures into a "
            "color series automatically -- overlay layers: are not supported with "
            "multi-y charts. Keep a single y field and overlay the other "
            "measures as layers: entries instead."
        ),
        doc=(
            "Fired when a bar, area, line, or scatter chart authors both "
            "y: [a, b] and layers: at the same time."
        ),
        docs_topic="charts",
    )
)

ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR = REGISTRY.register(
    ErrorCode(
        code="ERR-WIDE-MEASURE-NAME-CONTAINS-SEPARATOR",
        domain="compile",
        title="Wide measure name contains the dimension composite separator",
        summary=(
            "Fired when a wide-measure chart's own measure column name "
            "contains the dimension composite separator."
        ),
        message_template=(
            "Chart authors y: [...] with color: as a dimension, and measure "
            "column {measure!r} contains the {separator!r} composite "
            "separator, so the composite series name cannot be split back "
            "into a dimension value and a measure. Rename the measure "
            "column, or alias it in the query."
        ),
        doc=(
            "A wide chart authoring a list of y: measures can also author "
            "color: as a dimension the measures cross with. Fires when one "
            "of the measure column names itself contains the separator the "
            "`<dimension value> - <measure>` composite string uses. The "
            "composite cannot be split back apart unambiguously in that "
            "case, so the measure column must be renamed, or aliased in "
            "the query, to avoid the separator."
        ),
        docs_topic="charts",
    )
)

ERR_CATEGORY_COLOR_PIN_DUPLICATE = REGISTRY.register(
    ErrorCode(
        code="ERR-CATEGORY-COLOR-PIN-DUPLICATE",
        domain="compile",
        title="Two category_colors pins name the same color",
        message_template=(
            "`style.charts.category_colors.{field}` pins both `{first_value}` "
            "and `{second_value}` to `{color}`: two categories cannot share "
            "a swatch. Give one of them a different color."
        ),
        doc=(
            "Fired when two values pinned under "
            "`style.charts.category_colors.<field>` resolve to the same "
            "color (case-insensitively), which would seat two categories on "
            "one swatch. Give one of the pins a different color."
        ),
        summary="Fired when two category_colors pins resolve to the same color.",
        docs_topic="charts",
    )
)

ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED = REGISTRY.register(
    ErrorCode(
        code="ERR-CATEGORY-COLOR-PALETTE-EXHAUSTED",
        domain="compile",
        title="An authored category_colors field has more values than the palette has swatches",
        message_template=(
            "{value_count} values for `{field}`, but the board's categorical "
            "palette has {swatch_count} swatches. Pick a wider palette, or "
            "reduce the distinct count `style.charts.category_colors.{field}` "
            "has to cover."
        ),
        doc=(
            "Fired when an authored `style.charts.category_colors.<field>` "
            "field has more distinct values than the board's categorical "
            "palette has swatches. Two categories must never share a swatch, "
            "so an authored field raises instead of silently declining to "
            "bind (an unauthored field just declines). Pick a wider palette, "
            "or reduce the distinct count the field has to cover."
        ),
        summary="Fired when an authored category_colors field outgrows the palette's swatch count.",
        docs_topic="charts",
    )
)

WARN_AXIS_ALIGN_DISCARDED = REGISTRY.register(
    WarningCode(
        code="WARN-AXIS-ALIGN-DISCARDED",
        domain="compile",
        title="Authored axis_y.labels.align has no effect on a house-format quantitative axis",
        message_template=(
            "Chart {chart_id!r}: axis_y.labels.align = {authored_align!r} has no "
            "effect: the format alias {format_alias!r} forces label.align = 'right' "
            "on right-edge quantitative axes for correct digit alignment."
        ),
        fix_template=(
            "Remove axis_y.labels.align from this chart. To opt out of forced "
            "alignment, use a literal d3 format spec (e.g. '.1%') instead of "
            "the alias."
        ),
        summary=(
            "Fired when a chart authors axis_y.labels.align alongside a house-rule "
            "format alias on a right-edge quantitative axis."
        ),
        doc=(
            "Fired when a chart authors axis_y.labels.align alongside a house-rule "
            "format alias (percent, currency, etc.) on a right-edge quantitative axis. "
            "The alias forces label.align = 'right' for place-value alignment; the "
            "authored align value is silently discarded."
        ),
        docs_topic="charts",
    )
)
