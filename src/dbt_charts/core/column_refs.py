"""Extract the base-table columns a compiled board's SQL queries consume.

The demand side of the column-drift story: `warehouse_check` asks "does this
SQL still bind", this module answers "which physical columns does it lean on"
— the reverse index behind "which boards break if `orders.customer_id`
changes". Resolution runs through sqlglot's scope walk, so a column reached
through a CTE, alias, or correlated subquery is attributed to the base table
that actually carries it, not the derived relation it was read from.

Honesty contract, same shape as `warehouse_check`'s ``unchecked``: a query
whose column set cannot be determined is reported as *indeterminate*, never
silently omitted — a board missing from an impact list reads as "safe to
rename", which is the one wrong answer that matters. That covers a ``SELECT
*``, unparseable SQL, a jinja expression standing where a column or table
name would be (``WHERE {{ filter('country', country) }}``), and a dbt call
spelling the substitution regexes don't cover. Every SQL entry of the
compiled registry is analyzed — inline chart queries, layers, cross-board
imports, shared-file refs: their registry *names* are synthetic, but their
SQL is authored, and a reverse index has no execute-time second chance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
import sqlglot.errors
import sqlglot.expressions as exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.tokens import TokenType

from dbt_charts.core.compile.models.query.normalized import is_sql_query
from dbt_charts.core.compile.normalize.queries import dialect_for_source
from dbt_charts.core.compile.sql_guard import (
    SKELETON_PLACEHOLDER_PREFIX,
    build_skeleton,
    sqlglot_dialect,
)
from dbt_charts.core.diagnostics.execution import UnparseableSqlError
from dbt_charts.core.execute.dbt_jinja import (
    REF_CALL_RE,
    SOURCE_CALL_RE,
    has_dbt_jinja,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.compiler import CompileResult

_PARSE_ERRORS = (
    UnparseableSqlError,
    sqlglot.errors.ParseError,
    sqlglot.errors.TokenError,
)


@dataclass
class QueryColumnRefs:
    """Base-table columns one query consumes, or why that cannot be known.

    Table names are as authored: bare (``orders``), qualified dotted with
    every authored part kept (``analytics.orders``,
    ``my_project.analytics.orders``), or the dbt source's canonical dotted
    spelling (``raw.orders`` for ``{{ source('raw', 'orders') }}``).
    """

    columns: set[tuple[str, str]] = field(default_factory=set)
    via_dbt: frozenset[str] = frozenset()
    """Lowercased table names that arrived via a ref()/source() call — the
    provenance that separates "is the dbt node" from a bare-name collision.
    ref() entries are bare model names; source() entries are the dotted
    source spelling, which the model-column check (keyed on model names)
    never consults — they record provenance, not a checked claim."""
    indeterminate: str | None = None


def extract_base_column_refs(
    compile_result: CompileResult,
) -> dict[str, QueryColumnRefs]:
    """Per-query (table, column) pairs for every SQL query of a compiled board.

    Non-SQL queries are skipped; every SQL entry — including synthetic-named
    inline chart queries and cross-board imports — is analyzed, keyed by its
    registry name. Each query parses under its own source's dialect when the
    registry can name one, and as generic SQL otherwise (a dbt-profile
    source's real dialect is unknowable until execute; a generic parse that
    succeeds yields the right columns, and one that fails is reported).

    A query's ``setup_sql`` contributes the base tables its ``CREATE ... AS
    SELECT`` statements read, and the relations it *defines* are subtracted
    from the body's tables — a temp view is not the base table the columns
    come from.
    """
    board = compile_result.board
    if board is None:
        raise ValueError("extract_base_column_refs requires a compiled board")
    sources = board.sources
    out: dict[str, QueryColumnRefs] = {}
    for name, query in compile_result.query_registry.items():
        if not is_sql_query(query):
            continue
        dialect = sqlglot_dialect(dialect_for_source(query.source, sources))
        refs = _refs_for_sql(query.sql, dialect=dialect)
        if query.setup_sql and refs.indeterminate is None:
            setup_columns, defined, setup_via_dbt, setup_indeterminate = _setup_refs(
                query.setup_sql, dialect=dialect
            )
            if setup_indeterminate is not None:
                refs = QueryColumnRefs(indeterminate=setup_indeterminate)
            else:
                refs.columns = {
                    (table, column)
                    for table, column in refs.columns
                    if table.lower() not in defined
                } | setup_columns
                refs.via_dbt = refs.via_dbt | setup_via_dbt
        out[name] = refs
    return out


def _substitute_dbt_calls(sql: str) -> tuple[str, frozenset[str]] | None:
    """ref()/source() → model / dotted source names, or None when unresolvable.

    The returned set carries the substituted names (lowercased) — the
    provenance consumers need to tell "this table *is* the dbt node" from a
    bare name that merely collides with one.
    """
    via_dbt: set[str] = set()

    def _ref(m: re.Match[str]) -> str:
        via_dbt.add(m.group(1).lower())
        return m.group(1)

    def _source(m: re.Match[str]) -> str:
        name = f"{m.group(1)}.{m.group(2)}"
        via_dbt.add(name.lower())
        return name

    sql = REF_CALL_RE.sub(_ref, sql)
    sql = SOURCE_CALL_RE.sub(_source, sql)
    # A spelling the regexes deliberately don't cover — package-qualified or
    # versioned ref(), macro-computed args. The skeleton would turn it into a
    # placeholder *table*, silently invisible to --table narrowing.
    return None if has_dbt_jinja(sql) else (sql, frozenset(via_dbt))


def parse_sql_statements(
    sql: str, dialect: str | None
) -> tuple[list[exp.Expression], frozenset[str]] | str:
    """Parsed statements of the all-branches skeleton, or the failure reason.

    The shared front half of every static SQL read in the column-drift story:
    dbt-call substitution (with provenance), all-branches skeletonization, and
    a multi-statement parse. Success returns the statements plus the set of
    table names that arrived via ref()/source().
    """
    substituted = _substitute_dbt_calls(sql)
    if substituted is None:
        return (
            "a dbt ref()/source() call uses a spelling this index cannot "
            "resolve to a model name"
        )
    substituted_sql, via_dbt = substituted
    try:
        # All branches of every {% if %} are kept — a column read only in an
        # {% else %} body is still a dependency. The branch-join can yield
        # multiple statements; columns union across them.
        skeleton = build_skeleton(substituted_sql, all_branches=True)
        parsed = sqlglot.parse(skeleton, read=dialect)
    except _PARSE_ERRORS as exc:
        return f"SQL could not be parsed: {exc}"
    statements = [
        s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)
    ]
    # sqlglot unwraps `WITH a AS (...) (WITH a AS (...) SELECT ...)` by
    # overwriting the inner WITH with the outer one: the tree then binds names
    # to the wrong CTE. A CTE-opening WITH with no WITH node is that loss.
    with_nodes = sum(len(list(s.find_all(exp.With))) for s in statements)
    if _cte_with_keywords(skeleton, dialect) > with_nodes:
        return "SQL cannot be analyzed: the parser dropped a WITH clause"
    return statements, via_dbt


def _cte_with_keywords(sql: str, dialect: str | None) -> int:
    """WITH tokens opening `[RECURSIVE] name [(cols)] AS [NOT] [MATERIALIZED] (`.

    Counts a lower bound: other WITHs (`WITH ORDINALITY AS t`, ClickHouse
    `WITH 1 AS x`) never match, so only a lost CTE list exceeds the tree.
    """
    tokens = sqlglot.tokenize(sql, read=dialect)
    texts = [t.text.upper() for t in tokens]
    count = 0
    for i, token in enumerate(tokens):
        if token.token_type != TokenType.WITH:
            continue
        j = i + 1 + (texts[i + 1 : i + 2] == ["RECURSIVE"])
        j += 1
        if j < len(tokens) and tokens[j].token_type == TokenType.L_PAREN:
            depth = 0
            while j < len(tokens):
                if tokens[j].token_type == TokenType.L_PAREN:
                    depth += 1
                elif tokens[j].token_type == TokenType.R_PAREN:
                    depth -= 1
                j += 1
                if depth == 0:
                    break
        if j >= len(tokens) or tokens[j].token_type != TokenType.ALIAS:
            continue
        j += 1
        while j < len(tokens) and texts[j] in ("NOT", "MATERIALIZED"):
            j += 1
        if j < len(tokens) and tokens[j].token_type == TokenType.L_PAREN:
            count += 1
    return count


def _refs_for_sql(sql: str, dialect: str | None) -> QueryColumnRefs:
    parsed = parse_sql_statements(sql, dialect)
    if isinstance(parsed, str):
        return QueryColumnRefs(indeterminate=parsed)
    statements, via_dbt = parsed
    refs = QueryColumnRefs(via_dbt=via_dbt)
    for statement in statements:
        _collect(statement, dialect, refs)
        if refs.indeterminate is not None:
            return QueryColumnRefs(indeterminate=refs.indeterminate)
    return refs


def _setup_refs(
    setup_sql: str, dialect: str | None
) -> tuple[set[tuple[str, str]], set[str], frozenset[str], str | None]:
    """(base columns read, relations defined, ref provenance, indeterminate).

    ``setup_sql`` holds DDL — temp functions/macros/tables/views. A ``CREATE
    … AS <query>`` contributes the query's base columns; a function or macro
    body has no SELECT scope and nothing to contribute, which is not an
    indeterminacy. Whatever a statement defines is subtracted from the body's
    tables by the caller.
    """
    parsed = parse_sql_statements(setup_sql, dialect)
    if isinstance(parsed, str):
        return set(), set(), frozenset(), parsed
    statements, via_dbt = parsed
    columns: set[tuple[str, str]] = set()
    defined: set[str] = set()
    for statement in statements:
        if not isinstance(statement, exp.Create):
            continue
        created = statement.this
        if isinstance(created, exp.Table):
            parts = [created.catalog, created.db, created.name]
            defined.add(".".join(x for x in parts if x).lower())
            defined.add(created.name.lower())
        query = statement.expression
        if isinstance(query, exp.Query):
            inner = QueryColumnRefs()
            _collect(query.copy(), dialect, inner)
            if inner.indeterminate is not None:
                return set(), set(), frozenset(), inner.indeterminate
            columns |= inner.columns
    return columns, defined, via_dbt, None


def _resolve_source(scope: Scope, table_alias: str) -> exp.Table | Scope | None:
    """The alias's source in this scope or, for a correlated reference, the
    nearest ancestor scope that knows it."""
    current: Scope | None = scope
    while current is not None:
        source = current.sources.get(table_alias)
        if source is not None:
            return source
        current = current.parent
    return None


def _restates_an_output_column(col: exp.Column) -> bool:
    """Is this an ORDER BY / DISTINCT ON restatement of the query's own output?

    Such a column names a projection the query's SELECT scopes already walked,
    so the base column behind it is collected there. Two shapes reach here with
    no table of their own: sqlglot's scope filter drops an ORDER BY restatement
    from a plain SELECT's columns, and a set operation's ORDER BY sits in a
    scope with no table sources at all — the arms are traversed as scopes of
    their own, so nothing there answers to the clause's empty table name.
    Bounded by ``named_selects``, so a name the query does not project still
    fails closed.

    The clause must be the SELECT's or set operation's *own*, so the walk stops
    at the first enclosing query: the scope filter also drops an unqualified
    HAVING/QUALIFY column, and one nested in a subquery inside an ORDER BY
    expression must not be forgiven for sharing a name with the outer query's
    output — its own table is the one that would go silently unrecorded.
    """
    ancestor = col.find_ancestor(exp.Order, exp.Distinct, exp.Query)
    return (
        isinstance(ancestor, (exp.Order, exp.Distinct))
        and isinstance(ancestor.parent, (exp.Select, exp.SetOperation))
        and not col.table
        and col.name in ancestor.parent.named_selects
    )


def _collect(
    statement: exp.Expression, dialect: str | None, refs: QueryColumnRefs
) -> None:
    # COLUMNS() selects by pattern, not by name — and the regex/lambda
    # spellings carry no Star node, so the star check below can't see them.
    # Ruled on first so every COLUMNS() spelling shares this reason.
    if next(statement.find_all(exp.Columns), None) is not None:
        refs.indeterminate = "a COLUMNS() selection hides which columns are consumed"
        return
    # Stars are ruled on *before* qualify: qualify can expand a star from a
    # CTE's declared alias list even with no schema, fabricating determinate
    # base-table columns out of alias names. Fail closed: every star is a
    # column-consuming selection except COUNT(*)'s row-count idiom.
    for star in statement.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            refs.indeterminate = "a `*` selection hides which columns are consumed"
            return

    try:
        statement = qualify(statement, dialect=dialect)
    except sqlglot.errors.SqlglotError:
        refs.indeterminate = "column-to-table attribution is ambiguous without a schema"
        return

    scopes = traverse_scope(statement)
    if not scopes:
        refs.indeterminate = "statement has no analyzable SELECT scope"
        return

    for scope in scopes:
        for col in scope.columns:
            # Lowered before the containment test: qualify normalizes
            # identifiers per dialect, and Snowflake uppercases them —
            # __dct_j0__ arrives as __DCT_J0__.
            if SKELETON_PLACEHOLDER_PREFIX in col.name.lower():
                refs.indeterminate = (
                    "a templated expression stands where a column name would be"
                )
                return
            source = _resolve_source(scope, col.table)
            if isinstance(source, exp.Table):
                parts = [source.catalog, source.db, source.name]
                # Containment, not prefix: build_skeleton splices placeholders
                # inline, so `events_{{ env }}` yields events___dct_j0__.
                if any(
                    SKELETON_PLACEHOLDER_PREFIX in part.lower()
                    for part in parts
                    if part
                ):
                    refs.indeterminate = (
                        "a templated expression stands where a table name would be"
                    )
                    return
                refs.columns.add((".".join(p for p in parts if p), col.name))
            elif source is None:
                if _restates_an_output_column(col):
                    continue
                refs.indeterminate = (
                    f"column {col.name!r} could not be attributed to a table"
                )
                return
            # A Scope source is a CTE/subquery: its own walk collects the
            # base columns it stands on.

    # Fail closed on anything the scope walk did not account for: sqlglot's
    # scope filter drops an unqualified column whose nearest clause is HAVING
    # or QUALIFY, and whatever arm it grows next should land here, not vanish.
    accounted = {id(c) for scope in scopes for c in scope.columns}
    for col in statement.find_all(exp.Column):
        if id(col) in accounted or _restates_an_output_column(col):
            continue
        refs.indeterminate = (
            f"column {col.name!r} sits in a clause this analysis cannot "
            "attribute to a table"
        )
        return
