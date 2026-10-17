"""Execution error types.

Stage: SHARED (compile + execute) — leaf module, no dependency on either.
Purpose: Define error types for query execution failures.

These errors are raised during:
- Query execution (QueryError)
- Adapter resolution/execution (AdapterError)
- Connection failures (ConnectionError)
- SQL-shape validation, at compile time and execute time (MutatingSqlError,
  UnparseableSqlError — see dbt_charts.core.compile.sql_guard)

All errors inherit from ExecutionError → DbtChartsError for easy catching.
"""

from typing import TYPE_CHECKING, Any, NamedTuple

from dbt_charts.core.diagnostics.ansi import strip_ansi
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_MUTATING_SQL,
    ERR_UNPARSEABLE_SQL,
)

if TYPE_CHECKING:
    from dbt_charts.core.diagnostics.registry import ErrorCode


class ExecutionError(DbtChartsError):
    """Base error for all execution failures.

    This is the parent class for all execution-related errors.
    Catch this to handle any execution error.

    Attributes:
        message: Human-readable error description
        query_name: Name of query that failed (if applicable)
    """

    # Class-level None default keeps `from_code`-constructed instances safe.
    query_name: str | None = None

    def __init__(self, message: str, query_name: str | None = None) -> None:
        self.message = message
        self.query_name = query_name
        self.fields: dict[str, Any] = {}
        if query_name is not None:
            self.fields["query_name"] = query_name
        super().__init__(self._format_message())
        if self.code is None:
            from dbt_charts.core.diagnostics.codes_unknown import ERR_INTERNAL

            self.code = ERR_INTERNAL

    def _format_message(self) -> str:
        """Format error message with optional query name."""
        if self.query_name:
            return f"{self.message} (query: {self.query_name})"
        return self.message


class QueryError(ExecutionError):
    """Error during query execution.

    Raised when:
    - SQL syntax is invalid
    - Table/column doesn't exist
    - Query returns unexpected results

    Example:
        >>> try:
        ...     executor.execute_query("broken_query")
        ... except QueryError as e:
        ...     print(f"Query failed: {e}")
    """

    sql: str | None = None

    def __init__(
        self,
        message: str,
        query_name: str | None = None,
        sql: str | None = None,
        *,
        code: "ErrorCode | None" = None,
    ) -> None:
        self.sql = sql
        if code is not None:
            self.code = code
        super().__init__(message, query_name)


class AdapterError(ExecutionError):
    """Error with adapter resolution or execution.

    Raised when:
    - Adapter not found for query type
    - Adapter initialization fails
    - Adapter-specific error occurs

    Example:
        >>> try:
        ...     executor.execute_query("unknown_type_query")
        ... except AdapterError as e:
        ...     print(f"Adapter error: {e}")
    """

    adapter_type: str | None = None

    def __init__(self, message: str, adapter_type: str | None = None) -> None:
        self.adapter_type = adapter_type
        super().__init__(f"Adapter error: {message}")


class ConnectionError(ExecutionError):
    """Error connecting to data source.

    Raised when:
    - Database connection fails
    - HTTP endpoint unreachable
    - File not found

    Example:
        >>> try:
        ...     executor.execute_query("db_query")
        ... except ConnectionError as e:
        ...     print(f"Connection failed: {e}")
    """

    source: str | None = None

    def __init__(self, message: str, source: str | None = None) -> None:
        self.source = source
        super().__init__(f"Connection failed: {message}")


class MutatingSqlError(ExecutionError):
    """SQL contains a statement type outside the validator's allowlist.

    Raised by both `validate_select_only` (query allowlist) and
    `validate_setup_sql` (setup allowlist). The `allowlist_label` arg
    distinguishes the policy in the user-facing message.

    Attributes:
        rejected_node_kind: sqlglot expression class name (e.g. "Drop", "Insert").
        fragment_preview: First ~60 chars of the rejected statement's .sql() output.
        allowlist_label: "query" (default) or "setup" — names the policy violated.
    """

    rejected_node_kind: str | None = None
    fragment_preview: str | None = None
    allowlist_label: str = "query"

    _QUERY_BODY = (
        "  Allowed: SELECT, WITH, UNION, INTERSECT, EXCEPT, DESCRIBE, SHOW, "
        "EXPLAIN of an allowed inner statement."
    )
    _SETUP_BODY = (
        "  Allowed: CREATE TEMP FUNCTION / TEMP TABLE / TEMP VIEW (any dialect) "
        "and CREATE [OR REPLACE] MACRO (DuckDB)."
    )

    def __init__(
        self,
        rejected_node_kind: str,
        fragment_preview: str,
        *,
        allowlist_label: str = "query",
    ) -> None:
        self.rejected_node_kind = rejected_node_kind
        self.fragment_preview = fragment_preview
        self.allowlist_label = allowlist_label
        self.code = ERR_MUTATING_SQL
        body = self._SETUP_BODY if allowlist_label == "setup" else self._QUERY_BODY
        policy = "setup_sql" if allowlist_label == "setup" else "read-only SQL"
        msg = (
            f"dct refuses to execute SQL outside the {policy} allowlist.\n\n"
            f"  Statement: {rejected_node_kind}\n"
            f"  Preview:   {fragment_preview}\n\n"
            f"{body}\n"
            f"  See dbt_charts/core/compile/sql_guard.py for the full allowlist."
        )
        super().__init__(msg)


class SqlErrorPosition(NamedTuple):
    """Where a SQL parse error occurred, in SQL-local coordinates: relative
    to the *SQL string* sqlglot parsed, not the board file. Bundled as one
    value rather than three independently-nullable fields — the three
    numbers are only ever meaningful (or absent) together, so a struct makes
    "line set but columns missing" unrepresentable instead of just unlikely.
    The caller that builds a Diagnostic (`compile.parse.source_map.stamp_diagnostics`)
    offsets these into board-file coordinates using the query block's own
    position.

    `line_text` is that line as sqlglot saw it, and is what makes the offset
    safe: the string handed to the guard is the *resolved* SQL (variables
    already swapped for bind placeholders, LIMIT possibly wrapped around it),
    so its columns only address the authored board text when the two are
    identical. Carrying the line lets the offsetting side prove that instead
    of assuming it.
    """

    line: int
    start_col: int
    end_col: int  # end-exclusive, matching ColumnSpan.end_col
    line_text: str


class UnparseableSqlError(ExecutionError):
    """SQL skeleton could not be determined — caller decides whether to defer.

    Attributes:
        cause: TemplateSyntaxError, sqlglot ParseError, sqlglot TokenError, or a
               str like "unsupported_jinja_node:<NodeType>" for the
               bail-out-node case.
        sql_position: The offending token's position, when `cause` is a
            sqlglot ParseError with a resolvable one. None for the
            TemplateSyntaxError / TokenError / bail-out-node causes, which carry
            no sqlglot coordinates to capture.
    """

    cause: Exception | str | None = None
    sql_position: SqlErrorPosition | None = None

    def __init__(
        self,
        cause: Exception | str,
        *,
        sql_position: SqlErrorPosition | None = None,
    ) -> None:
        self.cause = cause
        self.sql_position = sql_position
        self.code = ERR_UNPARSEABLE_SQL
        detail = strip_ansi(str(cause))
        super().__init__(ERR_UNPARSEABLE_SQL.message_template.format(cause=detail))
        if sql_position is not None:
            # Carried on .fields too (not just the plain attr) so the
            # position survives a QueryResult -> QueryError.from_code(**fields)
            # reconstruction, which only rebuilds attributes it's handed
            # explicitly (see execute.adapters.base.handle_adapter_error).
            # `cause` goes in stringified: these fields reach Cloud's editor
            # through Diagnostic.model_dump(mode="json"), which raises on a
            # bare exception object.
            self.fields.update(
                cause=detail,
                sql_line=sql_position.line,
                sql_start_col=sql_position.start_col,
                sql_end_col=sql_position.end_col,
                sql_line_text=sql_position.line_text,
            )
