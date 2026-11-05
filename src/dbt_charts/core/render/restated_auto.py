"""Render-time WARN-RESTATED-INFERRED/-FORMAT: keys the renderer already picks for this data.

Needs query rows, so it runs after execution: each placed chart is resolved
again with one authored style leaf removed (``compile.restated.auto_restated``)
through the same runtime entry point the render uses.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias

from dbt_charts.core.compile.format import inferred_number_format
from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized import CalloutChart, Chart
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.primitives import LinkOpen
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)
from dbt_charts.core.compile.normalize.variables import infer_variable_input
from dbt_charts.core.compile.restated import (
    auto_restated,
    auto_restated_variable,
    chart_restated_paths,
    filtered_column,
    restated_diagnostic,
    scoped_charts,
    static_options_restated,
)
from dbt_charts.core.diagnostics import WARN_RESTATED_FORMAT, Diagnostic
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.cache_backend import ResultRows
from dbt_charts.core.execute.chart_data_provider import ResolveDataProvider, chart_rows
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.execute.query_results import UncollectedQueryError
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.variables_resolve import resolve_controls
from dbt_charts.core.text.case import field_title

Row: TypeAlias = dict[str, Any]  # type-state: explicit_any — DB rows
Artifact: TypeAlias = (
    object  # type-state: object_annotation — only compared for equality
)


def _resolver(
    scope: Board,
    data: ResultRows,
    width: float,
    results: ResolveDataProvider,
    variables: VariableValues,
    filter_variables: frozenset[str],
    link_open: LinkOpen,
) -> Callable[[Chart], tuple[ResolvedChart, Artifact]]:
    def resolve(candidate: Chart) -> tuple[ResolvedChart, Artifact]:
        resolved = resolve_chart_with_runtime_inputs(
            candidate,
            data.rows,
            scope.chart_style_context,
            width,
            results,
            variables,
            filter_variables=filter_variables,
            link_open=link_open,
        )
        artifact = render_resolved_chart(
            resolved, data, scope.resolved_style, width=width
        )
        return resolved, (artifact.payload, _plotted_titles(resolved))

    return resolve


def _plotted_titles(resolved: ResolvedChart) -> dict[str, str]:
    """The title each plotted field would print, painted or not: an axis whose
    title is hidden still changes with its authored label."""
    if not isinstance(resolved, _CartesianResolvedChartFields):
        return {}
    channels = [resolved.x, resolved.y, resolved.color]
    fields = [
        field
        for channel in channels
        for field in (channel if isinstance(channel, list) else [channel])
        if isinstance(field, str)
    ]
    return {field: field_title(field, resolved.field_labels) for field in fields}


def _declared_format_warnings(
    chart: Chart,
    scope: Board,
    data: list[Row],
) -> list[Diagnostic]:
    """A query ``formats:`` entry naming what the column's type already prints."""
    query = None if isinstance(chart, CalloutChart) else chart.query
    redefined = scope.chart_style_context.formats
    if query is None or not query.formats or (redefined and "number" in redefined):
        return []
    return [
        restated_diagnostic(
            WARN_RESTATED_FORMAT,
            f"queries.{chart.query_name}.formats.{column}",
            declared,
            "what the column's type prints without it",
            "inferred format",
            field=f"formats.{column}",
        )
        for column, declared in query.formats.items()
        if declared == inferred_number_format(column, (row.get(column) for row in data))
    ]


def auto_restated_warnings(
    board: Board,
    widths: Mapping[str, float],
    results: ResolveDataProvider,
    variables: VariableValues,
) -> list[Diagnostic]:
    """Warnings for every active-layout chart; ``widths`` maps chart id to its slot."""
    registry = board.variable_registry
    filter_variables = frozenset(registry) if registry else frozenset()
    warnings: list[Diagnostic] = []
    # Only a table prints a column by its type; a KPI or axis has its own default.
    declared_seen = {c.query_name for c, _ in scoped_charts(board) if c.type != "table"}
    for chart, scope in scoped_charts(board):
        width = widths.get(chart.id)
        if width is None:
            continue
        data = chart_rows(chart, results, variables)
        if chart.query_name and chart.query_name not in declared_seen:
            declared_seen.add(chart.query_name)
            warnings.extend(_declared_format_warnings(chart, scope, data.rows))

        resolve = _resolver(
            scope, data, width, results, variables, filter_variables, board.links.open
        )
        warnings.extend(
            auto_restated(
                chart, resolve, chart_restated_paths(chart, scope.chart_style_context)
            )
        )
    return warnings


@dataclasses.dataclass(frozen=True)
class ExtraQueries:
    """The queries the restated checks need beyond the render's own.

    ``definitions`` are probe queries the board does not define; ``calls`` pair
    the names to run with the variable values to run them under.
    """

    definitions: dict[str, SqlQuery]
    calls: list[tuple[frozenset[str], VariableValues]]


def _probe_name(name: str) -> str:
    return f"_restated_distinct_{name}"


def _static_options_probe(name: str, board: Board) -> tuple[SqlQuery, str, str] | None:
    """``SELECT DISTINCT`` of the column variable *name* filters, with its table and column."""
    sql_queries = [q for q in board.queries.values() if isinstance(q, SqlQuery)]
    found = filtered_column(name, sql_queries)
    if found is None:
        return None
    query, table, column = found
    probe = SqlQuery(
        sql=f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}",
        source=query.source,
        dialect=query.dialect,
        cache=query.cache,
    )
    return probe, table, column


def _is_static(var: Variable) -> bool:
    return var.options is not None and bool(var.options.static)


def plan_extra(board: Board, variables: VariableValues) -> ExtraQueries:
    """Every query ``auto_restated_variable_warnings`` reads that render did not collect.

    Planned from the board alone, so the render collects them with its own
    queries: a ``static`` options probe under *variables*, and each control's
    option and ``enabled`` queries under *variables* without that control.
    """
    definitions: dict[str, SqlQuery] = {}
    calls: list[tuple[frozenset[str], VariableValues]] = []
    for name, var in board.variables.items():
        if not var.authored_fields:
            continue
        if _is_static(var) and (probe := _static_options_probe(name, board)):
            probe_name = _probe_name(name)
            definitions[probe_name] = probe[0]
            calls.append((frozenset({probe_name}), variables))
        names = {
            q
            for q in (
                None if _is_static(var) else var.get_option_query(),
                var.enabled.query
                if isinstance(var.enabled, SingleRowBoolProbe)
                else None,
            )
            if q
        }
        if names:
            calls.append(
                (
                    frozenset(names),
                    {k: v for k, v in variables.items() if k != name},
                )
            )
    for item in board.layout.items:
        if item.board is not None and item.source_path:
            nested = plan_extra(item.board, variables)
            definitions |= nested.definitions
            calls += nested.calls
    return ExtraQueries(definitions, calls)


def _control_signature(
    name: str, scope: Board, results: ResolveDataProvider, values: VariableValues
) -> Callable[[Variable], Artifact]:
    def settle(var: Variable) -> Artifact:
        (control,) = resolve_controls(
            {name: var.model_copy(update={"visible": True})},
            {key: value for key, value in values.items() if key != name},
            results,
            scope.resolved_style.variables,
        )
        return tuple(
            getattr(control, field.name)
            for field in dataclasses.fields(control)
            if field.name != "var_def"
        )

    return settle


def _static_options_warnings(
    name: str,
    prefix: str,
    var: Variable,
    board: Board,
    results: ResolveDataProvider,
    variables: VariableValues,
) -> list[Diagnostic]:
    """``options.static`` against ``SELECT DISTINCT`` of the column the variable filters."""
    found = _static_options_probe(name, board)
    if found is None:
        return []
    _, table, column = found
    try:
        rows = results.execute_query(_probe_name(name), variables)
    except UncollectedQueryError:
        raise  # plan_extra and this walk disagree: a bug, not a failed probe
    except ExecutionError:
        return []  # advice only: a failing probe must not fail the render
    return static_options_restated(
        name, var, table, column, [r[column] for r in rows], prefix
    )


def auto_restated_variable_warnings(
    board: Board, results: ResolveDataProvider, variables: VariableValues, prefix: str
) -> list[Diagnostic]:
    """Warnings for every board's authored variable settings the data already settles."""
    warnings: list[Diagnostic] = []
    for name, var in board.variables.items():
        if not var.authored_fields:
            continue
        if var.options is not None and var.options.static:
            warnings += _static_options_warnings(
                name, prefix, var, board, results, variables
            )
        warnings += auto_restated_variable(
            name,
            var,
            _control_signature(name, board, results, variables),
            infer_variable_input,
            prefix,
        )
    for item in board.layout.items:
        if item.board is not None and item.source_path:
            warnings += auto_restated_variable_warnings(
                item.board, results, variables, f"{item.source_path}."
            )
    return warnings
