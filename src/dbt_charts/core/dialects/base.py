"""Base SQL dialect interface for database-specific behavior.

Stage: SHARED (compile + execute) — leaf module, no dependency on either.
Purpose: Define the abstract interface for database-specific SQL generation.

This module provides the base SQLDialect class that all database-specific
dialects inherit from. Each dialect encapsulates its own quirks for:
- Parameter placeholders
- (Future) SQL transpilation

Rather than spreading conditionals throughout the codebase, each dialect
encapsulates its own behavior.
"""

from abc import ABC, abstractmethod
from typing import Any

# Allowlist of valid SQL operators for filter() method
# This prevents SQL injection via operator parameter
VALID_OPERATORS = frozenset(
    {
        "=",
        "!=",
        "<>",
        ">",
        "<",
        ">=",
        "<=",
        "LIKE",
        "NOT LIKE",
        "ILIKE",
        "NOT ILIKE",
        "IN",
        "NOT IN",
        "IS",
        "IS NOT",
        "BETWEEN",
        "NOT BETWEEN",
        "~",
        "~*",
        "!~",
        "!~*",  # PostgreSQL regex operators
    }
)


def ansi_information_schema_columns_sql() -> str:
    """Standard ANSI ``information_schema.columns`` bulk query.

    Shared by dialects whose ``information_schema`` is a single per-source view
    (DuckDB, Postgres/Redshift). System schemas are excluded so the result is
    user tables only, ordered by schema → table → column position.

    DuckDB's ``information_schema`` spans every attached catalog, so the built-in
    ``system``/``temp`` catalogs are excluded too — otherwise a system schema
    could shadow a user table in the flattened schema tree. On Postgres this
    filter is a no-op (``table_catalog`` is the single connected database).
    """
    return (
        "SELECT table_schema, table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema NOT IN "
        "('information_schema', 'pg_catalog', 'pg_toast') "
        "AND table_catalog NOT IN ('system', 'temp') "
        "ORDER BY table_schema, table_name, ordinal_position"
    )


class SQLDialect(ABC):
    """Abstract base class for SQL dialect implementations.

    Each database dialect inherits from this class and implements
    database-specific behavior for SQL generation.

    Attributes:
        name: The canonical name of the dialect (e.g., 'postgres', 'mysql')

    Example:
        >>> dialect = PostgresDialect()
        >>> dialect.param(1)
        '$1'
    """

    name: str

    # True for every warehouse whose dbt-adapters cursor implements fetchmany.
    # Spark (PyhiveConnectionWrapper / PyodbcConnectionWrapper) raises
    # AttributeError — those cursors only implement fetchall.  When False, the
    # adapters fall back to a full fetchall + post-fetch slice via
    # apply_row_limit_truncation instead of using the limit= kwarg.
    cursor_supports_driver_limit: bool = True

    # --- String literals ---

    escapes_backslashes: bool
    """Whether `\\` escapes the next character inside a '...' string literal.

    Read when a value is inlined as a literal, which is every dbt-adapter
    execution path — dbt's adapter.execute() carries a SQL string and no
    bindings. Both answers are load-bearing and neither is safe as a default,
    so this is declared, never inherited and never defaulted: doubling
    backslashes on an engine that treats them as ordinary characters corrupts
    the value silently, and not doubling them on an engine that does lets a
    value close its own literal and reach code position. A dialect that
    declares nothing raises at the escaper instead of getting a guess.

    It also decides how a quote inside a value is escaped. `\\'` is accepted
    wherever backslash escapes, and on BigQuery and Spark it is the only form
    that works: those grammars concatenate adjacent string literals, so `''`
    there is not an escaped quote but the end of one literal and the start of
    another, turning O'Brien into OBrien. Classify from the backslash question
    alone — "accepts `''`" is no evidence either way, since MySQL, Snowflake
    and Redshift accept both forms while still escaping with backslashes.

    Each dialect cites the documented grammar it read this off; a transpiler's
    tokenizer is not the source, since those encode what a reader will accept,
    not what an engine means.
    """

    # --- Parameters ---

    @abstractmethod
    def param(self, index: int) -> str:
        """Generate parameter placeholder for the given index.

        This is the only abstract method that must be implemented by all dialects,
        as parameter syntax varies significantly between databases.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder string (e.g., '$1', '?', '%s', '@p1')
        """
        pass

    def params(self, count: int) -> list[str]:
        """Generate a list of parameter placeholders.

        Args:
            count: Number of parameters needed

        Returns:
            List of parameter placeholders (e.g., ['$1', '$2', '$3'])
        """
        return [self.param(i) for i in range(1, count + 1)]

    # --- Param Conversion ---

    uses_named_params: bool = False
    """Whether this dialect uses named parameters (dict) vs positional (list).

    Positional dialects (default): $1, ?, %s — params passed as list.
    Named dialects: @param1, @p1, :param1 — params passed as dict.
    """

    # --- Statement timeout ---

    def statement_timeout_sql(self, seconds: int) -> str | None:
        """Return this dialect's SQL for a server-side per-statement timeout.

        Sent once at connection setup so the warehouse itself cancels a runaway
        query — never a client-side abandon (the query keeps burning warehouse
        credits if the client just stops waiting). Returns None when the dialect
        has no confirmed native SQL mechanism for this: the base default is
        deliberately conservative rather than guessing at syntax. BigQuery's
        equivalent is a job-level setting (QueryJobConfig.job_timeout_ms), not
        SQL, so it is applied at connection setup in sql_adapter.py instead.

        Args:
            seconds: Maximum seconds a statement may run.

        Returns:
            A SQL statement to run once at connection setup, or None for no
            dialect-native mechanism.
        """
        return None

    def resolve_timeout_seconds(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> int:
        """The cap actually in force, given what the warehouse's own profile asks for.

        ``max_query_duration_seconds`` is a ceiling, so a profile that caps
        itself harder keeps its own value. Every consumer of the cap reads the
        number this returns — the statement sent at connect, the credentials
        :meth:`connect_credentials` writes, and the seconds a
        ``_QueryDurationExceeded`` reports — so what a timeout error claims
        stays true by construction.

        The base returns `seconds`: only a profile that can spell the cap itself
        can narrow it, and ClickHouse's ``custom_settings`` is the one that can.

        Raises:
            ValueError: the profile's own cap cannot be compared against the
                ceiling, so neither value can be chosen without guessing.
        """
        return seconds

    def connect_credentials(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> dict[str, Any]:  # type-state: explicit_any — raw source_config
        """Return `creds` with the statement cap in it, for a cap that is credential-shaped.

        The third way a dialect can carry the cap, beside
        :meth:`statement_timeout_sql` (a statement sent at connect) and
        BigQuery's job config (set on the handle after connect). Used when the
        cap has to be inside the credentials *before* the adapter is built,
        because the warehouse has no statement to send that would survive a
        pooled connection.

        `seconds` is the resolved cap from :meth:`resolve_timeout_seconds`, and
        is written unconditionally: deciding again here is how the number the
        warehouse enforces drifts from the number an error reports.

        Never mutates `creds` — the pool hashes the config it was handed to key
        the connection pool, so an in-place write would fork pools by cap. The
        base returns it unchanged: for every dialect but ClickHouse the cap is
        not credential-shaped.
        """
        return creds

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when exc was raised by the warehouse enforcing a statement timeout.

        Used to distinguish a server-side statement cancellation from a generic
        driver error (dropped connection, auth failure, etc.) so the adapter can
        raise a clean _QueryDurationExceeded instead of a retryable error.

        The base implementation always returns False — only dialects with a known
        server-side timeout mechanism override this.

        Args:
            exc: The exception raised by the dbt adapter's execute() call.

        Returns:
            True if exc signals a statement timeout; False otherwise.
        """
        return False

    # --- Session state ---

    def session_reset_statements(self) -> tuple[str, ...]:
        """Return the statements that drop every session-scoped object a query left.

        A pooled connection outlives the query that used it, so the temp tables,
        functions and views a ``setup_sql`` creates are still there for the next
        query on that connection: the next render of the same board collides with
        its own leftovers, and an unrelated board can read them.

        Empty when the dialect has no such statement — the pool answers that by
        dropping the connection instead, a fresh session being the only other way
        to be sure. Guessing at syntax here would send a statement the warehouse
        rejects on every query.
        """
        return ()

    # --- Date comparison ---

    def date_expr(self, column: str) -> str:
        """Return this dialect's SQL for comparing `column` as a DATE.

        `filter()` and `filter_date_range()` need the column truncated to a date regardless
        of whether it is stored as DATE or TIMESTAMP — a DATE column must be a
        no-op, and a TIMESTAMP column must not be compared bare against DATE
        bounds (BigQuery rejects that comparison outright). `CAST(x AS DATE)`
        is the standard spelling and correct for every dialect we ship except
        SQLite, which has no DATE type: CAST there takes NUMERIC affinity and
        truncates a date-like string to its leading integer run, so SQLite
        overrides this with `DATE(x)`.

        Args:
            column: Column expression to compare as a date (already validated
                as a bare identifier by the caller).
        """
        return f"CAST({column} AS DATE)"

    # --- Bulk schema introspection ---

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Return SQL that lists every (schema, table, column, type) in the source
        in a SINGLE query — the whole-source alternative to the per-schema relation
        walk (one ``INFORMATION_SCHEMA.COLUMNS``-style query instead of ``1 + N``
        metadata round-trips).

        The result set must have columns named (case-insensitively)
        ``table_schema``, ``table_name``, ``column_name``, ``data_type``, ordered
        by schema, table, then column position. ``scope`` carries the dialect-
        specific whole-source qualifier a query needs (BigQuery: the
        ``region-<location>`` prefix; Snowflake: the database).

        The base raises — a dialect without a confirmed bulk form (mysql, athena,
        databricks, sqlserver) inherits this and degrades to the on-demand schema
        tool rather than guessing at syntax.
        """
        raise NotImplementedError(f"bulk_schema_sql not implemented for {self.name!r}")

    # --- Representation ---

    def __repr__(self) -> str:
        """Return string representation of the dialect."""
        return f"{self.__class__.__name__}(name={self.name!r})"
