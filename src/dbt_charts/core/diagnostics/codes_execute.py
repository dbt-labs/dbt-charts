"""ERR-* error codes for the execute layer.

Covers source-resolution and runtime failures in AdapterRegistry.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.hints import (
    suggest_close_column,
    suggest_close_ref,
    suggest_close_source_table,
)
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode, WarningCode

ERR_GLOB_EMPTY = REGISTRY.register(
    ErrorCode(
        code="ERR-GLOB-EMPTY",
        domain="execute",
        title="Glob pattern in file source matched no files",
        message_template=(
            "File source {source_name!r}, table {table_name!r}: "
            "glob pattern {pattern!r} matched no files. "
            "Provide at least one matching file or fix the pattern."
        ),
        doc=(
            "Fired when a glob pattern in a file source's `files:` mapping expands "
            "to zero files. Every glob must match at least one file; an empty match "
            "is always a configuration error. Check the pattern for typos, verify the "
            "files exist at the expected paths, and confirm the path is relative to "
            "the project root."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_NOT_FOUND = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-NOT-FOUND",
        domain="execute",
        title="File source path could not be read from disk",
        message_template=(
            "File source {source_name!r}: {relpath!r} could not be read "
            "({detail}). Restore the missing file, fix the path, or fix "
            "its permissions."
        ),
        summary=(
            "Fired when a file source names a path that cannot be opened "
            "on disk at query-execution time — missing, a path component "
            "that isn't a directory, or unreadable."
        ),
        doc=(
            "Fired when a `type: csv`/`json`/`parquet` source's `files:` "
            "mapping, or a query's inline `source: <path>` ref, names a "
            "literal (non-glob) path that cannot be opened "
            "on disk: the leaf is missing, a path component traverses "
            "through an existing file instead of a directory, or the OS "
            "denies read access. `{detail}` carries the OS error string "
            '(e.g. "No such file or directory", "Not a directory", '
            '"Permission denied") so the message doesn\'t call a '
            "permissions problem a missing file. Unlike an empty glob "
            "match (`ERR-GLOB-EMPTY`), a literal path is never expanded, "
            "so this is the only place these failures surface. Compile "
            "probes an inline ref's two candidate locations (board "
            "directory, project root) only to choose between them; a "
            "path that exists at neither, like a `files:` path, fails "
            "here at query-execution time (`dct render`/`dct serve`), "
            "one chart at a time. Fix the path, add the missing file, or "
            "fix its permissions."
        ),
        docs_topic="queries",
    )
)

ERR_GLOB_TOO_MANY = REGISTRY.register(
    ErrorCode(
        code="ERR-GLOB-TOO-MANY",
        domain="execute",
        title="Glob pattern in file source exceeded the file-count cap",
        message_template=(
            "File source {source_name!r}, table {table_name!r}: "
            "glob pattern {pattern!r} matched {count} files, "
            "exceeding the {cap}-file cap. "
            "Increase execution.max_glob_file_count in dbt_charts.yml if needed."
        ),
        summary=(
            "Fired when a glob pattern in a file source matches more files "
            "than the configured cap."
        ),
        doc=(
            "Fired when a glob pattern in a file source's `files:` mapping matches "
            "more files than the configured `execution.max_glob_file_count` limit. "
            "Narrow the pattern, or raise the cap in `dbt_charts.yml` under "
            "`execution: max_glob_file_count: <N>`."
        ),
        docs_topic="queries",
    )
)

ERR_GLOB_SCHEMA_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-GLOB-SCHEMA-MISMATCH",
        domain="execute",
        title="Glob-matched files have inconsistent column schemas",
        message_template=(
            "File source {source_name!r}, table {table_name!r}: "
            "{path!r} disagrees with {first_path!r} on column names or types. "
            "{detail}"
            "All files matched by a glob must share the same column names and "
            "types."
        ),
        summary=(
            "Fired when files matched by a glob pattern disagree on column "
            "names or types."
        ),
        doc=(
            "Fired when a glob pattern in a file source's `files:` mapping expands "
            "to files whose column schemas disagree — either a differing set of "
            "column names, or the same column carrying a different type in one "
            "file than another. Align the schemas across all matched files, or "
            "split the source into separate entries with non-overlapping patterns."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_TOO_MANY_TABLES = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-TOO-MANY-TABLES",
        domain="execute",
        title="File source exceeded the table-count cap",
        message_template=(
            "File source {source_name!r}: `files:` has {count} tables, "
            "exceeding the {cap}-table cap. "
            "Split the source, raise execution.file_source_max_tables in "
            "dbt_charts.yml, or use a database connection for large-scale data."
        ),
        summary=(
            "Fired when a file source's `files:` mapping declares more tables "
            "than the configured cap."
        ),
        doc=(
            "Fired when a file source's `files:` mapping declares more table "
            "entries than the configured `execution.file_source_max_tables` "
            "limit. A wide `files:` map can quietly fill the shared cache disk, "
            "split the source into multiple sources, raise the cap in "
            "`dbt_charts.yml` under `execution: file_source_max_tables: <N>`, "
            "or use a database connection for large-scale data."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_TOO_LARGE = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-TOO-LARGE",
        domain="execute",
        title="File source relation exceeded the uncompressed-size cap",
        message_template=(
            "File source {source_name!r}, table {table_name!r}: reading "
            "{relpath!r} ({raw_mb:.1f} MB on disk) took this table to "
            "{size_mb:.1f} MB uncompressed, exceeding the {cap_mb:.1f} MB cap. "
            "Load less into this table — fewer files, fewer columns, or a "
            "pre-aggregated extract — or use a database connection for data "
            "this size."
        ),
        summary=(
            "Fired when a file source's uncompressed size exceeds the "
            "effective byte cap."
        ),
        doc=(
            "Fired when the uncompressed size of a file-source relation (the "
            "file(s) backing one `files:` table entry) exceeds the effective "
            "`execution.file_source_max_bytes` limit. Every format is "
            "measured on the same basis: the file's own bytes for CSV and "
            "JSON, and for Parquet the uncompressed total its footer records, "
            "so choosing the compact format is never what gets a relation "
            "rejected. The message names no config key deliberately — the "
            "effective limit is the lower of the project's own "
            "`execution.file_source_max_bytes` and any deployment ceiling "
            "(`DCT_FILE_SOURCE_MAX_BYTES_CEILING`), so where a ceiling is "
            "what fired, raising the project setting does nothing. Lower the "
            "data volume, or use a database connection for data this size."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_DECIMAL_TOO_WIDE = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-DECIMAL-TOO-WIDE",
        domain="execute",
        title="File source decimal column exceeds precision 38",
        message_template=(
            "File source {source_name!r}, table {table_name!r}: column "
            "{column!r} in {relpath!r} is {column_type}, and at least one value "
            "needs more than the 38 significant digits the query engine's "
            "DECIMAL can hold. Re-export the column at precision 38 or less "
            "(BigQuery: CAST(col AS NUMERIC) instead of BIGNUMERIC), or split "
            "the digits across columns."
        ),
        summary=(
            "Fired when a file's decimal column holds a value wider than the "
            "38-digit maximum the query engine supports."
        ),
        doc=(
            "Fired when a decimal column in a file source holds a value needing "
            "more than 38 significant digits. Parquet's decimal type allows more "
            "precision than the query engine's DECIMAL, and BigQuery BIGNUMERIC "
            "exports routinely use it. Re-export the column at precision 38 or "
            "less, or split the digits across columns."
        ),
        docs_topic="queries",
    )
)

ERR_FILE_SOURCE_UNSUPPORTED_TYPE = REGISTRY.register(
    ErrorCode(
        code="ERR-FILE-SOURCE-UNSUPPORTED-TYPE",
        domain="execute",
        title="File source column type is not supported by the query engine",
        message_template=(
            "File table {table_name!r}: the query engine cannot load one of its "
            "column types. {detail} Re-export the file with the column cast to "
            "a standard SQL type."
        ),
        summary=(
            "Fired when a file's column type cannot be loaded into the query engine."
        ),
        doc=(
            "Fired when a file carries a column type the query engine cannot "
            "represent. dbt charts converts the cases it can (a half-precision "
            "float widens, a 256-bit decimal narrows) and reports this for the "
            "rest. Re-export the file with the column cast to a standard SQL "
            "type."
        ),
        docs_topic="queries",
    )
)

ERR_REPO_FILE_TOO_LARGE = REGISTRY.register(
    ErrorCode(
        code="ERR-REPO-FILE-TOO-LARGE",
        domain="execute",
        title="Repository file exceeds the per-file size limit",
        message_template=(
            "Repo file too large: {file_path} is at or over the "
            "{limit_mb:.0f} MB per-file limit in a connected repo. "
            "Use a database connection for data this size."
        ),
        doc=(
            "Fired when a file in a connected repository is at or over the "
            "deployment's per-file size limit. dbt charts imposes this limit "
            "because large files are better served by a direct database "
            "connection rather than loading the entire file into memory."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_NOT_FOUND_EMPTY = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-NOT-FOUND-EMPTY",
        domain="execute",
        title="No source profiles are configured",
        message_template=(
            "Source {source!r} not found. No source profiles are configured. "
            "Declare sources under `sources:` in your dbt_charts.yml."
        ),
        doc=(
            "Fired when a query references a source but no source profiles are "
            "configured at all. Declare sources under `sources:` in your "
            "dbt_charts.yml."
        ),
        docs_topic="queries",
    )
)

ERR_NO_DEFAULT_SOURCE = REGISTRY.register(
    ErrorCode(
        code="ERR-NO-DEFAULT-SOURCE",
        domain="execute",
        title="Source name required but none specified",
        message_template=(
            "Source name required: {available}. Name a source for the query."
        ),
        doc=(
            "Fired when a query reaches execution without a source name and there "
            "is no default source configured. Specify a source name on the query "
            "or configure a default source."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_CROSS_FILE_FORBIDDEN = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-CROSS-FILE-FORBIDDEN",
        domain="execute",
        title="Cross-file source reference is not allowed",
        message_template=(
            "Cross-file source reference "
            "(`#` anchor form) is not allowed: {offending_value!r}."
        ),
        doc=(
            "Fired when a source reference uses the YAML anchor cross-file form "
            "(`#`). Cross-file source references are not allowed; use a named "
            "source from the project allowlist instead."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_INVALID_TYPE = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-INVALID-TYPE",
        domain="execute",
        title="Unknown source type",
        message_template=(
            "Unknown source type {offending_value!r}. Valid types: {available}."
        ),
        doc=(
            "Fired when a source's `type:` field names a source type that is not "
            "registered. Check for typos and refer to the documentation for the "
            "supported source types."
        ),
        docs_topic="queries",
    )
)

ERR_SOURCE_MISSING_TYPE = REGISTRY.register(
    ErrorCode(
        code="ERR-SOURCE-MISSING-TYPE",
        domain="execute",
        title="Source is missing the required type field",
        message_template=(
            "Source is missing the required `type` field: {offending_value!r}."
        ),
        doc=(
            "Fired when a source definition omits the required `type:` field. "
            "Add a `type:` field naming the source adapter to use."
        ),
        docs_topic="queries",
    )
)

ERR_DBT_MANIFEST_MISSING = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-MANIFEST-MISSING",
        domain="execute",
        title="SQL uses a dbt macro but no manifest is available",
        message_template=(
            "SQL uses {kind} but no dbt manifest was found (looked for {paths}). "
            "Build one with `dbt parse`."
        ),
        doc=(
            "Fired when a query's SQL calls `ref()` or `source()` but the project "
            "has no dbt manifest to resolve the call against. The manifest is what "
            "maps a model name to its warehouse relation, so without it dbt charts "
            "cannot know which table the query means. Run `dbt parse` (or any "
            "command that writes `target/manifest.json`) in the dbt project."
        ),
        docs_topic="queries",
    )
)

ERR_DBT_MANIFEST_UNREADABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-MANIFEST-UNREADABLE",
        domain="execute",
        title="dbt manifest could not be read",
        message_template=(
            "The dbt manifest at {relpath!r} could not be read: {detail}. "
            "Rebuild it with `dbt parse`."
        ),
        summary="Fired when the manifest file is unreadable or its JSON is corrupt.",
        doc=(
            "Fired when the manifest exists but cannot be read: the file is "
            "unreadable (OSError) or the JSON is malformed: usually a "
            "truncated write from an interrupted dbt run. Rebuild with "
            "`dbt parse`. The manifest's schema version is not checked: "
            "dbt charts reads the manifest as plain JSON rather than through "
            "dbt's typed contract, so a manifest written by a different "
            "dbt version still resolves refs normally."
        ),
        docs_topic="queries",
    )
)

ERR_ADAPTER_RELATIVE_PATH_NO_DATA_DIR = REGISTRY.register(
    ErrorCode(
        code="ERR-ADAPTER-RELATIVE-PATH-NO-DATA-DIR",
        domain="execute",
        title="Relative source path needs a data directory to resolve against",
        message_template=(
            "Relative {adapter} path {path!r} requires a data_dir to resolve "
            "against; none was configured for this adapter. "
            "Use an absolute path or configure a data directory."
        ),
        doc=(
            "Fired when a file-backed source (DuckDB, SQLite) declares a relative "
            "`path:` but the adapter has no data directory to resolve it against. "
            "Resolving against the process working directory would make the source "
            "depend on where `dct` was invoked from, so dbt charts refuses. Use an "
            "absolute path or configure a data directory for the project."
        ),
        docs_topic="queries",
    )
)

ERR_MUTATING_SQL = REGISTRY.register(
    ErrorCode(
        code="ERR-MUTATING-SQL",
        domain="execute",
        title="Non-read-only SQL is not allowed",
        message_template=(
            "dct refuses to execute non-read-only SQL. "
            "Statement: {rejected_node_kind}. Preview: {fragment_preview}."
        ),
        doc=(
            "Fired when dbt charts detects a non-SELECT statement (INSERT, UPDATE, "
            "DELETE, DROP, etc.) in a query. dbt charts only executes read-only SQL "
            "to prevent accidental data modification."
        ),
        summary="Fired when dbt charts detects a non-SELECT statement in a query.",
        docs_topic="queries",
    )
)

ERR_UNPARSEABLE_SQL = REGISTRY.register(
    ErrorCode(
        code="ERR-UNPARSEABLE-SQL",
        domain="execute",
        title="SQL could not be parsed for static checks",
        message_template="Could not parse this SQL: {cause}",
        fix_template=(
            "If the warehouse accepts this query, the SQL is fine; the parser "
            "just does not model that dialect or macro yet, and only dbt charts' "
            "static checks (read-only enforcement, fanout and reaggregation "
            "lint) are skipped for it. If the warehouse rejects it too, fix the "
            "syntax at the reported position."
        ),
        doc=(
            "Fired when dbt charts' static SQL parser cannot parse a query. The "
            "query is still sent to the warehouse; what is lost is the static "
            "read-only check and the semantic lint that run on parseable SQL. "
            "It is not necessarily an error in the SQL itself; unmodeled "
            "dialect syntax and dbt macros land here too."
        ),
        docs_topic="queries",
    )
)

ERR_BINDER_UNKNOWN_COLUMN = REGISTRY.register(
    ErrorCode(
        code="ERR-BINDER-UNKNOWN-COLUMN",
        domain="execute",
        title="Warehouse rejected an unknown column or table reference",
        message_template=(
            "Warehouse could not resolve a column or table reference: {detail}. "
            "Check that all referenced columns and tables exist in the source."
        ),
        doc=(
            "Fired when the warehouse reports that a column or table reference "
            "could not be resolved during query binding. Check that all referenced "
            "columns and tables exist in the source."
        ),
        docs_topic="queries",
    )
)

ERR_BINDER_TYPE_MISMATCH = REGISTRY.register(
    ErrorCode(
        code="ERR-BINDER-TYPE-MISMATCH",
        domain="execute",
        title="Warehouse rejected the query due to a type mismatch",
        message_template=(
            "Warehouse rejected the query due to a type mismatch: {detail}. "
            "Add explicit type casts to resolve ambiguity."
        ),
        doc=(
            "Fired when the warehouse reports a type mismatch during query binding. "
            "Add explicit type casts to resolve the ambiguity."
        ),
        docs_topic="queries",
    )
)

ERR_WAREHOUSE_RUNTIME = REGISTRY.register(
    ErrorCode(
        code="ERR-WAREHOUSE-RUNTIME",
        domain="execute",
        title="Warehouse rejected the query at runtime",
        message_template=("Warehouse rejected the query: {detail}."),
        doc=(
            "Fired when the warehouse rejects a query for an unclassified runtime "
            "error. The detail carries the warehouse's original error message."
        ),
        docs_topic="queries",
    )
)

ERR_WAREHOUSE_CONNECTION = REGISTRY.register(
    ErrorCode(
        code="ERR-WAREHOUSE-CONNECTION",
        domain="execute",
        title="Could not open the warehouse",
        message_template="Could not open the warehouse: {reason}",
        doc=(
            "Fired when opening the connection fails, before any SQL is sent: "
            "a database file that is missing, unreadable, or lock-held by "
            "another process (DuckDB, SQLite), or bad credentials, an "
            "unreachable host, or a missing database/role on a network "
            "warehouse (Postgres, Snowflake, BigQuery, Databricks, …). "
            "Distinct from ERR-WAREHOUSE-RUNTIME because nothing ever read "
            "the SQL: the query may be perfectly good, so callers that judge "
            "queries (`dct validate --warehouse`) must not report it as a "
            "query defect. `{reason}` names the classified cause; the "
            "driver's own text (which may carry hostnames, PIDs, or "
            "usernames) is not shown here but is available in `dct`'s "
            "diagnostics output and server logs. Check the source's "
            "credentials, host, and network reachability in "
            "`dbt_charts.yml` or `profiles.yml`."
        ),
        docs_topic="queries",
    )
)

ERR_QUERY_DURATION_EXCEEDED = REGISTRY.register(
    ErrorCode(
        code="ERR-QUERY-DURATION-EXCEEDED",
        domain="execute",
        title="Query exceeded the maximum allowed duration",
        message_template=(
            "Query exceeded max_query_duration_seconds={seconds}s on source {source!r}."
        ),
        doc=(
            "Fired when a query runs longer than the configured "
            "`max_query_duration_seconds` limit on a source. Optimize the query, "
            "raise the limit, or add a WHERE clause to reduce the result set."
        ),
        docs_topic="queries",
    )
)

ERR_DBT_REF_UNKNOWN_NODE = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-REF-UNKNOWN-NODE",
        domain="execute",
        title="ref() names a node that isn't in the dbt manifest",
        message_template=(
            "SQL references {{{{ ref({ref_name!r}) }}}}, but no model, seed, or "
            "snapshot named {ref_name!r} exists in the dbt manifest. "
            "Available: {available}."
        ),
        doc=(
            "Fired when a query's SQL calls the dbt `ref()` Jinja function with a "
            "name that is not present in the loaded manifest. `ref()` addresses "
            "models, seeds, and snapshots, not data tests or sources (use "
            "`source()` for those). Check for a typo, or refresh the manifest "
            "(`dbt parse`) if the node was added recently."
        ),
        docs_topic="queries",
        hint_generator=suggest_close_ref,
    )
)

ERR_DBT_MODEL_COLUMN_MISSING = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-MODEL-COLUMN-MISSING",
        domain="execute",
        title="Query references a column the dbt model no longer produces",
        summary=(
            "A board query reads a column the dbt model's own SQL no longer "
            "produces, caught before dbt run rebuilds the warehouse."
        ),
        message_template=(
            "Query {query_name!r} references column {column_name!r} of dbt model "
            "{model!r}, but the model's SQL does not produce it. "
            "Model columns: {available}."
        ),
        doc=(
            "Fired when a board query references a column of a dbt model whose "
            "output columns, derived statically from the model's SQL in "
            "`target/manifest.json`, do not include it. This catches a column "
            "renamed or dropped in the model *before* `dbt run` rebuilds the "
            "warehouse, when `--warehouse` validation still passes against the "
            "old table. If the model was just changed on purpose, update the "
            "board; if the manifest is stale, re-run `dbt parse`."
        ),
        docs_topic="queries",
        hint_generator=suggest_close_column,
    )
)

WARN_DBT_MODEL_COLUMNS_UNRESOLVED = REGISTRY.register(
    WarningCode(
        code="WARN-DBT-MODEL-COLUMNS-UNRESOLVED",
        domain="execute",
        title="A dbt model's output columns could not be derived statically",
        message_template=(
            "Query {query_name!r} reads dbt model {model!r}, whose output columns "
            "could not be derived from its SQL ({reason}); column references "
            "against it were not checked."
        ),
        fix_template=(
            "The reason names what blocks static derivation (a `SELECT *` "
            "wants explicit projections; an unaliased cast or expression "
            "wants an alias; seeds and snapshots are never derivable); the "
            "columns can always be verified with `dct validate --warehouse` "
            "after `dbt run`."
        ),
        summary=(
            "Static model-column derivation gave no answer for this model, so "
            "nothing vouches for the columns read from it."
        ),
        doc=(
            "Fired when a board query reads a dbt model whose output columns "
            "cannot be derived statically from its manifest SQL: a `SELECT *`, "
            "a macro in projection position, a snapshot (dbt injects meta "
            "columns at build time), or SQL that does not parse. Reported "
            "rather than silently skipped: an unchecked column reference is "
            "not a verified one."
        ),
        docs_topic="queries",
    )
)

ERR_DBT_SOURCE_UNKNOWN_TABLE = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-SOURCE-UNKNOWN-TABLE",
        domain="execute",
        title="source() names a table that isn't in the dbt manifest",
        message_template=(
            "SQL references {{{{ source({source_name!r}, {table_name!r}) }}}}, but "
            "no matching source table exists in the dbt manifest. "
            "Available sources: {available}."
        ),
        doc=(
            "Fired when a query's SQL calls the dbt `source()` Jinja function with "
            "a source/table pair that is not present in the loaded manifest. Check "
            "for a typo, or refresh the manifest (`dbt parse`) if the "
            "source was added recently."
        ),
        docs_topic="queries",
        hint_generator=suggest_close_source_table,
    )
)

WARN_DBT_QUERY_COLUMNS_INDETERMINATE = REGISTRY.register(
    WarningCode(
        code="WARN-DBT-QUERY-COLUMNS-INDETERMINATE",
        domain="execute",
        title="A query's dbt column references could not be determined",
        message_template=(
            "Query {query_name!r} uses dbt ref()/source() but the columns it "
            "reads could not be determined ({reason}); they were not checked "
            "against the dbt models."
        ),
        fix_template=(
            "The reason names what blocks the analysis (a `SELECT *` wants "
            "explicit columns; a templated identifier is decided at render "
            "time); the query can always be verified with "
            "`dct validate --warehouse` after `dbt run`."
        ),
        summary=(
            "Static analysis gave no answer for which columns this dbt-backed "
            "query reads, so nothing vouches for them against the models."
        ),
        doc=(
            "Fired when a board query that calls ref()/source() cannot be "
            "statically analyzed for the (table, column) pairs it consumes: "
            "a `SELECT *`, a templated identifier, an ambiguous unqualified "
            "column, or SQL that does not parse. The model-column drift check "
            "makes no claim either way for such a query; this warning keeps "
            "that gap visible instead of passing it in silence. Queries with "
            "no dbt calls are out of scope; the SQL lint tiers own those."
        ),
        docs_topic="queries",
    )
)

WARN_DBT_MANIFEST_MISSING = REGISTRY.register(
    WarningCode(
        code="WARN-DBT-MANIFEST-MISSING",
        domain="execute",
        title="Queries use dbt macros but no manifest was found",
        message_template=(
            "Queries use {kind} but no dbt manifest was found "
            "(looked for {paths}); refs were not validated."
        ),
        fix_template="Run 'dbt parse' in the dbt project.",
        summary=(
            "Fired when queries use ref()/source() but no manifest is present; "
            "refs were not validated."
        ),
        doc=(
            "Fired once per validate run and once per board render when one or "
            "more queries call ref() or source() but no manifest is present at "
            "any of the looked-for paths. "
            "The refs were not checked against the manifest. Run 'dbt parse' to "
            "generate target/manifest.json."
        ),
        docs_topic="queries",
    )
)

ERR_DBT_CALL_UNSUPPORTED = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-CALL-UNSUPPORTED",
        domain="execute",
        title="dbt call form this engine cannot resolve",
        message_template=(
            "Could not resolve {call!r} against the dbt manifest: dbt charts reads "
            "the relation name from the call itself, so each argument must be a "
            "plain quoted string: {{{{ ref('model') }}}} or "
            "{{{{ source('source', 'table') }}}}."
        ),
        fix_template=(
            "Package-qualified `ref('package', 'model')`, versioned "
            "`ref('model', v=2)`, and macro-computed arguments are not supported: "
            "each needs information the manifest lookup does not use, so resolving "
            "one would mean guessing which relation you meant. Name the model "
            "directly, or write the relation out."
        ),
        doc=(
            "Fired when a query's SQL calls dbt's `ref()` or `source()` in a form "
            "dbt charts cannot resolve to a single relation. dbt charts rewrites these "
            "calls textually against the manifest rather than executing dbt's "
            "Jinja, so it reads the names straight out of the call and every "
            "argument must be a plain quoted string. Rather than let an "
            "unrecognized call through to the warehouse (where it fails as a SQL "
            "syntax error naming `{{`, far from the cause), dbt charts reports it "
            "here."
        ),
        docs_topic="queries",
    )
)

ERR_WAREHOUSE_QUERY_INVALID = REGISTRY.register(
    ErrorCode(
        code="ERR-WAREHOUSE-QUERY-INVALID",
        domain="execute",
        title="Query failed warehouse validation",
        message_template=(
            "Query '{name}' failed warehouse validation ({mechanism}): {warehouse_message}"
        ),
        doc=(
            "Fired when a query's SQL is rejected by the warehouse during "
            "`dct validate --warehouse`. The warehouse message carries the "
            "specific failure reason (unknown column, syntax error, etc.). "
            "Fix the SQL or re-run `dbt parse` if a referenced model was recently renamed."
        ),
        docs_topic="queries",
    )
)

ERR_CHART_COLUMN_NOT_IN_RESULT = REGISTRY.register(
    ErrorCode(
        code="ERR-CHART-COLUMN-NOT-IN-RESULT",
        domain="execute",
        title="Chart channel references a column absent from the query result",
        summary="A chart channel names a column the backing query does not return.",
        message_template=(
            "Chart '{chart}' channel '{channel}' references column '{column}' "
            "which is not in the result of query '{name}'. "
            "Available columns: {columns}"
        ),
        doc=(
            "Fired during `dct validate --warehouse` when a chart's channel "
            "(x, y, color, value, etc.) names a column that the backing query "
            "does not return. Rename the channel to match a returned column, "
            "or update the query to return the expected column."
        ),
        docs_topic="queries",
    )
)

WARN_COLUMN_CHECK_UNAVAILABLE = REGISTRY.register(
    WarningCode(
        code="WARN-COLUMN-CHECK-UNAVAILABLE",
        domain="execute",
        title="Column check unavailable on this adapter",
        message_template=(
            "Column check unavailable on {adapter_type} "
            "({mechanism} validates the query but returns no result schema)."
        ),
        fix_template=(
            "Chart column checks need a schema-bearing mechanism (DuckDB, BigQuery dry run). "
            "Verify chart columns against a sample run, e.g. dct query <board> <name>."
        ),
        doc=(
            "Fired during `dct validate --warehouse` when the adapter can validate "
            "that the query is accepted by the warehouse but cannot return a result "
            "schema, so chart channel column checks are skipped. "
            "Use a DuckDB or BigQuery source for full column verification."
        ),
        docs_topic="queries",
    )
)

WARN_WAREHOUSE_CHECK_UNAVAILABLE = REGISTRY.register(
    WarningCode(
        code="WARN-WAREHOUSE-CHECK-UNAVAILABLE",
        domain="execute",
        title="Warehouse check unavailable on this adapter",
        message_template="Query '{name}' was not checked: {reason}.",
        fix_template=(
            "The query is unverified, not verified. Check it against a source "
            "with a check mechanism (DuckDB, BigQuery, Postgres, Redshift, "
            "Snowflake), or run it directly, e.g. dct query <board> <name>."
        ),
        summary="Nothing inspected this query, so nothing can vouch for it.",
        doc=(
            "Fired during `dct validate --warehouse` when a query could not be "
            "checked without running it at full cost: the adapter offers no "
            "mechanism (DESCRIBE, EXPLAIN, or a dry run), the query composes "
            "another query's cached result, or the warehouse was never reached. "
            "The query is reported as unchecked rather than valid: nothing "
            "inspected the SQL. `--warehouse` will never run the query itself "
            "to find out."
        ),
        docs_topic="queries",
    )
)
