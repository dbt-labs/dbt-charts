"""Carry a dbt column's ``meta.format`` through board SQL to its output columns.

Built on ``column_refs``' front end (Jinja skeleton, scope resolution). An
output column takes a format only by one of the rules in ``infer``; anything
else, or inputs that disagree, gives none. A missed format is acceptable and a
guessed one is not, so every unmodeled expression and every SQL this cannot
parse types nothing.
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass, field, replace

import sqlglot.errors
import sqlglot.expressions as exp
from sqlglot.optimizer.scope import Scope, build_scope

from dbt_charts.core.column_refs import (
    name_dbt_nodes,
    parse_sql_statements,
    qualify_without_schema,
    resolve_source,
)
from dbt_charts.core.dbt_manifest import RawFormats, RawTableFormats
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_COLUMN_FORMAT_UNKNOWN
from dbt_charts.core.diagnostics.execution import ExecutionError

_PASS_THROUGH_AGGREGATES: tuple[type[exp.Expr], ...] = (
    exp.Sum,
    exp.Avg,
    exp.Min,
    exp.Max,
    exp.Median,
    exp.PercentileCont,
    exp.PercentileDisc,
)
_PERCENT_FORMATS = frozenset({"percent", "percent_number"})
_KEEP_INPUT_FUNCTIONS: tuple[type[exp.Expr], ...] = (
    exp.Round,
    exp.Abs,
    exp.Floor,
    exp.Ceil,
)
_ALIAS_CLASH = "dct_alias_clash"


def _mark_alias_clashes(query: exp.Query) -> None:
    """Flag each unqualified column named like another projection's alias in its
    SELECT: dialects disagree on whether it means the alias or the base column."""
    for select in query.find_all(exp.Select):
        aliases = {
            p.alias.lower() for p in select.expressions if isinstance(p, exp.Alias)
        }
        for projection in select.expressions:
            own = projection.alias.lower() if isinstance(projection, exp.Alias) else ""
            for col in projection.find_all(exp.Column):
                name = col.name.lower()
                if (
                    not col.table
                    and col.find_ancestor(exp.Select) is select
                    and name in aliases - {own}
                ):
                    col.meta[_ALIAS_CLASH] = True


@dataclass
class _Lineage:
    dialect: str | None
    # lowercased manifest name -> (authored name, lowercased column -> format)
    base: dict[str, tuple[str, RawFormats]]
    # reserved identifier -> manifest name
    nodes: dict[str, str]
    known: Container[str]
    # Relations the query's own setup_sql defines, typed only as a column is read.
    defined: dict[str, exp.Query] = field(default_factory=dict)

    def leaf(self, scope: Scope, col: exp.Column) -> str | None:
        source = resolve_source(scope, col.table)
        name = col.name.lower()
        if isinstance(source, Scope):
            return self.output(source, name)
        if not isinstance(source, exp.Table):
            return None
        table = source.name.lower()
        if table in self.nodes:
            entry = self.base.get(self.nodes[table].lower())
            if entry is None or name not in entry[1]:
                return None
            model, columns = entry
            spec = columns[name]
            if not isinstance(spec, str) or spec not in self.known:
                raise ExecutionError.from_code(
                    ERR_DBT_COLUMN_FORMAT_UNKNOWN,
                    kind="source" if "." in model else "model",
                    model=model,
                    column=col.name,
                    spec=spec if isinstance(spec, str) else repr(spec),
                )
            return spec
        if source.catalog or source.db or table not in self.defined:
            return None
        # A view's body reads the relations that exist before it, never itself.
        earlier = replace(
            self, defined={k: v for k, v in self.defined.items() if k != table}
        )
        return earlier.outputs(self.defined[table], only=name).get(name)

    def output(self, scope: Scope, name: str) -> str | None:
        query = scope.expression
        if not isinstance(query, exp.Query):
            return None
        names = [s.alias_or_name.lower() for s in query.selects]
        if name not in names:
            return None
        return self.output_at(scope, names.index(name))

    def output_at(self, scope: Scope, index: int) -> str | None:
        if isinstance(scope.expression, exp.SetOperation):
            branches = [self.output_at(s, index) for s in scope.set_operation_scopes]
            first = branches[0] if branches else None
            if first is not None and all(b == first for b in branches):
                return first
            return None
        query = scope.expression
        if not isinstance(query, exp.Query):
            return None
        return self.infer(scope, query.selects[index])

    def infer(self, scope: Scope, node: exp.Expr) -> str | None:
        if isinstance(node, (exp.Alias, exp.Paren, exp.Window)):
            return self.infer(scope, node.this)
        if isinstance(node, exp.Column):
            if node.meta.get(_ALIAS_CLASH):
                return None
            return self.leaf(scope, node)
        if isinstance(node, exp.Cast):
            if node.to.this not in exp.DataType.NUMERIC_TYPES:
                return None
            return self.infer(scope, node.this)
        if isinstance(node, exp.Count):
            return "integer"
        if isinstance(node, exp.Year) or (
            isinstance(node, exp.Extract) and node.this.name.upper() == "YEAR"
        ):
            return "year"
        if isinstance(node, exp.WithinGroup) and isinstance(
            node.this, _PASS_THROUGH_AGGREGATES
        ):
            ordered = node.expression.expressions[0]
            return self._aggregate(node.this, self.infer(scope, ordered.this))
        if isinstance(node, _PASS_THROUGH_AGGREGATES):
            # MAX(a, b) with several arguments is GREATEST, over unlike columns.
            if node.expressions:
                return None
            return self._aggregate(node, self.infer(scope, node.this))
        if isinstance(node, _KEEP_INPUT_FUNCTIONS):
            return self.infer(scope, node.this)
        if isinstance(node, (exp.Add, exp.Sub)):
            left = self.infer(scope, node.this)
            return (
                left
                if left is not None and left == self.infer(scope, node.expression)
                else None
            )
        if isinstance(node, exp.Div):
            return self._divide(
                self.infer(scope, node.this), self.infer(scope, node.expression)
            )
        if isinstance(node, exp.Case):
            branches = [i.args["true"] for i in node.args["ifs"]]
            default = node.args.get("default")
            return self._same(scope, branches + ([default] if default else []))
        if isinstance(node, exp.If):
            return self._same(scope, [node.args["true"], node.args.get("false")])
        if isinstance(node, exp.Coalesce):
            return self._same(scope, [node.this, *node.expressions])
        return None

    @staticmethod
    def _aggregate(node: exp.Expr, inner: str | None) -> str | None:
        # An id has no meaningful aggregate but COUNT; summed percentages are
        # not a percentage.
        if inner is None or inner == "id":
            return None
        if isinstance(node, exp.Sum) and inner in _PERCENT_FORMATS:
            return None
        return inner

    @staticmethod
    def _divide(left: str | None, right: str | None) -> str | None:
        if left is None or right is None:
            return None
        if left == right:
            return "percent"
        if (left, right) == ("currency", "integer"):
            return "currency"
        return None

    def _same(self, scope: Scope, branches: list[exp.Expr | None]) -> str | None:
        typed = [
            self.infer(scope, b)
            for b in branches
            if b is not None and not isinstance(b, exp.Null)
        ]
        first = typed[0] if typed else None
        if first is None or any(t != first for t in typed):
            return None
        return first

    def outputs(
        self,
        query: exp.Query,
        exclude: Container[str] = (),
        only: str | None = None,
    ) -> dict[str, str | None]:
        """Output name (as authored) -> format, for one SELECT or set operation.

        ``only`` (lowercased) types that one output and no other.
        """
        if any(not isinstance(s.parent, exp.Count) for s in query.find_all(exp.Star)):
            return {}
        # Names and positions come from one list: an unnamed projection still
        # holds its place.
        names = [s.alias_or_name for s in query.selects]
        query = query.copy()
        _mark_alias_clashes(query)
        try:
            # Alias expansion stays off: a column sharing a projection's alias is
            # marked above and types nothing, so it is never guessed either way.
            root = build_scope(
                qualify_without_schema(query, self.dialect, expand_alias_refs=False)
            )
        except sqlglot.errors.SqlglotError:
            return {}
        if root is None:
            return {}
        return {
            name: self.output_at(root, i)
            for i, name in enumerate(names)
            if name
            and name.lower() not in exclude
            and (only is None or name.lower() == only)
        }


def lineage_column_formats(
    sql: str,
    *,
    dialect: str | None,
    setup_sql: str | None,
    base_formats: RawTableFormats,
    known: Container[str],
    exclude: Container[str] = (),
) -> dict[str, str]:
    """Format of each named output column of ``sql`` that lineage can type.

    ``base_formats`` maps a ref name, or ``source.table``, to its columns'
    ``meta.format``. ``known`` is every format name the board resolves; a base
    column read through the query whose name is not in it raises
    ERR-DBT-COLUMN-FORMAT-UNKNOWN. SQL that cannot be parsed or attributed
    types nothing. Output columns named in ``exclude`` (lowercased) are not
    derived, so a bad format feeding only them is never read.
    """
    nodes: dict[str, str] = {}
    parsed = parse_sql_statements(name_dbt_nodes(sql, nodes), dialect)
    if isinstance(parsed, str):
        return {}
    statements = parsed[0]
    # An {% if %} that splits the body into several statements has no single
    # output to type.
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return {}
    lineage = _Lineage(
        dialect=dialect,
        base={
            table.lower(): (table, {c.lower(): f for c, f in columns.items()})
            for table, columns in base_formats.items()
        },
        nodes=nodes,
        known=known,
    )
    if setup_sql:
        setup = parse_sql_statements(name_dbt_nodes(setup_sql, nodes), dialect)
        if isinstance(setup, str):
            return {}
        for statement in setup[0]:
            if (
                isinstance(statement, exp.Create)
                and isinstance(statement.this, exp.Table)
                and isinstance(statement.expression, exp.Query)
            ):
                lineage.defined[statement.this.name.lower()] = statement.expression
    return {
        name: fmt
        for name, fmt in lineage.outputs(statements[0], exclude).items()
        if fmt is not None
    }
