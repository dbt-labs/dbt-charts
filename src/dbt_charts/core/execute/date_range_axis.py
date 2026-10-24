"""Which output column of a query is bounded by its own ``filter_date_range()``."""

from __future__ import annotations

from datetime import date, datetime

import sqlglot
import sqlglot.errors
from sqlglot import exp
from sqlglot.lineage import lineage

from dbt_charts.core.column_refs import qualify_without_schema
from dbt_charts.core.compile.template.parameterized import DateRangeFilter

_DATE_TYPES = (
    exp.DataType.Type.DATE,
    exp.DataType.Type.DATETIME,
    exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.TIMESTAMPLTZ,
    exp.DataType.Type.TIMESTAMPNTZ,
)


def _date_column(expression: exp.Expr, *, truncated: bool) -> exp.Column | None:
    """The column ``expression`` reads when it is that column, aliased, or cast to
    a date/timestamp, or (if ``truncated``) truncated to a date part.

    A ``zone`` (which sqlglot also uses for SQLite date modifiers) can shift the
    date, so it disqualifies.
    """
    while True:
        if isinstance(expression, (exp.Alias, exp.Paren)):
            expression = expression.this
        elif isinstance(expression, (exp.Date, exp.DateTrunc, exp.TimestampTrunc)):
            if expression.args.get("zone") or (
                not truncated and not isinstance(expression, exp.Date)
            ):
                return None
            expression = expression.this
        elif isinstance(expression, exp.Cast) and expression.to.is_type(*_DATE_TYPES):
            expression = expression.this
        else:
            return expression if isinstance(expression, exp.Column) else None


def _bound(expression: exp.Expr) -> date | None:
    """The calendar date a BETWEEN bound literally states, or None.

    Only a date cast or date-literal wrapper is stripped: arithmetic on the
    literal (``DATE '2026-03-01' - INTERVAL '1' YEAR``, SQLite's
    ``date('2026-03-01', '-1 year')``) states another date.
    """
    while True:
        if (
            isinstance(expression, exp.Cast)
            and expression.to.is_type(*_DATE_TYPES)
            or isinstance(expression, exp.Date)
            and set(expression.args) == {"this"}
        ):
            expression = expression.this
        else:
            break
    if not isinstance(expression, exp.Literal):
        return None
    try:
        return datetime.fromisoformat(expression.name).date()
    except ValueError:
        return None


def _conjuncts(condition: exp.Expr) -> list[exp.Expr]:
    if isinstance(condition, exp.And):
        return _conjuncts(condition.left) + _conjuncts(condition.right)
    if isinstance(condition, exp.Paren):
        return _conjuncts(condition.this)
    return [condition]


def _filters_column(range_filter: DateRangeFilter, column: exp.Column) -> bool:
    *qualifier, name = range_filter.column.lower().split(".")
    return name == column.name.lower() and (
        not qualifier or qualifier[-1] == column.table.lower()
    )


def proven_range(
    sql: str,
    dialect: str | None,
    output_column: str,
    filters: list[DateRangeFilter],
) -> tuple[date, date] | None:
    """The date range ``output_column`` of ``sql`` is proven bounded to, or None.

    ``filters`` are the ``filter_date_range()`` calls that rendered ``sql``. The
    link is proven only when the select reading the source column ANDs, at the top
    level of its ``WHERE``, the one ``BETWEEN`` carrying a recorded filter's bounds.
    """
    try:
        qualified = qualify_without_schema(
            sqlglot.parse_one(sql, dialect), dialect, validate=False
        )
        node = lineage(output_column, qualified, schema={}, dialect=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    chain = [node]
    while chain[-1].downstream:
        if len(chain[-1].downstream) != 1:
            return None
        chain.append(chain[-1].downstream[0])
    *steps, leaf = chain
    if not steps or not isinstance(leaf.expression, exp.Table):
        return None
    if any(_date_column(step.expression, truncated=True) is None for step in steps):
        return None
    reader = steps[-1]
    column = _date_column(reader.expression, truncated=True)
    select = reader.source
    if column is None or not isinstance(select, exp.Select):
        return None
    where = select.args.get("where")
    matching = [f for f in filters if _filters_column(f, column)]
    if len(matching) != 1:
        return None
    rendered = [
        conjunct
        for conjunct in (_conjuncts(where.this) if where else [])
        if isinstance(conjunct, exp.Between)
        and (filtered := _date_column(conjunct.this, truncated=False)) is not None
        and filtered.name == column.name
        and filtered.table == column.table
    ]
    (recorded,) = matching
    if len(rendered) != 1:
        return None
    predicate = rendered[0]
    if (_bound(predicate.args["low"]), _bound(predicate.args["high"])) != (
        recorded.start,
        recorded.end,
    ):
        return None
    return recorded.start, recorded.end
