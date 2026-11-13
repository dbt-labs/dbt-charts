"""The one place a name Python's compiled board gives a query becomes a plan key.

Stage: EXECUTE (collect)
Purpose: A chart or variable that reads a query written inline has no authored
    name: the Python board calls it by a name it generates (``_inline_query_<chart>``,
    ``_var_options_*``), the plan keys it by position. The plan names each chart's and
    variable's queries (``chart_queries``, ``variable_queries``), and a read of one
    goes through that map by public chart id or variable name; no name is synthesized
    here. A named query is a key of the plan under its own name.
"""

from collections.abc import Iterator

from dbt_charts.core.compile.compiler import cross_board_query_key
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.authored._layer import CartesianLayer
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.variable.authored import (
    SingleRowBoolProbe,
    Variable,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_UNKNOWN_CHART,
    ERR_UNKNOWN_VARIABLE,
)
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.query_plan import QueryPlan, VariableQueries


def _layers(chart: Chart) -> list[CartesianLayer]:
    """The layers of a chart; only the cartesian families carry them."""
    if isinstance(chart, BarChart | LineChart | AreaChart | ScatterChart):
        return chart.layers
    return []


def _declared(board: Board) -> dict[str, Variable]:
    return board.variable_registry or {}  # type-state: silent_fallback — none declared


def _readers(board: Board, name: str) -> Iterator[tuple[str, str, int | str]]:
    """The charts and variables of *board* that read the query *name*: (kind, id, slot), the
    slot a layer's index or the variable's ``query``, ``options`` or ``enabled``."""
    for chart in board.placed_charts():
        if chart.query_name == name:
            yield "chart", chart.id, "query"
        layers = _layers(chart)
        for index, layer in enumerate(layers):
            if layer.query == name:
                yield "chart", chart.id, index
    registry = _declared(board)
    for variable_name, variable in registry.items():
        if variable.query == name:
            yield "variable", variable_name, "query"
        if variable.get_option_query() == name:
            yield "variable", variable_name, "options"
        if (
            isinstance(variable.enabled, SingleRowBoolProbe)
            and variable.enabled.query == name
        ):
            yield "variable", variable_name, "enabled"


def _variable_key(entry: VariableQueries, slot: str) -> str | None:
    match slot:
        case "query":
            return entry["query"]
        case "options":
            return entry["options"]
        case _:
            assert slot == "enabled"
            return entry["enabled"]


def plan_key(plan: QueryPlan, board: Board, name: str) -> str | None:
    """The key of the plan's query that *name* names for *board*, or None when no chart,
    variable or imported chart's text reads *name* and the plan does not hold it.

    Raises:
        ExecutionError: A chart or variable that reads *name* is not in the plan's
            ``chart_queries`` or ``variable_queries`` (ERR-UNKNOWN-CHART,
            ERR-UNKNOWN-VARIABLE), or has no query in that slot.
    """
    if name in plan["queries"]:
        return name
    for kind, owner, slot in _readers(board, name):
        if kind == "chart":
            chart_entry = plan["chart_queries"].get(owner)
            if chart_entry is None:
                raise ExecutionError.from_code(
                    ERR_UNKNOWN_CHART,
                    chart=owner,
                    available=sorted(plan["chart_queries"]),
                )
            key = (
                chart_entry["query"]
                if slot == "query"
                else chart_entry["layers"][int(slot)]
            )
        else:
            variable_entry = plan["variable_queries"].get(owner)
            if variable_entry is None:
                raise ExecutionError.from_code(
                    ERR_UNKNOWN_VARIABLE,
                    surface="variable",
                    owner_name=owner,
                    var_name=owner,
                )
            key = _variable_key(variable_entry, str(slot))
        if key is not None:
            return key
    # A query an imported chart's display text reads: the board's own name for it is the key
    # its file and name make, and the plan keys it `file#name`.
    for key in plan["queries"]:
        file, separator, local = key.rpartition("#")
        if separator and cross_board_query_key(file, local) == name:
            return key
    return None


def aliases(plan: QueryPlan, board: Board) -> dict[str, str]:
    """The Python name of each query a planned chart or variable of *board* reads, to its plan
    key: for what is keyed by name and read back by name (truncations, provenance). A chart
    or variable the plan does not show has none."""
    found: dict[str, str] = {}
    for chart in board.placed_charts():
        entry = plan["chart_queries"].get(chart.id)
        if entry is None:
            continue
        if chart.query_name is not None and entry["query"] is not None:
            found[chart.query_name] = entry["query"]
        layers = _layers(chart)
        for layer, key in zip(layers, entry["layers"], strict=True):
            if layer.query is not None and key is not None:
                found[layer.query] = key
    registry = _declared(board)
    for name, variable in registry.items():
        entry = plan["variable_queries"].get(name)
        if entry is None:
            continue
        for python_name, key in (
            (
                variable.query if isinstance(variable.query, str) else None,
                entry["query"],
            ),
            (variable.get_option_query(), entry["options"]),
            (
                variable.enabled.query
                if isinstance(variable.enabled, SingleRowBoolProbe)
                else None,
                entry["enabled"],
            ),
        ):
            if python_name is not None and key is not None:
                found[python_name] = key
    return found
