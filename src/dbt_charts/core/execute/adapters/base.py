"""Base adapter interface for query execution.

Stage: EXECUTE
Purpose: Define the base interface and types for query adapters.

All adapters inherit from BaseAdapter and implement the unified query interface.
QueryResult is the standard return type for all adapter executions.

The unified interface uses:
- `supported_types` property: Set of query types this adapter handles
- `can_execute()`: Uses supported_types for type-based routing
- `execute()`: Executes the query using type guards for type safety

Security:
- Adapters support parameterized queries via the optional `params` argument
- When params are provided, values are passed to the database driver separately
  from the SQL, preventing SQL injection
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from dbt_charts.core.compile.models.board.normalized import VariableValues
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_BINDER_UNKNOWN_COLUMN,
    ERR_WAREHOUSE_CONNECTION,
    ERR_WAREHOUSE_RUNTIME,
)
from dbt_charts.core.dialects import SQLDialect, get_dialect

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.diagnostics.registry import ErrorCode

# Type alias for query parameters
QueryParams = list[Any] | None


@dataclass(frozen=True)
class ResolvedRelation:
    """A dbt ref/source resolved during query execution.

    Lineage: which schema each relation resolved to.
    """

    ref_name: str
    schema: str


class QueryResult:
    """Result of a query execution.

    This is the standard return type for all adapter executions, providing
    a consistent interface for data retrieval and error handling.

    Attributes:
        data: List of dictionaries containing query results (each dict is a row)
        columns: List of column names
        column_descriptions: Full PEP 249 cursor.description tuples keyed by column name.
            Each tuple: (name, type_code, display_size, internal_size, precision, scale, null_ok)
        error: Error message if query failed, None if successful
    """

    def __init__(
        self,
        data: list[dict[str, Any]],
        columns: list[str] | None = None,
        column_descriptions: dict[str, tuple[Any, ...]] | None = None,
        error: str | None = None,
        error_code: ErrorCode | None = None,
        resolved_relations: list[ResolvedRelation] | None = None,
        fields: dict[str, Any] | None = None,
        truncated_reason: Literal["max_rows"] | None = None,
    ):
        """Initialize query result.

        Args:
            data: List of dictionaries containing query results (each dict is a row)
            columns: Optional list of column names (inferred from data if not provided)
            column_descriptions: Optional PEP 249 cursor.description tuples keyed by
                column name. Each value is a 7-tuple: (name, type_code, display_size,
                internal_size, precision, scale, null_ok). Populated from
                cursor.description when a database adapter executes the query.
            error: Optional error message if query failed
            error_code: Optional typed ErrorCode for this failure. When set, the
                executor propagates it to QueryError.code instead of the default
                ERR-INTERNAL. Only meaningful when error is also set.
            resolved_relations: dbt ref/source relations resolved during execution.
            fields: Optional structured fields for error_code's message_template
                (e.g. {"detail": ...}). When set alongside error_code, the executor
                constructs the raised QueryError via ErrorCode.from_code(**fields)
                instead of wrapping `error` in another prefix. None means the
                adapter hasn't migrated to structured fields yet — the executor
                falls back to using `error` as the raw message.
            truncated_reason: Set to "max_rows" when the execution.max_rows
                ceiling (not the author's own Query.limit) bound this result at
                fetch time — i.e. the adapter requested one extra row over the
                ceiling and got it back. None when the author's own limit was
                the binding value, or no ceiling fired.
        """
        self.data = data or []
        if columns is None and self.data:
            self.columns = list(self.data[0].keys())
        else:
            self.columns = columns or []
        self.column_descriptions = column_descriptions
        self.error = error
        self.error_code = error_code
        self.resolved_relations: list[ResolvedRelation] = resolved_relations or []
        self.fields = fields
        self.truncated_reason: Literal["max_rows"] | None = truncated_reason

    @property
    def is_success(self) -> bool:
        """Check if query executed successfully."""
        return self.error is None


@dataclass(frozen=True)
class RowFetchLimit:
    """How many rows to ask the driver for via a bounded fetch (dbt-adapters'
    ``execute(..., limit=...)`` kwarg, or a direct cursor ``fetchmany()``),
    resolved against the author's own Query.limit and the configured
    execution.max_rows ceiling.

    ``fetch_limit`` is what actually gets requested from the driver. When the
    ceiling is binding, it over-fetches by one row (``effective_limit + 1``)
    so the caller can tell "there were more rows than the ceiling" without
    knowing the true total — compare the fetched row count against
    ``effective_limit`` and slice/flag if over (apply_row_limit_truncation).
    ``ceiling_binding`` is False when the author's own limit is already at or
    below the ceiling — in that case fetch_limit == effective_limit and the
    fetch already returns at most that many rows, so no slicing or
    truncation flag is ever needed for it.
    """

    fetch_limit: int
    effective_limit: int
    ceiling_binding: bool


def resolve_effective_row_limit(query_limit: int | None) -> RowFetchLimit:
    """Resolve the row limit to fetch: author's Query.limit, clamped to the
    execution.max_rows ceiling (which may itself be clamped to a deployment
    ceiling — see resolve_max_rows()).
    """
    from dbt_charts.core.compile.config import resolve_max_rows

    ceiling = resolve_max_rows()
    if query_limit is not None and query_limit > 0 and query_limit <= ceiling:
        return RowFetchLimit(
            fetch_limit=query_limit, effective_limit=query_limit, ceiling_binding=False
        )
    return RowFetchLimit(
        fetch_limit=ceiling + 1, effective_limit=ceiling, ceiling_binding=True
    )


_RowT = TypeVar("_RowT")


def apply_row_limit_truncation(
    rows: list[_RowT], limit: RowFetchLimit
) -> tuple[list[_RowT], Literal["max_rows"] | None]:
    """Slice fetched rows to the effective limit, flagging truncation only
    when the ceiling (not the author's own limit) was the binding value.

    Generic over the row type: each adapter calls this on its own driver's
    native row shape (agate rows, DuckDB/sqlite3 tuples) before converting to
    dicts, so the row type is preserved rather than widened to Any.
    """
    if len(rows) > limit.effective_limit:
        return rows[
            : limit.effective_limit
        ], "max_rows" if limit.ceiling_binding else None
    return rows, None


def resolve_setup_sql(
    setup_sql: str,
    variables: VariableValues | None,
) -> str | QueryResult:
    """Resolve Jinja in setup_sql. Returns resolved string or a QueryResult on error."""
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    try:
        return resolve_jinja_template(setup_sql, variables=variables or {})
    except Exception as e:  # noqa: BLE001
        return handle_adapter_error("setup_sql template resolution", e)


def handle_adapter_error(operation: str, error: Exception) -> QueryResult:
    """Standard error handling for adapters.

    Provides consistent error handling across all adapters, ensuring
    uniform error messages and making it easier to add logging/metrics
    in one place.

    Args:
        operation: Description of the operation that failed (e.g., "SQL execution", "HTTP request")
        error: The exception that occurred

    Returns:
        QueryResult with error information
    """
    dbt_charts_error = error if isinstance(error, DbtChartsError) else None
    return QueryResult(
        data=[],
        error=f"{operation} failed: {str(error)}",
        error_code=dbt_charts_error.code if dbt_charts_error is not None else None,
        # Only forward when non-empty: an empty dict is truthy-checked away
        # to None so `_query_error_from_result` takes its plain-message
        # branch instead of trying `error_code.message_template.format()`
        # with no kwargs (most DbtChartsError subclasses don't populate
        # `.fields` with their own template args, only the ones that do —
        # e.g. UnparseableSqlError's sql_line/sql_start_col/sql_end_col).
        fields=(
            dbt_charts_error.fields
            if dbt_charts_error is not None and dbt_charts_error.fields
            else None
        ),
    )


def connection_failure_message(warehouse: str, error: Exception) -> str:
    """The one sentence a warehouse that could not be opened gets.

    Used on the query-execution path only (``connection_failure`` below).
    ``dbt_charts.core.connections.test_connection`` classifies its own connect
    failures into a ``WarehouseProbeError`` whose ``str(exc)`` is authored
    display copy, separate from this template.
    """
    return (
        f"{warehouse}: {ERR_WAREHOUSE_CONNECTION.message_template.format(detail=error)}"
    )


def connection_failure(warehouse: str, error: Exception) -> QueryResult:
    """The verdict for a warehouse that could not be opened.

    The in-process adapters connect inside the same block that classifies query
    errors, so without this a missing or lock-held database file arrives
    carrying a query-defect code and every query in the board reads as broken.
    Nothing looked at the SQL, and the result has to say so.
    """
    return QueryResult(
        data=[],
        error=connection_failure_message(warehouse, error),
        error_code=ERR_WAREHOUSE_CONNECTION,
        fields={"detail": str(error)},
    )


# BigQuery's REST API error text for an unresolvable column/table/dataset
# reference (confirmed against real dbt-bigquery output, which joins
# error.errors[]["message"] verbatim into the exception string). Snowflake and
# Databricks route generic dbt-adapter failures through the same bare-string
# exception wrapper but in an unverified message format — classifying those by
# a guessed pattern risks a confidently wrong code, so they fall through to
# ERR_WAREHOUSE_RUNTIME in classify_warehouse_error below.
BIGQUERY_UNKNOWN_REF_SUBSTRINGS = (
    "Unrecognized name:",
    "Not found: Table",
    "Not found: Dataset",
)


def classify_warehouse_error(
    operation: str, error: Exception, dialect: str
) -> QueryResult:
    """Map a warehouse-execution exception to a typed ERR-* QueryResult.

    dbt funnels every warehouse driver's error (BigQuery, Snowflake,
    Databricks, ...) through a bare-string exception, with no shared
    hierarchy to isinstance-check — so classification is by message content,
    gated on dialect so a coincidental substring match under the wrong
    warehouse can't mislabel it. Delegates to handle_adapter_error first, so
    a DbtChartsError raised inside the classified scope (e.g. MutatingSqlError
    or UnparseableSqlError from the SQL guard) keeps its own registered code
    instead of being overwritten by the dialect-based fallback below.

    Args:
        operation: Description of the failed operation, used as the error
            message prefix (e.g. "dbt SQL execution", "bigquery SQL execution").
        error: The exception raised during warehouse execution.
        dialect: The resolved warehouse dialect name (e.g. "bigquery").

    Returns:
        QueryResult with the original message preserved verbatim and a typed
        error_code — never None, so callers never fall back to ERR-INTERNAL.
    """
    result = handle_adapter_error(operation, error)
    if result.error_code is not None:
        return result
    if dialect == "bigquery" and any(
        substring in str(error) for substring in BIGQUERY_UNKNOWN_REF_SUBSTRINGS
    ):
        result.error_code = ERR_BINDER_UNKNOWN_COLUMN
    else:
        result.error_code = ERR_WAREHOUSE_RUNTIME
    return result


class BaseAdapter(ABC):
    """Base interface for query adapters.

    All adapters must implement this interface to execute queries
    against different backends (SQL, HTTP, CSV, etc.).

    The unified interface pattern uses:
    - `supported_types`: Property returning set of query types this adapter handles
    - Type guards for type-safe field access in execute methods

    Subclasses must implement:
        - supported_types: Property returning Set[str] of supported query types
        - _execute(): Perform the actual query execution

    Optional override:
        - _can_execute(): Override for custom eligibility logic beyond type matching

    Example:
        >>> class MyAdapter(BaseAdapter):
        ...     @property
        ...     def supported_types(self) -> Set[str]:
        ...         return {"sql"}
        ...
        ...     def _execute(self, query, variables):
        ...         if is_sql_query(query):
        ...             # Type checker knows query.sql exists
        ...             return self._run_sql(query.sql)
    """

    @property
    @abstractmethod
    def supported_types(self) -> set[str]:
        """Query types this adapter can execute.

        Returns:
            Set of query type strings (e.g., {"sql"}, {"csv", "http"})

        Example:
            >>> adapter.supported_types
            {"sql"}
        """
        pass

    def can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Check if this adapter can execute the given query against a source.

        Gates on supported_types, then defers to _can_execute() for source-aware
        eligibility. source_config is the resolved source (or None), so a SQL
        adapter claims only queries whose resolved warehouse type it owns.

        Args:
            query: AnyQuery object (guaranteed by compiler)
            source_config: Typed source config resolved by SourceResolver, or None.

        Returns:
            True if this adapter can execute the query, False otherwise
        """
        # First check if type is supported
        if query.query_type not in self.supported_types:
            return False
        # Then check any additional conditions
        return self._can_execute(query, source_config)

    def param_render_dialect(self, source_config: ResolvedSourceConfig) -> SQLDialect:
        """The placeholder style pre-rendered SQL handed to this adapter must use.

        The registry's composition point renders {{ queries.* }} references and
        variables before dispatch and passes the adapter SQL plus params.
        Adapters that bind params through a driver need the warehouse's real
        placeholder syntax (the default here). Adapters that flatten params to
        literals instead override this with a collision-free internal style —
        a warehouse's own syntax ($1, ?) can occur in authored string literals,
        where the flattening step would corrupt it.
        """
        return get_dialect(source_config.type)

    def _can_execute(
        self, query: AnyQuery, source_config: ResolvedSourceConfig | None
    ) -> bool:
        """Source-aware eligibility check beyond query-type matching.

        Override to claim only the resolved source types this adapter owns.
        The base implementation always returns True (query-type match suffices).

        Args:
            query: AnyQuery object (guaranteed)
            source_config: Typed source config resolved by SourceResolver, or None.

        Returns:
            True if this adapter can execute the query, False otherwise
        """
        return True

    def execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Execute a query and return results.

        Args:
            query: AnyQuery object (guaranteed by compiler)
            variables: Optional dictionary of variable values for query resolution
            params: Optional pre-computed parameter values for parameterized execution.
                When provided, the adapter should use these params directly instead
                of re-processing variables. This is used by batch execution where
                parameterization happens before adapter execution.
            source_config: Typed source config resolved by SourceResolver before
                adapter dispatch. SqlAdapter consumes this; other adapters ignore it.

        Returns:
            QueryResult containing data or error information
        """
        return self._execute(query, variables, params, source_config)

    @abstractmethod
    def _execute(
        self,
        query: AnyQuery,
        variables: VariableValues | None = None,
        params: QueryParams = None,
        source_config: ResolvedSourceConfig | None = None,
    ) -> QueryResult:
        """Internal method to execute query.

        Use type guards for type-safe field access:
            if is_sql_query(query):
                sql = query.sql  # Type checker knows this is str

        Args:
            query: AnyQuery object (guaranteed)
            variables: Optional dictionary of variable values for query resolution
            params: Optional pre-computed parameter values. When provided, skip
                internal parameterization and use these params directly.
            source_config: Typed source config from SourceResolver; SqlAdapter
                reads it, other adapters accept and ignore.

        Returns:
            QueryResult containing data or error information
        """
        pass
