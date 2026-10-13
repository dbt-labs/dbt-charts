"""The formats a query's columns print in: its own ``formats:`` over dbt lineage."""

from __future__ import annotations

from collections.abc import Container

from dbt_charts.core.column_lineage import lineage_column_formats
from dbt_charts.core.compile.format import grain_is_redefined
from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.chart.normalized._base import _BaseChartFields
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.normalize.queries import dialect_for_source
from dbt_charts.core.compile.sql_guard import sqlglot_dialect
from dbt_charts.core.dbt_ref_check import named_source_target_path
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.dbt_jinja import has_dbt_jinja
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.text.predefined_formats import ALL_PREDEFINED_NAMES, GRAIN_NAMES


def known_format_names(chart_style_context: ChartStyleContext) -> frozenset[str]:
    """Every format name the board resolves; a grain name only once redefined."""
    table = chart_style_context.formats
    aliases = {} if table is None else table.aliases
    redefined = {name for name in GRAIN_NAMES if grain_is_redefined(name, aliases)}
    return frozenset((ALL_PREDEFINED_NAMES - GRAIN_NAMES) | aliases.keys() | redefined)


def reads_dbt_node(query: SqlQuery) -> bool:
    """Only a dbt node carries a format, and one reaches SQL through ref()/source()."""
    if query.setup_sql is None:
        return has_dbt_jinja(query.sql)
    return has_dbt_jinja(query.sql) or has_dbt_jinja(query.setup_sql)


def with_dbt_column_formats(
    board: Board, executor: Executor, variables: VariableValues
) -> Board:
    """The board with each dbt-reading chart query's ``formats:`` widened by lineage.

    Lineage runs once per query, before anything resolves, against the engine
    names and the root board's ``style.formats`` (nested boards inherit them): a
    name outside those fails the query's charts in every placement.
    """
    derived: dict[str, dict[str, str] | None] = {}

    def widen(node: Board) -> Board:
        items = []
        for item in node.layout.items:
            if item.board is not None:
                nested = widen(item.board)
                if nested is not item.board:
                    item = item.model_copy(update={"board": nested})
            chart = item.chart
            if (
                isinstance(chart, _BaseChartFields)
                and isinstance(chart.query, SqlQuery)
                and chart.query_name is not None
                and reads_dbt_node(chart.query)
            ):
                if chart.query_name not in derived:
                    derived[chart.query_name] = _derive(
                        chart.query_name,
                        chart.query,
                        executor,
                        variables,
                        known_format_names(board.chart_style_context),
                    )
                found = derived[chart.query_name]
                authored = {} if chart.query.formats is None else chart.query.formats
                usable = (
                    {}
                    if found is None
                    else {col: fmt for col, fmt in found.items() if col not in authored}
                )
                if usable:
                    query = chart.query.model_copy(
                        update={"formats": {**usable, **authored}}
                    )
                    item = item.model_copy(
                        update={"chart": chart.model_copy(update={"query": query})}
                    )
            items.append(item)
        # Sizing mutates the layout in place; an untouched board stays the caller's.
        if all(new is old for new, old in zip(items, node.layout.items, strict=True)):
            return node
        return node.model_copy(
            update={"layout": node.layout.model_copy(update={"items": items})}
        )

    return widen(board)


def raise_column_format_error(chart: Chart, executor: Executor) -> None:
    """Raise the lineage failure recorded for the chart's query, if any.

    Called where a chart resolves, so a bad ``meta.format`` fails the charts
    that read the column and no others.
    """
    if (
        isinstance(chart, _BaseChartFields)
        and isinstance(chart.query, SqlQuery)
        and reads_dbt_node(chart.query)
        and chart.query_name in executor.column_format_errors
    ):
        raise executor.column_format_errors[chart.query_name]


def _derive(
    query_name: str,
    query: SqlQuery,
    executor: Executor,
    variables: VariableValues,
    known: Container[str],
) -> dict[str, str] | None:
    """Column -> format lineage derives for the query, keyed as its result names
    them (a dbt_profile adapter lowercases what the SQL aliased). The query's
    own ``formats:`` columns are never derived, so a bad ``meta.format`` under
    one cannot fail. None, with the failure recorded, when lineage fails."""
    authored = {} if query.formats is None else query.formats
    try:
        target_path = named_source_target_path(
            query.source,
            executor.board.sources,
            executor.adapter_registry.project.sources.sources,
        )
        base_formats = executor.adapter_registry.dbt_refs.column_formats(target_path)
        if not base_formats:
            return {}
        dialect = sqlglot_dialect(query.dialect) or sqlglot_dialect(
            dialect_for_source(query.source, executor.board.sources)
        )
        found = lineage_column_formats(
            query.sql,
            dialect=dialect,
            setup_sql=query.setup_sql,
            base_formats=base_formats,
            known=known,
            exclude={col.lower() for col in authored},
        )
    except DbtChartsError as exc:
        executor.column_format_errors[query_name] = exc
        return None
    try:
        rows = executor.execute_query(query_name, variables)
    except DbtChartsError:
        # The chart reports its own failed query when it resolves.
        return found
    if not rows:
        return found
    exact = set(rows[0])
    lowered = {col.lower(): col for col in reversed(list(rows[0]))}
    decided: dict[str, str] = {}
    for col, fmt in found.items():
        if col in exact:
            decided[col] = fmt
        elif col.lower() in lowered:
            decided[lowered[col.lower()]] = fmt
    return decided
