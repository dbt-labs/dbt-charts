"""YAML render output format — resolved board YAML.

Maps the dict produced by board_to_dict back to valid board YAML.
The output can be re-compiled and rendered without a database connection.
"""

from typing import Any

import yaml

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.chart_data_provider import ResolveDataProvider
from dbt_charts.core.render.board_to_dict import (
    NO_ROW_CAP,
    board_to_dict,
    chart_to_dict,
    clean_value,
    kept_rows_phrase,
)


def _board_to_yaml_dict(
    board_dict: dict[str, Any],
    truncation_notes: list[str],
) -> dict[str, Any]:
    """Convert a board_to_dict result into valid board YAML.

    Row-cap truncations can't live inside the document (an extra query key
    would break the round-trip re-compile contract), so they're reported as
    note strings appended to ``truncation_notes`` and emitted as comments.
    """
    result: dict[str, Any] = {}

    if board_dict.get("title"):
        result["title"] = board_dict["title"]

    # Collect queries and charts from items
    queries: dict[str, Any] = {}
    charts: dict[str, Any] = {}
    layout_refs: list[Any] = []

    for item in board_dict.get("items", []):
        if item["type"] == "chart":
            # A chart whose data failed carries no "chart" key at all. Emit it
            # as a callout so the diagnostic survives into the document and the
            # round-trip still re-compiles — a callout takes no `query`, which
            # is what the failed chart no longer has.
            if "_error" in item:
                err = item["_error"]
                chart_id = item["id"]
                charts[chart_id] = {
                    "type": "callout",
                    "message": f"{err['code']}: {err['message']}",
                    "style": {"tone": "negative"},
                }
                layout_refs.append(chart_id)
                continue

            chart = item["chart"]
            chart_id = chart.get("id", "chart")
            # None for a chart with no query at all (a callout). Emitting a
            # `query:` for it — and a fabricated values entry to point at —
            # makes the document fail to re-compile: the authored callout
            # surface forbids `query`.
            query_name = chart.get("query_name")

            if query_name:
                # Query: inline data via rows
                data = item.get("data", [])
                queries[query_name] = {"type": "values", "rows": clean_value(data)}
                truncated = item.get("rows_truncated")
                if truncated:
                    truncation_notes.append(
                        f"query {query_name} shows {kept_rows_phrase(truncated)}"
                    )

            # Chart definition
            charts[chart_id] = chart_to_dict(item, query_name)
            layout_refs.append(chart_id)

        elif item["type"] == "board":
            # Nested board — inline as a sub-board in layout
            nested = _board_to_yaml_dict(item["board"], truncation_notes)
            layout_refs.append(nested)

    if queries:
        result["queries"] = queries
    if charts:
        result["charts"] = charts
    if layout_refs:
        result["rows"] = layout_refs

    # Variables
    if board_dict.get("variables"):
        result["variables"] = clean_value(board_dict["variables"])

    return result


def render_board_yaml(
    board: Board,
    results: ResolveDataProvider,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> str:
    """Render a compiled board to resolved board YAML.

    Walks the layout tree, executes queries, resolves charts, and produces
    valid board YAML where queries use `values:` with inline data rows.
    The output can be fed back into `compile()` as valid input. A row cap
    truncates the inline rows; every truncation is declared in a comment
    header so the document is explicitly partial, never silently so.
    """
    board_dict = board_to_dict(
        board, results, variables, error_collector, max_rows_per_query
    )
    truncation_notes: list[str] = []
    yaml_dict = _board_to_yaml_dict(board_dict, truncation_notes)
    document = yaml.safe_dump(
        yaml_dict, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    header = "".join(f"# rows truncated: {note}\n" for note in truncation_notes)
    return header + document
