"""Query execution engine.

Stage: EXECUTE (Service)
Purpose: Execute queries for compiled boards.

Entry Points:
    - Executor.execute_query(query_name, variables) -> List[Dict]
    - Executor.execute_chart(chart, variables) -> List[Dict]

The executor:
- Resolves which adapter to use for each query
- Executes queries and returns data
- Caches results for efficiency

Does NOT:
- Compile boards
- Render charts

Dependencies:
    - dbt_charts.compile (for types)
    - .adapters (for data source adapters)

See also:
    - render/renderer.py: Uses executor for data
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache, partial
from typing import TYPE_CHECKING, Any, Literal, NoReturn

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp
import yaml
from pydantic import ValidationError

from dbt_charts.core.column_types import ColumnType

# Regex that detects __dct_cache_ref__NAME__ sentinels produced by
# {{ queries.X.cache }} in a composing query's SQL.
_CACHE_REF_RE: re.Pattern[str] = re.compile(r"__dct_cache_ref__(\w+)__")

# Regex that matches {{ queries.NAME.cache }} in authored SQL so we can
# pre-substitute it to its sentinel form before passing the SQL to the
# existing {{ queries.X }} Jinja resolver (which doesn't handle .cache).
# The \s* around the dot tolerates whitespace variants like
# {{ queries.A . cache }} for consistency with the dep-graph regex.
CACHE_REF_JINJA_RE: re.Pattern[str] = re.compile(
    r"\{\{\s*queries\.(\w+)\s*\.\s*cache\s*\}\}"
)

logger = logging.getLogger(__name__)

from dbt_charts.core.board_listings import resolve_listings
from dbt_charts.core.compile.format import missing_format_columns
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.compile.models.cache import CachePolicy
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SqlQuery,
    is_sql_query,
)
from dbt_charts.core.compile.models.source import (
    DuckDBSourceConfig,
    ResolvedSourceConfig,
    SQLiteSourceConfig,
    declared_metricflow,
    parse_source_config,
)
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.compile.template.listings import ListingCall, TextListings
from dbt_charts.core.compile.template.parameterized import DateRangeFilter
from dbt_charts.core.compile.template.variables import (
    coerce_variable_values,
    parse_variable_json_strings,
    resolve_daterange_presets,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_execute import (
    ERR_FILE_SOURCE_NOT_FOUND,
    ERR_QUERY_FORMAT_COLUMN_MISSING,
)
from dbt_charts.core.diagnostics.execution import ExecutionError, QueryError
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode
from dbt_charts.core.execute.adapters.base import (
    apply_row_limit_truncation,
    handle_adapter_error,
    resolve_effective_row_limit,
)
from dbt_charts.core.execute.adapters.dbt_utils import source_target_path
from dbt_charts.core.execute.cache_backend import (
    BrowsableResultCache,
    CachedQueryFailure,
    CacheRows,
    ResultRows,
    TruncatedReason,
)
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.duckdb_cache import (
    compute_cache_key,
    compute_relevant_variables,
)
from dbt_charts.core.execute.metricflow import bundle_files, bundle_fingerprint
from dbt_charts.core.execute.sql_translate import (
    dialect_differs,
    dialect_unsupported,
)
from dbt_charts.core.project import is_glob

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.source import (
        CsvSourceConfig,
        JsonSourceConfig,
        ParquetSourceConfig,
    )
    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
    from dbt_charts.core.execute.adapters.base import ResolvedRelation
    from dbt_charts.core.execute.cache_backend import QueryResultCache


def _query_name_for(query: AnyQuery, registry: dict[str, AnyQuery]) -> str | None:
    """Find the registry name for a query object, or None if not found."""
    for name, q in registry.items():
        if q is query:
            return name
    return None


def _compute_watermark(
    rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows are Any-valued
    key_col: str,
    query_name: str,
) -> (
    Any | None  # type-state: explicit_any — MAX watermark; source-dialect-typed
):
    """Return MAX(key_col) over rows, or None when rows are empty.

    The watermark's type is whatever the cache handed back for *rows* — never
    guessed from string shape here. ``QueryResultCache.get()`` is contractually
    required to return each column with the same Python type its ``put()``
    received (``cache_backend.py``'s ``QueryResultCache`` docstring); a backend
    that cannot uphold that (e.g. one that serializes to JSON) must carry
    enough metadata to restore the original type itself. Guessing from a
    string's shape here previously misclassified a genuinely-VARCHAR
    incremental watermark column holding ISO-date-shaped values
    ('2026-08-22') as a DATE, emitting a CAST that a VARCHAR column rejects
    outright — see the incremental-chart-tail task history.

    Raises QueryError if key_col is absent from any row or if the column
    values are not orderable — a misspelled incremental watermark column is a
    hard error, not a silent fallback to full refresh.
    """
    if not rows:
        return None
    try:
        return max(row[key_col] for row in rows)
    except KeyError as exc:
        raise QueryError(
            f"incremental watermark column {key_col!r} not found in cached rows for query {query_name!r}",
            query_name,
        ) from exc
    except TypeError as exc:
        raise QueryError(
            f"incremental watermark column {key_col!r} values are not orderable for query {query_name!r}",
            query_name,
        ) from exc


def _watermark_to_sql_node(
    watermark: Any,  # type-state: explicit_any — MAX value from cached row; type is source-dialect-specific
    query_name: str,
) -> exp.Expression:
    """Return a sqlglot expression node for the watermark value.

    Builds a typed node only — no dialect. Dialect-correct rendering happens
    where the caller composes the full predicate (identifier + this node) and
    calls ``.sql(dialect=...)`` on it, so the same node renders correctly
    under any dialect.

    Uses sqlglot expressions so the output is properly quoted/cast rather
    than Python repr, which breaks for datetime, date, Decimal, and strings
    containing apostrophes.
    """
    # bool must be checked before int because bool is a subclass of int.
    if isinstance(watermark, bool):
        return exp.true() if watermark else exp.false()
    if isinstance(watermark, int):
        return exp.Literal.number(watermark)
    if isinstance(watermark, float | Decimal):
        return exp.Literal.number(str(watermark))
    if isinstance(watermark, datetime):
        # A naive datetime has no source-dialect offset to encode, so TIMESTAMP
        # is correct; a tz-aware one (BigQuery TIMESTAMP, Postgres timestamptz,
        # Snowflake TIMESTAMP_TZ) must CAST to a timezone-aware SQL type or the
        # embedded offset in the literal is silently misread as local time.
        cast_type = (
            exp.DataType.Type.TIMESTAMPTZ
            if watermark.tzinfo is not None
            else exp.DataType.Type.TIMESTAMP
        )
        return exp.Cast(
            this=exp.Literal.string(watermark.isoformat()),
            to=exp.DataType(this=cast_type),
        )
    if isinstance(watermark, date):
        return exp.Cast(
            this=exp.Literal.string(watermark.isoformat()),
            to=exp.DataType(this=exp.DataType.Type.DATE),
        )
    if isinstance(watermark, str):
        return exp.Literal.string(watermark)
    raise QueryError(
        f"incremental watermark has unsupported type {type(watermark).__name__!r}",
        query_name,
    )


def _incremental_safety_reason(sql: str, dialect_name: str | None) -> str | None:
    """Return a reason string if SQL is unsafe for incremental tail, else None.

    Queries with a top-level LIMIT (including dialect-specific top-N spellings
    like T-SQL's TOP and the ANSI FETCH FIRST n ROWS ONLY, which sqlglot parses
    as exp.Fetch rather than exp.Limit), ORDER BY (on a bare Select or a set
    operation), a
    window function, or a bare aggregate/DISTINCT with no GROUP BY cannot be
    safely tail-filtered: a LIMIT would silently cap new rows, ORDER BY is
    meaningless over a partial range, window functions and ungrouped
    aggregates produce results whose value depends on the full dataset, and
    an unparseable statement carries no provable safety at all — every one of
    these falls back to a full refresh rather than a wrong-looking result.
    """
    try:
        stmts = sqlglot.parse(
            sql, read=dialect_name, error_level=sqlglot.ErrorLevel.IGNORE
        )
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return "unparseable"
    if not stmts or stmts[0] is None:
        return "unparseable"
    stmt = stmts[0]
    if stmt.find(exp.Limit, exp.Fetch):
        return "limit"
    if isinstance(stmt, exp.Select | exp.SetOperation) and stmt.args.get("order"):
        return "order_by"
    if stmt.find(exp.Window):
        return "window"
    if isinstance(stmt, exp.Select):
        if stmt.args.get("distinct"):
            return "distinct"
        if not stmt.args.get("group") and any(
            e.find(exp.AggFunc) is not None for e in stmt.expressions
        ):
            return "aggregate"
    return None


def _merge_incremental_rows(
    tail_rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows
    prior_rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows
    key_col: str,
    query_name: str,
) -> list[dict[str, Any]]:  # type-state: explicit_any — row dict values
    """Merge tail rows over prior cached rows, tail wins on key collision.

    Dedup: tail wins for restated keys (prior rows at the watermark are
    overwritten by the tail's version). Sorted descending by key so HEAD
    truncation (_enforce_result_limits uses rows[:max_rows]) keeps the
    newest rows when the merged set exceeds the limit.

    No re-typing of prior rows happens here: the cache contract
    (``cache_backend.py``) requires ``get()`` to hand back each column with
    its original ``put()``-time Python type, so tail rows (freshly executed,
    never cached) and prior rows (read from a compliant cache) are already
    comparable. A str-typed prior row against a date/datetime-typed tail row
    is therefore a genuine contract violation, not a recoverable shape — the
    sort below raises on exactly that disagreement.

    Raises QueryError if the merged keys are not mutually orderable — a
    cache backend that lost the key column's type (or a genuinely
    heterogeneous key) cannot be trusted to sort correctly, and truncating
    an unsorted list would silently drop the wrong rows.
    """
    tail_keys = {row[key_col] for row in tail_rows}
    merged = tail_rows + [r for r in prior_rows if r[key_col] not in tail_keys]
    try:
        merged.sort(key=lambda r: r[key_col], reverse=True)
    except TypeError as exc:
        raise QueryError(
            f"incremental watermark column {key_col!r} values are not mutually "
            f"orderable across cached and tail rows for query {query_name!r}",
            query_name,
        ) from exc
    return merged


def _replayed_query_error(failure: CachedQueryFailure, query_name: str) -> QueryError:
    """Rebuild the QueryError a fresh run raised from its cached record."""
    code = None
    if failure.error_code is not None:
        registered = REGISTRY.get(failure.error_code)
        # _warm_from_cache only replays a failure whose code is a registered error.
        assert isinstance(registered, ErrorCode)
        code = registered
    # The stored message is already the final text, so query_name goes in
    # fields only — through QueryError's constructor it would be appended again.
    error = QueryError(failure.error_message, code=code)
    error.fields = {**failure.error_fields}
    error.fields.setdefault("query_name", query_name)
    return error


# Probes (is_cached, cache_key) must not resolve a source the key does not read,
# so the cache key takes the resolution as a thunk.
SourceThunk = Callable[[], ResolvedSourceConfig | None]


def _memo_key(
    query: AnyQuery, cache_key: tuple[str, str, str]
) -> tuple[str, str, str, str]:
    """Scope a content cache key to the asking query's resolved cache policy.

    ``compute_cache_key`` is content-addressed on purpose: identical SQL against
    one source is a single *stored* entry, so siblings share the warehouse work.
    The in-memory memo cannot be keyed that way. It is warmed from that stored
    entry, and freshness is a property of the query doing the asking — so one
    slot shared across policies lets whichever query rendered first decide what
    its siblings are served, with no ttl check and no `enabled` check in the way.

    Appending the policy gives each distinct policy its own slot. Identical
    policies still share one, which is where the memoization win lives. The
    *persistent* key is untouched — folding policy in there would cold-start the
    stored entry on every ttl edit.
    """
    if not query.cache.enabled:
        # Disabled queries never warm from the store — `_warm_from_cache`
        # refuses on `enabled` at the read itself — so this slot only ever holds
        # rows the current render executed. That is what makes `cache: false` a
        # real no-store without a special case here, while still memoizing
        # across a query's own consumers.
        return (*cache_key, "off")
    ttl = query.cache.ttl_timedelta
    return (*cache_key, "forever" if ttl is None else str(ttl.total_seconds()))


def merge_board_variables(
    board: Board, variables: VariableValues, clock: BoardClock
) -> VariableValues:
    """Merge a board's declared variables with caller overrides, coerced to type,
    each daterange preset dated on the board's ``clock``.

    Declared names first (so an undeclared-but-defaulted variable still binds),
    then board defaults, then the caller's values. Coercion is the runtime
    boundary: string inputs become their declared semantic type (date/number/
    bool) so the SQL layer binds typed literals. Execution and the
    ``--warehouse`` check share this so both see identical SQL.
    """
    variable_registry = board.variable_registry or {}
    all_variables: VariableValues = dict.fromkeys(variable_registry)
    all_variables.update(board.variable_defaults)
    merged = {**all_variables, **parse_variable_json_strings(variables)}
    return resolve_daterange_presets(
        coerce_variable_values(merged, variable_registry), variable_registry, clock
    )


def resolve_query_references(
    query: AnyQuery,
    *,
    all_queries: dict[str, AnyQuery],
    query_name: str | None = None,
) -> AnyQuery:
    """Resolve ``{{ queries.* }}`` references in SQL as text, leaving variables untouched.

    Resolves nested references in topological dependency order so that
    multi-level query composition (e.g. style -> calc -> base) works, and
    propagates ``setup_sql`` from referenced queries so their preambles run
    before the dependent query. Execution and the ``--warehouse`` check share
    this: a check that resolved only one hop would report a syntax error on the
    unrendered ``{{ queries.X }}`` of SQL that renders fine.

    Variables are NOT substituted here. They stay author-written so the
    parameterized render on the way to the warehouse is the single render over
    them — substituting here and rendering again would corrupt authored literals
    that look like Jinja templates.

    Args:
        query: Query to resolve.
        all_queries: Every query the reference could name — board and registry.
        query_name: Name of this query (for dependency-ordered lookup).
    """
    # Only SQL queries can have query references
    if not is_sql_query(query):
        return query

    # Collect setup_sql from the full dependency chain
    from dbt_charts.core.execute.setup_sql import collect_setup_sql

    setup_stmts = collect_setup_sql(
        # We need a name to look up, but we may be called with an unnamed query.
        # Pass the query's own name by searching all_queries; fallback to just
        # using the query's own setup_sql.
        _query_name_for(query, all_queries) or "__anonymous__",
        {**all_queries, "__anonymous__": query},
    )
    merged_setup = "\n".join(setup_stmts) if setup_stmts else None

    # Only inline {{ queries.X }} refs expand here; {{ queries.X.cache }}
    # refs stay in place because the composer runs them over X's cached rows.
    if "{{ queries." not in query.sql:
        # No inline {{ queries.X }} refs to resolve, but may still need
        # propagated setup_sql.
        if merged_setup != query.setup_sql:
            return query.model_copy(update={"setup_sql": merged_setup})
        return query

    from dbt_charts.core.compile.template.jinja import expand_query_refs

    # References only. Variables stay author-written so the parameterized
    # render on the way to the warehouse is the single render over them.
    resolved_sql = expand_query_refs(query_name or "", all_queries)

    updates: dict[str, Any] = {"sql": resolved_sql}
    if merged_setup:
        updates["setup_sql"] = merged_setup
    return query.model_copy(update=updates)


@dataclass(frozen=True)
class TruncationInfo:
    """A query's result was truncated by execution.max_rows or
    max_result_bytes before reaching the cache. No original_row_count field:
    the adapter's over-fetch-by-one only proves "more rows existed than the
    ceiling," never the true total, so nothing here may claim to know it.
    """

    query_name: str
    kept_row_count: int
    reason: TruncatedReason


def utc_now() -> datetime:
    """The real clock, in UTC — ``Executor``'s default time source."""
    return datetime.now(UTC)


class Executor:
    """Executes queries for boards.

    Stage: EXECUTE (Service Module)

    The executor manages query execution for a compiled board.
    It handles:
    - Query lookup from the board
    - Adapter selection based on query type
    - Result caching for efficiency
    - Variable substitution

    Does NOT:
    - Compile boards (use compile module)
    - Render charts (use render module)

    Attributes:
        board: The compiled board
        adapter_registry: Registry of adapters
        query_registry: Complete query registry (for cross-references)

    Example:
        >>> from pathlib import Path
        >>> from dbt_charts.core.compile import compile
        >>> from dbt_charts.core.execute import Executor
        >>> from dbt_charts.core.execute.adapters import build_adapter_registry
        >>> from dbt_charts.cli.filesystem_project import FilesystemProject
        >>>
        >>> result = compile(yaml_content)
        >>> registry = build_adapter_registry(FilesystemProject(Path.cwd()))
        >>> executor = Executor(result.board, registry, query_registry=result.query_registry)
        >>>
        >>> # Execute a query
        >>> data = executor.execute_query("sales", {"year": 2024})
        >>> print(data)  # [{"date": "2024-01", "amount": 1000}, ...]
    """

    def __init__(
        self,
        board: Board,
        adapter_registry: "AdapterRegistry",
        query_registry: dict[str, AnyQuery] | None = None,
        use_cache: bool = True,
        result_cache: "QueryResultCache | None" = None,
        clock: Callable[[], datetime] = utc_now,
        board_slug: str | None = None,
    ) -> None:
        """Initialize executor.

        Args:
            board: Compiled board
            adapter_registry: Adapter registry. Build at the entry point with
                ``build_adapter_registry(FilesystemProject(root), ...)``
                (``FilesystemProject`` from ``dbt_charts.cli.filesystem_project``);
                the executor does not invent one from cwd.
            query_registry: Optional query registry for cross-file references
            use_cache: Default *persistent-store* participation for query
                execution. False means no-store: results are neither read from
                nor written to ``result_cache``. The per-render in-memory memo
                is unaffected — one execution per query per Executor holds
                regardless, because resolve and render must see identical rows
                (see execute_query's Step 3 comment).
            result_cache: Optional cache backend for persistent caching (Suite context)
            clock: This render's only time source. Read once here for
                ``rendered_at`` and again as each query's rows come back; pin it
                and the render's bytes stop depending on when it ran.
            board_slug: The board's file-identity slug (``board_slug(relpath)``),
                recorded on every cache entry this render writes so the cache can
                be listed by board. None for a board with no file (in-memory
                content), whose entries carry the compiled id instead.
        """
        self.board = board
        self._board_slug = board_slug
        self._clock = clock
        self.rendered_at = clock()
        self.query_registry = query_registry or {}
        # Keyed by content *plus* the asking query's resolved policy (_memo_key):
        # the slot is warmed from the persistent store, so a content-only key
        # would let one query's policy answer a sibling's read.
        self._cache: dict[tuple[str, str, str, str], ResultRows] = {}
        self._provenance_cache: dict[str, list[ResolvedRelation]] = {}
        self._query_errors: dict[str, Exception] = {}
        # Query name -> why its dbt column formats could not be derived; raised
        # where a chart reading that query resolves (column_formats.py).
        self.column_format_errors: dict[str, DbtChartsError] = {}
        self._use_cache = use_cache
        self._result_cache = result_cache
        # Per-query (name, data_as_of, policy, is_cache_hit) records for every
        # query that completed this render. Fresh executions set data_as_of to
        # the instant the rows came back; cache hits use the entry's written_at.
        # Powers both the snapshot expires_at computation (all records) and the
        # "data as of" board-chrome stamp (cache-hit records only).
        self._query_data_ages: list[tuple[str, datetime, CachePolicy, bool]] = []
        # Query name → truncation record, for every query whose result was cut
        # by execution.max_rows/max_result_bytes this render. Read by the
        # render-warnings pass (query_result_truncated.py) via truncations().
        self._truncations: dict[str, TruncationInfo] = {}
        self._range_filter_memo: dict[
            tuple[str, str], tuple[str, str | None, list[DateRangeFilter]] | None
        ] = {}
        # Per-render memo of each file source's version token, so the cache key a
        # query writes matches the one a later query in the same render reads even
        # if the file's mtime changes mid-render. Keyed by source name.
        self._source_versions: dict[str, str] = {}
        self._semantic_versions: dict[str, str] = {}
        self._warehouse_hit_ats: list[datetime] = []
        # Same idea for the dbt manifest a ref()/source() resolves against,
        # keyed by the source's authored target_path.
        self._manifest_versions: dict[str | None, str] = {}
        # Sizing and paint resolve the same text, so each listing is read once.
        self._text_listings: dict[tuple[str, frozenset[ListingCall]], TextListings] = {}
        self.adapter_registry = adapter_registry
        # Pre-parse file source configs once so _resolve_file_source does not
        # re-run Pydantic on every hot-path query call.
        from dbt_charts.core.compile.models.source import (
            CsvSourceConfig,
            JsonSourceConfig,
            ParquetSourceConfig,
            parse_source_config,
        )

        self._file_source_configs: dict[
            str, CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig
        ] = {}
        # Board-level inline sources take priority over project-level sources.
        for src_name, raw in board.sources.items():
            cfg = parse_source_config(raw)
            if isinstance(
                cfg, (CsvSourceConfig, JsonSourceConfig, ParquetSourceConfig)
            ):
                self._file_source_configs[src_name] = cfg
        self._project_file_sources_merged = False

    def text_listings(
        self, base_dir: str, calls: frozenset[ListingCall]
    ) -> TextListings:
        key = (base_dir, calls)
        if key not in self._text_listings:
            directory = self.adapter_registry.project.directory(base_dir)
            self._text_listings[key] = resolve_listings(calls, directory)
        return self._text_listings[key]

    def execute_query(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:  # type-state: explicit_any — DB rows
        """Execute a query and return results.

        Stage: EXECUTE (Main Entry Point)

        Looks up the query by name, resolves the appropriate adapter,
        executes the query, and returns the data.

        Args:
            query_name: Name of query to execute (may include "queries." prefix)
            variables: Variable values for query resolution
            use_cache: Whether to read/write the persistent result store for
                this call (defaults to the instance setting). Does not disable
                the per-render memo — a query already executed by this
                Executor returns those same rows.
            force_refresh: If True, clear both success and failure caches
                (memo included) for this query and re-run it from scratch.

        Returns:
            List of dictionaries with query results (each dict is a row)

        Raises:
            ExecutionError: If query not found or execution fails, or if the
                query's ``formats:`` names a column the rows do not carry
                (a zero-row result carries no columns and passes)
            QueryError: If a cached failure exists within its retry window —
                rebuilt with the code it was first raised with, the
                ``CachedQueryFailure`` as its ``__cause__``

        Example:
            >>> data = executor.execute_query("sales", {"year": 2024})
            >>> for row in data:
            ...     print(f"{row['date']}: {row['amount']}")
        """
        return self.execute_query_result(
            query_name, variables, use_cache, force_refresh
        ).rows

    def execute_query_result(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> ResultRows:
        """:meth:`execute_query` with the result's declared schema: rows plus the
        type of each column, the same whether the rows came from a warehouse run,
        the per-render memo or the persistent cache."""
        # Outside a render nothing scopes the clock a preset literal in the SQL
        # (`filter_date_range('d', 'last_7_days')`) dates on.
        with board_clock(self.board.clock(self.rendered_at)):
            result = self._execute_result(
                query_name, variables, use_cache, force_refresh
            )
        rows = result.rows
        name = query_name.removeprefix("queries.")
        query = self.get_query(name)
        if rows:
            for column in missing_format_columns(query.formats, rows[0]):
                raise QueryError.from_code(
                    ERR_QUERY_FORMAT_COLUMN_MISSING,
                    query=name,
                    column=column,
                    columns=", ".join(sorted(rows[0])),
                )
        return result

    def _execute_result(
        self,
        query_name: str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
        force_refresh: bool = False,
    ) -> ResultRows:
        """Run (or recall from cache) a query; ``execute_query_result`` checks it."""
        # Step 1: strip the "queries." prefix, merge variables with board
        # defaults, and look up the query. is_cached shares this exact prologue
        # (via _prepare_call) so its cache-key prediction matches this path.
        query_name, query, variables = self._prepare_call(query_name, variables)
        # One resolution, taken lazily: a memo or persistent hit must not resolve
        # (is_cached agrees), and a resolver failure is raised inside the query's
        # failure boundary below, never out of the board.
        resolve_source = cache(partial(self._resolve_source, query, query_name))

        # ────────────────────────────────────────────────────────────────
        # Step 3: Check cache (use instance default if not explicitly set).
        # Two different caches here, and only one of them is optional.
        # The per-render `_cache` dict is not a freshness cache at all — it is
        # the render's identity guarantee: one execution per query per
        # Executor, since every chart consuming a query calls execute_query on
        # top of the parallel prefetch. It is unconditional. A query with tied
        # ORDER BY values can legally return a different row order on each
        # execution, so a second execution within one emission would let the
        # resolve pass and the render/record pass see different rows (the
        # ERR-RESOLVED-PIE-DATA-MISMATCH flake). `force_refresh` is the one
        # way to drop it. `use_cache` (call-level, else instance) governs only
        # the *persistent* store, and `should_use_cache` additionally honors
        # the resolved policy: a disabled query is never read from nor written
        # to that store (no-store, not store-and-ignore). Folding `enabled`
        # into a memo gate would fan one live query out to a warehouse round
        # trip per consumer, each at a different instant — two charts off one
        # query could then disagree. So the policy scopes the memo *key*
        # instead (_memo_key): a disabled query still memoizes for its own
        # consumers, it just no longer shares a slot with a query whose policy
        # differs.
        # ────────────────────────────────────────────────────────────────
        should_use_cache = (
            use_cache if use_cache is not None else self._use_cache
        ) and query.cache.enabled
        cache_key = self._cache_key(query_name, query, variables, resolve_source)
        memo_key = _memo_key(query, cache_key)

        # Saved in step 3c when a persistent cache hit is eligible for
        # incremental tail; consumed after reference resolution in step 4.
        _prior_rows: list[dict[str, Any]] | None = (  # type-state: explicit_any — rows
            None
        )
        _wmark: Any | None = (  # type-state: explicit_any — watermark
            None
        )

        # force_refresh: clear all caches for this key before running
        if force_refresh:
            # Every policy slot for this content, not just the caller's. The
            # line below clears the *shared* persistent entry for all of them,
            # so popping one slot would leave a different-policy sibling serving
            # pre-refresh rows against a store that no longer has them —
            # narrowing what force_refresh has always meant.
            for key in [k for k in self._cache if k[:3] == cache_key]:
                del self._cache[key]
            self._query_errors.pop(query_name, None)
            if self._result_cache:
                self._result_cache.clear(*cache_key)
        else:
            # ────────────────────────────────────────────────────────────
            # Step 3a: In-memory success cache (O(1) dict lookup, no I/O).
            # Must precede the _query_errors check: the parallel pre-execution
            # pass writes results into _cache *before* attempting the persistent
            # write-through (_cache_outcome). If that write-through raises
            # (e.g. a Postgres FK constraint in Cloud's snapshots backend), the
            # exception is stored in _query_errors even though _cache already
            # holds valid rows. Checking _cache first ensures those valid rows
            # are returned; _query_errors is only consulted when _cache misses,
            # which is the only time it correctly reflects a real query failure.
            # ────────────────────────────────────────────────────────────
            if memo_key in self._cache:
                return self._cache[memo_key]

            # ────────────────────────────────────────────────────────────
            # Step 3b: In-memory error memo — skip the persistent cache I/O.
            # After the in-memory hit check above, a _query_errors entry means
            # the query genuinely failed (no valid rows in _cache). Skip the
            # persistent success/failure cache probes — guaranteed misses and,
            # in Cloud, a full Postgres round-trip per chart on the serial
            # layout-sizing pass.
            # ────────────────────────────────────────────────────────────
            if query_name in self._query_errors:
                stored = self._query_errors[query_name]
                if isinstance(stored, QueryError):
                    raise stored
                stored_code = (
                    stored.code if isinstance(stored, DbtChartsError) else None
                )
                raise QueryError(str(stored), query_name, code=stored_code) from stored

            # ────────────────────────────────────────────────────────────
            # Step 3c: Persistent outcome cache (duckdb/Postgres). One
            # lookup: the backend holds a single entry per key recording
            # either the rows or the error, so a rows miss is never followed
            # by a second probe for a cached failure. The in-memory layer was
            # already checked in 3a.
            # ────────────────────────────────────────────────────────────
            if should_use_cache:
                try:
                    hit = self._warm_from_cache(cache_key, query, query_name)
                except CachedQueryFailure as exc:
                    self._query_data_ages.append(
                        (query_name, exc.failed_at, query.cache, False)
                    )
                    raise _replayed_query_error(exc, query_name) from exc
                if hit:
                    if (
                        is_sql_query(query)
                        # Same activity gate as the cache-key fold
                        # (duckdb_cache.py): isinstance, not truthiness, so
                        # the two can never disagree on what "active" means.
                        and isinstance(query.incremental, str)
                        # File-source queries (CSV/JSON/Parquet) have no
                        # warehouse dialect to tail-wrap against — they always
                        # take the normal cache path below, exactly like a
                        # non-incremental query.
                        and self._resolve_file_source(query) is None
                    ):
                        prior_rows = self._cache[memo_key].rows
                        # Raises QueryError if key_col absent or values non-orderable.
                        watermark = _compute_watermark(
                            prior_rows, query.incremental, query_name
                        )
                        if watermark is not None:
                            # Defer tail until after reference resolution (step 4)
                            # so the tail SQL wraps the fully resolved query, not
                            # the authored Jinja template.
                            _prior_rows = prior_rows
                            _wmark = watermark
                        del self._cache[memo_key]
                    else:
                        return self._cache[memo_key]

        # ────────────────────────────────────────────────────────────────
        # Step 4: Resolve query references in SQL
        # ────────────────────────────────────────────────────────────────
        # The pre-resolution query: its cache policy governs the write-through,
        # and the file-source and cache-ref paths detect on its authored SQL.
        original_query = query

        def fail_query(error: Exception) -> NoReturn:
            if should_use_cache:
                self._cache_failure(query_name, query, variables, cache_key, error)
            self._query_data_ages.append(
                (query_name, self._clock(), original_query.cache, False)
            )
            if isinstance(error, QueryError):
                raise error
            raise QueryError(
                str(error),
                query_name,
                code=error.code if isinstance(error, DbtChartsError) else None,
            ) from error

        # ────────────────────────────────────────────────────────────────
        # Step 4b: Cache-ref composition ({{ queries.X.cache }})
        # Detected on the *authored* SQL, before reference resolution. The
        # composing query is rendered (inline refs resolved, runtime variables
        # parameterized) and executed over the upstream rows in an isolated
        # in-process DuckDB — never string-interpolated onto the source adapter
        # or an application-database connection.
        #
        # Cache-ref composition is incompatible with incremental tail: the
        # in-process execution reruns the whole composing query over the already-
        # cached upstream rows, so no watermark predicate can be appended to the
        # source SQL. Clear state and let the cache-ref path execute fresh.
        # ────────────────────────────────────────────────────────────────
        if (
            _prior_rows is not None
            and is_sql_query(query)
            and CACHE_REF_JINJA_RE.search(query.sql)
        ):
            logger.debug(
                "%s: cache-ref query is ineligible for incremental tail; full refresh",
                query_name,
            )
            _prior_rows = None
            _wmark = None
        if is_sql_query(query) and CACHE_REF_JINJA_RE.search(query.sql):
            composed, cache_ref_reason = self._execute_cache_ref_query(
                query, variables, query_name
            )
            result_data = self._enforce_result_limits(
                composed, query_name, seed_reason=cache_ref_reason
            )
            data_as_of = self._clock()
            self._cache[memo_key] = result_data
            if should_use_cache:
                # Write through to the persistent backend.
                truncation = self._truncations.get(query_name)
                self._cache_outcome(
                    query_name,
                    original_query,
                    result_data,
                    variables,
                    cache_key,
                    truncated_reason=truncation.reason if truncation else None,
                )
            self._query_data_ages.append(
                (query_name, data_as_of, original_query.cache, False)
            )
            return result_data

        query = resolve_query_references(
            query,
            all_queries={**self.board.queries, **self.query_registry},
            query_name=query_name,
        )

        # ────────────────────────────────────────────────────────────────
        # Incremental tail: execute after reference resolution so the tail
        # SQL wraps the fully resolved query (no Jinja refs remain).
        # Safety predicate: LIMIT, ORDER BY, or window functions make the
        # tail semantically incorrect — fall back to a full refresh.
        # ────────────────────────────────────────────────────────────────
        try:
            source_config = resolve_source()
            self._require_translated_path(query, source_config, query_name)
        except Exception as e:  # noqa: BLE001 — a resolver may raise anything (SSRF guard, DNS)
            fail_query(e)

        if _prior_rows is not None and _wmark is not None:
            # Step 3c set _prior_rows/_wmark only when
            # is_sql_query(query) was True; resolve_query_references preserves the subtype.
            assert is_sql_query(query) and is_sql_query(original_query)
            prior_rows, watermark = _prior_rows, _wmark
            # The dialect the adapter will actually execute against — the
            # safety predicate and the tail SQL below must agree with it, or a
            # dialect-specific LIMIT spelling (T-SQL TOP) slips the safety
            # guard and a non-ANSI identifier quote gets silently misread.
            dialect = (
                None if source_config is None else sqlglot_dialect(source_config.type)
            )
            reason = _incremental_safety_reason(query.sql, dialect)
            if reason is not None:
                logger.debug(
                    "%s: SQL contains %s — ineligible for incremental tail; full refresh",
                    query_name,
                    reason,
                )
            else:
                return self._run_incremental_tail(
                    query_name,
                    query,
                    variables,
                    memo_key,
                    cache_key,
                    should_use_cache,
                    original_query,
                    prior_rows,
                    watermark,
                    dialect,
                    source_config,
                )

        # ────────────────────────────────────────────────────────────────
        # Step 5: Execute via adapter
        # ────────────────────────────────────────────────────────────────
        try:
            result = self.adapter_registry.execute(
                query,
                variables,
                board=self.board,
                query_name=query_name,
                source_config=source_config,
            )
        except Exception as e:  # noqa: BLE001 — an adapter may raise anything
            fail_query(e)

        if not result.is_success:
            assert result.error is not None
            result.error.fields.setdefault("query_name", query_name)
            if should_use_cache:
                self._cache_failure(
                    query_name, query, variables, cache_key, result.error
                )
            self._query_data_ages.append(
                (query_name, self._clock(), original_query.cache, False)
            )
            raise result.error

        # ────────────────────────────────────────────────────────────────
        # Step 6: Cache and return
        # ────────────────────────────────────────────────────────────────
        data_as_of = self._clock()
        enforced_data = self._enforce_result_limits(
            ResultRows(result.data, result.schema),
            query_name,
            seed_reason=result.truncated_reason,
        )
        self._cache[memo_key] = enforced_data

        # Cache dbt relation lineage (which ref()/source() calls resolved to)
        if result.resolved_relations:
            self._provenance_cache[query_name] = result.resolved_relations

        # Also cache to DuckDB if configured (Suite context). Gated on
        # should_use_cache — a disabled policy or a call-level use_cache=False
        # must write nothing to the persistent store (no-store, not
        # store-and-ignore).
        if should_use_cache:
            truncation = self._truncations.get(query_name)
            self._cache_outcome(
                query_name,
                query,
                enforced_data,
                variables,
                cache_key,
                truncated_reason=truncation.reason if truncation else None,
            )

        self._query_data_ages.append(
            (query_name, data_as_of, original_query.cache, False)
        )
        return enforced_data

    def execute_chart(
        self,
        chart: Chart | str,
        variables: VariableValues | None = None,
        use_cache: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the query for a chart.

        Convenience method that handles chart → query lookup.

        Args:
            chart: Chart or chart name string
            variables: Variable values
            use_cache: Whether to use the persistent result store
                (defaults to instance setting)

        Returns:
            Query results for the chart

        Example:
            >>> chart = board.charts["revenue"]
            >>> data = executor.execute_chart(chart, {"year": 2024})
        """
        if isinstance(chart, str):
            if chart not in self.board.charts:
                raise ExecutionError(f"Chart '{chart}' not found")
            chart = self.board.charts[chart]

        # Handle blank charts (no query) - return empty data
        if chart.query_name is None:
            return []

        # Get data (use_cache will fall back to instance default in execute_query)
        return self.execute_query(chart.query_name, variables, use_cache)

    def get_query_provenance(self, query_name: str) -> list["ResolvedRelation"]:
        """Get cached dbt relation lineage for a query.

        Returns the ResolvedRelation objects captured during execution, or an
        empty list when no provenance is available (non-dbt queries, or not yet
        executed). Empty rather than None because the distinction carries no
        information — a nullable that nothing distinguishes is the type-state
        rot ``scripts/type_state_counter.py`` counts.
        """
        if query_name and query_name.startswith("queries."):
            query_name = query_name[8:]
        if query_name not in self._provenance_cache:
            return []
        return self._provenance_cache[query_name]

    def get_query(self, query_name: str) -> AnyQuery:
        """Look up a query by name.

        Checks board.queries first, then query_registry.

        Args:
            query_name: Query name

        Returns:
            AnyQuery

        Raises:
            ExecutionError: If query not found
        """
        if query_name in self.board.queries:
            return self.board.queries[query_name]

        if query_name in self.query_registry:
            return self.query_registry[query_name]

        raise ExecutionError(f"Query '{query_name}' not found")

    def is_cached(
        self,
        query_name: str,
        variables: VariableValues | None = None,
    ) -> bool:
        """Return True if execute_query would return immediately from cache.

        Performs the same variable normalization and cache-key computation as
        execute_query, then reports only a rows outcome as a hit. A cached
        error is not a hit — that query must go through execute_query so the
        failure is raised rather than silently skipped. Likewise, an
        ExecutionError raised *while computing* the cache key (e.g. a missing
        file-source file surfacing from _source_version) reports a miss rather
        than propagating: is_cached must never raise, so a fresh execute_query
        call is what turns the failure into a proper per-chart diagnostic.

        Args:
            query_name: Name of the query (may include "queries." prefix).
            variables: Variable values — same values passed to execute_query.

        Returns:
            True if the result is available in cache; False otherwise.
        """
        try:
            with board_clock(self.board.clock(self.rendered_at)):
                name, query, merged = self._prepare_call(query_name, variables)
                return self._lookup_cached(
                    query, merged, name, partial(self._resolve_source, query, name)
                )
        except ExecutionError:
            return False

    def cache_key(
        self, query_name: str, variables: VariableValues | None = None
    ) -> tuple[str, str, str]:
        """The persistent-cache key ``execute_query`` reads and writes for
        *query_name* under *variables* (board defaults fill the rest).

        Raises:
            ExecutionError: If the query is not found, or a file source's
                version cannot be read.
        """
        with board_clock(self.board.clock(self.rendered_at)):
            name, query, merged = self._prepare_call(query_name, variables)
            return self._cache_key(
                name, query, merged, partial(self._resolve_source, query, name)
            )

    def _prepare_call(
        self,
        query_name: str,
        variables: VariableValues | None,
    ) -> tuple[str, AnyQuery, dict[str, Any]]:
        """Strip the ``queries.`` prefix, merge variables with board defaults, and
        look up the query.

        Shared by execute_query and is_cached so both derive the identical
        (query, variables) pair — and therefore the identical cache key. Keeping
        this in one place is load-bearing: if the two drifted, is_cached would
        mispredict execute_query and the cache-hit partition would silently stop
        skipping the pool.

        Raises:
            ExecutionError: If the query name is not found.
        """
        if query_name.startswith("queries."):
            query_name = query_name[8:]
        merged = merge_board_variables(
            self.board,
            variables or {},  # type-state: silent_fallback — None: no caller overrides
            self.board.clock(self.rendered_at),
        )
        return query_name, self.get_query(query_name), merged

    def _cache_key(
        self,
        query_name: str,
        query: AnyQuery,
        variables: VariableValues | None,
        resolve_source: SourceThunk,
    ) -> tuple[str, str, str]:
        """Cache key over the SQL as it executes, ``{{ queries.* }}`` expanded.

        Keying the authored text would let an edit to an upstream query leave
        every downstream entry serving old rows. A ``{{ queries.X.cache }}``
        reference is not inlined — the composing query runs over X's cached rows —
        so X's own key is folded in instead, recursively. Reference expansion
        rejects cycles first, which is what keeps that recursion finite.
        """
        all_queries = {**self.board.queries, **self.query_registry}
        expanded = resolve_query_references(
            query, all_queries=all_queries, query_name=query_name
        )
        ref_names = (
            dict.fromkeys(CACHE_REF_JINJA_RE.findall(expanded.sql))
            if is_sql_query(expanded)
            else ()
        )
        upstream_keys = []
        for ref_name in ref_names:
            upstream = self._cache_ref_upstream(query_name, ref_name, all_queries)
            upstream_keys.append(
                self._cache_key(
                    ref_name,
                    upstream,
                    variables,
                    partial(self._resolve_source, upstream, ref_name),
                )
            )
        return compute_cache_key(
            expanded,
            variables,
            self.board.sources,
            source_version=self._source_version(query)
            + self._manifest_version(expanded, resolve_source)
            + self._semantic_version(query)
            + "".join(f"|{ref_key}" for ref_key in upstream_keys),
        )

    def _semantic_version(self, query: "AnyQuery") -> str:
        """Fingerprint of the MetricFlow bundle the query's source declares, so a
        changed bundle never replays rows, or an error, compiled from the old one.
        ``""`` for a source without one. Memoized per source name like
        ``_source_version``, for the same read-key/write-key consistency.

        Reads the declared ``metricflow:`` rather than resolving the source, for
        the reason ``_database_file_version`` gives, and hashes the files without
        loading the bundle: a declaration that does not parse, a bundle that
        fails to load, or a path that escapes the project fails at execution
        with its own error, so nothing is cached under this key.
        """
        if not is_sql_query(query) or query.source is None:
            return ""
        cached = self._semantic_versions.get(query.source)
        if cached is not None:
            return cached
        raw = self.board.sources.get(
            query.source
        ) or self.adapter_registry.project_sources.sources.get(query.source)
        try:
            bundle = declared_metricflow(raw) if raw is not None else None
        except ValidationError:
            bundle = None
        version = ""
        if bundle is not None:
            try:
                files = bundle_files(self.adapter_registry.project, bundle.path)
            except ValueError:
                files = None
            if files is not None:
                version = f"|metricflow:{bundle_fingerprint(files)}"
                if bundle.required:
                    version += ":required"
        self._semantic_versions[query.source] = version
        return version

    def _lookup_cached(
        self,
        query: AnyQuery,
        variables: VariableValues | None,
        query_name: str,
        resolve_source: SourceThunk,
    ) -> bool:
        """Return True if rows for (query, variables) are available without executing.

        Checks in-memory _cache first (microseconds) — unconditionally, since
        the memo serves regardless of cache settings — then the persistent
        backend (sub-ms), gated on self._use_cache and the query's policy. A
        cached *error* is not a hit: that query must go through execute_query
        so the failure is raised rather than skipped.

        Args:
            query: The compiled query object (already looked up).
            variables: Merged variable values (board defaults applied).
            query_name: Query name to record in _query_data_ages on a warm.
            resolve_source: Yields the query's source; called only when the cache key
                needs it.

        Returns:
            True if cached rows exist; False on any miss or cached error.
        """
        cache_key = self._cache_key(query_name, query, variables, resolve_source)
        if _memo_key(query, cache_key) in self._cache:
            return True
        if not self._use_cache or not query.cache.enabled:
            return False
        try:
            return self._warm_from_cache(cache_key, query, query_name)
        except CachedQueryFailure:
            # A cached error is not a hit: is_cached must not report one, and
            # must not raise either — that query goes through execute_query.
            return False

    def _warm_from_cache(
        self,
        cache_key: tuple[str, str, str],
        query: AnyQuery,
        query_name: str,
    ) -> bool:
        """Probe the persistent backend; return written_at once this key's rows are in _cache.

        One round trip: the backend stores one entry per key, so the rows probe
        and the failure probe are the same lookup. A rows hit warms the
        in-memory cache under *query*'s policy-scoped memo key, which is why the
        caller can then read it back from _cache — at that same memo key, not
        the raw content key — without touching the backend again.

        *query* supplies this reader's resolved cache policy, and **both halves
        of it are enforced here** — this is the only place the persistent store
        is read, so it is the only place that can guarantee either. The key
        excludes the policy, so an entry written by a longer-ttl query would
        otherwise be served in defiance of this query's freshness contract, and
        a `cache: false` query would have persisted rows loaded into its memo
        slot for `execute_query` to serve — the no-store guarantee, gone.

        Both checks are redundant with today's two callers (`execute_query`
        gates on `should_use_cache`, `_lookup_cached` gates the probe on
        `self._use_cache` and the policy), which is the point: a third caller
        that forgets cannot reintroduce the bug.

        Records a cache-hit entry in ``_query_data_ages`` on a successful warm —
        powers both snapshot ``expires_at`` and the "data as of" footer stamp.
        Also repopulates ``_truncations`` from the hit's ``truncated_reason``,
        so the render-warnings pass surfaces the same WARN-QUERY-RESULT-
        TRUNCATED warning on a warm hit that it would on a fresh execution —
        the warning is a property of the cached data, not of how (or whether)
        this render fetched it.

        Raises:
            CachedQueryFailure: the entry is a cached error, still inside its
                retry window. Propagating it is the point — execute_query wants
                exactly that; is_cached catches it.
        """
        if not self._result_cache or not query.cache.enabled:
            return False
        outcome = self._result_cache.get(*cache_key, ttl=query.cache.ttl_timedelta)
        if isinstance(outcome, CachedQueryFailure):
            if outcome.error_code is not None and outcome.error_code not in (
                REGISTRY.codes(level="error")
            ):
                # Written under a code this version no longer registers: it
                # cannot be replayed as it was raised, so the query re-runs.
                return False
            raise outcome
        if outcome is None:
            return False
        # Warm the memo on *this* query's policy-scoped slot. That scoping is
        # what stops the warm from answering a read for a different policy —
        # these rows passed this query's ttl, and no other query's.
        # Apply ceilings on the warm path: a cache entry written under a higher
        # ceiling must not be served verbatim if the ceiling was later lowered.
        # _enforce_result_limits re-checks both row and byte limits and updates
        # _truncations accordingly. seed_reason=outcome.truncated_reason propagates
        # the original write's truncation signal so the warning fires even when
        # the data is already under the current ceiling (e.g. ceiling was raised
        # since the write — the data is still legitimately truncated).
        enforced = self._enforce_result_limits(
            ResultRows(outcome.rows, outcome.schema),
            query_name,
            seed_reason=outcome.truncated_reason,
        )
        self._cache[_memo_key(query, cache_key)] = enforced
        if isinstance(self._result_cache, BrowsableResultCache):
            self._result_cache.note_use(*cache_key, self._get_board_slug(), query_name)
        self._query_data_ages.append(
            (query_name, outcome.written_at, query.cache, True)
        )
        if not self._source_version(query):
            self._warehouse_hit_ats.append(outcome.written_at)
        return True

    def _resolve_source(
        self, query: AnyQuery, query_name: str
    ) -> ResolvedSourceConfig | None:
        """The one resolution of a query's source; everything downstream is handed it.

        Raises:
            QueryError: The source cannot be resolved.
        """
        try:
            return self.adapter_registry.resolve_query_source(
                query, board=self.board, query_name=query_name
            )
        except DbtChartsError as exc:
            error = handle_adapter_error("Source resolution", exc).error
            assert error is not None
            error.fields.setdefault("query_name", query_name)
            raise error from exc

    @staticmethod
    def _require_translated_path(
        query: AnyQuery,
        source_config: ResolvedSourceConfig | None,
        query_name: str,
    ) -> None:
        """Refuse an incremental `dialect:` query that differs from the warehouse."""
        if not (
            is_sql_query(query)
            and query.dialect is not None
            and isinstance(query.incremental, str)
        ):
            return
        try:
            if source_config is not None and not dialect_differs(
                query.dialect, source_config
            ):
                return
            err = dialect_unsupported(
                query.dialect,
                "the source's warehouse",
                "incremental refresh wraps the SQL in warehouse SQL, so write it "
                "in the source's dialect",
            )
        except ExecutionError as exc:
            err = exc
        raise QueryError(str(err), query_name, code=err.code)

    def render_with_range_filters(
        self, query_name: str, variables: VariableValues | None
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None:
        """The SQL a query executes as, and the date ranges its filters recorded.

        None when the query has no ``filter_date_range()`` or can't be rendered
        here; it never fails a render.
        """
        query_name, query, merged = self._prepare_call(query_name, variables)
        memo_key = (query_name, json.dumps(merged, sort_keys=True, default=str))
        if memo_key not in self._range_filter_memo:
            self._range_filter_memo[memo_key] = self._render_with_range_filters(
                query_name, query, merged
            )
        return self._range_filter_memo[memo_key]

    def _render_with_range_filters(
        self, query_name: str, query: AnyQuery, merged: VariableValues
    ) -> tuple[str, str | None, list[DateRangeFilter]] | None:
        if not is_sql_query(query) or CACHE_REF_JINJA_RE.search(query.sql):
            return None
        query = resolve_query_references(
            query,
            all_queries={**self.board.queries, **self.query_registry},
            query_name=query_name,
        )
        if not is_sql_query(query) or "filter_date_range" not in query.sql:
            return None
        try:
            return self.adapter_registry.render_with_range_filters(
                query, merged, board=self.board
            )
        except (DbtChartsError, OSError, ValueError, yaml.YAMLError):
            return None

    def _run_incremental_tail(
        self,
        query_name: str,
        query: SqlQuery,
        variables: VariableValues | None,
        memo_key: tuple[str, str, str, str],
        cache_key: tuple[str, str, str],
        should_use_cache: bool,
        original_query: SqlQuery,
        prior_rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows
        watermark: Any,  # type-state: explicit_any — MAX watermark; dialect-typed
        dialect: str | None,
        source_config: ResolvedSourceConfig | None,
    ) -> ResultRows:
        """Fetch only new rows (>= watermark) and merge with the prior cached set.

        On success the merged set overwrites the memo entry and is written back
        to the persistent store under original_query's cache key.
        """
        key_col = query.incremental
        assert key_col is not None  # caller checks query.incremental before calling

        # Built entirely via sqlglot expressions (never f-string interpolation)
        # so the identifier and literal are quoted/cast per the query's actual
        # dialect — a hardcoded ANSI `"col"` tokenizes as a STRING LITERAL on
        # MySQL/BigQuery, silently freezing the tail at zero new rows.
        predicate = exp.column(key_col, quoted=True) >= _watermark_to_sql_node(
            watermark, query_name
        )
        tail_sql = (
            f"SELECT * FROM ({query.sql}) AS _dct_base "
            f"WHERE {predicate.sql(dialect=dialect)}"
        )
        tail_query = query.model_copy(update={"sql": tail_sql})

        result = self.adapter_registry.execute(
            tail_query,
            variables,
            board=self.board,
            query_name=query_name,
            source_config=source_config,
        )
        if not result.is_success:
            assert result.error is not None
            result.error.fields.setdefault("query_name", query_name)
            raise result.error

        tail_rows: list[dict[str, Any]] = list(  # type-state: explicit_any — rows
            result.data
        )

        merged = _merge_incremental_rows(tail_rows, prior_rows, key_col, query_name)

        enforced = self._enforce_result_limits(
            ResultRows(merged, result.schema), query_name
        )

        self._cache[memo_key] = enforced
        if should_use_cache:
            truncation = self._truncations.get(query_name)
            self._cache_outcome(
                query_name,
                original_query,
                enforced,
                variables,
                cache_key,
                truncated_reason=truncation.reason if truncation else None,
            )

        return enforced

    @property
    def cache_hit_ats(self) -> list[datetime]:
        """Write-timestamps of every persistent-cache hit this render served.

        Empty when every query ran fresh. The board-chrome freshness stamp reads
        this to disclose data age; the selection policy lives with the stamp in
        ``render/boards.py``.
        """
        return [da for _n, da, _p, is_hit in self._query_data_ages if is_hit]

    @property
    def warehouse_cache_hit_ats(self) -> list[datetime]:
        """The ``cache_hit_ats`` whose cache key folds no content version, so
        their data may have changed since the write."""
        return list(self._warehouse_hit_ats)

    def data_age(self, query_name: str) -> tuple[datetime, bool] | None:
        """When *query_name*'s rows were produced, and whether the persistent
        cache served them, as of its latest completion in this executor; None
        when it has not completed here."""
        for name, data_as_of, _policy, is_hit in reversed(self._query_data_ages):
            if name == query_name:
                return data_as_of, is_hit
        return None

    @property
    def query_data_ages(self) -> list[tuple[str, datetime, CachePolicy]]:
        """Per-query (name, data_as_of, policy) for every query that completed.

        Consumed by the snapshot writer to compute ``expires_at`` anchored on
        the actual data age rather than the render timestamp.
        """
        return [(name, da, policy) for name, da, policy, _ in self._query_data_ages]

    def _resolve_file_source(
        self, query: "AnyQuery"
    ) -> "CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig | None":
        """Return the file SourceConfig if *query* targets a named file source, else None.

        Looks up the query's ``source`` name in the pre-parsed file source configs
        (board.sources at __init__ time, then the project-level ``sources:`` on the
        first name the board does not define — the cache key folds in their file
        version). Returns None for database sources, unresolved sources, or
        non-SQL queries.
        """
        if not is_sql_query(query) or not query.source:
            return None
        if (
            query.source not in self._file_source_configs
            and not self._project_file_sources_merged
        ):
            for name, cfg in self.adapter_registry.project_file_sources().items():
                self._file_source_configs.setdefault(name, cfg)
            self._project_file_sources_merged = True
        return self._file_source_configs.get(query.source)

    def _source_version(self, query: "AnyQuery") -> str:
        """Content version of the files a query's source reads.

        The single content-version home threaded into every ``compute_cache_key``
        call for this query: the combined ``Project.file_version`` of a file
        source's files, or the database file version of a DuckDB/SQLite source.
        Returns ``""`` for warehouse sources (nothing to fold — their config
        identity is the whole key). Memoized per source name for the executor's
        lifetime so a file edited mid-render cannot make a later read key differ
        from the earlier write key.
        """
        file_source = self._resolve_file_source(query)
        if file_source is None:
            return self._database_file_version(query)
        # _resolve_file_source returns non-None only for a SqlQuery naming a source.
        assert is_sql_query(query) and query.source is not None
        source_name = query.source
        cached = self._source_versions.get(source_name)
        if cached is not None:
            return cached
        project = self.adapter_registry.project
        # Pre-hash each file's token to a fixed hex digest before joining, so the
        # combination is independent of any delimiter appearing inside a token
        # (the Project.file_version contract does not restrict the token alphabet).
        # Glob patterns must be expanded first — file_version on a literal glob
        # path would fail with FileNotFoundError on a local project.
        relpaths: list[str] = []
        for path_or_glob in file_source.files.values():
            if is_glob(path_or_glob):
                relpaths.extend(
                    sorted(p.relpath for p in project.files.glob(path_or_glob))
                )
            else:
                relpaths.append(path_or_glob)
        parts = []
        for relpath in relpaths:
            try:
                token = project.file_version(relpath)
            except OSError as exc:
                # OSError, not FileNotFoundError: stat() raises
                # NotADirectoryError when a path component traverses through
                # an existing file (only absolute paths are rejected at
                # compile time — this shape compiles clean) and
                # PermissionError on an unreadable parent. Both must degrade
                # the same as a missing leaf, or they escape is_cached's
                # ExecutionError guard and reproduce the exact 500 this code
                # exists to prevent. exc.strerror carries the specific OS
                # reason so a permission error doesn't read as "not found".
                raise QueryError.from_code(
                    ERR_FILE_SOURCE_NOT_FOUND,
                    source_name=source_name,
                    relpath=relpath,
                    detail=exc.strerror or str(exc),
                ) from None
            parts.append(hashlib.sha256(token.encode()).hexdigest())
        parts.sort()
        version = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
        self._source_versions[source_name] = version
        return version

    def _database_file_version(self, query: "AnyQuery") -> str:
        """``AdapterRegistry.database_file_version`` of a query on a DuckDB/SQLite
        source, memoized per source name like file-source versions. ``""`` for
        any other source, and for one whose config does not parse: execution
        raises that error itself, so nothing is cached under this key.

        Reads the declared ``type`` rather than calling the source resolver: a
        host's resolver may refuse a warehouse source (an SSRF guard) with its
        own exception, and that refusal belongs to execution, not a cache probe.
        """
        # An incremental query finds its prior rows under an unchanged key and
        # fetches only the tail (append-only, as on a warehouse); a file version
        # would turn every append into a full re-query.
        if (
            not is_sql_query(query)
            or query.source is None
            or isinstance(query.incremental, str)
        ):
            return ""
        cached = self._source_versions.get(query.source)
        if cached is not None:
            return cached
        raw = self.board.sources.get(
            query.source
        ) or self.adapter_registry.project_sources.sources.get(query.source)
        if raw is None or raw.get("type") not in ("duckdb", "sqlite"):
            self._source_versions[query.source] = ""
            return ""
        try:
            source_config = parse_source_config(raw)
        except ValueError:
            self._source_versions[query.source] = ""
            return ""
        assert isinstance(source_config, (DuckDBSourceConfig, SQLiteSourceConfig))
        try:
            version = self.adapter_registry.database_file_version(source_config)
        except OSError as exc:
            # A QueryError, so is_cached reports a miss rather than raising.
            raise QueryError.from_code(
                ERR_FILE_SOURCE_NOT_FOUND,
                source_name=query.source,
                relpath=str(exc.filename),
                detail=exc.strerror or str(exc),
            ) from None
        except DbtChartsError as exc:
            raise QueryError(str(exc), code=exc.code) from None
        self._source_versions[query.source] = version
        return version

    def _manifest_version(self, query: "AnyQuery", resolve_source: SourceThunk) -> str:
        """Version of the dbt manifest this query's ``ref()`` / ``source()``
        resolve against, read from the resolver that resolves them; folded into
        the cache key beside ``_source_version``. ``""`` for a query without dbt
        Jinja, or whose source cannot be resolved. Memoized per ``target_path``.
        """
        if not is_sql_query(query) or not (
            has_dbt_jinja(query.sql)
            or (query.setup_sql is not None and has_dbt_jinja(query.setup_sql))
        ):
            return ""
        try:
            target_path = source_target_path(resolve_source())
            if target_path not in self._manifest_versions:
                self._manifest_versions[target_path] = (
                    self.adapter_registry.dbt_manifest_version(target_path)
                )
        except Exception:  # noqa: BLE001 — a resolver may raise anything (SSRF guard, DNS)
            # Execution re-raises the specific error as this query's failure; a
            # cache-key probe must not raise it.
            return ""
        return self._manifest_versions[target_path]

    # ─────────────────────────────────────────────────────────────────────
    # Cache-ref composition ({{ queries.X.cache }})
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _cache_ref_upstream(
        query_name: str, ref_name: str, all_queries: dict[str, AnyQuery]
    ) -> AnyQuery:
        """The query a ``{{ queries.X.cache }}`` reference names, if usable as one.

        Raises:
            ExecutionError: If it does not exist or has ``cache: false``.
        """
        upstream = all_queries.get(ref_name)
        if upstream is None:
            raise ExecutionError(
                f"Query '{query_name}' references {{{{ queries.{ref_name}.cache }}}} "
                f"but query '{ref_name}' does not exist in this board or registry."
            )
        if not upstream.cache.enabled:
            raise ExecutionError(
                f"Query '{query_name}' references {{{{ queries.{ref_name}.cache }}}} "
                f"but query '{ref_name}' has cache: false and cannot be "
                "used as a cache-ref upstream."
            )
        return upstream

    def _execute_cache_ref_query(
        self,
        query: "AnyQuery",
        variables: dict[str, Any] | None,
        query_name: str,
    ) -> tuple[ResultRows, Literal["max_rows"] | None]:
        """Execute a ``{{ queries.X.cache }}`` composing query in an isolated engine.

        For each referenced upstream query NAME:
          1. Validate it exists and is cacheable.
          2. Demand-execute it and capture its rows.
        Then render the composing SQL — inline ``{{ queries.X }}`` refs resolved,
        runtime variables **parameterized** (not string-interpolated) — and run
        it over the upstream rows in a fresh in-process DuckDB whose namespace
        contains ONLY those rows (no application tables, no other tenant's cache,
        nothing on a shared/application connection). The composing SQL's own
        fetch is bounded the same way the four adapters bound theirs — a
        driver-level ``fetchmany()``, not a rewrite of the composing SQL — so a
        join across two upstream tables each near the row ceiling can't blow up
        memory during composition, before any post-fetch check runs.

        Args:
            query: The composing SqlQuery, authored SQL (``{{ queries.X.cache }}``
                and ``{{ variable }}`` templates still present).
            variables: Merged variable values.
            query_name: Name of the composing query (for error messages).

        Returns:
            The isolated composition engine's rows with their declared schema, and "max_rows" when the
            composed result came back over the ceiling (rows already sliced to
            it) — mirroring how adapters signal via QueryResult.truncated_reason.

        Raises:
            ExecutionError: If no cache backend, cache doesn't support refs,
                an upstream query is missing, or an upstream has
                cache: false.
        """
        if not self._result_cache:
            raise ExecutionError(
                f"Query '{query_name}' uses {{{{ queries.X.cache }}}} composition but "
                "no cache backend is configured. Attach a result cache (TrivialDuckDBCache "
                "or PostgresResultCache) to enable cross-source cache-ref queries."
            )
        if not self._result_cache.supports_cache_refs():
            raise ExecutionError(
                f"Query '{query_name}' uses {{{{ queries.X.cache }}}} composition but "
                "the attached cache backend does not support cache-ref execution."
            )

        assert is_sql_query(query)
        sql = query.sql

        # Collect all referenced upstream names (may be multiple)
        all_queries = {**self.board.queries, **self.query_registry}
        ref_names = list(
            dict.fromkeys(CACHE_REF_JINJA_RE.findall(sql))
        )  # preserve order, dedupe

        # name → upstream rows, registered as isolated-DuckDB tables below; the
        # declared schema beside them, so an upstream with no row still gets a
        # table of the right columns.
        named_rows: dict[str, CacheRows] = {}
        schemas: dict[str, dict[str, ColumnType]] = {}

        for ref_name in ref_names:
            self._cache_ref_upstream(query_name, ref_name, all_queries)
            # Demand-execute and capture the upstream rows directly (the run
            # also writes them through to the cache).
            upstream = self.execute_query_result(ref_name, variables)
            named_rows[ref_name] = upstream.rows
            schemas[ref_name] = upstream.schema

        # Render inline {{ queries.X }} refs and parameterize {{ variable }}
        # values ($1/$2/…). {{ queries.X.cache }} renders to its sentinel via
        # the query namespace; we then swap each sentinel for the bare upstream
        # name so it resolves against the registered rows (matching the inline
        # `(sql) AS name` shape).
        from dbt_charts.core.compile.template.parameterized import (
            render_parameterized_with_queries,
        )
        from dbt_charts.core.execute.cache_composition import compose_over_named_rows

        rendered = render_parameterized_with_queries(
            sql, variables or {}, queries=all_queries, profile_type="duckdb"
        )
        composed_sql = _CACHE_REF_RE.sub(lambda m: m.group(1), rendered.sql)

        # Bounds the composition engine's own fetch (fetchmany()) — composed_sql
        # runs unmodified, so an author's own inline LIMIT in the composing SQL
        # behaves exactly as it would without this ceiling.
        row_fetch_limit = resolve_effective_row_limit(query.limit)
        rows, schema = compose_over_named_rows(
            composed_sql,
            named_rows,
            rendered.params,
            limit=row_fetch_limit.fetch_limit,
            schemas=schemas,
        )
        rows, truncated_reason = apply_row_limit_truncation(rows, row_fetch_limit)
        return ResultRows(rows, schema), truncated_reason

    # ─────────────────────────────────────────────────────────────────────
    # Persistent outcome cache (Suite context)
    # ─────────────────────────────────────────────────────────────────────

    def _cache_outcome(
        self,
        query_name: str,
        query: AnyQuery,
        outcome: ResultRows | Exception,
        variables: VariableValues | None,
        cache_key: tuple[str, str, str],
        truncated_reason: TruncatedReason | None = None,
    ) -> None:
        """Write this query's outcome — its rows or its error — to the cache.

        One entry per key, so the write is the whole reconciliation: a success
        (zero-row included) replaces a stale error, and an error replaces rows
        the reader can no longer use, with no "clear the other store"
        bookkeeping. Nothing about expiry is stored — freshness is derived on
        read (see the cache_backend module docstring).

        An error never displaces live rows — the backends enforce that, not
        this caller. Within one execute_query the error write is only reached
        after the read missed, but renders run concurrently over a shared key,
        so a worker whose query timed out could otherwise wipe rows another
        worker had just computed successfully.

        ``truncated_reason`` persists the truncation fact alongside the rows
        it describes — the caller passes ``self._truncations.get(query_name)``
        when *outcome* went through ``_enforce_result_limits``, so a later warm
        read (``_warm_from_cache``) can reconstruct the same warning a fresh
        execution would, instead of losing it on a cache hit.
        """
        if not self._result_cache:
            return

        source_hash, query_hash, variables_hash = cache_key
        self._result_cache.put(
            source_hash,
            query_hash,
            variables_hash,
            outcome,
            board_slug=self._get_board_slug(),
            query_name=query_name,
            source_name=(
                query.source
                if is_sql_query(query) and isinstance(query.source, str)
                else ""
            ),
            truncated_reason=truncated_reason,
            variables=compute_relevant_variables(query, variables),
        )

    def truncations(self) -> dict[str, TruncationInfo]:
        """Query name → truncation record for every query cut by
        execution.max_rows/max_result_bytes this render. Read by the
        render-warnings pass to emit WARN_QUERY_RESULT_TRUNCATED.
        """
        return dict(self._truncations)

    def _enforce_result_limits(
        self,
        result: ResultRows,
        query_name: str,
        seed_reason: TruncatedReason | None = None,
    ) -> ResultRows:
        """The one chokepoint every in-scope cache-write call site routes
        through before ``self._cache``/``self._result_cache.put()`` — the
        "never cache an oversized result" guarantee lives entirely here.

        ``seed_reason`` carries a truncation signal already detected upstream:
        - ``"max_rows"``: adapter-return or cache-ref fetch already detected row
          truncation (``QueryResult.truncated_reason`` or the post-fetchmany slice).
        - ``"max_result_bytes"``: warm-cache path propagates a byte-truncation
          signal from a prior write so the warning still fires on a cache hit
          where the data is already under the current byte ceiling.
        - ``None``: no upstream signal; both row- and byte-checks run from
          scratch.
        The row-count check is defense-in-depth for adapter paths (fetch already
        bounded); it is the primary check on the warm-cache path, where a lowered
        ceiling could otherwise serve more rows than the new limit allows.

        Byte size is checked by accumulating each row's serialized size
        incrementally and cutting the moment the running total crosses
        max_result_bytes — never by serializing the whole result to measure
        it, which would defeat the point on an oversized result.
        """
        from dbt_charts.core.compile.config import (
            resolve_max_result_bytes,
            resolve_max_rows,
        )

        reason: TruncatedReason | None = seed_reason
        rows = result.rows
        max_rows = resolve_max_rows()
        if len(rows) > max_rows:
            rows = rows[:max_rows]
            if reason is None:
                reason = "max_rows"

        max_bytes = resolve_max_result_bytes()
        kept: CacheRows = []
        running_bytes = 0
        for row in rows:
            row_bytes = len(json.dumps(row, default=str))
            if running_bytes + row_bytes > max_bytes:
                reason = "max_result_bytes"
                break
            kept.append(row)
            running_bytes += row_bytes

        if reason is not None:
            self._truncations[query_name] = TruncationInfo(
                query_name=query_name, kept_row_count=len(kept), reason=reason
            )
        return ResultRows(kept, result.schema)

    def _cache_failure(
        self,
        query_name: str,
        query: AnyQuery,
        variables: VariableValues | None,
        cache_key: tuple[str, str, str],
        exception: Exception,
    ) -> None:
        """Cache a query failure (if a backend is configured). Never raises.

        DbtChartsError (policy / compile errors) are not cached: they are fast
        to re-derive, always deterministic, and caching them would discard the
        structured error code and message that callers check.

        A cache write that fails must not replace the real query error the
        caller is about to raise — hence the swallow, which the rows path
        (whose failures the parallel prefetch records) deliberately lacks.
        """
        from dbt_charts.core.diagnostics.base import DbtChartsError
        from dbt_charts.core.diagnostics.codes_execute import (
            ERR_QUERY_INTERRUPTED,
            ERR_WAREHOUSE_AUTH,
            ERR_WAREHOUSE_CONNECTION,
        )
        from dbt_charts.core.diagnostics.execution import ExecutionError

        if isinstance(exception, DbtChartsError) and not isinstance(
            exception, ExecutionError
        ):
            return
        # Says nothing about the query: it was stopped, not answered.
        # Credential and connection failures are fixed outside the board, so
        # the next render must run the query again rather than replay them.
        if isinstance(exception, DbtChartsError) and (
            exception.code
            in (ERR_QUERY_INTERRUPTED, ERR_WAREHOUSE_AUTH, ERR_WAREHOUSE_CONNECTION)
        ):
            return
        try:
            self._cache_outcome(query_name, query, exception, variables, cache_key)
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to cache query failure for '%s'",
                query_name,
                exc_info=True,
            )

    def _get_board_slug(self) -> str:
        """The board identity a cache entry records: its file slug, or, for a
        board with no file (a preview from YAML text), its compiled id behind a
        ``/`` that no file slug starts with, so a draft titled like a stored
        board never lists or serves as that board."""
        return self._board_slug if self._board_slug is not None else f"/{self.board.id}"
