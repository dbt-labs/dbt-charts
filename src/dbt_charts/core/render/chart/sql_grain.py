"""SQL grain/lineage reasoning for auto-link: given a query's raw SQL, prove
whether its output is row-grain over a single base entity, and trace a base
column to its final output name.

This module owns the sqlglot AST reasoning; it has no dependency on links,
URLs, or the render context — that half lives in ``auto_link.py``, which
consumes ``resolve_cte_spine``, ``resolve_join_spine``, and
``build_col_output_alias_map`` as its grain/lineage layer.

Entry points:
  - ``resolve_cte_spine(tree)`` — resolve a single-CTE-wrap query to its base
    (schema, table), or ``_BAIL``. Pure AST walk, no adapter needed.
  - ``resolve_join_spine(tree, adapter_registry)`` — resolve a JOIN query to
    its spine (schema, table) via FK-edge proof, or ``_BAIL``.
  - ``build_col_output_alias_map(sql)`` — map base column names to their
    output names, traced through CTEs and subqueries, plus whether an
    unlisted column is still safe to assume identity for. See its docstring
    for the full contract: never assume an unlisted column shares its base
    name unless every relevant projection is a bare star.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

import sqlglot.errors
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

from dbt_charts.core.column_refs import fold_relation_names, resolve_source

if TYPE_CHECKING:
    from sqlglot.expressions import Expression as _SqlglotExpression

    from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry

# Sentinel used by private helpers to signal "bail — no location resolved".
# Callers check `if not result` to detect it.
_BAIL: tuple[()] = ()


def parse_sql(sql: str) -> _SqlglotExpression | None:
    """Strip Jinja tokens and parse ``sql`` into a sqlglot AST, or None on failure.

    Shared by every resolver in this module that needs to parse raw SQL.
    """
    import sqlglot
    import sqlglot.errors

    from dbt_charts.core.compile.sql_guard import as_expression

    stripped = re.sub(r"\{\{.*?\}\}", "1", sql, flags=re.DOTALL)
    stripped = re.sub(r"\{%.*?%\}", "", stripped, flags=re.DOTALL)
    try:
        parsed = sqlglot.parse_one(stripped)
    except sqlglot.errors.ParseError:
        return None
    return as_expression(parsed)


def resolve_cte_spine(tree: _SqlglotExpression) -> tuple[str, str] | tuple[()]:
    """Resolve a single-CTE row-grain query to its base (schema, table), or _BAIL.

    Succeeds only when:
    - Exactly one CTE is defined in the WITH clause, and the outer ``FROM``
      reads from *that CTE by name* — not some other table, and not a
      differently-named reference (a query whose outer SELECT ignores the
      CTE entirely must not resolve to the CTE's base table).
    - The CTE declares no explicit column-alias list (``WITH t(a, b) AS
      (...)``) — that syntax renames the body's projected columns
      *positionally*, which ``build_col_output_alias_map`` has no tracing
      for; without this bail it would derive output names straight from the
      body's own projection, silently wrong whenever the column list
      reorders or renames them (e.g. ``WITH t(ticket_number, id) AS (SELECT
      id, ticket_number FROM ...)`` swaps the two column names).
    - Nowhere in the whole query — outer or CTE body — is there a JOIN,
      GROUP BY, DISTINCT, aggregate function, or subquery. Any of those
      anywhere breaks the "one row in, one row out, over one base entity"
      guarantee, not just when they appear in the CTE body.
    - The CTE body reads from exactly one schema-qualified, non-catalog-
      qualified base table.

    Args:
        tree: Parsed sqlglot AST (a Select node with a With clause).

    Returns:
        ``(schema, table)`` on success, ``_BAIL`` on any violation.
    """
    from sqlglot import exp

    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) != 1:
        return _BAIL
    cte = ctes[0]

    cte_alias_node = cte.args.get("alias")
    if isinstance(cte_alias_node, exp.TableAlias) and cte_alias_node.columns:
        return _BAIL

    # The outer SELECT must read from exactly this CTE (by its alias) — a
    # query whose FROM is some other table (or the CTE plus something else)
    # doesn't have "the CTE's base table" as its identity at all.
    from_clause = tree.args.get("from_")
    if not isinstance(from_clause, exp.From):
        return _BAIL
    from_table_expr = from_clause.this
    if not isinstance(from_table_expr, exp.Table):
        return _BAIL
    if from_table_expr.db or from_table_expr.catalog:
        return _BAIL  # a real schema-qualified table, not a CTE reference
    # Unquoted SQL identifiers are case-insensitive — `SELECT * FROM Resolved`
    # against `WITH resolved AS (...)` is the same reference, not a mismatch.
    if from_table_expr.name.casefold() != cte.alias.casefold():
        return _BAIL

    # Grain-breaking constructs anywhere in the query — outer or CTE body —
    # bail. `find_all` walks the whole tree (including the CTE body, a nested
    # Select), so these checks cover both without duplicating the outer/body
    # split. `tree.args.get("joins")` alone would miss joins nested inside the
    # CTE body's own Select node — use `find_all(exp.Join)` instead.
    if list(tree.find_all(exp.Join)):
        return _BAIL
    if list(tree.find_all(exp.Group)):
        return _BAIL
    if list(tree.find_all(exp.Distinct)):
        return _BAIL
    if list(tree.find_all(exp.AggFunc)):
        return _BAIL
    if list(tree.find_all(exp.Subquery)):
        return _BAIL

    cte_body = cte.this
    body_tables = list(cte_body.find_all(exp.Table))
    if len(body_tables) != 1:
        return _BAIL

    t = body_tables[0]
    if not t.name or not t.db or t.catalog:
        return _BAIL

    return t.db, t.name


def resolve_join_spine(
    tree: _SqlglotExpression,
    adapter_registry: AdapterRegistry,
) -> tuple[str, str] | tuple[()]:
    """Resolve a JOIN query to its spine (schema, table) via FK proof, or _BAIL.

    Succeeds only when the FROM clause is a single schema-qualified table
    (the spine), no CTE/GROUP BY/DISTINCT/aggregate function/scalar subquery
    appears anywhere in the query, and every JOIN's ``ON`` clause contains an
    equality conjunct matching a known FK edge (a `fetch_fk_edges` result —
    declared dbt ``relationships:`` or a super-schema recommended heuristic,
    confidence ≥ 0.80) from the spine table to the joined table, exactly on
    column names (``spine.from_column = joined.to_column``) — not merely a
    join against a table that happens to be *some* FK target of the spine.

    The FK edge proves referential existence (spine values exist in the
    target), not target uniqueness (no ``unique:`` signal is checked) — a
    fan-out here duplicates spine rows without changing which entity each
    duplicate drills to, so it's bounded, not a wrong-link risk.

    An ``OR`` anywhere in the ``ON`` clause, or a join with no ``ON`` clause
    at all, bails — those shapes can't be safely reduced to a provable
    equi-join on the declared FK columns.

    Args:
        tree: Parsed sqlglot AST (a Select node with joins).
        adapter_registry: Executor adapter registry for FK-edge lookup.

    Returns:
        ``(schema, table)`` for the spine, or ``_BAIL``.
    """
    from sqlglot import exp

    # A CTE anywhere means this isn't the plain-JOIN shape this resolver
    # handles — build_col_output_alias_map has no tracing for "CTE + JOIN"
    # (an untraced key would bail there anyway), so bail here too rather
    # than resolve a location no link could ever safely be keyed on.
    if list(tree.find_all(exp.CTE)):
        return _BAIL
    # Aggregation collapses grain even with FK-proven joins — checked over the
    # whole tree (not just the outer node's own args), mirroring the CTE path.
    if list(tree.find_all(exp.Group)):
        return _BAIL
    if list(tree.find_all(exp.Distinct)):
        return _BAIL
    if list(tree.find_all(exp.AggFunc)):
        return _BAIL
    if list(tree.find_all(exp.Subquery)):
        return _BAIL

    # sqlglot stores the FROM clause under "from_" (not "from") because
    # "from" is a Python keyword.
    from_clause = tree.args.get("from_")
    if not isinstance(from_clause, exp.From):
        return _BAIL

    from_table_expr = from_clause.this
    if not isinstance(from_table_expr, exp.Table):
        return _BAIL

    spine_table = from_table_expr.name
    spine_schema = from_table_expr.db
    if not spine_table or not spine_schema or from_table_expr.catalog:
        return _BAIL
    spine_alias = from_table_expr.alias_or_name

    joins = tree.args.get("joins")
    if not joins:
        return _BAIL

    from dbt_charts.core.execute.adapters.schema_adapter import (  # noqa: PLC0415
        fetch_fk_edges,
    )

    fk_edges = fetch_fk_edges(adapter_registry, spine_table)
    if not fk_edges:
        return _BAIL

    for join in joins:
        joined_expr = join.this
        if not isinstance(joined_expr, exp.Table):
            return _BAIL
        joined_table = joined_expr.name
        joined_alias = joined_expr.alias_or_name

        on_expr = join.args.get("on")
        if on_expr is None:
            return _BAIL
        conjuncts = _flatten_and_conjuncts(on_expr)
        if conjuncts is None:
            return _BAIL

        candidate_edges = [e for e in fk_edges if e["to_table"] == joined_table]
        if not any(
            _join_predicate_matches_edge(conjuncts, spine_alias, joined_alias, edge)
            for edge in candidate_edges
        ):
            return _BAIL

    return spine_schema, spine_table


def _flatten_and_conjuncts(
    node: _SqlglotExpression,
) -> list[_SqlglotExpression] | None:
    """Flatten an AND-chain into its leaf conjuncts, or None if an OR is present.

    A join ``ON`` clause with an ``OR`` anywhere can't be safely reduced to "this
    equi-join predicate always holds" — callers must bail rather than guess.
    ``None`` here means "this shape can't be reasoned about", distinct from
    the module's usual ``_BAIL`` sentinel (which means "no location resolved")
    — reusing ``_BAIL`` for two different meanings would conflate them.

    Unwraps a top-level ``exp.Paren`` first — ``ON (a = b)`` is a common
    formatting choice and shouldn't be treated any differently from
    ``ON a = b``.
    """
    from sqlglot import exp

    if isinstance(node, exp.Paren):
        return _flatten_and_conjuncts(node.this)
    if isinstance(node, exp.Or):
        return None
    if isinstance(node, exp.And):
        left = _flatten_and_conjuncts(node.this)
        right = _flatten_and_conjuncts(node.expression)
        if left is None or right is None:
            return None
        return left + right
    return [node]


def _join_predicate_matches_edge(
    conjuncts: list[_SqlglotExpression],
    spine_alias: str,
    joined_alias: str,
    edge: dict[str, str],
) -> bool:
    """True when some conjunct equates spine.from_column with joined.to_column.

    Column-qualifier comparison uses the table alias actually used in the SQL
    (``spine_alias``/``joined_alias``, from ``Table.alias_or_name``), matched
    against the declared FK edge's column names — not table names guessed from
    naming convention. Comparisons are case-insensitive (``.casefold()``) on
    both sides — unquoted SQL identifiers and manifest-declared column names
    from a different casing convention are still the same reference.
    """
    from sqlglot import exp

    for cond in conjuncts:
        if not isinstance(cond, exp.EQ):
            continue
        left, right = cond.this, cond.expression
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        for x, y in ((left, right), (right, left)):
            if (
                x.table.casefold() == spine_alias.casefold()
                and x.name.casefold() == edge["from_column"].casefold()
                and y.table.casefold() == joined_alias.casefold()
                and y.name.casefold() == edge["to_column"].casefold()
            ):
                return True
    return False


# A base column: the physical table node and the column's name on it.
_Leaf = tuple[exp.Table, str]


def _is_star(node: exp.Expr) -> bool:
    return isinstance(node, exp.Star) or (
        isinstance(node, exp.Column) and isinstance(node.this, exp.Star)
    )


def _selects(scope: Scope) -> list[exp.Expr]:
    """A set operation has no projection list of its own to read."""
    query = scope.expression
    return query.selects if isinstance(query, exp.Select) else []


def _selected(scope: Scope) -> list[exp.Table | Scope]:
    return [source for _, source in scope.selected_sources.values()]


def _renamed_by_alias_list(scope: Scope) -> bool:
    """``WITH t(a, b) AS (...)`` renames the body's columns by position."""
    owner = scope.expression.parent
    if not isinstance(owner, (exp.CTE, exp.Subquery)):
        return False
    alias = owner.args.get("alias")
    return bool(alias and alias.columns)


def _resolve(scope: Scope, qualifier: str) -> exp.Table | Scope | None:
    """``resolve_source``, refusing a name that reads back the scope being traced.

    ``WITH p AS (SELECT p.id FROM t) ...``: ``p`` there is a column's table
    qualifier the engine may bind to a struct, not the CTE, and following it
    would trace in circles.
    """
    source = resolve_source(scope, qualifier)
    enclosing: Scope | None = scope
    while enclosing is not None:
        if source is enclosing:
            return None
        enclosing = enclosing.parent
    return source


def _star_source(scope: Scope) -> exp.Table | Scope | None:
    """The one relation the SELECT's single star expands, or None if unknowable."""
    stars = [p for p in _selects(scope) if _is_star(p)]
    if len(stars) != 1:
        return None
    if isinstance(stars[0], exp.Column) and stars[0].table:
        return _resolve(scope, stars[0].table)
    sources = _selected(scope)
    return sources[0] if len(sources) == 1 else None


def _leaf(scope: Scope, projection: exp.Expr) -> _Leaf | None:
    """The base column a bare-column projection reads, through CTEs and aliases."""
    column = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(column, exp.Column) or _is_star(column):
        return None
    if column.table:
        source = _resolve(scope, column.table)
    else:
        # Dialects disagree on whether this names a sibling alias or a base column.
        siblings = {
            p.alias.casefold()
            for p in _selects(scope)
            if isinstance(p, exp.Alias) and p is not projection
        }
        sources = _selected(scope)
        if column.name.casefold() in siblings or len(sources) != 1:
            return None
        source = sources[0]
    if isinstance(source, exp.Table):
        return source, column.name
    return None if source is None else _lookup(source, column.name)


def _outputs(scope: Scope) -> list[tuple[str, _Leaf | None]] | None:
    """(output name, base column) per projection; None when names are unknowable.

    A star over a CTE or subquery contributes that relation's own outputs; a
    star over a table contributes nothing (its columns are not listed).
    """
    query = scope.expression
    if not isinstance(query, exp.Select) or _renamed_by_alias_list(scope):
        return None
    outputs: list[tuple[str, _Leaf | None]] = []
    for projection in query.selects:
        if not _is_star(projection):
            outputs.append((projection.alias_or_name, _leaf(scope, projection)))
            continue
        source = _star_source(scope)
        if source is None:
            return None
        if isinstance(source, Scope):
            inner = _outputs(source)
            if inner is None:
                return None
            outputs.extend(inner)
    return outputs


def _lookup(scope: Scope, name: str) -> _Leaf | None:
    """The base column behind output ``name`` of ``scope``; None if not provable."""
    outputs = _outputs(scope)
    if outputs is None:
        return None
    matches = [leaf for out, leaf in outputs if out.casefold() == name.casefold()]
    if len(matches) > 1:
        return None
    if matches:
        return matches[0]
    # Not listed: only a star over one relation could carry it through.
    source = _star_source(scope)
    if isinstance(source, exp.Table):
        return source, name
    return None if source is None else _lookup(source, name)


def _spine(scope: Scope) -> exp.Table | None:
    """The table the query's first FROM entry reads, through CTEs and subqueries."""
    from_ = scope.expression.args.get("from_")
    if from_ is None:
        return None
    entry = scope.selected_sources.get(from_.this.alias_or_name)
    if entry is None:
        return None
    source = entry[1]
    return source if isinstance(source, exp.Table) else _spine(source)


def _is_identity(scope: Scope) -> bool:
    """Whether every layer down to the table is one bare star over one relation."""
    selects = _selects(scope)
    if (
        _renamed_by_alias_list(scope)
        or len(selects) != 1
        or not _is_star(selects[0])
        or len(_selected(scope)) != 1
    ):
        return False
    source = _star_source(scope)
    return isinstance(source, exp.Table) or (
        source is not None and _is_identity(source)
    )


def build_col_output_alias_map(sql: str) -> tuple[dict[str, str], bool]:
    """Map base table column names to their output aliases.

    The base table is the one the query's first FROM entry reads, followed
    through CTEs and subqueries. Anything not provable traces nothing and
    assumes no identity: a bare column that may be a sibling alias, a
    positional CTE column list, a star across a join, duplicate output names.

    Returns ``(alias_map, identity_safe_for_unlisted)``:

    - ``alias_map`` maps ``base_column_name → output_column_name`` for every
      column *provably* present in the final output under a traced name. An
      output name claimed by more than one projection (case-insensitively) is
      excluded: nothing proves which value survives into it.
    - ``identity_safe_for_unlisted`` is ``True`` only when every layer is a
      lone ``*`` over a single relation, so every base column provably passes
      straight through. When ``False``, a column absent from ``alias_map`` is
      **not** proven to appear in the output at all — callers must drop it
      (no false drill), never fall back to the base name. ``SELECT *, x AS id``
      is not identity-safe: the alias could shadow the sought key.

    This distinction is load-bearing: ``WITH t AS (SELECT ticket_number AS id,
    subject FROM main.tickets) SELECT * FROM t`` never selects the base ``id``,
    so a blind identity fallback would drill to the wrong record. The same
    holds for ``SELECT c.id, o.amount FROM orders o JOIN customers c ...`` —
    the spine is ``orders``, but the output's only ``id`` is ``customers.id``.

    See ``test_sql_grain.py`` for the worked-example matrix.

    Args:
        sql: Raw SQL string (Jinja tokens are stripped before parsing).
    """
    query = parse_sql(sql)
    if not isinstance(query, exp.Select) or (
        query.find(exp.CTE) and query.find(exp.Join)
    ):
        return {}, False
    try:
        root = build_scope(fold_relation_names(query))
        outputs = None if root is None else _outputs(root)
        if root is None or outputs is None:
            return {}, False
        spine = _spine(root)
        identity = _is_identity(root)
    except (sqlglot.errors.OptimizeError, RecursionError):
        # Scope resolution raises lazily, e.g. on a name used twice in one FROM.
        return {}, False
    claims = Counter(name.casefold() for name, _ in outputs if name)
    alias_map = {
        leaf[1]: name
        for name, leaf in outputs
        if leaf is not None and leaf[0] is spine and claims[name.casefold()] == 1
    }
    return alias_map, identity
