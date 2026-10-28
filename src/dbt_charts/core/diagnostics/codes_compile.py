"""ERR-* error codes and WARN-* warning codes for the compile domain."""

from __future__ import annotations

from dbt_charts.core.diagnostics.hints import (
    suggest_close_chart,
    suggest_close_extends,
    suggest_close_format,
    suggest_close_palette,
    suggest_close_source,
    suggest_close_theme,
    suggest_layout_chart,
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

ERR_TAB_DEFAULT_UNKNOWN = REGISTRY.register(
    ErrorCode(
        code="ERR-TAB-DEFAULT-UNKNOWN",
        domain="compile",
        title="tabs.default names no tab",
        message_template=(
            "`tabs.default: {default}` slugs to {slug!r}, which is not a tab on "
            "this board (tabs: {slugs})."
        ),
        doc=(
            "Fired when the `default:` of a `tabs:` layout matches no tab title. It is "
            "compared by slug: the title lowercased with spaces replaced by "
            "underscores."
        ),
        docs_topic="layout",
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

ERR_UNKNOWN_CHART = REGISTRY.register(
    ErrorCode(
        code="ERR-UNKNOWN-CHART",
        domain="compile",
        title="Chart id is not on the board",
        message_template="No chart {chart!r} on this board. Available charts: {available}.",
        doc=(
            "Fired when a caller addresses one chart by board path + chart id "
            "(`dct render BOARD --chart ID`, `dct describe BOARD --chart ID`) and "
            "the board declares no chart with that id."
        ),
        summary="Fired when a board path + chart id names a chart the board doesn't declare.",
        docs_topic="charts",
        hint_generator=suggest_close_chart,
    )
)

ERR_TEXT_UNKNOWN_QUERY = REGISTRY.register(
    ErrorCode(
        code="ERR-TEXT-UNKNOWN-QUERY",
        domain="compile",
        title="Text references an unknown query",
        message_template=(
            "{surface} references unknown query {query_name!r}. "
            "Defined queries: {defined}."
        ),
        doc=(
            "Fired when board `text:` or `title:`, or a chart's `title`, `subtitle`, "
            "KPI `label` or callout `title` or `message`, reads `queries.<name>` for a query "
            "that is not defined on the board, its `extends:` parents or its "
            "imports. Check for typos and declare the query under `queries:`."
        ),
        summary="Fired when board or chart text reads a query the board does not define.",
        docs_topic="board",
    )
)

ERR_TEXT_QUERIES_NONLITERAL = REGISTRY.register(
    ErrorCode(
        code="ERR-TEXT-QUERIES-NONLITERAL",
        domain="compile",
        title="Text uses queries without a literal name",
        message_template=(
            "Text must reference queries by literal name "
            "(queries.<name> or queries['<name>'])."
        ),
        doc=(
            "Fired when board `text:` or `title:`, or chart text, uses `queries` in any way other than "
            "`queries.<name>` or `queries['<name>']`, such as `queries[var]`, "
            "`queries | list` or `{% set q = queries %}`. The queries a board "
            "reads must be known before it runs."
        ),
        summary="Fired when text uses `queries` without a literal query name.",
        docs_topic="board",
    )
)

ERR_TEXT_LISTING_NONLITERAL = REGISTRY.register(
    ErrorCode(
        code="ERR-TEXT-LISTING-NONLITERAL",
        domain="compile",
        title="Text lists boards without a literal pattern",
        message_template=(
            "{function} takes exactly one literal string pattern, e.g. {function}('*')."
        ),
        doc=(
            "Fired when board `text:` calls `glob_boards` or `glob_folders` with "
            "anything but one string literal, or uses the name without calling it. "
            "The files a board lists must be known before it renders."
        ),
        summary="Fired when `glob_boards`/`glob_folders` get a non-literal pattern.",
        docs_topic="board",
    )
)

ERR_TEXT_LISTING_PATTERN = REGISTRY.register(
    ErrorCode(
        code="ERR-TEXT-LISTING-PATTERN",
        domain="compile",
        title="Invalid board listing",
        message_template="{function}({pattern!r}): {reason}",
        doc=(
            "Fired at compile when `glob_boards`/`glob_folders` is called "
            "outside board `text:` (a board title or chart text), and at "
            "render when a pattern leaves the listing board's root (`charts/`) "
            "or the board has no file location."
        ),
        summary="Fired when a board listing pattern or call site is invalid.",
        docs_topic="board",
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
            "for the supported keys. A key a past grammar renamed, moved, or "
            "removed is ERR-RETIRED-FIELD instead. When the key is valid in a "
            "nearby block (the chart for a key written under `style:`, or the "
            "reverse), the hint names that block."
        ),
        docs_topic="board",
    )
)

ERR_RETIRED_FIELD = REGISTRY.register(
    ErrorCode(
        code="ERR-RETIRED-FIELD",
        domain="compile",
        title="Retired field in board YAML",
        message_template=(
            "Field {field_path!r} was retired by a past grammar version. "
            "See the hint for its successor."
        ),
        doc=(
            "Fired when the board YAML spells a key a past grammar version renamed, "
            "moved, or removed and the file could not be migrated automatically "
            "(for example it also uses newer keys, or both spellings are present). "
            "The message names the version that retired the key and where it went; "
            "the hint says what to change. The facts come from the migration "
            "declarations, so every key ever retired stays known."
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

ERR_QUERY_DIALECT_MIXED = REGISTRY.register(
    ErrorCode(
        code="ERR-QUERY-DIALECT-MIXED",
        domain="compile",
        title="Composed queries declare different dialects",
        message_template=(
            "Query {query_name!r} (dialect: {dialect}) embeds "
            "{{{{ queries.{dep_name} }}}} (dialect: {dep_dialect}). Queries "
            "composed with {{{{ queries.X }}}} become one statement, so they "
            "must all declare the same `dialect:`."
        ),
        fix_template=(
            "Set the same `dialect:` on both queries, or on neither. A query "
            "that sets none is written in its source's own dialect."
        ),
        summary="Fired when a query embeds another query that declares a different `dialect:`.",
        doc=(
            "Fired when a query embeds another with `{{ queries.X }}` and the "
            "two declare different `dialect:` values, counting an unset "
            "`dialect:` as a value."
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
            "the source name is not the dbt project name.{advice}"
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
        title="Board fragment contains an unknown or invalid field",
        message_template=(
            "Board fragment schema error: {message}. "
            "Check that all keys are valid board fields."
        ),
        doc=(
            "Fired when a meta.yml file or a private (`_`-prefixed) partial "
            "contains a field that is not recognized by the board schema, or a "
            "field with an invalid value. Check that all keys match the "
            "supported board fields and remove any extras. The offending file "
            "is named separately, in the diagnostic's `file` location."
        ),
        summary="Fired when a board fragment (meta.yml or a private partial) contains a field the board schema doesn't recognize.",
        docs_topic="board",
    )
)

ERR_PROJECT_CONFIG_SCHEMA = REGISTRY.register(
    ErrorCode(
        code="ERR-PROJECT-CONFIG-SCHEMA",
        domain="compile",
        title="dbt_charts.yml contains an unknown or invalid field",
        message_template=(
            "Project config schema error: {message}. "
            "Check that all keys are valid project config fields."
        ),
        doc=(
            "Fired when dbt_charts.yml (the project config file) contains a "
            "top-level field that is not recognized (e.g. `style:`, which "
            "belongs in charts/meta.yml instead), or a field with an invalid "
            "value. Check the project config reference for the supported keys."
        ),
        summary="Fired when dbt_charts.yml contains a field the project config schema doesn't recognize.",
        docs_topic="board",
    )
)

ERR_LOCKED_SCHEMA_VERSION = REGISTRY.register(
    ErrorCode(
        code="ERR-LOCKED-SCHEMA-VERSION",
        domain="compile",
        title="Board file does not match the project's locked schema version",
        message_template="{message}",
        doc=(
            "Fired when dbt_charts.yml sets `locked_schema_version` and a board, "
            "its meta.yml, or an `extends:` file uses syntax that release does "
            "not accept, typically because a newer dbt Charts wrote it. Rewrite "
            "the file in the locked release's syntax, or raise the lock."
        ),
        summary="Fired when a board file uses syntax newer than the project's locked_schema_version.",
        docs_topic="board",
    )
)

ERR_CONFIG_KEY_MOVED = REGISTRY.register(
    ErrorCode(
        code="ERR-CONFIG-KEY-MOVED",
        domain="compile",
        title="dbt_charts.yml key moved out of project config",
        message_template="dbt_charts.yml key {key!r} has moved: {replacement}",
        doc=(
            "Fired when dbt_charts.yml sets a key that no longer exists there. "
            "Design values (formerly under `chart_rendering:`, and the `vega:` "
            "block) are theme fields: set the successor named in the message "
            "under `style:` in charts/meta.yml. Engine constants are not "
            "configurable; delete the key."
        ),
        summary="Fired when dbt_charts.yml sets a key that moved to the theme or was removed.",
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
        hint_generator=suggest_layout_chart,
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
            "The message carries the specific error. A missing required field "
            "lists the fields the block accepts. Check the field value against "
            "the schema."
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

ERR_LAYER_BASELINE_ON_DATE_MEASURE = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-BASELINE-ON-DATE-MEASURE",
        domain="compile",
        title="A bar or area layer has no baseline on a date axis",
        message_template=(
            "{field_path} has type {layer_type}, which grows from a baseline, but "
            "this bar's value axis holds dates, which have none. Give a bar layer "
            "a `y_start` to span two dates, or plot the dates as a `scatter` or "
            "`line` layer."
        ),
        doc=(
            "A bar between two date columns puts dates on its value axis. A layer "
            "that grows from a zero baseline (a bar without `y_start`, or an area) "
            "has no zero on it, so it is refused. A layer pinned to its own axis "
            "with `axis_y.position` keeps its own numeric scale."
        ),
        summary="Bar or area layer without a baseline over a date measure.",
        docs_topic="charts",
    )
)

ERR_LAYER_MIXED_AXES_ON_DATE_MEASURE = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-MIXED-AXES-ON-DATE-MEASURE",
        domain="compile",
        title="Layers over a date axis cannot mix pinned and unpinned sides",
        message_template=(
            "{field_path}: this bar's value axis holds dates, and some layers pin "
            "`axis_y.position` while others do not. A pinned layer gets a numeric "
            "scale of its own, so the others' dates would have nowhere to draw. "
            "Pin every layer, or none."
        ),
        doc=(
            "Layers over a date-measure bar share its date axis unless one pins a "
            "side. Pinning makes the layers' scales independent, and a date column "
            "cannot ride a numeric one."
        ),
        summary="Pinned and unpinned layers over a date measure.",
        docs_topic="charts",
    )
)

ERR_LAYER_Y_KIND_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-Y-KIND-MISMATCH",
        domain="compile",
        title="A layer's y column does not fit the axis it plots on",
        message_template=(
            "{field_path}: column {y_field!r} holds {y_kind} values, but it plots on "
            "{axis_where}, which takes {axis_kind} values. Plot a {axis_kind} column "
            "there."
        ),
        doc=(
            "A layer on a bar's value axis shares its scale, so its `y` must be the "
            "same kind as the bar's: dates over a date measure, numbers over a "
            "numeric bar. A layer pinned with `axis_y.position` gets a numeric scale "
            "of its own. A column of the wrong kind draws no marks."
        ),
        summary="Layer y column of the wrong kind for its axis.",
        docs_topic="charts",
    )
)

ERR_BAR_Y_START_NULL = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-Y-START-NULL",
        domain="compile",
        title="Bar y_start column has an empty cell",
        message_template=(
            "Chart {chart_id!r}: bar y_start column {y_start_field!r} is empty "
            "in row {row}. Every row needs a start; write 0 for a bar that "
            "starts at zero."
        ),
        doc=(
            "Fired when a bar's or bar layer's `y_start:` column holds a NULL. A missing "
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
        title="Bar y and y_start are different kinds",
        message_template=(
            "Chart {chart_id!r}: column {y_field!r} is {y_kind} but "
            "column {y_start_field!r} is {y_start_kind}. A bar's start and end "
            "sit on one value axis: make them all numeric, or all dates."
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

ERR_ANNOTATION_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-ANNOTATION-INVALID",
        domain="compile",
        title="Annotation cannot be resolved against the chart",
        message_template="Chart {chart_id!r}: annotation {index}: {reason}.",
        doc=(
            "Fired when an annotation does not fit the chart it is on: an anchor "
            "whose value does not match the axis column's type, an aggregate or "
            "fit the axis cannot take, a missing or unknown `series`, a range "
            "whose `from` is past its `to`, or text that does not render."
        ),
        summary="Fired when an annotation does not fit the chart it is on.",
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

ERR_LAYER_AXIS_TITLE_WITHOUT_POSITION = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYER-AXIS-TITLE-WITHOUT-POSITION",
        domain="compile",
        title="A layer's y-axis title has no axis of its own to land on",
        message_template=(
            "{field_path} sets a title, but no layer on this chart pins "
            "`axis_y.position`, so every layer shares the base chart's y axis "
            "and a layer title has nowhere to go. Pin `axis_y.position` on a "
            "layer of a vertical base chart to give it its own axis, or drop "
            "the layer `axis_y.title`."
        ),
        doc=(
            "A layer's `axis_y.title` names that layer's own y axis. A layer "
            "only gets its own axis when some layer pins `axis_y.position` to "
            "a side; until then all layers share the base chart's axis, which "
            "has a single title slot that several layers could claim. Pin a "
            "side to split the axes, or remove the layer title."
        ),
        summary="Layer `axis_y.title` set on a chart whose layers share one y axis.",
        docs_topic="charts",
    )
)

ERR_LAYERS_AMBIGUOUS_Y_DOMAIN = REGISTRY.register(
    ErrorCode(
        code="ERR-LAYERS-AMBIGUOUS-Y-DOMAIN",
        domain="compile",
        title="Chart-level y domain is ambiguous with independent layer scales",
        message_template=(
            "Chart {chart_id!r} sets axis_y.scale.continuous.domain={domain!r} but "
            "a layer pins its y axis to one side, so the chart has two independent "
            "y scales and one chart-level domain cannot apply to both. Remove the "
            "chart-level axis_y.scale.continuous.domain and set "
            "axis_y.scale.domain on each layer's own axis_y; the base series' "
            "domain cannot be pinned once a layer sets axis_y.position."
        ),
        doc=(
            "Fired when a chart sets `axis_y.scale.continuous.domain` at the chart "
            "level and any layer pins its y axis to a side with `axis_y.position`. "
            "The base "
            "chart takes the opposite side, so the chart has two independent y "
            "scales and a single chart-level domain is ambiguous. Remove the "
            "chart-level `axis_y.scale.continuous.domain` and set "
            "`axis_y.scale.domain` on each layer's own `axis_y`; the base series' "
            "domain cannot be pinned once a layer sets `axis_y.position`."
        ),
        summary="Fired when a chart sets a chart-level y-axis domain but a side-pinned layer gives it two independent y scales.",
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

ERR_SUPPORT_TABLE_LABEL_IGNORED = REGISTRY.register(
    ErrorCode(
        code="ERR-SUPPORT-TABLE-LABEL-IGNORED",
        domain="compile",
        title="support_table per_series label: names nothing in this layout",
        message_template="{message}",
        summary=(
            "Fired when a `per_series:` support_table entry sets `label:` on a "
            "layout that names its rows or columns by series."
        ),
        doc=(
            "Fired when a `per_series:` entry without `by_measure: true` sets "
            "`label:` on a chart whose strip rows (vertical bar, line, area) or "
            "per-series columns (stacked horizontal bar) are named by the color "
            "field's series values, so the label would be silently dropped. A "
            "horizontal grouped bar's single per_series column still takes its "
            "header from `label:`. Remove the label; series names come from the "
            "color field and legend."
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

ERR_LOG_SCALE_REQUIRES_POSITIVE_X_DATA = REGISTRY.register(
    ErrorCode(
        code="ERR-LOG-SCALE-REQUIRES-POSITIVE-X-DATA",
        domain="compile",
        title="Log x scale requires strictly positive data",
        message_template=(
            "Chart {chart_id!r}: column {x_field!r} has a value <= 0, but "
            "axis_x.scale.type: log requires strictly positive data; a log "
            "domain is undefined at and below zero. Filter out the "
            "non-positive rows, or drop the log scale."
        ),
        doc=(
            "Fired when `axis_x.scale.type: log` is used but the x column "
            "contains a value ≤ 0. A log domain is undefined at and below zero. "
            "Filter out the non-positive rows, or drop the log scale."
        ),
        summary="Fired when a log x-axis scale is used but the data contains a value at or below zero.",
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

ERR_SORT_FIELD_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-SORT-FIELD-NOT-FOUND",
        domain="compile",
        title="sort.by column not found in the query result",
        message_template=(
            "sort.by column {field!r} not found in the query result. "
            "Available columns: {available}."
        ),
        doc=(
            "Fired when `sort.by` names a column that is not present on every "
            "row of the chart's query result. Without it the chart would "
            "render in natural order while claiming to be sorted. Check for a "
            "typo, or add the column to the query."
        ),
        summary="Fired when sort.by names a column missing from the query result.",
        docs_topic="charts",
    )
)

ERR_FIELD_LABELS_FIELD_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-FIELD-LABELS-FIELD-NOT-FOUND",
        domain="compile",
        title="field_labels key names no column the chart reads",
        message_template=(
            "field_labels key(s) {fields} name no column of the chart's query. "
            "Available columns: {available}."
        ),
        doc=(
            "Fired when a `field_labels` key is neither a column of the chart's "
            "query result nor a field one of its layers names. The label would "
            "never paint. Check for a typo, including letter case."
        ),
        summary="Fired when a field_labels key names no column the chart reads.",
        docs_topic="charts",
    )
)

ERR_FIELD_LABELS_WIDE_COLLISION = REGISTRY.register(
    ErrorCode(
        code="ERR-FIELD-LABELS-WIDE-COLLISION",
        domain="compile",
        title="field_labels gives two wide measures the same label",
        message_template=(
            "field_labels makes measures {measures} of `y:` share the label "
            "{label!r}, so their series cannot be told apart."
        ),
        doc=(
            "Fired when a `field_labels` entry for a `y: [...]` measure equals "
            "another measure's label. Derived labels fall back to the raw "
            "column name on a collision; an authored label never does. Give "
            "each measure distinct text."
        ),
        summary="Fired when field_labels gives two wide measures the same label.",
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
        title="marks.area.stacked.stroke is missing, or lacks width, cap or join",
        message_template=(
            "Chart {chart_id!r} (area): `marks.area.stacked.stroke` must "
            "declare `width`, `cap` and `join`. Stacked and single-series area "
            "charts replace `marks.line.stroke` with it wholesale, so a "
            "property it omits is dropped rather than inherited and the edge "
            "falls back to SVG butt/miter, spiking each vertex. Set all "
            "three explicitly (`cap: butt` for the renderer's own default)."
        ),
        doc=(
            "Fired when the area stacked recipe's stroke is null or is "
            "missing a width, cap or join, usually because a board or chart "
            "authored one of them as null. The recipe's stroke replaces the top-edge line stroke "
            "wholesale rather than merging into it, so an omitted property is "
            "not inherited. Set width, cap and join explicitly."
        ),
        summary="Fired when the area stacked recipe's stroke is null or omits width, cap or join.",
        docs_topic="charts",
    )
)

ERR_FILE_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-NOT-FOUND",
        domain="compile",
        title="File not found",
        message_template=("File not found: {path}. Check the path for typos."),
        doc=(
            "Fired when a file path given to a dbt charts verb does not exist on "
            "the filesystem. Check for typos in the path and ensure the file exists."
        ),
        docs_topic="errors",
    )
)

ERR_PATH_OUTSIDE_PROJECT = REGISTRY.register(
    ErrorCode(
        code="ERR-PATH-OUTSIDE-PROJECT",
        domain="compile",
        title="Board path is outside the project",
        message_template="Board path {path} is outside the project root.",
        doc=(
            "Fired when a board path given to a dbt charts verb resolves "
            "outside the project root. Pass a path to a board inside the "
            "project, or run the verb from the project that owns the board."
        ),
        docs_topic="errors",
    )
)

ERR_EMPTY_YAML_DOCUMENT = REGISTRY.register(
    ErrorCode(
        code="ERR-EMPTY-YAML-DOCUMENT",
        domain="compile",
        title="YAML document is empty",
        message_template="Empty YAML document: {path}.",
        doc=(
            "Fired when a board file's content is empty, contains only comments, "
            "or parses to a null/blank YAML document. A genuinely empty board "
            "file needs at least a minimal board mapping, or should be removed. "
            "A `dct init` scaffold's `meta.yml`/`meta.yaml` given directly to a "
            "verb that expects a board is a different case: don't add a mapping "
            "or delete it. The defaults file is meant to stay empty or "
            "comment-only. Pass a real board file to the verb instead."
        ),
        summary="Fired when a board file's content is empty or contains only comments.",
        docs_topic="errors",
    )
)

ERR_YAML_SYNTAX = REGISTRY.register(
    ErrorCode(
        code="ERR-YAML-SYNTAX",
        domain="compile",
        title="YAML syntax error",
        message_template=(
            "Invalid YAML syntax in {source}{where}: {problem}{quoted_line}"
        ),
        fix_template=(
            "Fix the line quoted above. The usual cause is a string that contains "
            "a colon followed by a space, or starts with a special character "
            "(`*`, `&`, `!`, `%`, `@`, `[`, `{`): wrap the whole value in quotes, "
            'for example `title: "Ten largest occupations in 1950: rank and '
            'workers"`.'
        ),
        doc=(
            "Fired when a board file, or a file it `extends:`, is not valid YAML. "
            "The message names the file, the line and column, "
            "and the offending line. The common authored mistake is an unquoted "
            "colon inside a title or note, such as `title: Largest in 1950: "
            "rank`, which YAML reads as a nested mapping; another is a flow "
            "sequence holding an unquoted `[` (`palette: [category[2]]`; quote "
            'it as `palette: ["category[2]"]`). For the colon the rest of the '
            "report comes from a copy of the file with the line quoted (the "
            "file is not changed and the board still does not render), so the "
            "schema and query errors appear in the same pass; any other syntax "
            "error is the only one reported until it is fixed."
        ),
        summary="Fired when a board file or an `extends:` file is not valid YAML.",
        docs_topic="errors",
    )
)

ERR_NOTHING_TO_RENDER = REGISTRY.register(
    ErrorCode(
        code="ERR-NOTHING-TO-RENDER",
        domain="compile",
        title="None of the paths given is a board",
        message_template="Nothing to render: none of the paths given is a board.",
        doc=(
            "Fired when `dct render` has no board left to render after "
            "skipping the paths that are not boards. Each skipped path gets "
            "its own warning saying why. Pass at least one board file."
        ),
        summary="Fired when none of the paths given to dct render is a board.",
        docs_topic="board",
    )
)

ERR_UNKNOWN_WARNING_CODE = REGISTRY.register(
    ErrorCode(
        code="ERR-UNKNOWN-WARNING-CODE",
        domain="compile",
        title="--ignore-warning names an unregistered code",
        message_template="unknown warning code: {code!r}",
        doc=(
            "Fired when `dct render --ignore-warning` names a code that is not "
            "a registered warning code: usually a typo or a stale suppression. "
            "Check the code against the warning reference."
        ),
        summary="Fired when --ignore-warning names a code that isn't registered.",
        docs_topic="errors",
    )
)

WARN_RETIRED_IGNORE_CODE = REGISTRY.register(
    WarningCode(
        code="WARN-RETIRED-IGNORE-CODE",
        domain="compile",
        title="--ignore-warning names a retired warning code",
        message_template=(
            "--ignore-warning: {code} no longer exists and suppresses nothing"
        ),
        fix_template="Remove it from the ignore list.",
        doc=(
            "Fired when `dct render --ignore-warning` names a warning code an "
            "earlier release shipped and a later one removed. The code can no "
            "longer fire, so the flag is dead weight rather than a typo, and "
            "the render proceeds."
        ),
        summary="Fired when --ignore-warning names a retired warning code.",
        docs_topic="board",
    )
)

ERR_IGNORE_ERROR_CODE = REGISTRY.register(
    ErrorCode(
        code="ERR-IGNORE-ERROR-CODE",
        domain="compile",
        title="--ignore-warning names an error code",
        message_template=(
            "cannot ignore {code!r}: it is an error code, not a warning code"
        ),
        doc=(
            "Fired when `dct render --ignore-warning` names an error code. Only "
            "warnings can be suppressed; fix the board to clear an error."
        ),
        summary="Fired when --ignore-warning names an error code.",
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
            "Unknown palette {name!r} (authored as {authored!r}) at {field_path}. Palette roles are "
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

ERR_PALETTE_REBIND_UNSATISFIED = REGISTRY.register(
    ErrorCode(
        code="ERR-PALETTE-REBIND-UNSATISFIED",
        domain="compile",
        title="A palette role is rebound to a palette of the wrong family",
        message_template=(
            "{field_path} is set to {bound!r}, a {family} palette, but this "
            "role needs a {required} palette."
        ),
        doc=(
            "Fired when a board or project layer rebinds `category`, "
            "`sequence`, or `diverge` under `style.palettes` to a palette of "
            "another family. Themes read `category` as an ordered categorical "
            "stop list and `sequence`/`diverge` as continuous ramps, so each "
            "role accepts one family, on every theme alike. Bind a "
            "categorical palette to `category`, a sequential one to "
            "`sequence`, and a diverging one to `diverge`."
        ),
        summary="Fired when `style.palettes.category`, `sequence`, or `diverge` names a palette of the wrong family.",
        docs_topic="charts",
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

ERR_THEME_AND_EXTENDS = REGISTRY.register(
    ErrorCode(
        code="ERR-THEME-AND-EXTENDS",
        domain="compile",
        title="Board sets both theme: and extends:",
        message_template=(
            "Cannot set both 'theme: {theme}' and 'extends:'; 'theme:' is "
            "shorthand for 'extends:'. Use one key: extends: {combined}"
        ),
        doc=(
            "Fired when a board or board fragment authors both `theme:` and "
            "`extends:`. `theme: X` is sugar for `extends: X`, so the two "
            "would compete for one slot. List the theme first in `extends:`, "
            "lowest priority, ahead of the boards it already extends."
        ),
        summary="Fired when a board authors both `theme:` and `extends:`.",
        docs_topic="board",
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
        title="Format spec is not a predefined name, a style.formats alias, or a valid d3-format/d3-time-format spec",
        message_template=(
            "Unknown {kind_noun} format {spec!r} at {field_path}: {explanation}"
        ),
        doc=(
            "Fired when an authored `format:` string is not one of the engine's "
            "predefined format names (e.g. `currency`, `number`, `percent_number`), "
            "not a key defined in `style.formats`, and fails to parse as a d3-format "
            "spec. Check for typos against the predefined names or your `style.formats` "
            "keys, or use a valid d3-format spec (https://d3js.org/d3-format). "
            "In a slot that accepts a raw strftime spec (`%b %Y`), the same code "
            "also fires for a directive the engine does not implement (`%K`); "
            "the message names the accepted directives."
        ),
        summary="Fired when `format:` is not a predefined name, alias, or valid spec.",
        docs_topic="charts",
        hint_generator=suggest_close_format,
    )
)

ERR_REGIONAL_NESTED_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-REGIONAL-NESTED-MISMATCH",
        domain="compile",
        title="A nested board's regional differs from its root board's",
        message_template=(
            "{board} sets regional {authored}, but the board it is nested in uses "
            "{root}. A board and everything nested in it write numbers and dates "
            "one way; remove the nested `regional:` or make it match."
        ),
        doc=(
            "Fired when a nested board (inline or imported) authors a "
            "`regional:` number locale, time locale or currency that differs "
            "from the root board's. One page reads numbers and dates in one "
            "locale and one currency, so nested boards take the root's `regional:`. Set it on the root board or in "
            "`charts/meta.yml`."
        ),
        summary="Fired when a nested board's `regional:` differs from its root's.",
        docs_topic="charts",
    )
)

ERR_LINKS_NESTED = REGISTRY.register(
    ErrorCode(
        code="ERR-LINKS-NESTED",
        domain="compile",
        title="A nested board authors links",
        message_template=(
            "{board} sets `links:`, but only the root board does. A board and "
            "everything nested in it link and open links one way; move `links:` "
            "to the root board."
        ),
        doc=(
            "Fired when a nested board (inline or imported) authors a `links:` "
            "block. `links.auto` and `links.open` apply to the whole page, so "
            "they are set once, on the root board or in `charts/meta.yml`."
        ),
        summary="Fired when a nested board authors `links:`.",
        docs_topic="charts",
    )
)

ERR_FONT_UNKNOWN = REGISTRY.register(
    ErrorCode(
        code="ERR-FONT-UNKNOWN",
        domain="compile",
        title="Font name is not an entry in style.fonts",
        message_template="{message}",
        doc=(
            "Fired when a font slot or a `style.fonts` entry names a font "
            "(`font: label`, `font: {extends: label}`, `fonts.h2: {extends: "
            "heading}`) that `style.fonts` does not define. The message lists "
            "the known names. Define the font under `style.fonts`, or fix the name."
        ),
        summary="Fired when a font name is not defined in `style.fonts`.",
        docs_topic="charts",
    )
)

ERR_FONT_REGISTRY = REGISTRY.register(
    ErrorCode(
        code="ERR-FONT-REGISTRY",
        domain="compile",
        title="style.fonts entries do not resolve",
        message_template="{message}",
        doc=(
            "Fired when `style.fonts` entries extend one another in a cycle "
            "(`a` extends `b` extends `a`; the message names the path), or "
            "when the `heading` entry inherits a `size` from the font it "
            "extends (`heading` takes the `sizes` ramp, never `size`). Break "
            "the cycle, or extend a font that sets no size."
        ),
        summary="Fired when `style.fonts` entries form a cycle or give `heading` a size.",
        docs_topic="charts",
    )
)

ERR_FORMAT_NATIVE_IN_VEGA_SLOT = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-NATIVE-IN-VEGA-SLOT",
        domain="compile",
        title="Native formatter used in a Vega-rendered format slot",
        # These members hold whole-number values, so a bare d3 percent spec
        # paints them 100x too large: the remedy names the division, never
        # ".1%" on its own.
        message_template=(
            "Format {spec!r} at {field_path} is a native formatter with no Vega "
            "arm and cannot be used in Vega-rendered slots (axis labels, mark value "
            "labels, support_table, heatmap and map "
            "tooltips). Move it to a "
            "table cell (or a KPI value, for the percent "
            "names), or transform the value in the query to match a plain d3 "
            "spec (for a whole-number percent, divide by 100 and then use a d3 "
            'percent spec such as ".1%"). Valid alternatives: '
            "{available}."
        ),
        doc=(
            "Fired when a native name appears in a Vega-rendered format slot "
            "it cannot paint: `percent_number`, `percent_number_delta` and "
            "`percentage_points_delta` anywhere Vega paints (axis label, mark "
            "value-label format), and every native "
            "(`duration` and `bytes` too) in a support_table format or a heatmap "
            "or map tooltip. These names bypass d3 entirely "
            "and are only valid in Python-rendered slots (KPI headline and table "
            "cells). To keep the value in a Vega-rendered slot, transform it in "
            "the query to match a plain d3 spec: for a whole-number percent, "
            "divide by 100 and then use a d3 percent spec such as `.1%`."
        ),
        summary="Python-only native formatter used in a Vega-rendered slot.",
        docs_topic="charts",
    )
)

ERR_FORMAT_KIND_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-KIND-MISMATCH",
        domain="compile",
        title="A redefined format name has the wrong kind",
        message_template=(
            "Format {spec!r} at {field_path} is not a {kind} format. This name "
            "takes only a {kind} spec; the engine's {kind} names are: {available}. "
            "{escape_hatch}"
        ),
        doc=(
            "Fired when `style.formats` redefines an engine format name as a "
            "spec of the other kind: a number name (`currency`, `percent`) "
            "must stay a number spec and a date name (`date_short`) a strftime "
            "spec, because every slot that reads the name expects its kind. A "
            "number spec baked onto a date axis paints garbage tick labels "
            "rather than failing. A plain `format:` slot is judged by the "
            "column it paints and takes either."
        ),
        summary="An engine format name redefined as a spec of the other kind.",
        docs_topic="charts",
        # No hint_generator: only an exact predefined name from the other half
        # reaches this raise, so the input domain is the vocabulary itself and no
        # member of it is a near-miss for the other half. The message names the
        # legal values outright instead.
    )
)

ERR_FORMAT_TEXT_IN_VEGA_SLOT = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-TEXT-IN-VEGA-SLOT",
        domain="compile",
        title="Text format name used in a Vega-rendered format slot",
        message_template=(
            "Format {spec!r} at {field_path} is a text format and cannot be used "
            "in Vega-rendered slots (axis labels, mark value labels, "
            "support_table). Use it on a table column or KPI value; a number or "
            "date slot takes a number or time name or a d3 spec."
        ),
        doc=(
            "Fired when `email` or `url` appears in a slot Vega paints. They "
            "name text a table cell or KPI value links, and have no number or "
            "date spec for Vega to apply."
        ),
        summary="`email` / `url` is not a number or time format for a Vega slot.",
        docs_topic="charts",
    )
)

ERR_FORMAT_TEXT_REDEFINED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-TEXT-REDEFINED",
        domain="compile",
        title="A text format name cannot be redefined",
        message_template=(
            "`{field_path}` redefines the engine's text format {name!r}. "
            "{name!r} is a text format with no spec; it cannot be redefined."
        ),
        doc=(
            "Fired when `style.formats` has a key named `email` or `url`. Those "
            "names format a value as itself and mark the column as an email "
            "address or a web URL, so a table cell or KPI value can link it. "
            "They carry no spec for an alias to replace. Pick another alias "
            "name for your own spec."
        ),
        summary="`style.formats.email` / `style.formats.url` cannot be redefined.",
        docs_topic="charts",
    )
)

ERR_QUERY_FORMAT_SPEC = REGISTRY.register(
    ErrorCode(
        code="ERR-QUERY-FORMAT-SPEC",
        domain="compile",
        title="A query's formats: value is a format spec, not a format name",
        message_template=(
            "Query {query!r} formats.{column} is a format spec ({spec!r}), not a "
            "format name. Name it in style.formats (for example "
            "`{column}_format: {spec!r}`) and use the name here."
        ),
        doc=(
            "Fired when a query's `formats:` value is a d3 or strftime spec "
            "(`$,.0f`, `%b %Y`) instead of a name. A query says what a column "
            "means by naming a format: an engine format (`currency`, `percent`, "
            "`year`, ...) or one the board defines in `style.formats`. The spec "
            "itself belongs in `style.formats`, where one definition restyles "
            "every column that names it."
        ),
        summary="A query's formats: value is a spec, not a format name.",
        docs_topic="queries",
    )
)

ERR_QUERY_FORMAT_UNKNOWN = REGISTRY.register(
    ErrorCode(
        code="ERR-QUERY-FORMAT-UNKNOWN",
        domain="compile",
        title="A query's formats: value names a format the board does not have",
        message_template=(
            "Query {query!r} formats.{column} names {spec!r}, which is neither "
            "an engine format nor defined in style.formats. Available: "
            "{available}"
        ),
        doc=(
            "Fired when a query's `formats:` value is not an engine format name "
            "and not a key in the rendering board's merged `style.formats`. A "
            "query imported into another board is checked against that board's "
            "names, so a shared query naming a format of your own needs it "
            "defined in every board that imports it."
        ),
        summary="A query's formats: value names a format the board lacks.",
        docs_topic="queries",
        hint_generator=suggest_close_format,
    )
)

ERR_FORMAT_AFFIX_TIME_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-AFFIX-TIME-UNSUPPORTED",
        domain="compile",
        title="A FormatConfig prefix/suffix/notation is a number-format feature only",
        message_template=(
            "Format at {field_path} combines a prefix/suffix/notation with "
            "time-format spec {spec!r}. A temporal axis "
            "paints through a date formatter that has no place for an affix. "
            "Drop the prefix/suffix/notation, or move a prefix/suffix to a "
            "number slot, a KPI value, or a table column; notation "
            "never applies to a date."
        ),
        doc=(
            "A prefix, suffix or notation next to a strftime spec ('%b %Y') "
            "is rejected on a temporal axis's format: "
            "it paints through a date formatter, which has no place for an "
            "affix. Author a prefix/suffix on a number slot (`axis_y.labels.format`), "
            "or on a KPI value or table column format, which paint it around "
            "the date (`FY Jan 2026`). `notation` only affects an SI number "
            "spec; it does nothing to a date anywhere, so drop it."
        ),
        summary="FormatConfig affix on a time-format spec.",
        docs_topic="charts",
    )
)

ERR_FORMAT_GRAIN_NAME_NOT_REDEFINED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED",
        domain="compile",
        title="An engine date grain name used as a format without being redefined",
        message_template=(
            "Format {spec!r} at {field_path} names the engine's own date axis "
            'labels. Replace {spec!r} with the strftime spec "{replacement}". '
            "To relabel that grain on every chart instead, redefine it in "
            '`style.formats` (`{spec}: "{replacement}"`).'
        ),
        doc=(
            "`date_year`, `date_quarter`, `date_month` and `date_week` mean the "
            "engine's own labels for a grain, which no single format expresses. "
            "They only take effect once a board redefines one in "
            "`style.formats`; used in a slot without that, the name would "
            "silently print something else, so it is rejected."
        ),
        summary="Engine date grain name used in a slot without being redefined.",
        docs_topic="charts",
    )
)

ERR_FORMAT_SIGN_PLACEMENT_TABLE_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-SIGN-PLACEMENT-TABLE-UNSUPPORTED",
        domain="compile",
        title="A table column keeps the sign with the digits",
        message_template=(
            "Format at {field_path} sets sign_placement, but a table column "
            "paints its prefix in its own lane and keeps the sign with the "
            "digits ('€  −500'). Drop sign_placement from this "
            "column's format."
        ),
        doc=(
            "A table column paints the prefix in a lane of its own, aligned "
            "down the column, and the sign beside the digits (`€  −500`), so "
            "`sign_placement` has nothing to move there and is rejected. Drop "
            "it, or give the column a format without it."
        ),
        summary="sign_placement on a table column.",
        docs_topic="charts",
    )
)

ERR_FORMAT_SIGN_BEFORE_ANCHORED_PREFIX = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-SIGN-BEFORE-ANCHORED-PREFIX",
        domain="compile",
        title="A sign cannot lead a prefix that paints on one value only",
        message_template=(
            "Format at {field_path} sets sign_placement: before_prefix, but its "
            "prefix is anchored on one value: the anchor would read '−EUR 50' "
            "beside a bare '−40'. Drop sign_placement (the anchor then reads "
            "'EUR −50'), or set repeat: every."
        ),
        doc=(
            "Where an authored prefix paints on one value of an axis or "
            "support_table row (`repeat: anchor`, or that surface's default), "
            "the sign follows the prefix (`EUR −50`, `€−50`) so the "
            "anchor's digits line up with the bare `−40` beside it. "
            "`sign_placement: before_prefix` there is rejected; drop it, or set "
            "`repeat: every` to put the prefix on every value."
        ),
        summary="sign_placement: before_prefix on an anchored prefix.",
        docs_topic="charts",
    )
)

ERR_GEOSHAPE_SHADING_WITHOUT_LOOKUP = REGISTRY.register(
    ErrorCode(
        code="ERR-GEOSHAPE-SHADING-WITHOUT-LOOKUP",
        domain="compile",
        title="A geoshape shades from a column but has no lookup",
        message_template=(
            "Geoshape shades from column {value_field!r}, but {field_path} is "
            "not set, so nothing joins the data to the map. Set `lookup:` to "
            "the data column that matches the geo source's {geo_join_key!r} key."
        ),
        doc=(
            "A choropleth shades each region by joining a data column to the "
            "geo source's key property. `value:` (or a data-driven `color:`) "
            "without `lookup:` has nothing to join on and would render an "
            "unshaded base map. Set `lookup:` to the column holding the region "
            "identifiers, or drop `value:`/`color:` to draw a plain base map."
        ),
        summary="Geoshape `value:`/`color:` set without `lookup:`.",
        docs_topic="charts",
    )
)

ERR_GEO_PROJECTION_REQUIRED = REGISTRY.register(
    ErrorCode(
        code="ERR-GEO-PROJECTION-REQUIRED",
        domain="compile",
        title="A map has no projection and no geo source to take one from",
        message_template=(
            "Chart {chart_id!r} is a {chart_type} with no {field_path}, and "
            "its geo source (if any) sets none. Set `projection:` (for example "
            "`projection: mercator`) or set `geo_source:` to a source that has "
            "one. See {maps_docs_url} for the available projections."
        ),
        doc=(
            "A map is drawn through a projection, and dbt Charts picks none on "
            "its own. A built-in `geo_source` (us-states, world-countries, ...) "
            "carries the projection suited to its region; a chart with no such "
            "source, such as a point_map without a basemap or a geoshape read "
            "from a raw URL, must say which projection to use. Set "
            "`projection:` to one of Vega's projection names, or to an object "
            "with `type` plus parameters like `center` and `scale`."
        ),
        summary="Map chart without `projection:` and without a built-in `geo_source`.",
        docs_topic="charts",
    )
)

ERR_LABELS_LAST_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-LABELS-LAST-UNSUPPORTED",
        domain="compile",
        title="`labels.visible: last` needs the chart's own series",
        message_template=(
            "Chart {chart_id!r} sets {field_path} to `last`, which labels "
            "each series' most recent mark and is not supported on {where}. "
            "Use `true` to label every mark."
        ),
        doc=(
            "`visible: last` on point, line or bar value labels prints each "
            "series' most recent mark. A bar chart has a most recent bar "
            "only on a time axis; a categorical axis has none. A scatter "
            "chart has no ordered series and a layer has no series of its "
            "own. Set `visible: true` or remove the key."
        ),
        summary="`labels.visible: last` on a scatter chart, a layer, or a categorical bar axis.",
        docs_topic="charts",
    )
)

ERR_BAR_ZERO_BASELINE_CROPPED = REGISTRY.register(
    ErrorCode(
        code="ERR-BAR-ZERO-BASELINE-CROPPED",
        domain="compile",
        title="A bar chart cannot drop its zero baseline",
        message_template=(
            "Chart {chart_id!r} sets {field_path} to false, but a bar's length "
            "is measured from zero, so a bar cropped away from it misstates "
            "the quantity (and paints nothing). Remove axis_y.scale.continuous."
            "zero, or use `type: line` to zoom into a range."
        ),
        doc=(
            "A bar encodes its value as length from the zero baseline, so "
            "`style.axis_y.scale.continuous.zero: false` on a bar chart would "
            "crop the bars and misstate the values. Remove the key to keep "
            "the zero baseline, or use `type: line` for a zoomed range. A "
            "floating bar (`y_start:`) has no zero baseline and is exempt."
        ),
        summary="`zero: false` on a bar chart without `y_start`.",
        docs_topic="charts",
    )
)

ERR_PIE_TOTAL_WITHOUT_HOLE = REGISTRY.register(
    ErrorCode(
        code="ERR-PIE-TOTAL-WITHOUT-HOLE",
        domain="compile",
        title="A pie total needs a donut hole to sit in",
        message_template=(
            "{field_path} is set, but this pie has no hole, so the total would "
            "print over the wedges. Set `style.inner_radius` above 0 to make it "
            "a donut, or remove `total:`."
        ),
        doc=(
            "`total:` draws the sum of the slice values at the center of the "
            "chart, which is the hole of a donut. A solid pie has no hole, so "
            "the text would overlap the wedges. Set `style.inner_radius` above "
            "0, use `type: donut`, or remove `total:` (`total.visible: false` "
            "is accepted on a solid pie)."
        ),
        summary="Pie `total:` authored on a chart with no hole.",
        docs_topic="charts",
    )
)

ERR_FORMAT_ALIAS_CYCLE = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-ALIAS-CYCLE",
        domain="compile",
        title="style.formats aliases point at each other in a circle",
        message_template=(
            "style.formats.{name} leads into a circle: {chain}. Give one of "
            "them a d3 spec of its own."
        ),
        doc=(
            "A `style.formats` entry whose target is another entry's name "
            "(`currency: currency_whole`) resolves through that entry. When the "
            "entries lead around a circle, no format is ever reached. Break the "
            "circle by giving one entry a literal d3 spec."
        ),
        summary="style.formats aliases form a cycle.",
        docs_topic="charts",
    )
)

ERR_FORMAT_AFFIX_NOMINAL_AXIS_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-AFFIX-NOMINAL-AXIS-UNSUPPORTED",
        domain="compile",
        title="A FormatConfig prefix/suffix/notation cannot reach a categorical axis",
        message_template=(
            "{field_path} authors a prefix/suffix/notation, but the "
            "{axis} channel resolves to a {channel_type} scale, whose tick "
            "labels paint from the plain format string. Drop the "
            "prefix/suffix/notation, or author a plain spec."
        ),
        doc=(
            "An affix applies to a quantitative axis only. A categorical "
            "(nominal or ordinal) axis paints its tick labels from the plain "
            "format string, so a prefix, suffix or notation there cannot "
            "paint. Remove it, or author the affix on the measure axis."
        ),
        summary="FormatConfig affix authored on a nominal/ordinal axis.",
        docs_topic="charts",
    )
)

ERR_FORMAT_TIME_DIRECTIVE_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-FORMAT-TIME-DIRECTIVE-UNSUPPORTED",
        domain="compile",
        title="strftime directive in a Vega-rendered time format cannot be measured",
        message_template=(
            "Format {spec!r} at {field_path} uses {directives}, which dbt "
            "Charts cannot render identically to what Vega paints on the "
            "axis: either it is not part of d3-time-format's directive set "
            "at all (Vega paints the literal letter), or Python renders it "
            "through the host's C locale or local timezone rather than "
            "d3's UTC/en-US output. This slot is baked onto a Vega-painted "
            "axis, whose label width is also measured from this same spec -- "
            "a mismatch here is a wrong render that looks right until the "
            "label overlaps its neighbor. Supported directives: {available}."
        ),
        doc=(
            "Fired when an authored Vega-painted "
            "time slot (an axis `labels.format`) uses a strftime directive dbt Charts "
            "cannot render byte-identical to what Vega's `utcFormat` "
            "paints. `%C`/`%k`/`%l`/`%z` and most other letters are not "
            "d3-time-format grammar at all -- Vega paints the literal "
            "letter, not a computed value. `%Z` is real d3 grammar "
            "too but empty on "
            "a naive datetime, unlike d3's `+0000`-style offset. `%s` is "
            "real d3 grammar but Python's value follows the host's local "
            "timezone rather than a true UTC epoch count; `%Q` (epoch "
            "milliseconds) is the supported equivalent. A format string "
            "outside a Vega-painted slot (a table column, the footer "
            "timestamp) is unaffected -- those render through Python "
            "directly, so no measure-vs-paint divergence is possible "
            "there."
        ),
        summary="A time-format directive Vega paints differently than dbt Charts measures.",
        docs_topic="charts",
    )
)

# ──────────────────────────────── Warning codes ───────────────────────────────
# Compile-time authoring warnings declared here in the leaf so the registry
# is complete on import of core.diagnostics. Emitters import their
# constants back rather than declaring codes in-module.

WARN_DEFAULTS_FILE_GIVEN_AS_BOARD = REGISTRY.register(
    WarningCode(
        code="WARN-DEFAULTS-FILE-GIVEN-AS-BOARD",
        domain="compile",
        title="A defaults file was given to dct render",
        message_template="{path} holds defaults for the boards beside it; skipped.",
        fix_template=(
            "Nothing to fix when a glob picked it up. To see its effect, "
            "render a board in the same directory."
        ),
        doc=(
            "Fired when a path given to `dct render` is a meta.yml or "
            "meta.yaml file. That file holds defaults the boards in its "
            "directory inherit, and has nothing of its own to draw. A glob "
            "like `charts/*.yml` picks it up alongside the boards, so it is "
            "skipped and the boards render."
        ),
        summary="Fired when a meta.yml defaults file is given to dct render.",
        docs_topic="board",
    )
)

WARN_SCHEMA_MIGRATED = REGISTRY.register(
    WarningCode(
        code="WARN-SCHEMA-MIGRATED",
        domain="compile",
        title="Board YAML uses retired syntax",
        message_template="{message}",
        fix_template=(
            "Run `dct migrate` to rewrite the file, or update the YAML by hand "
            "where the message says `dct migrate` cannot."
        ),
        doc=(
            "Fired when a board, meta.yml, or `extends:` file uses syntax from "
            "an older dbt charts release. The board is migrated in memory and "
            "still renders, but the file on disk is stale. When migration could "
            "not finish, the message says so, and errors reported alongside it "
            "may name retired syntax."
        ),
        summary="Fired when a board uses retired syntax that was migrated in memory.",
        docs_topic="board",
    )
)

WARN_UNREFERENCED_QUERY = REGISTRY.register(
    WarningCode(
        code="WARN-UNREFERENCED-QUERY",
        domain="compile",
        redundant=True,
        title="Query is defined but nothing reads it",
        message_template=(
            "Query `{query}` is not used by any chart, variable, text or query; "
            "remove it."
        ),
        fix_template="Delete the query, or reference it from a chart or variable.",
        doc=(
            "Fires at compile time for a query defined in the board tree that no "
            "chart (or layer), variable option list or `enabled`/`visible` probe, "
            "board title or text, or other query's `{{ queries.<name> }}` reads. "
            "A chart-focused board is judged against the unfocused one. Emitted by "
            "`compile/validate/board_warnings.py`."
        ),
        summary="Fired when a query is defined but no chart, variable, text or query reads it.",
        docs_topic="queries",
    )
)

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

WARN_INHERITED_LAYOUT_REPLACED = REGISTRY.register(
    WarningCode(
        code="WARN-INHERITED-LAYOUT-REPLACED",
        domain="compile",
        title="A board's own layout or tags replace the inherited ones",
        message_template=(
            "`{field}:` {replacer} replaces {inherited} inherited from {source}."
        ),
        fix_template=(
            "To keep the inherited items, move the shared parts into a "
            "partial and import it from both boards."
        ),
        doc=(
            "Fired when a `meta.yml`, an extended board or the board itself sets "
            "`rows:`, `cols:`, `grid:`, `tabs:` or `tags:` over a non-empty "
            "value a lower layer set. The closer value replaces it; nothing "
            "is appended."
        ),
        summary="Fired when a board restates a layout or tag list that a meta.yml or extended board already sets.",
        docs_topic="layout",
    )
)

WARN_RESTATED_STYLE = REGISTRY.register(
    WarningCode(
        code="WARN-RESTATED-STYLE",
        domain="compile",
        redundant=True,
        title="Authored style equals the value it inherits",
        message_template="`{path}: {value}` is already {source}; remove it.",
        fix_template=(
            "Delete `{path}`. Keep a key only to override a different "
            "inherited or inferred value."
        ),
        doc=(
            "Fires when an authored style value equals the value the same key resolves to "
            "without it. That value comes from the cascade: shipped defaults, theme, "
            "`extends:`, meta.yml, parent boards, or the board's style for the chart "
            "type. Comparison is exact, leaf by leaf, on unresolved values, so `8` "
            "against `8.5`, or a color token against its hex, never warns. Emitted by "
            "`compile/restated.py`."
        ),
        docs_topic="board",
    )
)

WARN_RESTATED_INFERRED = REGISTRY.register(
    WarningCode(
        code="WARN-RESTATED-INFERRED",
        domain="compile",
        redundant=True,
        title="Authored value equals the one the engine infers",
        message_template="`{path}: {value}` is already {source}; remove it.",
        fix_template=(
            "Delete `{path}`. Keep a key only to override a different "
            "inherited or inferred value."
        ),
        doc=(
            "Fires when an authored value equals what the engine would infer without it: "
            "a variable's input type, label or schema default, a table header derived "
            "from the column name, a `source:` the enclosing board already sets, or an "
            "`options.static` list that is every value of the column the variable filters. "
            "Emitted by `compile/restated.py`."
        ),
        summary="Fired when an authored value equals what the engine would infer without it.",
        docs_topic="board",
    )
)

WARN_RESTATED_FORMAT = REGISTRY.register(
    WarningCode(
        code="WARN-RESTATED-FORMAT",
        domain="compile",
        redundant=True,
        title="Authored format equals the query's declared format",
        message_template="`{path}: {value}` is already {source}; remove it.",
        fix_template=(
            "Delete `{path}`. Keep a key only to override a different "
            "inherited or inferred value."
        ),
        doc=(
            "Fires when a chart or table column names the same format its query already "
            "declares for that column in `formats:`. Emitted by `compile/restated.py`."
        ),
        docs_topic="board",
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

WARN_LABEL_TITLE_CASE = REGISTRY.register(
    WarningCode(
        code="WARN-LABEL-TITLE-CASE",
        domain="compile",
        title="Authored label is in Title Case",
        message_template=(
            "{text!r} capitalizes its words like a heading; labels print as written, "
            "so it will show in Title Case."
        ),
        fix_template=(
            "Write the label in sentence case, keeping proper nouns and acronyms "
            "capitalized. Leave it as is if the capitals are proper nouns."
        ),
        doc=(
            "Fires when an authored `label`, `x_label`, `y_label` or `field_labels` "
            "entry is shaped like a headline: its second word is a plain capitalized "
            "word, every later word that is not a small word (`by`, `of`, `per`, ...) "
            "is one too, and at least two words after the first are capitalized "
            "(`Avg Order Value`, `Revenue By Region`). `Sales in North America` and "
            "`Griddle Royale launched in March` do not fire. Subtitles and query "
            "data are never checked. Headings are title-cased for you; every other "
            "label is sentence case, and an authored label prints exactly as typed. "
            "Acronyms (`ARR`) and mixed-case words (`iOS`) are not plain capitalized "
            "words. Emitted by `compile/validate/authoring_warnings.py`."
        ),
        summary="Fired when an authored label is shaped like a headline in Title Case.",
        docs_topic="styling",
    )
)

WARN_HEADING_TRAILING_PERIOD = REGISTRY.register(
    WarningCode(
        code="WARN-HEADING-TRAILING-PERIOD",
        domain="compile",
        title="Heading ends with a period",
        message_template="Title {text!r} ends with a period; headings do not take one.",
        fix_template="Delete the trailing period from the title.",
        doc=(
            "Fires when a board, section, tab, or chart `title:` ends with a period; "
            "axis and legend titles are labels and are not checked. "
            "Headings are title-cased and carry no closing punctuation; the engine "
            "never strips an authored period for you. An ellipsis does not count. "
            "Emitted by `compile/validate/authoring_warnings.py`."
        ),
        docs_topic="styling",
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
            "digit shares one advance, which a proportional font does not give, "
            "so the column would misalign silently. A quantitative axis takes "
            "its label font from the `numeral` slot (style.fonts.numeral), any "
            "other axis from the `label` slot (style.fonts.label); one of those "
            "slots, or an axis override (style.axis_y.labels.font.family on this "
            "chart, style.charts.axis_quantitative.labels.font.family on the "
            "board), carries this family."
        ),
        fix_template=(
            "Put the body typeface on `style.fonts.body` (and "
            "`style.fonts.heading`), and leave `style.fonts.label` and "
            "`style.fonts.numeral` on a tabular family (e.g. 'dbt Sans "
            "Tabular'). To keep a different family on the axis itself, set a "
            "tabular one on `style.axis_y.labels.font.family` for this chart, or "
            "on `style.charts.axis_quantitative.labels.font.family` to cover "
            "every chart."
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
            "{value_count} values for `{field}` need a palette swatch, but the "
            "board's categorical palette has {swatch_count}. Pick a wider "
            "palette, or pin the extra values in "
            "`style.charts.category_colors.{field}` to a color outside it "
            "(a variant like `category[3].light`, or a hex)."
        ),
        doc=(
            "Fired when an authored `style.charts.category_colors.<field>` "
            "field has more values needing a palette swatch (unpinned values "
            "plus pins naming a palette color) than the board's categorical "
            "palette has. Pins to a color outside the palette (a variant or "
            "a hex) need no swatch. Two categories must never share a swatch, "
            "so an authored field raises instead of silently declining to "
            "bind (an unauthored field just declines). Pick a wider palette, "
            "or pin the extra values to colors outside it."
        ),
        summary="Fired when an authored category_colors field outgrows the palette's swatch count.",
        docs_topic="charts",
    )
)

WARN_AREA_ZERO_BASELINE_CROPPED = REGISTRY.register(
    WarningCode(
        code="WARN-AREA-ZERO-BASELINE-CROPPED",
        domain="compile",
        title="An area chart's zero baseline is cropped",
        message_template=(
            "Chart {chart_id!r} sets axis_y.scale.continuous.zero to false, so "
            "the area's fill starts above zero and overstates the change."
        ),
        fix_template=(
            "Remove axis_y.scale.continuous.zero to keep the zero baseline, or "
            "use `type: line` to zoom into a range. If the crop is deliberate, "
            "add WARN-AREA-ZERO-BASELINE-CROPPED to the chart's warnings_ignore."
        ),
        summary="Fired when an area chart authors `zero: false` on its y axis.",
        doc=(
            "An area's fill encodes magnitude from the zero baseline, so "
            "`style.axis_y.scale.continuous.zero: false` shows a cropped fill "
            "that overstates change. A zoomed area is sometimes deliberate, so "
            "this warns instead of failing and can be suppressed with "
            "`warnings_ignore`."
        ),
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

WARN_FORMAT_UNUSED_NAME = REGISTRY.register(
    WarningCode(
        code="WARN-FORMAT-UNUSED-NAME",
        domain="compile",
        title="style.formats name is defined but nothing uses it",
        message_template=(
            "`style.formats.{name}` is defined but nothing uses it.{did_you_mean} "
            "To restyle an engine format, redefine its name (`number`, `currency`, "
            "`date_short`, ...)."
        ),
        fix_template=(
            "Name it in a `format:` slot, redefine an engine name instead, or "
            "delete it."
        ),
        summary="A style.formats name is defined in a board file that nothing uses.",
        doc=(
            "Fires at compile time for a name this board file's own "
            "`style.formats` defines that no format slot on the board references "
            "and that is not an engine format name. The engine's names are the "
            "keys that restyle every chart (`number`, `currency`, `percent`, "
            "`date_short`, ...). A name inherited from `meta.yml` or `extends` "
            "never warns here, since a sibling board may use it. Emitted by "
            "`compile/validate/board_warnings.py`."
        ),
        docs_topic="charts",
    )
)

ERR_LINK_SCHEME_UNSAFE = REGISTRY.register(
    ErrorCode(
        code="ERR-LINK-SCHEME-UNSAFE",
        domain="compile",
        title="Link uses a URL scheme dbt Charts does not allow",
        message_template=(
            "Link {link!r} at {field_path} uses scheme {scheme!r}. Use a "
            "board path (/, ./, ?, #) or http, https, mailto."
        ),
        doc=(
            "Fired when an authored link (chart link, table column link/"
            "header_link, or footer link) resolves to a URL scheme other "
            "than http, https, or mailto. A board path (starting with /, "
            "./, ../, ?, or #) is always allowed; any other scheme — "
            "javascript:, data:, vbscript:, or an editor deeplink like "
            "vscode:// — is rejected so a link can never execute script or "
            "navigate somewhere unexpected."
        ),
        summary="Fired when a link resolves to a disallowed URL scheme.",
        docs_topic="charts",
    )
)

ERR_LINK_SCHEME_UNANCHORED = REGISTRY.register(
    ErrorCode(
        code="ERR-LINK-SCHEME-UNANCHORED",
        domain="compile",
        title="Link template starts with data, so its scheme is unknown",
        message_template=(
            "Link {link!r} at {field_path} begins with a template. Start it "
            "with /, ?, # or https:// so data cannot choose the scheme."
        ),
        doc=(
            "Fired when a chart or footer link's literal text before its "
            "first `{{` template placeholder does not itself fix a safe "
            "scheme — the query row or variable value filling the template "
            "would then decide what scheme the browser navigates to. Start "
            "the link with a board path or a fixed https:// prefix instead."
        ),
        summary="Fired when a link template's scheme is decided by data.",
        docs_topic="charts",
    )
)

ERR_VARIABLE_NAME_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-VARIABLE-NAME-INVALID",
        domain="compile",
        title="Variable name is not an identifier",
        message_template=(
            "Variable {name!r} must be a valid identifier: letters, digits "
            "and underscores, not starting with a digit."
        ),
        doc=(
            "Fired when a name under `variables:` is not a valid identifier. "
            "Variables are referenced as bare Jinja names in queries and "
            "templates, so a name with a space, hyphen, or leading digit "
            "never actually worked — this catches it at compile instead of "
            "a confusing render-time failure."
        ),
        summary="Fired when a variable name is not a valid identifier.",
        docs_topic="variables",
    )
)

ERR_COLOR_TOKEN = REGISTRY.register(
    ErrorCode(
        code="ERR-COLOR-TOKEN",
        domain="compile",
        title="Style color token does not resolve",
        message_template="Color token in {scope} does not resolve: {reason}",
        doc=(
            "Fired when a color in a `style:` block is a palette token "
            "(`palette.slot`, `role.alias`, `role[N]`) that no palette or "
            "theme role resolves: a misspelled palette, an alias or slot the "
            "palette lacks, or a role the theme does not bind. A bad token in "
            "a chart's own style names the chart, and other charts on the "
            "board render normally; one in board style names the field."
        ),
        summary="Fired when a style color token matches no palette or theme role.",
        docs_topic="charts",
    )
)

ERR_STYLE_CSS_VALUE_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-STYLE-CSS-VALUE-INVALID",
        domain="compile",
        title="A style value for markdown text is not valid CSS",
        summary="A style value written into CSS contains characters that break out of it.",
        message_template="Invalid style value for markdown text: {detail}.",
        doc=(
            "Fired when a font color, weight, style or decoration that styles "
            "markdown text (prose, callouts, table cells) contains a character "
            "that could close the CSS declaration or open a new rule: one of "
            "`<>;{}\"'\\*` or a control character. Use a plain CSS value such "
            "as a hex color, `oklch(...)`, `var(--x)`, or `bold`."
        ),
        docs_topic="charts",
    )
)

ERR_CUSTOM_TEMPLATE = REGISTRY.register(
    ErrorCode(
        code="ERR-CUSTOM-TEMPLATE",
        domain="compile",
        title="Custom chart template is missing or invalid",
        message_template="Chart {chart_id!r} (custom): {reason}",
        doc=(
            "Fired when a `type: custom` chart's template cannot be loaded: "
            "`template:` names no `templates/<name>.svg.j2` file in the project, "
            "the board has no project to read it from, the `{#--- ... ---#}` "
            "header is not valid YAML or declares an unknown param type, or the "
            "Jinja source has a syntax error."
        ),
        summary="Fired when a custom chart's template is missing, unparseable, or has a bad header.",
        docs_topic="charts",
    )
)

ERR_CUSTOM_FIELDS = REGISTRY.register(
    ErrorCode(
        code="ERR-CUSTOM-FIELDS",
        domain="compile",
        title="Custom chart channels or options do not match the template",
        message_template="Chart {chart_id!r} (custom): {reason}",
        doc=(
            "Fired when a `type: custom` chart's channels (`x`, `y`, `color`, ...) "
            "or `options:` disagree with what its template declares in its "
            "header: an undeclared channel or option, a required one left out, "
            "a list on a channel without `multiple: true`, a value of the wrong "
            "type, or a color option that is not a color. A template with no "
            "header accepts no channels and no options."
        ),
        summary="Fired when a custom chart's channels or options are undeclared, missing, or the wrong type.",
        docs_topic="charts",
    )
)

ERR_MARKDOWN_NOT_BOARD = REGISTRY.register(
    ErrorCode(
        code="ERR-MARKDOWN-NOT-BOARD",
        domain="compile",
        title="Markdown file is not a board",
        message_template=(
            "Not a dbt Charts board file: markdown files outside charts/ must "
            "declare a top-level 'board:' key in their YAML frontmatter"
        ),
        doc=(
            "Fired when a markdown file outside `charts/` is compiled as a board "
            "but its frontmatter has no top-level `board:` key. Under `charts/` "
            "every markdown file is a board by path convention; elsewhere the "
            "`board:` key opts a page in. Add a `board:` block to the frontmatter "
            "or move the file under `charts/`."
        ),
        docs_topic="board",
    )
)

ERR_MARKDOWN_PARSE = REGISTRY.register(
    ErrorCode(
        code="ERR-MARKDOWN-PARSE",
        domain="compile",
        title="Markdown board cannot be parsed",
        message_template="Markdown board cannot be parsed: {detail}",
        doc=(
            "Fired when a markdown board's frontmatter or body cannot be turned "
            "into a board: the opening `---` has no closing delimiter, the "
            "frontmatter is not a YAML mapping, or `board:` is not a mapping. "
            "YAML syntax errors inside the frontmatter report ERR-YAML-SYNTAX "
            "instead."
        ),
        docs_topic="board",
    )
)
