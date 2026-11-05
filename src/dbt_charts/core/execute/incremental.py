"""Incremental tail: refresh a stored incremental query with only its new rows.

Stage: EXECUTE (run)
Purpose: When the store held prior rows for an ``incremental:`` query, run the
    query filtered to ``>= watermark`` and merge the result over the prior rows.
    ``incremental_safety_reason`` says which queries a tail may not touch.

Entry Points:
    - incremental_safety_reason(sql, dialect_name) -> str | None
    - run_incremental_tail(ctx, query_name, query, variables, tail, dialect,
      source_config) -> (ResultRows, TruncationInfo | None)
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp

from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.diagnostics.execution import QueryError
from dbt_charts.core.execute.cache_backend import ResultRows, TruncationInfo
from dbt_charts.core.execute.lookup import Tail, limit_rows
from dbt_charts.core.execute.plan import ExecContext


def watermark_to_sql_node(
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


def incremental_safety_reason(sql: str, dialect_name: str | None) -> str | None:
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


def merge_incremental_rows(
    tail_rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows
    prior_rows: list[dict[str, Any]],  # type-state: explicit_any — cache rows
    key_col: str,
    query_name: str,
) -> list[dict[str, Any]]:  # type-state: explicit_any — row dict values
    """Merge tail rows over prior cached rows, tail wins on key collision.

    Dedup: tail wins for restated keys (prior rows at the watermark are
    overwritten by the tail's version). Sorted descending by key so HEAD
    truncation (``limit_rows`` uses rows[:max_rows]) keeps the
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


def run_incremental_tail(
    ctx: ExecContext,
    query_name: str,
    query: SqlQuery,
    variables: dict[str, Any],  # type-state: explicit_any — variable values
    tail: Tail,
    dialect: str | None,
    source_config: ResolvedSourceConfig | None,
) -> tuple[ResultRows, TruncationInfo | None]:
    """Fetch only new rows (>= watermark) and merge with the prior cached set.

    The merged set carries no data age of its own: its prior rows' age was
    recorded when the lookup served them.
    """
    key_col = query.incremental
    assert key_col is not None  # the lookup builds a tail only for incremental

    # Built entirely via sqlglot expressions (never f-string interpolation)
    # so the identifier and literal are quoted/cast per the query's actual
    # dialect — a hardcoded ANSI `"col"` tokenizes as a STRING LITERAL on
    # MySQL/BigQuery, silently freezing the tail at zero new rows.
    predicate = exp.column(key_col, quoted=True) >= watermark_to_sql_node(
        tail.watermark, query_name
    )
    tail_sql = (
        f"SELECT * FROM ({query.sql}) AS _dct_base "
        f"WHERE {predicate.sql(dialect=dialect)}"
    )
    tail_query = query.model_copy(update={"sql": tail_sql})

    result = ctx.adapter_registry.execute(
        tail_query,
        variables,
        board=ctx.board,
        query_name=query_name,
        source_config=source_config,
    )
    if not result.is_success:
        assert result.error is not None
        result.error.fields.setdefault("query_name", query_name)
        raise result.error

    merged = merge_incremental_rows(
        list(result.data), tail.prior_rows, key_col, query_name
    )
    return limit_rows(ResultRows(merged, result.schema), query_name)
