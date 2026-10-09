"""Deterministic query validator using SQLGlot AST analysis.

Parses SQL and detects structural issues that indicate likely query bugs:

- **WARN-MISSING-JOIN-PREDICATE**: Cross joins, comma-separated tables, or
  joins on a constant (``ON TRUE``) between sources that can both return many
  rows — usually an accidental cartesian product. A source that is one row by
  construction (an aggregate with no GROUP BY, ``LIMIT 1``) or a table
  function is not flagged.
- **WARN-REAGGREGATION**: Outer query applies an aggregate whose result
  depends on grouping (AVG, MEDIAN, a percentile, SUM over an AVG, or any
  DISTINCT aggregate) to a
  column that is already aggregate-derived in a subquery or CTE. Uses
  propagation of aggregate lineage through nested scopes to detect these
  patterns. SUM/COUNT over SUM/COUNT, MAX over MAX and MIN over MIN are exact,
  and window functions and FILTER pivots do not collapse rows, so none fire.
- **WARN-PARSE-ERROR**: SQL that SQLGlot cannot parse.

The codes above are registered in core/diagnostics/codes_query.py — this
module imports them rather than declaring its own vocabulary, so a suppressed
or reported code here is always a real entry in the unified registry.

Pure functions — no DB queries, no side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, overload

import sqlglot
import sqlglot.errors
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.dialects.postgres import Postgres
from sqlglot.dialects.tsql import TSQL

from dbt_charts.core.compile.sql_guard import require_sqlglot_dialect
from dbt_charts.core.diagnostics.codes_query import (
    WARN_DATE_AS_TEXT,
    WARN_MISSING_JOIN_PREDICATE,
    WARN_PARSE_ERROR,
    WARN_REAGGREGATION,
)

# Pattern for -- dct:ignore [code1 code2 ...]
_DCT_IGNORE_RE = re.compile(r"--\s*dct:ignore\b\s*(.*)", re.IGNORECASE)

# Codes that must never be suppressed.
_UNSUPPRESSIBLE_CODES = frozenset({WARN_PARSE_ERROR.code})

# The only codes a QueryDiagnostic may carry. A true Literal can't reference
# these constants' runtime `.code` values, so the guarantee a 4-member
# Literal used to give is enforced here instead.
_QUERY_CODES = frozenset(
    {
        WARN_DATE_AS_TEXT.code,
        WARN_MISSING_JOIN_PREDICATE.code,
        WARN_PARSE_ERROR.code,
        WARN_REAGGREGATION.code,
    }
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryDiagnostic:
    """A single diagnostic finding from query validation.

    ``code`` is one of the WARN-* codes registered in
    core/diagnostics/codes_query.py — every
    construction site below sets it from one of those constants.
    """

    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    detail: str | None = None
    recommendation: str | None = None
    confidence: float | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in _QUERY_CODES:
            raise ValueError(
                f"QueryDiagnostic.code {self.code!r} is not a registered query "
                f"code. Must be one of: {', '.join(sorted(_QUERY_CODES))}."
            )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _source_name(node: exp.Table | exp.Subquery) -> str:
    """Return the alias if present, else the table name.

    For subqueries, returns the alias (subqueries always need one in valid SQL).
    """
    if isinstance(node, exp.Subquery):
        return node.alias or "<subquery>"
    return node.alias or node.name


def _direct_from_sources(select: exp.Select) -> list[exp.Table | exp.Subquery]:
    """Get direct table/subquery sources from FROM clause (non-recursive)."""
    from_ = select.find(exp.From)
    if not from_:
        return []
    # iter_expressions() yields From's heterogeneous children (Table, Subquery,
    # Join, ...), not other From instances — sqlglot's self-bound `E` generic on
    # the method just can't express that, so this names the real element type
    # instead of letting the mismatch surface as an unrelated `in` complaint.
    from_children: list[exp.Expression] = list(from_.iter_expressions())
    sources: list[exp.Table | exp.Subquery] = []
    for child in from_children:
        if isinstance(child, exp.Subquery):
            sources.append(child)
        elif isinstance(child, exp.Table):
            # Only include if not nested inside a subquery
            parent_subquery = child.find_ancestor(exp.Subquery)
            if parent_subquery is None or parent_subquery not in from_children:
                sources.append(child)
    return sources


def _direct_join_sources(select: exp.Select) -> list[exp.Table | exp.Subquery]:
    """Get direct table/subquery sources from JOIN clauses (non-recursive)."""
    sources: list[exp.Table | exp.Subquery] = []
    for join in select.find_all(exp.Join):
        # Skip joins that don't belong directly to `select`'s own scope.
        # `find_all` walks the whole tree regardless of scope, so a nested
        # subquery's or CTE's own join would otherwise leak into `select`'s
        # source list as if it joined there directly. This must be checked
        # scope-relative (nearest enclosing SELECT) rather than by ancestor
        # chain: `select` is itself a CTE body when this runs via lineage
        # propagation, and an ancestor-chain check for exp.CTE can't tell
        # "this join is inside a CTE nested in select" apart from "this join
        # IS select's own body, which happens to sit inside a CTE".
        if join.parent_select is not select:
            continue
        for child in join.iter_expressions():
            if isinstance(child, (exp.Subquery, exp.Table)):
                sources.append(child)
    return sources


def _is_single_row_select(select: exp.Expression) -> bool:
    """Whether ``select`` returns at most one row by construction.

    That is a ``LIMIT 1``, a SELECT with no FROM, or an aggregate query with no
    GROUP BY. A filter that happens to match one row does not count.
    """
    if not isinstance(select, exp.Select):
        return False
    limit = select.args.get("limit")
    if limit is not None and limit.expression.name == "1":
        return True
    if select.args.get("from_") is None:
        return True
    return not select.args.get("group") and bool(_get_aggregate_functions(select))


def _single_row_ctes(select: exp.Select) -> set[str]:
    with_ = select.args.get("with_")
    if with_ is None:
        return set()
    return {
        cte.alias.lower()
        for cte in with_.expressions
        if _is_single_row_select(cte.this)
    }


def _is_single_row_source(
    source: exp.Table | exp.Subquery, single_row_ctes: set[str]
) -> bool:
    """Whether a join source cannot multiply the rows it is joined to."""
    if isinstance(source, exp.Subquery):
        return _is_single_row_select(source.this)
    # A table function (generate_series, range) expands rows on purpose.
    if not isinstance(source.this, exp.Identifier):
        return True
    return not source.db and source.name.lower() in single_row_ctes


def _unconstrained_join_label(join: exp.Join) -> str | None:
    """How ``join`` is spelled when nothing constrains it, else ``None``."""
    on = join.args.get("on")
    if on is not None:
        # ON TRUE and ON 1 = 1 constrain nothing: no column is compared.
        return f"JOIN ... ON {on.sql()}" if on.find(exp.Column) is None else None
    if join.args.get("using"):
        return None
    method = join.args.get("method")
    if method is not None and method.upper() == "NATURAL":
        return None
    # A bare JOIN (or a comma join) has no kind and is a cross join.
    kind = join.args.get("kind")
    return "CROSS JOIN" if kind is None or kind.upper() == "CROSS" else None


def _find_cross_joins(select: exp.Select) -> list[tuple[str, str, str]]:
    """Find joins with no predicate between sources that can both multiply rows.

    Returns ``(left_name, right_name, how)`` per join, where ``how`` spells the
    join as authored. A source that is one row by construction multiplies
    nothing, so it neither raises a join nor hides a later one.
    """
    from_sources = _direct_from_sources(select)
    if not from_sources:
        return []

    single_row_ctes = _single_row_ctes(select)
    multi_row_names = [
        _source_name(source)
        for source in from_sources
        if not _is_single_row_source(source, single_row_ctes)
    ]
    found: list[tuple[str, str, str]] = []

    for join in select.find_all(exp.Join):
        # A cross join inside a CTE or subquery nested in `select` is that
        # scope's own problem, not `select`'s.
        if join.parent_select is not select:
            continue
        right = next(
            (
                child
                for child in join.iter_expressions()
                if isinstance(child, (exp.Table, exp.Subquery))
            ),
            None,
        )
        if right is None or _is_single_row_source(right, single_row_ctes):
            continue
        how = _unconstrained_join_label(join)
        if how and multi_row_names:
            found.append((multi_row_names[0], _source_name(right), how))
        multi_row_names.append(_source_name(right))
    return found


def _collapses_rows(agg: exp.AggFunc) -> bool:
    """Whether ``agg`` collapses rows, rather than running per row (``OVER``) or
    selecting a subset of one (``FILTER (WHERE ...)``)."""
    scope = agg.find_ancestor(exp.Window, exp.Filter, exp.Select)
    return not isinstance(scope, (exp.Window, exp.Filter))


def _get_aggregate_functions(select: exp.Select) -> list[exp.AggFunc]:
    """Get the row-collapsing aggregate calls in SELECT and HAVING clauses."""
    having = select.find(exp.Having)
    clauses = [*select.expressions, *([having] if having else [])]
    return [
        agg
        for clause in clauses
        for agg in clause.find_all(exp.AggFunc)
        if _collapses_rows(agg)
    ]


# ---------------------------------------------------------------------------
# Diagnostic detectors
# ---------------------------------------------------------------------------


def _detect_missing_join_predicates(
    select: exp.Select,
) -> list[QueryDiagnostic]:
    """Detect cross joins, comma joins, and constant-predicate joins."""
    return [
        QueryDiagnostic(
            code=WARN_MISSING_JOIN_PREDICATE.code,
            severity="error",
            message=f"{how} between {left} and {right} — no join predicate",
            detail=f"{how} produces a cartesian product of {left} × {right}.",
            recommendation="If intentional, document why. Otherwise, "
            "use JOIN with ON clause.",
        )
        for left, right, how in _find_cross_joins(select)
    ]


# ---------------------------------------------------------------------------
# Propagation-backed re-aggregation detection
# ---------------------------------------------------------------------------


_AggFuncType = type[exp.AggFunc]
# source name -> output column -> the aggregate function it derives from
_AggMap = dict[str, dict[str, _AggFuncType | None]]

# Aggregates whose result depends on the grouping they ran over: taking one of
# them over a prior aggregate, or a SUM over one of them, changes the answer.
_GROUPING_DEPENDENT = (
    exp.Avg,
    exp.Median,
    exp.Quantile,
    exp.PercentileCont,
    exp.PercentileDisc,
    exp.ApproxQuantile,
    exp.ApproxQuantiles,
)


def _is_non_composable(
    outer: _AggFuncType, inner: _AggFuncType, *, distinct: bool
) -> bool:
    """Whether ``outer`` over a column produced by ``inner`` is grouping-dependent.

    SUM/COUNT over SUM/COUNT, MAX over MAX and MIN over MIN are exact. DISTINCT
    never composes: it collapses equal pre-aggregated values.
    """
    return (
        distinct
        or issubclass(outer, _GROUPING_DEPENDENT)
        or (issubclass(outer, exp.Sum) and issubclass(inner, _GROUPING_DEPENDENT))
    )


def _select_output_columns(select: exp.Select) -> dict[str, _AggFuncType | None]:
    """Map output alias → the aggregate function it derives from, for a SELECT.

    Keys are the output column names (alias or column name); a value is the
    aggregate function class the column derives from, ``None`` when it does not.
    Group-by keys are explicitly marked as non-aggregate.
    """
    # Collect GROUP BY column names to exclude them from aggregate tagging
    group_by_keys: set[str] = set()
    group = select.args.get("group")
    if group:
        for expr in group.expressions:
            if isinstance(expr, exp.Column):
                group_by_keys.add(expr.name.lower())

    outputs: dict[str, _AggFuncType | None] = {}
    for expr in select.expressions:
        alias = expr.alias if isinstance(expr, exp.Alias) else None
        if alias:
            inner = expr.this
            # AggFunc inside a Window node is a window function (per-row),
            # not a collapsed aggregate — don't tag as aggregate-derived.
            agg_node = inner.find(exp.AggFunc)
            if agg_node is not None and inner.find(exp.Window) is None:
                outputs[alias.lower()] = type(agg_node)
            else:
                outputs[alias.lower()] = None
        elif isinstance(expr, exp.Column):
            outputs[expr.name.lower()] = None
        elif isinstance(expr, exp.Star):
            # Can't track through SELECT * — skip
            pass
    # Group-by keys are never aggregate-derived even if aliased from one
    for key in group_by_keys:
        if key in outputs:
            outputs[key] = None
    return outputs


def _propagate_aggregate_columns(
    parsed: exp.Expression,
) -> _AggMap:
    """Build a map of source_name → {column_name: is_aggregate_derived}.

    Walks CTEs and inline subqueries to determine which output columns
    are aggregate-derived so the outer query can detect re-aggregation.
    """
    agg_map: _AggMap = {}

    # CTEs — process in order so later CTEs can inherit from earlier ones
    with_ = parsed.find(exp.With)
    if with_:
        for cte in with_.expressions:
            if not isinstance(cte, exp.CTE):
                continue
            alias = cte.alias
            inner_select = cte.find(exp.Select)
            if alias and inner_select:
                outputs = _select_output_columns(inner_select)
                # Propagate aggregate lineage: if this CTE selects from
                # another CTE/source already in agg_map, columns that are
                # merely passed through (not re-aggregated, not in GROUP BY)
                # inherit aggregate status from the upstream source.
                _inherit_aggregate_lineage(inner_select, outputs, agg_map)
                agg_map[alias.lower()] = outputs

    # Inline subqueries in FROM / JOIN of the main SELECT
    if isinstance(parsed, exp.Select):
        for src in _direct_from_sources(parsed) + _direct_join_sources(parsed):
            if isinstance(src, exp.Subquery):
                name = (src.alias or "<subquery>").lower()
                inner_select = src.find(exp.Select)
                if inner_select:
                    outputs = _select_output_columns(inner_select)
                    _inherit_aggregate_lineage(inner_select, outputs, agg_map)
                    agg_map[name] = outputs

    return agg_map


def _resolve_known_sources(select: exp.Select, agg_map: _AggMap) -> dict[str, str]:
    """Map each FROM/JOIN source name visible in ``select`` to its ``agg_map`` key.

    Only sources we've actually analyzed — CTEs and inline subqueries, whose
    own output columns ``agg_map`` tracks — resolve to an entry. A raw base
    table never appears here since we have no way to see its columns.

    Re-aggregation detection uses it to resolve a column reference back to
    the source that actually defines it, rather than guessing from the
    column's name alone.
    """
    resolved: dict[str, str] = {}
    for src in _direct_from_sources(select) + _direct_join_sources(select):
        name = _source_name(src).lower()
        if isinstance(src, exp.Table):
            table_name = src.name.lower()
            if table_name in agg_map:
                resolved[name] = table_name
        elif name in agg_map:
            resolved[name] = name
    return resolved


def _column_inherits_aggregate(
    col_name: str,
    ref: str,
    source_keys: dict[str, str],
    agg_map: _AggMap,
) -> _AggFuncType | None:
    """The aggregate function an output column inherits from lineage, if any.

    A qualified reference (``ref`` present in ``source_keys``) binds
    unambiguously to that one source — trust it directly. An unqualified
    reference must NOT be resolved by matching the column's *name* against
    any joined source: a join used only for filtering can carry an unrelated
    column that happens to share a name with a genuinely aggregate one
    elsewhere in the query (FR-79). Instead, only the sources that actually
    define the column count as evidence, and all of them must agree it's
    aggregate-derived — a real name collision (some agree, some don't) must
    not silently propagate as if it were unambiguous. Sources that agree via
    different functions report the first source's.
    """
    # source_keys.values() are, by construction, all agg_map keys — direct
    # indexing throughout, not .get(..., {}), matching _resolve_unqualified_
    # agg_source's reasoning above.
    if ref and ref in source_keys:
        return agg_map[source_keys[ref]].get(col_name)
    containing = [s for s in source_keys.values() if col_name in agg_map[s]]
    if containing and all(agg_map[s][col_name] for s in containing):
        return agg_map[containing[0]][col_name]
    return None


def _inherit_aggregate_lineage(
    select: exp.Select,
    outputs: dict[str, _AggFuncType | None],
    agg_map: _AggMap,
) -> None:
    """Propagate aggregate-derived status through pass-through columns.

    When a CTE or subquery selects a column from an upstream source that
    is already in ``agg_map``, and the column is not wrapped in an
    aggregate function, it inherits the upstream aggregate status.

    Limitations:
    - ``SELECT *`` pass-throughs are not tracked (same as ``_select_output_columns``).
    """
    source_keys = _resolve_known_sources(select, agg_map)
    if not source_keys:
        return

    for expr in select.expressions:
        if isinstance(expr, exp.Column):
            col_name = expr.name.lower()
            # Already marked as aggregate by _select_output_columns — skip
            if outputs.get(col_name):
                continue
            ref = (expr.table or "").lower()
            if inherited := _column_inherits_aggregate(
                col_name, ref, source_keys, agg_map
            ):
                outputs[col_name] = inherited
        elif isinstance(expr, exp.Alias):
            inner = expr.this
            alias_name = expr.alias.lower()
            if outputs.get(alias_name):
                continue
            # Check if the inner expression is a simple column reference
            if isinstance(inner, exp.Column):
                col_name = inner.name.lower()
                ref = (inner.table or "").lower()
                if inherited := _column_inherits_aggregate(
                    col_name, ref, source_keys, agg_map
                ):
                    outputs[alias_name] = inherited


def _detect_reaggregation(
    select: exp.Select, parsed: exp.Expression
) -> list[QueryDiagnostic]:
    """Detect re-aggregation: a grouping-dependent aggregate over an aggregate.

    Uses propagation of aggregate lineage through subqueries and CTEs to
    identify columns that are aggregate-derived and which function produced
    them, then checks whether the outer query applies a non-composable
    aggregate to those columns.
    """
    agg_map = _propagate_aggregate_columns(parsed)
    if not agg_map:
        return []

    source_names = _resolve_known_sources(select, agg_map)
    if not source_names:
        return []

    # Find aggregate functions in the outer SELECT and HAVING
    outer_aggs = _get_aggregate_functions(select)
    if not outer_aggs:
        return []

    diags: list[QueryDiagnostic] = []
    seen: set[tuple[str, str, _AggFuncType, bool]] = set()

    for agg in outer_aggs:
        outer_type = type(agg)
        is_distinct = isinstance(agg.this, exp.Distinct)
        # An ordered-set aggregate keeps its column in the WITHIN GROUP wrapper.
        call = agg.parent if isinstance(agg.parent, exp.WithinGroup) else agg
        for col in call.find_all(exp.Column):
            col_name = col.name.lower()
            # Try to resolve which source this column comes from
            table_ref = (col.table or "").lower()
            if table_ref and table_ref in source_names:
                # Qualified column — resolve to the specific source
                candidate_sources = [source_names[table_ref]]
            else:
                # Unqualified column — only flag if aggregate-derived in ALL
                # sources that contain it (ambiguous ownership → skip)
                candidate_sources = list(source_names.values())
                containing = [
                    s for s in candidate_sources if col_name in agg_map.get(s, {})
                ]
                if not containing or not all(agg_map[s][col_name] for s in containing):
                    continue
                candidate_sources = containing

            for src_name in candidate_sources:
                inner_type = agg_map[src_name].get(col_name)
                key = (src_name, col_name, outer_type, is_distinct)
                if (
                    inner_type
                    and key not in seen
                    and _is_non_composable(outer_type, inner_type, distinct=is_distinct)
                ):
                    seen.add(key)
                    outer_func = outer_type.sql_name()
                    outer_call = (
                        f"{outer_func}({'DISTINCT ' if is_distinct else ''}{col.name})"
                    )
                    if is_distinct:
                        outer_func += " DISTINCT"
                    inner_func = inner_type.sql_name()
                    diags.append(
                        QueryDiagnostic(
                            code=WARN_REAGGREGATION.code,
                            severity="warning",
                            message=(
                                f"{outer_func} over {inner_func}: "
                                f"{outer_call} re-aggregates a column "
                                f"already computed with {inner_func}"
                            ),
                            detail=(
                                f"Column '{col.name}' is produced by "
                                f"{inner_func} in source '{src_name}'. "
                                f"{outer_func} of it depends on how the rows "
                                f"were grouped, so the result is likely wrong."
                            ),
                            recommendation=(
                                "Compute the aggregate once over the underlying "
                                "rows, or carry the SUM and COUNT and divide "
                                "them at the outer level."
                            ),
                        )
                    )
    return diags


# ---------------------------------------------------------------------------
# Date turned into text
# ---------------------------------------------------------------------------

# sqlglot wraps formatted values in coercions (strftime over a column parses as
# TimeToStr(TsOrDsToTimestamp(col))); look through them.
_COERCIONS = (
    exp.Paren,
    exp.TsOrDsToDate,
    exp.TsOrDsToTimestamp,
    exp.TsOrDsToDatetime,
    exp.TsOrDsToTime,
    exp.TimeStrToTime,
    exp.TimeStrToDate,
)

_TRUNCS = (exp.TimestampTrunc, exp.DateTrunc, exp.TimeTrunc)

_TEMPORAL_FUNCTIONS = (
    *_TRUNCS,
    exp.CurrentDate,
    exp.CurrentTimestamp,
    exp.CurrentDatetime,
    exp.Date,
    exp.DateAdd,
    exp.DateSub,
    exp.TimestampAdd,
    exp.TimestampSub,
    exp.DatetimeAdd,
    exp.DatetimeSub,
    exp.StrToDate,
    exp.StrToTime,
    exp.DateFromParts,
    exp.TimestampFromParts,
    exp.LastDay,
)

_DATE_COLUMN_NAME = re.compile(r"(^|_)(date|datetime|timestamp|ts|dt)$|_(at|on)$")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# sqlglot reads `date_trunc(g, d)` and `date_trunc('week', d)` alike (a Var),
# so a unit is trusted only when it names a date part.
_GRAINS = frozenset(
    {
        "microsecond",
        "millisecond",
        "second",
        "minute",
        "hour",
        "day",
        "week",
        "isoweek",
        "month",
        "quarter",
        "year",
        "isoyear",
        "decade",
        "century",
        "millennium",
    }
)

# sqlglot normalizes most name formats to these codes; to_char, Spark 'LLL'
# and .NET 'ddd' keep their own spelling ('DDD' is day of year in to_char).
_NAME_CODES = frozenset({"%b", "%B", "%h", "%a", "%A"})
_TO_CHAR_NAME = re.compile(r"(fm)?(mon|month|mmmm|dy|day)|l{3,4}", re.IGNORECASE)
_QUOTED_LITERAL = re.compile(r"'((?:[^']|'')*)'")

# Dialects whose to-text call also formats numbers.
_NUMBER_FORMATTING_DIALECTS = (Postgres, TSQL)


def _unwrap_coercions(node: exp.Expression) -> exp.Expression:
    while isinstance(node, _COERCIONS):
        node = node.this
    return node


def _is_evidently_temporal(node: exp.Expression) -> bool:
    node = _unwrap_coercions(node)
    if isinstance(node, _TEMPORAL_FUNCTIONS):
        return True
    if isinstance(node, exp.Cast):
        return node.to.this in exp.DataType.TEMPORAL_TYPES
    if isinstance(node, exp.Column):
        snake = _CAMEL_BOUNDARY.sub("_", node.name).lower()
        return _DATE_COLUMN_NAME.search(snake) is not None
    return False


def _is_name_only(
    node: exp.TimeToStr | exp.ToChar, authored: str | None, is_tsql: bool
) -> bool:
    fmt = node.args.get("format")
    if isinstance(fmt, exp.Literal) and fmt.name in _NAME_CODES:
        return True
    if authored is None:
        return False
    return _TO_CHAR_NAME.fullmatch(authored) is not None or (
        is_tsql and authored == "ddd"
    )


def _authored_format(
    node: exp.TimeToStr | exp.ToChar, dialect: str | None
) -> str | None:
    """The literal format as the dialect spells it; None when absent or computed."""
    fmt = node.args.get("format")
    if not isinstance(fmt, exp.Literal):
        return None
    if isinstance(node, exp.ToChar):
        return fmt.name
    probe = exp.TimeToStr(this=exp.column("x"), format=fmt.copy())
    match = _QUOTED_LITERAL.search(probe.sql(dialect=dialect))
    return match.group(1) if match else fmt.name


def _replacement(
    value: exp.Expression, alias: str, dialect: str | None
) -> tuple[str | None, str | None]:
    """The SQL to return instead (None when the grain is computed) and its grain."""
    value = _unwrap_coercions(value)
    if not isinstance(value, _TRUNCS):
        return f"{value.sql(dialect=dialect)} AS {alias}", None
    unit = value.args["unit"]
    grain = "week" if isinstance(unit, exp.WeekStart) else unit.name.lower()
    if grain not in _GRAINS:
        return None, None
    # The generator upper-cases the unit; house style is lower-case.
    rendered = re.sub(
        rf"\b{grain.upper()}\b",
        grain,
        value.sql(dialect=dialect, normalize_functions="lower"),
    )
    return f"{rendered} AS {alias}", grain


def _detect_date_as_text(
    select: exp.Select, x_columns: frozenset[str], dialect: str | None
) -> list[QueryDiagnostic]:
    """WARN-DATE-AS-TEXT over the outermost projections; rules in codes_query.py."""
    wanted = {name.lower() for name in x_columns}
    family = Dialect.get_or_raise(dialect)
    takes_numbers = isinstance(family, _NUMBER_FORMATTING_DIALECTS)
    is_tsql = isinstance(family, TSQL)
    diags: list[QueryDiagnostic] = []
    for projection in select.expressions:
        alias = projection.alias_or_name
        if alias.lower() not in wanted:
            continue
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        inner = _unwrap_coercions(inner)
        if isinstance(inner, exp.TimeToStr | exp.ToChar):
            value = inner.this
            if (
                isinstance(inner, exp.ToChar) or takes_numbers
            ) and not _is_evidently_temporal(value):
                continue
            fmt = _authored_format(inner, dialect)
            if _is_name_only(inner, fmt, is_tsql):
                continue
            what = "a date turned into text"
            if fmt is not None:
                what += f" ({fmt!r})"
        else:
            if isinstance(inner, exp.Cast | exp.TryCast):
                is_text, value = inner.to.this in exp.DataType.TEXT_TYPES, inner.this
            elif isinstance(inner, exp.Convert):
                is_text = inner.this.this in exp.DataType.TEXT_TYPES
                value = inner.expression
            else:
                continue
            if not is_text or not _is_evidently_temporal(value):
                continue
            what = "a date cast to text"
        replacement, grain = _replacement(value, alias, dialect)
        message = f"{alias} is {what}. Return the date itself"
        if replacement is not None:
            message += f", {replacement}"
        if grain is None:
            message += ", and the axis labels it by its grain."
        else:
            message += f", and the axis labels it by {grain}."
        diags.append(
            QueryDiagnostic(
                code=WARN_DATE_AS_TEXT.code, severity="warning", message=message
            )
        )
    return diags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_inline_suppressions(sql: str) -> set[str]:
    """Extract suppressed diagnostic codes from ``-- dct:ignore`` comments.

    Supports:
    - ``-- dct:ignore WARN-REAGGREGATION`` — suppress one code
    - ``-- dct:ignore WARN-REAGGREGATION WARN-DATE-AS-TEXT`` — suppress multiple
    - ``-- dct:ignore`` — blanket suppress all (returns ``{"*"}``)

    Note: Uses a simple line scan. Does not distinguish SQL comments from
    string literals — a pattern inside a string literal will also match.
    This is consistent with how sqlfluff and other linters handle noqa.
    """
    codes: set[str] = set()
    for line in sql.splitlines():
        m = _DCT_IGNORE_RE.search(line.strip())
        if m:
            rest = m.group(1).strip()
            if rest:
                codes.update(rest.split())
            else:
                codes.add("*")
    return codes


def _apply_suppression(
    diags: list[QueryDiagnostic],
    suppressed_codes: set[str],
) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
    """Split diagnostics into active and suppressed lists.

    Unsuppressible codes (e.g. WARN-PARSE-ERROR) are never suppressed.
    """
    if not suppressed_codes:
        return diags, []
    blanket = "*" in suppressed_codes
    active: list[QueryDiagnostic] = []
    suppressed: list[QueryDiagnostic] = []
    for d in diags:
        if d.code in _UNSUPPRESSIBLE_CODES:
            active.append(d)
        elif blanket or d.code in suppressed_codes:
            suppressed.append(d)
        else:
            active.append(d)
    return active, suppressed


@overload
def validate_query(
    sql: str,
    *,
    dialect: str | None = ...,
    x_columns: frozenset[str] = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[False] = ...,
) -> list[QueryDiagnostic]: ...


@overload
def validate_query(
    sql: str,
    *,
    dialect: str | None = ...,
    x_columns: frozenset[str] = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[True],
) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]: ...


def validate_query(
    sql: str,
    *,
    dialect: str | None = None,
    x_columns: frozenset[str] = frozenset(),
    suppress: set[str] | None = None,
    return_suppressed: bool = False,
) -> list[QueryDiagnostic] | tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
    """Validate a SQL query and return structural diagnostics.

    Args:
        sql: SQL query string to validate.
        dialect: Optional SQLGlot dialect name (e.g. "duckdb", "bigquery").
        x_columns: Output column names some chart uses as its x. Only these
            are checked for dates turned into text; empty skips that check.
        suppress: Optional set of diagnostic codes to suppress externally
            (e.g. from YAML ``ignore`` or ``meta.yml`` lint config).
        return_suppressed: If True, return a tuple of
            (active_diagnostics, suppressed_diagnostics).

    Returns:
        List of QueryDiagnostic findings (or tuple if return_suppressed=True).
        Empty list means no issues found.
    """
    if not sql or not sql.strip():
        result = [
            QueryDiagnostic(
                code=WARN_PARSE_ERROR.code,
                severity="error",
                message="Empty SQL query",
            )
        ]
        return (result, []) if return_suppressed else result

    # Collect suppression codes from SQL-inline comments + external callers
    suppressed_codes = parse_inline_suppressions(sql)
    if suppress:
        suppressed_codes.update(suppress)

    try:
        dialect = require_sqlglot_dialect(dialect)
        parsed = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        result = [
            QueryDiagnostic(
                code=WARN_PARSE_ERROR.code,
                severity="error",
                message=f"SQL parse error: {e}",
            )
        ]
        return (result, []) if return_suppressed else result

    if not isinstance(parsed, exp.Select):
        # Only validate SELECT statements for now
        return ([], []) if return_suppressed else []

    diags: list[QueryDiagnostic] = []
    diags.extend(_detect_missing_join_predicates(parsed))
    diags.extend(_detect_reaggregation(parsed, parsed))
    diags.extend(_detect_date_as_text(parsed, x_columns, dialect))

    active, suppressed_diags = _apply_suppression(diags, suppressed_codes)
    if return_suppressed:
        return active, suppressed_diags
    return active
