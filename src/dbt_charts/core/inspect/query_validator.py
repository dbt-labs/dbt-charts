"""Deterministic query validator using SQLGlot AST analysis.

Parses SQL and detects structural issues that indicate likely query bugs:

- **WARN-MISSING-JOIN-PREDICATE**: Cross joins or comma-separated tables
  without an explicit join predicate — usually an accidental cartesian
  product.
- **WARN-FANOUT-RISK**: Aggregation over a joined query where aggregate
  expressions reference columns from multiple tables, use unqualified
  columns with 2+ tables in scope, or COUNT(*) with joins — the structural
  signal for double-counting / aggregate inflation.
- **WARN-REAGGREGATION**: Outer query applies an aggregate function to a
  column that is already aggregate-derived in a subquery or CTE — e.g. SUM
  of a SUM, AVG of an AVG. Uses propagation of aggregate lineage through
  nested scopes to detect these patterns. Looker's symmetric aggregate (a
  paired ``SUM(DISTINCT ...)`` difference over a hash-packed dedup key) is
  exempt — it is an identity over a per-key value, not a second aggregation.
  See ``_is_symmetric_aggregate_half``.
- **WARN-PARSE-ERROR**: SQL that SQLGlot cannot parse.

The four codes above are registered in core/diagnostics/codes_query.py — this
module imports them rather than declaring its own vocabulary, so a suppressed
or reported code here is always a real entry in the unified registry.

Relationship context (multiplicity, fanout factor) is optional. When
available, it calibrates WARN-FANOUT-RISK severity and grounds
recommendations in known join metadata.

Pure functions — no DB queries, no side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, overload

import sqlglot
import sqlglot.errors
from sqlglot import exp

from dbt_charts.core.compile.sql_guard import as_expression
from dbt_charts.core.diagnostics.codes_query import (
    WARN_FANOUT_RISK,
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
        WARN_FANOUT_RISK.code,
        WARN_MISSING_JOIN_PREDICATE.code,
        WARN_PARSE_ERROR.code,
        WARN_REAGGREGATION.code,
    }
)

# Row multiplication factor above which a 1:N join with aggregation is flagged
# high risk. Also imported by dbt-charts-super-schema's score_fanout_risk.
HIGH_FANOUT_THRESHOLD = 10.0

# Minimum confidence to trust a relationship hint for severity changes.
_MIN_CALIBRATION_CONFIDENCE = 0.75

# Multiplicity flip table for direction normalization.
_FLIP_MULTIPLICITY = {
    "one-to-many": "many-to-one",
    "many-to-one": "one-to-many",
    "one-to-one": "one-to-one",
    "many-to-many": "many-to-many",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryDiagnostic:
    """A single diagnostic finding from query validation.

    ``code`` is one of the four WARN-* codes registered in
    core/diagnostics/codes_query.py (WARN_MISSING_JOIN_PREDICATE,
    WARN_FANOUT_RISK, WARN_PARSE_ERROR, WARN_REAGGREGATION) — every
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


@dataclass(frozen=True)
class RelationshipHint:
    """Known relationship between two tables for severity calibration."""

    left_table: str
    right_table: str
    multiplicity: str  # "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many"
    fanout_factor: float
    confidence: float  # 0.0–1.0


@dataclass(frozen=True)
class RelationshipContext:
    """Relationship metadata for query validator severity calibration."""

    hints: tuple[RelationshipHint, ...] = ()


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


def _build_alias_map(select: exp.Select) -> dict[str, str]:
    """Map alias → table name for direct (non-subquery) table sources."""
    alias_map: dict[str, str] = {}
    for src in _direct_from_sources(select) + _direct_join_sources(select):
        if isinstance(src, exp.Table):
            alias_map[_source_name(src)] = src.name
    return alias_map


def _has_join(select: exp.Select) -> bool:
    """Check if the SELECT has any direct JOIN clause (not in subqueries)."""
    return bool(_direct_join_sources(select))


def _has_comma_join(select: exp.Select) -> bool:
    """Check if FROM clause has multiple direct sources (comma join)."""
    return len(_direct_from_sources(select)) > 1


def _find_cross_joins(select: exp.Select) -> list[tuple[str, str]]:
    """Find explicit CROSS JOINs and return (left_name, right_name)."""
    pairs: list[tuple[str, str]] = []
    from_sources = _direct_from_sources(select)
    if not from_sources:
        return pairs

    left_name = _source_name(from_sources[0])

    for join in select.find_all(exp.Join):
        # Same scope guard as _direct_join_sources — a cross join inside a
        # CTE or subquery nested in `select` is that scope's own problem,
        # not `select`'s.
        if join.parent_select is not select:
            continue
        if join.args.get("on") or join.args.get("using"):
            continue
        # NATURAL JOIN has implicit predicates — not a cross join
        if (join.args.get("method") or "").upper() == "NATURAL":
            continue
        kind = (join.args.get("kind") or "").upper()
        if kind == "CROSS" or kind == "":
            # Find the table/subquery in this join
            for child in join.iter_expressions():
                if isinstance(child, (exp.Table, exp.Subquery)):
                    pairs.append((left_name, _source_name(child)))
                    break
    return pairs


def _get_aggregate_functions(select: exp.Select) -> list[exp.AggFunc]:
    """Get all aggregate function calls in SELECT and HAVING clauses."""
    aggs: list[exp.AggFunc] = []
    for expr in select.expressions:
        aggs.extend(expr.find_all(exp.AggFunc))
    having = select.find(exp.Having)
    if having:
        aggs.extend(having.find_all(exp.AggFunc))
    return aggs


def _column_table_ref(col: exp.Column) -> str | None:
    """Get the table reference (alias or name) from a Column expression."""
    return col.table if col.table else None


def _resolve_table(ref: str, alias_map: dict[str, str]) -> str:
    """Resolve a table reference through the alias map."""
    return alias_map.get(ref, ref)


# ---------------------------------------------------------------------------
# Relationship hint matching and calibration
# ---------------------------------------------------------------------------


def _find_matching_hint(
    query_tables: set[str],
    from_tables: set[str],
    hints: tuple[RelationshipHint, ...],
) -> tuple[RelationshipHint, str, bool] | None:
    """Find the riskiest matching hint, direction-normalized multiplicity, and flip flag.

    MVP limitation: returns only one hint. For multi-join queries (3+ tables),
    only one table pair gets calibrated. Selects the riskiest multiplicity
    first (error > warning > info), then highest confidence as tiebreaker.

    Matches when both sides of a hint appear in the query's resolved table set.
    Flips multiplicity when the hint's left_table is not in FROM (i.e. tables
    are reversed relative to the query's join direction).

    When both or neither hint sides are in FROM (e.g. comma joins), no flip
    occurs — there's no clear directionality to normalize against.

    Returns (hint, normalized_multiplicity, was_flipped) or None.
    """
    tables_lower = {t.lower() for t in query_tables}
    from_lower = {t.lower() for t in from_tables}
    best: tuple[RelationshipHint, str, bool] | None = None
    best_risk: int = -1  # Higher = riskier
    for hint in hints:
        left = hint.left_table.lower()
        right = hint.right_table.lower()
        if left not in tables_lower or right not in tables_lower:
            continue
        mult = hint.multiplicity
        flipped = False
        if right in from_lower and left not in from_lower:
            mult = _FLIP_MULTIPLICITY.get(mult, mult)
            flipped = True
        severity = _severity_for_multiplicity(mult, hint.confidence, hint.fanout_factor)
        risk = {"error": 2, "warning": 1, "info": 0}.get(severity, 1)
        # Prefer riskiest; break ties by confidence.
        if risk > best_risk or (
            risk == best_risk and (best is None or hint.confidence > best[0].confidence)
        ):
            best = (hint, mult, flipped)
            best_risk = risk
    return best


def _severity_for_multiplicity(
    mult: str,
    confidence: float,
    fanout_factor: float,
) -> Literal["error", "warning", "info"]:
    """Determine calibrated severity from multiplicity, confidence, and fanout."""
    if confidence < _MIN_CALIBRATION_CONFIDENCE:
        return "warning"
    if mult == "one-to-one":
        return "info"
    if mult == "many-to-one":
        return "info"
    if mult == "many-to-many":
        return "error"
    if mult == "one-to-many" and fanout_factor > HIGH_FANOUT_THRESHOLD:
        return "error"
    return "warning"


def _message_for_multiplicity(
    mult: str,
    from_table: str,
    join_table: str,
    fanout_factor: float,
    severity: Literal["error", "warning", "info"],
) -> tuple[str, str]:
    """Return (message, recommendation) grounded in known multiplicity.

    ``from_table`` / ``join_table`` are query-oriented: the FROM-clause side
    and the JOIN-clause side after direction normalization.
    """
    lt, rt = from_table, join_table
    ff = fanout_factor
    if mult == "one-to-one":
        return (
            "Aggregation over joined tables — 1:1 join confirmed, no inflation risk",
            "Join is 1:1 — aggregation is safe. "
            "Verify the 1:1 assumption holds as data evolves.",
        )
    if mult == "many-to-one":
        return (
            "Aggregation over joined tables — N:1 join, no row multiplication",
            f"Join {lt} → {rt} is N:1 (dimension lookup). "
            f"Aggregates on {lt} are safe. "
            f"Aggregates on {rt} columns may repeat values — "
            f"verify they are grouping keys, not measures.",
        )
    if mult == "many-to-many":
        if severity == "error":
            return (
                "N:M join with aggregation — results almost certainly inflated",
                f"Use a bridge table or pre-aggregate each side to the correct "
                f"grain before joining {lt} ↔ {rt}.",
            )
        return (
            "Possible N:M join with aggregation — review join relationship",
            f"Verify the join relationship between {lt} and {rt}. "
            f"If N:M, pre-aggregate each side before joining.",
        )
    if mult == "one-to-many":
        if severity == "error":
            return (
                f"1:N join with aggregation and high fanout "
                f"({ff:.1f}x) — results likely inflated",
                f"Pre-aggregate {rt} to the {lt} grain before joining "
                f"({ff:.1f}x average row multiplication).",
            )
        return (
            f"1:N join with aggregation ({ff:.1f}x fanout) "
            f"— verify aggregate correctness",
            f"Pre-aggregate {rt} to the {lt} join key grain before joining, "
            f"or verify aggregation handles the {ff:.1f}x row expansion correctly.",
        )
    # Unknown multiplicity
    return (
        "Aggregation over joined tables may inflate results",
        "Pre-aggregate each table to the join key grain "
        "before joining, or verify the join is 1:1 / N:1.",
    )


def _calibrate_fanout(
    hint: RelationshipHint,
    mult: str,
    flipped: bool,
    detail_parts: list[str],
) -> QueryDiagnostic:
    """Produce a calibrated fanout_risk diagnostic using a matched relationship hint."""
    # Orient table names to match the query's join direction.
    from_table = hint.right_table if flipped else hint.left_table
    join_table = hint.left_table if flipped else hint.right_table
    evidence = (
        f"Relationship: {from_table} ↔ {join_table} "
        f"({mult}, {hint.fanout_factor:.1f}x fanout, "
        f"confidence {hint.confidence:.0%})",
    )
    severity = _severity_for_multiplicity(mult, hint.confidence, hint.fanout_factor)
    message, recommendation = _message_for_multiplicity(
        mult, from_table, join_table, hint.fanout_factor, severity
    )
    return QueryDiagnostic(
        code=WARN_FANOUT_RISK.code,
        severity=severity,
        message=message,
        detail=". ".join(detail_parts) if detail_parts else None,
        recommendation=recommendation,
        confidence=hint.confidence,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Diagnostic detectors
# ---------------------------------------------------------------------------


def _detect_missing_join_predicates(
    select: exp.Select,
) -> list[QueryDiagnostic]:
    """Detect comma joins and explicit CROSS JOINs."""
    diags: list[QueryDiagnostic] = []

    # Comma joins: multiple direct sources in FROM
    if _has_comma_join(select):
        sources = _direct_from_sources(select)
        names = [_source_name(s) for s in sources]
        diags.append(
            QueryDiagnostic(
                code=WARN_MISSING_JOIN_PREDICATE.code,
                severity="error",
                message=f"Implicit cross join between {', '.join(names)} — "
                f"no explicit join predicate",
                detail=f"Tables {', '.join(names)} appear in FROM without "
                f"a JOIN ... ON clause. This produces a cartesian product.",
                recommendation="Use explicit JOIN with ON clause to specify "
                "the join relationship.",
            )
        )

    # Explicit CROSS JOINs
    for left, right in _find_cross_joins(select):
        diags.append(
            QueryDiagnostic(
                code=WARN_MISSING_JOIN_PREDICATE.code,
                severity="error",
                message=f"CROSS JOIN between {left} and {right} — no join predicate",
                detail=f"CROSS JOIN produces a cartesian product of {left} × {right}.",
                recommendation="If intentional, document why. Otherwise, "
                "use JOIN with ON clause.",
            )
        )

    return diags


def _resolve_unqualified_agg_source(
    col_name: str,
    known_sources: dict[str, str],
    agg_map: dict[str, dict[str, bool]],
) -> str | None:
    """Resolve an unqualified aggregate column to the one source that defines it.

    Whether a column is written qualified or not is cosmetic — the fanout
    verdict must be driven by which table it actually belongs to (FR-79).
    Only sources we've actually analyzed (``known_sources``, from CTEs/inline
    subqueries) count as evidence. Exactly one match resolves the column to
    that source, to be treated identically to a qualified reference. Zero
    matches (no lineage evidence at all — e.g. a raw base table) or 2+
    matches (a genuine name collision across sources) stay unresolved, since
    neither case lets us safely name a single owning table.
    """
    # known_sources.values() are, by construction, all agg_map keys — direct
    # indexing, not .get(s, {}), since a miss here would mean the resolver
    # above is broken, not that the source is merely unanalyzed.
    containing = {s for s in known_sources.values() if col_name in agg_map[s]}
    return next(iter(containing)) if len(containing) == 1 else None


def _detect_fanout_risk(
    select: exp.Select,
    relationship_context: RelationshipContext | None,
) -> list[QueryDiagnostic]:
    """Detect structural fanout risk: aggregation over joined tables.

    Triggers when:
    1. The query has JOINs (explicit or comma-join)
    2. The query has aggregate functions (in SELECT or HAVING)
    3. Any of:
       a. Aggregate functions reference columns from 2+ distinct tables, OR
       b. COUNT(*) is used (inflated by row multiplication from join), OR
       c. Unqualified columns appear in aggregates with 2+ tables in scope

    When relationship_context is provided, matched hints calibrate severity:
    - 1:1 → info (no inflation possible)
    - N:1 → info (dimension lookup, safe for many-side aggregates)
    - 1:N + high fanout → error
    - N:M → error (with sufficient confidence)
    """
    has_joins = _has_join(select) or _has_comma_join(select)
    if not has_joins:
        return []

    aggs = _get_aggregate_functions(select)
    if not aggs:
        return []

    alias_map = _build_alias_map(select)
    table_count = len(_direct_from_sources(select)) + len(_direct_join_sources(select))
    agg_lineage_map = _propagate_aggregate_columns(select)
    known_sources = _resolve_known_sources(select, agg_lineage_map)

    # Check for COUNT(*) — always risky with joins
    has_count_star = any(
        isinstance(agg, exp.Count) and isinstance(agg.this, exp.Star) for agg in aggs
    )

    # Track aliases (source refs) for multi-table detection — so self-joins
    # (e1.salary, e2.salary → same table) are correctly flagged.
    # Resolved names are only for detail messages.
    agg_source_refs: set[str] = set()
    agg_resolved_tables: set[str] = set()
    has_unqualified_agg_cols = False
    for agg in aggs:
        for col in agg.find_all(exp.Column):
            ref = _column_table_ref(col)
            if ref:
                agg_source_refs.add(ref)
                agg_resolved_tables.add(_resolve_table(ref, alias_map))
                continue
            # Unqualified — try to resolve it against known lineage before
            # falling back to "ambiguous" so a mere qualification change
            # doesn't flip the verdict (FR-79).
            resolved = _resolve_unqualified_agg_source(
                col.name.lower(), known_sources, agg_lineage_map
            )
            if resolved is not None:
                agg_source_refs.add(resolved)
                agg_resolved_tables.add(resolved)
            else:
                has_unqualified_agg_cols = True

    multi_table_agg = len(agg_source_refs) > 1
    # Unqualified columns in aggregates with 2+ tables = ambiguous ownership
    ambiguous_agg = has_unqualified_agg_cols and table_count >= 2

    has_structural_signal = has_count_star or multi_table_agg or ambiguous_agg

    # --- Relationship-triggered detection path ---
    # When no structural signal (COUNT(*), multi-table agg, ambiguous cols) but
    # relationship context indicates a risky join (1:N or N:M), any aggregation
    # + join should fire. This covers the most common real-world fanout pattern:
    # single-table aggregate inflated by a 1:N join (e.g. SUM(o.amount) with
    # JOIN line_items).
    if not has_structural_signal:
        if not relationship_context or not relationship_context.hints:
            return []
        all_query_tables = set(alias_map.values())
        from_tables = {
            src.name
            for src in _direct_from_sources(select)
            if isinstance(src, exp.Table)
        }
        match = _find_matching_hint(
            all_query_tables, from_tables, relationship_context.hints
        )
        if match is None:
            return []
        hint, mult, flipped = match
        # Only fire for risky multiplicities — 1:1 and N:1 are safe.
        if mult in ("one-to-one", "many-to-one"):
            return []
        return [
            _calibrate_fanout(
                hint,
                mult,
                flipped,
                [
                    "Single-table aggregation after row-multiplying join — "
                    "aggregate values are inflated by the join fanout"
                ],
            )
        ]

    # Build detail parts describing the structural signal
    detail_parts: list[str] = []
    if has_count_star:
        detail_parts.append("COUNT(*) is inflated by row multiplication from JOIN")
    if multi_table_agg:
        if len(agg_resolved_tables) == 1:
            tname = next(iter(agg_resolved_tables))
            aliases = sorted(agg_source_refs)
            detail_parts.append(
                f"Aggregate expressions reference columns from "
                f"aliases {', '.join(aliases)} (all from table {tname})"
            )
        else:
            detail_parts.append(
                f"Aggregate expressions reference columns from tables: "
                f"{', '.join(sorted(agg_resolved_tables))}"
            )
    if ambiguous_agg:
        detail_parts.append(
            "Unqualified columns in aggregate expressions with multiple "
            "tables in scope — column ownership is ambiguous"
        )

    # --- Relationship-based severity calibration ---
    # Match against resolved table names only (not aliases) to avoid
    # phantom matches with short alias names like 'o' or 'c'.
    all_query_tables = set(alias_map.values())
    # FROM tables for direction normalization.
    from_tables = {
        src.name for src in _direct_from_sources(select) if isinstance(src, exp.Table)
    }
    if relationship_context and relationship_context.hints:
        match = _find_matching_hint(
            all_query_tables, from_tables, relationship_context.hints
        )
        if match is not None:
            hint, mult, flipped = match
            # Early return: the calibrated finding subsumes the generic
            # structural warning. detail_parts (COUNT(*), multi-table refs)
            # are preserved in the calibrated diagnostic.
            return [_calibrate_fanout(hint, mult, flipped, detail_parts)]

    # No relationship context or no matching hint — generic structural finding
    return [
        QueryDiagnostic(
            code=WARN_FANOUT_RISK.code,
            severity="warning",
            message="Aggregation over joined tables may inflate results",
            detail=". ".join(detail_parts) if detail_parts else None,
            recommendation="Pre-aggregate each table to the join key grain "
            "before joining, or verify the join is 1:1 / N:1.",
        )
    ]


# ---------------------------------------------------------------------------
# Propagation-backed re-aggregation detection
# ---------------------------------------------------------------------------


def _select_output_columns(select: exp.Select) -> dict[str, bool]:
    """Map output alias → is_aggregate_derived for a SELECT.

    Returns a dict where keys are the output column names (alias or column name)
    and values indicate whether the column is derived from an aggregate function.
    Group-by keys are explicitly marked as non-aggregate.
    """
    # Collect GROUP BY column names to exclude them from aggregate tagging
    group_by_keys: set[str] = set()
    group = select.args.get("group")
    if group:
        for expr in group.expressions:
            if isinstance(expr, exp.Column):
                group_by_keys.add(expr.name.lower())

    outputs: dict[str, bool] = {}
    for expr in select.expressions:
        alias = expr.alias if isinstance(expr, exp.Alias) else None
        if alias:
            inner = expr.this
            # AggFunc inside a Window node is a window function (per-row),
            # not a collapsed aggregate — don't tag as aggregate-derived.
            agg_node = inner.find(exp.AggFunc)
            is_agg = bool(agg_node) and not bool(inner.find(exp.Window))
            outputs[alias.lower()] = is_agg
        elif isinstance(expr, exp.Column):
            outputs[expr.name.lower()] = False
        elif isinstance(expr, exp.Star):
            # Can't track through SELECT * — skip
            pass
    # Group-by keys are never aggregate-derived even if aliased from one
    for key in group_by_keys:
        if key in outputs:
            outputs[key] = False
    return outputs


def _propagate_aggregate_columns(
    parsed: exp.Expression,
) -> dict[str, dict[str, bool]]:
    """Build a map of source_name → {column_name: is_aggregate_derived}.

    Walks CTEs and inline subqueries to determine which output columns
    are aggregate-derived so the outer query can detect re-aggregation.
    """
    agg_map: dict[str, dict[str, bool]] = {}

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


def _resolve_known_sources(
    select: exp.Select, agg_map: dict[str, dict[str, bool]]
) -> dict[str, str]:
    """Map each FROM/JOIN source name visible in ``select`` to its ``agg_map`` key.

    Only sources we've actually analyzed — CTEs and inline subqueries, whose
    own output columns ``agg_map`` tracks — resolve to an entry. A raw base
    table never appears here since we have no way to see its columns.

    Shared by re-aggregation and fanout-risk detection: both need to resolve
    a column reference (qualified or not) back to the source that actually
    defines it, rather than guessing from the column's name alone.
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
    agg_map: dict[str, dict[str, bool]],
) -> bool:
    """Whether an output column inherits aggregate-derived status from lineage.

    A qualified reference (``ref`` present in ``source_keys``) binds
    unambiguously to that one source — trust it directly. An unqualified
    reference must NOT be resolved by matching the column's *name* against
    any joined source: a join used only for filtering can carry an unrelated
    column that happens to share a name with a genuinely aggregate one
    elsewhere in the query (FR-79). Instead, only the sources that actually
    define the column count as evidence, and all of them must agree it's
    aggregate-derived — a real name collision (some agree, some don't) must
    not silently propagate as if it were unambiguous.
    """
    # source_keys.values() are, by construction, all agg_map keys — direct
    # indexing throughout, not .get(..., {}), matching _resolve_unqualified_
    # agg_source's reasoning above.
    if ref and ref in source_keys:
        return bool(agg_map[source_keys[ref]].get(col_name))
    containing = [s for s in source_keys.values() if col_name in agg_map[s]]
    return bool(containing) and all(agg_map[s][col_name] for s in containing)


def _inherit_aggregate_lineage(
    select: exp.Select,
    outputs: dict[str, bool],
    agg_map: dict[str, dict[str, bool]],
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
            if _column_inherits_aggregate(col_name, ref, source_keys, agg_map):
                outputs[col_name] = True
        elif isinstance(expr, exp.Alias):
            inner = expr.this
            alias_name = expr.alias.lower()
            if outputs.get(alias_name):
                continue
            # Check if the inner expression is a simple column reference
            if isinstance(inner, exp.Column):
                col_name = inner.name.lower()
                ref = (inner.table or "").lower()
                if _column_inherits_aggregate(col_name, ref, source_keys, agg_map):
                    outputs[alias_name] = True


# Hash-of-a-key expressions, as sqlglot types the BigQuery/Snowflake spellings —
# TO_HEX(MD5(k)) and bare MD5(k) parse to different nodes, hence both forms.
_KEY_HASH_NODES = (
    exp.MD5,
    exp.MD5Digest,
    exp.SHA,
    exp.SHA1Digest,
    exp.SHA2,
    exp.SHA2Digest,
    exp.FarmFingerprint,
)


def _is_hashed_sum_distinct(node: exp.Expression) -> bool:
    """True for ``SUM(DISTINCT <expr containing a hash of a key>)``."""
    return (
        isinstance(node, exp.Sum)
        and node.find(exp.Distinct) is not None
        and node.find(*_KEY_HASH_NODES) is not None
    )


def _unwrap_paren(node: exp.Expression) -> exp.Expression:
    """``node`` with any wrapping parentheses stripped."""
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _enclosing_sub(node: exp.Expression) -> exp.Sub | None:
    """The subtraction ``node`` is an operand of, looking through parentheses."""
    parent = node.parent
    while isinstance(parent, exp.Paren):
        parent = parent.parent
    return parent if isinstance(parent, exp.Sub) else None


def _is_symmetric_aggregate_half(agg: exp.AggFunc) -> bool:
    """True when ``agg`` is one half of Looker's symmetric aggregate.

    Looker reads a measure across a fan-out join without double-counting by
    packing the measure with a hash of the dedup key, ``SUM(DISTINCT ...)``-ing
    so duplicate keys collapse, then subtracting a second ``SUM(DISTINCT
    hash-only)`` to remove the packed hash mass::

        ( SUM(DISTINCT (CAST(ROUND(COALESCE(M,0)*s, 9) AS NUMERIC) + OFFSET(K)))
          - SUM(DISTINCT OFFSET(K)) ) / s        ==  sum of M over distinct K

    Both halves are required: the subtraction is what makes the pair an identity
    over a per-key value, and a lone ``SUM(DISTINCT value + hash)`` is not a
    meaningful quantity at all. So this stays a narrow shape match rather than an
    exemption for ``SUM(DISTINCT ...)`` generally, which would hide real
    re-aggregation. The emitting side is
    ``libs/looker/likeml/src/likeml/symmetric.py``.
    """
    agg_expr = as_expression(agg)
    # AggFunc is an Expr trait, not an Expression subclass, so this narrowing
    # is what satisfies the type checker, not a runtime-meaningful check. None
    # falls through to the same "not a match" answer this predicate already
    # gives a plain non-hashed-sum aggregate.
    if agg_expr is None or not _is_hashed_sum_distinct(agg_expr):
        return False
    sub = _enclosing_sub(agg_expr)
    if sub is None:
        return False
    # Each operand must itself BE a hashed SUM(DISTINCT ...) — searching its
    # subtree instead would let any hashed sum buried anywhere inside the sibling
    # satisfy the pairing, and a real re-aggregation could ride along beside it.
    return all(
        _is_hashed_sum_distinct(_unwrap_paren(side))
        for side in (sub.this, sub.expression)
    )


def _detect_reaggregation(
    select: exp.Select, parsed: exp.Expression
) -> list[QueryDiagnostic]:
    """Detect re-aggregation: outer aggregate wrapping an already-aggregated column.

    Uses propagation of aggregate lineage through subqueries and CTEs to
    identify columns that are aggregate-derived, then checks whether
    the outer query applies aggregate functions to those columns.
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
    seen: set[tuple[str, str]] = set()

    for agg in outer_aggs:
        if _is_symmetric_aggregate_half(agg):
            continue
        for col in agg.find_all(exp.Column):
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
                col_info = agg_map.get(src_name, {})
                key = (src_name, col_name)
                if col_info.get(col_name) and key not in seen:
                    seen.add(key)
                    outer_func = type(agg).__name__.upper()
                    diags.append(
                        QueryDiagnostic(
                            code=WARN_REAGGREGATION.code,
                            severity="warning",
                            message=(
                                f"{outer_func}({col.name}) re-aggregates an "
                                f"already aggregate-derived column"
                            ),
                            detail=(
                                f"Column '{col.name}' is produced by an aggregate "
                                f"in source '{src_name}'. Applying {outer_func} "
                                f"on top is likely a statistical error."
                            ),
                            recommendation=(
                                "Review whether the outer aggregation is correct. "
                                "Summing a pre-summed column or averaging an "
                                "already-averaged column usually produces "
                                "incorrect results."
                            ),
                        )
                    )
    return diags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_inline_suppressions(sql: str) -> set[str]:
    """Extract suppressed diagnostic codes from ``-- dct:ignore`` comments.

    Supports:
    - ``-- dct:ignore WARN-FANOUT-RISK`` — suppress one code
    - ``-- dct:ignore WARN-FANOUT-RISK WARN-REAGGREGATION`` — suppress multiple
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
    relationship_context: RelationshipContext | None = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[False] = ...,
) -> list[QueryDiagnostic]: ...


@overload
def validate_query(
    sql: str,
    *,
    dialect: str | None = ...,
    relationship_context: RelationshipContext | None = ...,
    suppress: set[str] | None = ...,
    return_suppressed: Literal[True],
) -> tuple[list[QueryDiagnostic], list[QueryDiagnostic]]: ...


def validate_query(
    sql: str,
    *,
    dialect: str | None = None,
    relationship_context: RelationshipContext | None = None,
    suppress: set[str] | None = None,
    return_suppressed: bool = False,
) -> list[QueryDiagnostic] | tuple[list[QueryDiagnostic], list[QueryDiagnostic]]:
    """Validate a SQL query and return structural diagnostics.

    Args:
        sql: SQL query string to validate.
        dialect: Optional SQLGlot dialect name (e.g. "duckdb", "bigquery").
        relationship_context: Optional relationship metadata for severity
            calibration. When provided, fanout_risk findings are refined
            using known multiplicity, fanout factor, and confidence.
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
    diags.extend(_detect_fanout_risk(parsed, relationship_context))
    diags.extend(_detect_reaggregation(parsed, parsed))

    active, suppressed_diags = _apply_suppression(diags, suppressed_codes)
    if return_suppressed:
        return active, suppressed_diags
    return active
