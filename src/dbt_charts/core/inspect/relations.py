"""The warehouse relations a board reads.

Answers "which tables must a credential be able to `SELECT` from for this board to
render?" — from the board's own queries, without touching a warehouse. A caller
with no access at all still gets the full list.

Deliberately *not* derived from dbt's manifest: the question is what the board
reads, which the board itself knows, and a manifest dependency would tie this to
`dbt parse` having run.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SchemaQuery,
    SqlQuery,
)
from dbt_charts.core.compile.sql_guard import (
    SKELETON_PLACEHOLDER_PREFIX,
    UnparseableSqlError,
    as_expression,
    build_skeleton,
)

# A relation part carrying the skeleton's placeholder prefix is Jinja in a
# position that decides which relation is read — the one place this module
# must not guess.
_JINJA_PREFIX = SKELETON_PLACEHOLDER_PREFIX


@dataclass(frozen=True)
class Relation:
    """A warehouse relation named by a query.

    ``database`` and ``schema`` are None when the query did not qualify the name.
    They are kept as written rather than defaulted from the connection: what the
    board asked for is the honest answer, and resolving defaults belongs to
    whoever runs the query.
    """

    name: str
    schema: str | None = None
    database: str | None = None


@dataclass(frozen=True)
class BoardRelations:
    """Relations a board reads, plus the queries that could not be determined.

    ``undetermined`` is not an error state and not an empty set — it is the
    honest middle answer. A caller probing readability must report those queries
    as unchecked rather than counting them as clean.
    """

    relations: frozenset[Relation]
    undetermined: tuple[str, ...]


def relations_read_by(queries: dict[str, AnyQuery]) -> BoardRelations:
    """Collect the distinct relations *queries* read.

    `http` and `values` queries reach no warehouse and contribute nothing.

    Pass `CompileResult.query_registry`, not `Board.queries` — an inline chart
    query only ever lands in the registry, so a board authored the ordinary way
    has an empty `Board.queries` and would report reading nothing at all.

    The result is flat: a board whose queries name different `source`s returns
    one set, with nothing to say which credential should reach which relation.
    Probing per connection means filtering *queries* by `source` before calling.
    """
    found: set[Relation] = set()
    undetermined: list[str] = []

    for query_name, query in queries.items():
        if isinstance(query, SchemaQuery):
            found.update(_schema_query_relations(query))
        elif isinstance(query, SqlQuery):
            parsed = _sql_query_relations(query)
            if parsed is None:
                undetermined.append(query_name)
            else:
                found.update(parsed)
        # The remaining member, ValuesQuery, reaches no warehouse and
        # contributes nothing. An `assert_never` else-branch would say so
        # structurally, but AnyQuery is closed today, so pyright rejects the
        # narrowing it needs as unnecessary.

    return BoardRelations(frozenset(found), tuple(sorted(undetermined)))


def _schema_query_relations(query: SchemaQuery) -> set[Relation]:
    """A schema query names its target in literal fields — no parsing involved.

    Without a `table` it is an introspection listing (every table in a schema, or
    every schema), which lists the schema rather than reading a relation.
    """
    if not query.table:
        return set()
    return {Relation(name=query.table, schema=query.schema_name)}


def _sql_query_relations(query: SqlQuery) -> set[Relation] | None:
    """Relations *query* reads, or None when they cannot be determined.

    `setup_sql` runs on the same connection immediately before `sql`, and may
    create temporary views over real relations (`validate_setup_sql` accepts
    `CREATE TEMP VIEW`). Reading `sql` alone would miss those relations — the
    only warehouse reads in the query — while emitting the temp name itself as a
    relation no credential can ever be granted.
    """
    created: set[str] = set()
    relations: set[Relation] = set()

    for statement_sql in (query.setup_sql, query.sql):
        if not statement_sql:
            continue
        parsed = _sql_relations(statement_sql, created)
        if parsed is None:
            return None
        relations.update(parsed)

    # A temp created by setup_sql is not a warehouse relation, and may be read by
    # either statement — subtract at the end so order of appearance cannot matter.
    # By bare name only, like the CTE gate: a qualified `analytics.orders` is a
    # real relation even when a temp happens to share its name.
    return {
        r
        for r in relations
        if not (
            r.schema is None and r.database is None and r.name.casefold() in created
        )
    }


def _placeholder_decides_a_read(tree: exp.Expression) -> bool:
    """True when a Jinja placeholder survived somewhere that decides a relation.

    Two shapes are safe, and between them they cover how boards actually use
    variables: a placeholder parsed as a column reference (`WHERE {{ filter() }}`)
    and one parsed as a value literal (`WHERE region = '{{ region }}'`, the
    dominant spelling since dates, plans, and ids are quoted). A value cannot
    change which relation is read.

    Everything else is the template supplying structure, and is refused:

    - an identifier that is not a column reference — a table name, a table alias
      (`FROM t {{ join_macro() }}`), a select alias (`SELECT count(*) {{ from_() }}`,
      where the macro brings the whole FROM clause and no table node exists to
      inspect), or an interior part of a dotted name;
    - any placeholder under a table node, however spelled — `IDENTIFIER('{{ t }}')`
      resolves a *string* to an object, so the value carve-out above must not
      extend into relation position.
    """
    for node in tree.walk():
        node_expr = as_expression(node)
        if node_expr is None:
            # RuntimeError, not assert: every node Expression.walk() can
            # yield multiply-inherits Expression — an invariant of
            # sqlglot's own class hierarchy, not something this query's SQL
            # can violate — but an assert here would compile away under
            # `python -O` and quietly under-scan a tree this gate has to
            # trust.
            raise RuntimeError(
                f"sqlglot returned {type(node).__name__!r}, which has no Expression base"
            )
        if not any(
            isinstance(value, str) and _JINJA_PREFIX in value
            for value in node_expr.args.values()
        ):
            continue
        if _under_a_table(node_expr):
            return True
        if isinstance(node_expr, exp.Identifier) and not isinstance(
            node_expr.parent, exp.Column
        ):
            return True
    return False


def _under_a_table(node: exp.Expression) -> bool:
    """True when *node* sits inside a table reference, whatever its own type."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Table):
            return True
        parent = parent.parent
    return False


def _sql_relations(sql: str, created: set[str]) -> set[Relation] | None:
    """Relations read by *sql*, or None when it cannot be determined.

    Records any relation *sql* creates into *created*, so a caller can subtract
    session-local temporaries the same way CTE aliases are subtracted.

    None means "this query was not understood" and must not be conflated with an
    empty set: an empty set reads as "nothing to check", which would pass a
    credential that can read nothing at all.
    """
    try:
        # The shared skeletonizer walks the jinja2 AST and default-denies node
        # types it does not model, so SQL cannot hide inside one.
        skeleton = build_skeleton(sql)
    except UnparseableSqlError:
        return None
    try:
        # `parse`, not `parse_one`: a query may carry several statements, and
        # parse_one silently keeps the first — dropping the rest as if they read
        # nothing, which reports a partially-checked query as fully checked.
        statements = sqlglot.parse(skeleton)
    except SqlglotError:
        return None

    relations: set[Relation] = set()
    for tree in statements:
        if tree is None:
            continue
        # Anything that is neither a query nor DDL was not understood, and
        # `find_all` over it finds nothing — which reads as "reads nothing,
        # fully checked". Two shapes reach here: a statement sqlglot does not
        # model degrades to `exp.Command` with its SQL held as an opaque string
        # (`EXPLAIN …`), and a `{% if %}…{% else %}` in table position leaves the
        # `else` branch as a bare `exp.Column` once the skeletonizer splits the
        # branches on `;`. Both must be undetermined, not empty.
        if isinstance(tree, exp.Semicolon):
            # A trailing comment after `;` parses as a bare separator. The
            # sibling guard in sql_guard skips it for the same reason.
            continue
        if not isinstance(tree, exp.Query | exp.DDL):
            return None
        tree_expr = as_expression(tree)
        if tree_expr is None:
            # Query/DDL are typed against a broader base than Expression in
            # newer sqlglot releases, but every concrete node either can
            # produce still multiply-inherits it — a non-Expression tree
            # here means this query is genuinely undetermined, the same
            # channel a Semicolon/Command/bare-Column shape above already
            # routes through, not a safe-to-proceed shape.
            return None
        if _placeholder_decides_a_read(tree_expr):
            return None
        for target in tree_expr.find_all(exp.Create):
            # The created name may sit under a column list (`exp.Schema`) or a
            # function signature (`exp.UserDefinedFunction`), not directly on the
            # target. Reaching for the table under it covers all three spellings;
            # requiring it to *be* the target leaked the temp's own name as a
            # relation no grant can ever cover.
            created_table = target.this.find(exp.Table) if target.this else None
            if created_table is not None:
                created.add(created_table.name.casefold())
        # A CTE reference is an exp.Table like any other. Left in, every
        # CTE-bearing board reports a phantom relation whose probe fails against
        # a name that was never in the warehouse.
        cte_aliases = {cte.alias.casefold() for cte in tree_expr.find_all(exp.CTE)}
        for table in tree_expr.find_all(exp.Table):
            # sqlglot fills the two qualifier slots from the first two dots and
            # folds every remaining part into `this` as a Dot, whose `name` is
            # only the rightmost. A 4-part `srv.db.dbo.orders` would therefore
            # report each part one position out with `dbo` dropped entirely — a
            # relation that does not exist, reported as determined. More parts
            # than the model holds means the read is not determinable.
            if isinstance(table.this, exp.Dot):
                return None
            name = table.name
            schema = table.text("db") or None
            database = table.text("catalog") or None
            # `Table.name` is "" for a table *function* — `lookback_days(7)`,
            # `flatten(...)` — which names no relation. Reading the name parts
            # directly would recover the function's own name and send a probe
            # chasing it.
            if not name:
                continue
            # Only an *unqualified* name can be a CTE reference. Subtracting by
            # bare name would also drop `analytics.orders` from a query whose CTE
            # happens to be called `orders`.
            if schema is None and database is None and name.casefold() in cte_aliases:
                continue
            relations.add(Relation(name=name, schema=schema, database=database))
    return relations
