"""Walk a Board layout tree, execute queries, and produce a JSON-serializable dict.

The dict shape is the shared primitive that the JSON, YAML, and text formatters
serialize into their respective wire formats.
"""

import sys
import uuid
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.variable.authored import INTERNAL_VARIABLE_FIELDS
from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.chart_resolution import collect_shared_y_datasets
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.chart_diagnostics import stamp_chart_diagnostic

#: "No cap" for ``max_rows_per_query`` — every row is embedded. A named
#: unbounded default keeps the cap a required, always-comparable int instead
#: of an Optional threaded through every layer.
NO_ROW_CAP = sys.maxsize

#: Fields to emit on a chart definition, in readable order. Shared by the
#: ``yaml`` and ``data`` formats so there is one chart projection rather than
#: two. Kept reconciled with the accepted authored chart surface (the table in
#: ``core/AGENTS.md``) by ``test_chart_fields_covers_authored_surface``.
CHART_FIELDS = (
    "type",
    "query",
    "title",
    "subtitle",
    # ``label`` is the KPI-only label slot (sibling of ``value``); the
    # chart-type-aware gate keeps it off other chart types.
    "label",
    "notes",
    # Callout body text — the whole content of that family.
    "message",
    "link",
    "x",
    "y",
    "y_start",
    "color",
    "size",
    "shape",
    "theta",
    "format",
    "x_label",
    "y_label",
    "geo",
    "geo_source",
    "lookup",
    "value",
    # KPI quantitative-text-object fields (siblings of `value`).
    "support",
    "variant",
    # Pivot-table channels (authored as field-name lists).
    "rows",
    "values",
    "total",
    "projection",
    "latitude",
    "longitude",
    "collapse",
    "basemap",
    "sort",
    "multiples",
    "support_table",
    "conditional_formatting",
    "style",
)


def clean_value(v: Any) -> Any:
    """Convert non-YAML/JSON-native types to serializable equivalents."""
    if isinstance(v, BaseModel):
        return clean_value(v.model_dump(exclude_none=True))
    if isinstance(v, Mapping):
        return {k: clean_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [clean_value(item) for item in v]
    if isinstance(v, Decimal):
        # Use int if lossless, else float
        if v == v.to_integral_value():
            return int(v)
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.isoformat()
    if isinstance(v, timedelta):
        # ISO-8601 duration (e.g. "PT1H15M") — the same shape Pydantic's own
        # encoder already produced for a bare `timedelta` in an `Any`-typed
        # field before this branch existed, so YAML safety doesn't reshape
        # the published `--format data`/`--format json` wire format.
        # `to_jsonable_python` is the encoder that produced it, not a
        # hand-rolled formatter — negative durations, sub-second precision,
        # and day rollover are all easy to get subtly wrong by hand.
        return to_jsonable_python(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, set):
        return sorted(v)
    return v


def chart_to_dict(item: dict[str, Any], query_name: str | None) -> dict[str, Any]:
    """Project a resolved chart item onto the authored chart-definition fields.

    ``query_name`` is None for a chart that has no query at all (a callout);
    the ``query`` key is then omitted rather than pointed at something invented.
    """
    chart = item["chart"]
    d: dict[str, Any] = {}

    # Map chart_type → type
    if chart.get("chart_type"):
        d["type"] = chart["chart_type"]

    # `pivot_columns` is a pure rename of the authored `columns:` pivot
    # dimension (resolve assigns `pivot_columns=normalized.columns`), so it
    # remaps like chart_type→type. Emitting `rows`/`values` without it would
    # describe a pivot at a different grain than the author wrote — and one
    # that still re-compiles, so no round-trip check would catch it. Distinct
    # from `ResolvedTableChart.columns`, the per-column style dict, which has
    # no authored counterpart and stays omitted.
    if chart.get("pivot_columns"):
        d["columns"] = clean_value(chart["pivot_columns"])

    # Reference the query by name
    if query_name is not None:
        d["query"] = query_name

    # Copy known chart fields. ``title``, ``subtitle``, ``label`` and
    # ``notes`` default to ``""`` on Chart; an empty string would emit
    # `title: ''` next to the real authored slot, so treat empty as unauthored.
    for field in CHART_FIELDS:
        if field in ("type", "query"):
            continue  # already handled
        val = chart.get(field)
        if val is None:
            continue
        if field in ("title", "subtitle", "label", "notes") and val == "":
            continue
        if field == "collapse" and val is False:
            continue
        d[field] = clean_value(val)

    return d


def kept_rows_phrase(truncated: dict[str, int]) -> str:
    """Phrase a ``rows_truncated`` record: which rows survived, out of how many."""
    if truncated["tail"]:
        return (
            f"first {truncated['head']} and last {truncated['tail']} "
            f"of {truncated['total']} rows"
        )
    return f"first {truncated['head']} of {truncated['total']} rows"


def _render_chart_item(
    chart: Chart,
    executor: Executor,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> dict[str, Any]:
    """Execute, resolve, and serialize a single chart item.

    ``max_rows_per_query`` caps the embedded rows, keeping the head and tail
    of the result and dropping the middle — the top of a ranked query and the
    latest points of an ascending time series (the chart's right edge) both
    survive. Truncation is always explicit via a
    ``rows_truncated: {head, tail, total}`` record on the item.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.diagnostics.execution import ExecutionError
    from dbt_charts.core.render.errors import RenderError

    try:
        data = executor.execute_chart(chart, variables)
        chart_style_context = resolve_chart_style_context(get_theme_style())
        datasets = collect_shared_y_datasets(chart, data, executor, variables)
        # Resolve against the full result so data-aware resolution (auto type,
        # auto fields) is unaffected by the cap; only the emitted rows shrink.
        resolved = resolve(
            chart, data, chart_style_context=chart_style_context, datasets=datasets
        )
        rows_truncated: dict[str, int] = {}
        if len(data) > max_rows_per_query:
            tail = max_rows_per_query // 2
            head = max_rows_per_query - tail
            rows_truncated = {"head": head, "tail": tail, "total": len(data)}
            data = data[:head] + (data[-tail:] if tail else [])
        return {
            "type": "chart",
            # Exclude resolved-internal fields that have no authored-schema counterpart
            # and would break yaml-format round-trip re-compile.
            "chart": resolved.model_dump(
                mode="json",
                exclude_none=True,
                exclude={
                    "style",  # resolved style — not authored schema
                    "resolved_channels",  # internal channel bindings
                    "palette",  # resolved palette
                    "legend",  # resolved legend
                    "variable_dependencies",  # internal frozenset
                    "layout_padding",  # internal layout info
                    "preferred_width",  # internal baked width
                    "query",  # AnyQuery object — use query_name instead
                },
            ),
            "data": data,
            **({"rows_truncated": rows_truncated} if rows_truncated else {}),
        }
    except (RenderError, ExecutionError, DbtChartsError) as e:
        diagnostic = stamp_chart_diagnostic(e, chart.id, chart.source_path)
        if error_collector is not None:
            error_collector.append(diagnostic)
        return {
            "type": "chart",
            "id": chart.id,
            "_error": diagnostic.model_dump(exclude_none=True, exclude={"detail"}),
        }
    except Exception as e:  # noqa: BLE001
        wrapped = RenderError.from_code(ERR_INTERNAL, message=str(e))
        diagnostic = stamp_chart_diagnostic(wrapped, chart.id, chart.source_path)
        if error_collector is not None:
            error_collector.append(diagnostic)
        return {
            "type": "chart",
            "id": chart.id,
            "_error": diagnostic.model_dump(exclude_none=True, exclude={"detail"}),
        }


def _render_board_items(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> list[dict[str, Any]]:
    """Walk layout items and serialize each."""
    results: list[dict[str, Any]] = []
    for item in board.layout.items:
        if item.type == "chart" and item.chart:
            results.append(
                _render_chart_item(
                    item.chart, executor, variables, error_collector, max_rows_per_query
                )
            )
        elif item.type == "board" and item.board:
            results.append(
                {
                    "type": "board",
                    "board": board_to_dict(
                        item.board,
                        executor,
                        variables,
                        error_collector,
                        max_rows_per_query,
                    ),
                }
            )
    return results


def board_to_dict(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> dict[str, Any]:
    """Convert a board to a JSON-serializable dict.

    ``max_rows_per_query`` caps the rows embedded per chart item — for
    model-facing output where an unbounded dump would blow out context.
    Truncation is never silent: capped items carry ``rows_truncated``.
    Defaults to ``NO_ROW_CAP`` (embed everything).
    """
    if max_rows_per_query < 1:
        from dbt_charts.core.render.errors import RenderError

        raise RenderError.from_code(
            ERR_INTERNAL,
            message=(
                f"max_rows_per_query must be a positive int, got {max_rows_per_query}"
            ),
        )
    result: dict[str, Any] = {
        "id": board.id,
        "title": board.title,
        "items": _render_board_items(
            board, executor, variables, error_collector, max_rows_per_query
        ),
    }
    if board.variables:
        result["variables"] = {
            name: var.model_dump(
                exclude_none=True,
                # set(): pydantic's IncEx does not admit frozenset, and the
                # constant stays immutable rather than a mutable global.
                exclude=set(INTERNAL_VARIABLE_FIELDS),
            )
            for name, var in board.variables.items()
        }
    return result
