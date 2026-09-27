"""Text render output format for AI agents.

Templates the dict produced by board_to_dict into compact markdown text.
"""

from typing import Any

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_to_dict import (
    NO_ROW_CAP,
    board_to_dict,
    kept_rows_phrase,
)


def render_board_text(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> str:
    """Render a compiled board to compact markdown text for AI agents."""
    d = board_to_dict(
        board,
        executor,
        variables,
        error_collector,
        max_rows_per_query,
        include_text_format=True,
    )
    lines: list[str] = []
    _render_board(d, lines, depth=1)
    return "\n".join(lines)


def _render_board(board: dict[str, Any], lines: list[str], depth: int) -> None:
    """Render a board dict to markdown lines at the given heading depth."""
    title = board.get("title") or board.get("id", "Untitled")
    lines.append(f"{'#' * depth} {title}")
    for item in board.get("items", []):
        if item["type"] == "chart":
            _render_chart(item, lines, depth + 1)
        elif item["type"] == "board":
            lines.append("")
            _render_board(item["board"], lines, depth + 1)


def _render_chart(item: dict[str, Any], lines: list[str], depth: int) -> None:
    """Render a chart item to markdown lines."""
    # Chart error: emit a compact error line instead of data
    if "_error" in item:
        err = item["_error"]
        code = err.get("code", "")
        msg = err.get("message", "error")
        chart_id = err.get("fields", {}).get("chart_id", "unknown")
        lines.append(f"\n[chart error: {code} {msg} (chart: {chart_id})]")
        return

    chart = item["chart"]
    data = item.get("data", [])

    chart_type = chart.get("chart_type", "unknown")
    title = chart.get("title") or chart.get("id", "chart")
    lines.append("")
    lines.append(f"{'#' * depth} {title} ({chart_type})")

    # KPI: show the value as it will be drawn (formatted), plus its support
    # line when authored. No field mappings / data summary below — a KPI has
    # neither.
    if chart_type == "kpi":
        _render_kpi_value(item, lines)
        return

    # Table: the resolved per-column display config (label, format) — already
    # merged from style.columns and table-level defaults, so listing it here
    # needs no re-implementation of the cascade.
    if chart_type == "table":
        columns_line = _table_columns_summary(chart)
        if columns_line:
            lines.append(f"- {columns_line}")

    # Field mappings
    fields = _field_mappings(chart)
    if fields:
        lines.append(f"- {', '.join(fields)}")

    # Data summary
    if data:
        summary = _data_summary(
            chart,
            data,
            item.get(
                "rows_truncated", {}
            ),  # type-state: silent_fallback — the wire dict omits this key when untruncated; {} is the documented "not truncated" sentinel
            item.get("y_range_display"),
        )
        if summary:
            lines.append(f"- {summary}")


def _render_kpi_value(
    item: dict[str, Any],  # type-state: explicit_any — wire dict
    lines: list[str],
) -> None:
    """Append the KPI's formatted value and, when authored, its support line.

    Both are pre-formatted by ``board_to_dict._kpi_text_parts`` inside that
    chart's own error isolation — this function only prints them.
    ``item["kpi_text"]`` is indexed directly, not defaulted: board_to_dict
    guarantees it for every non-error KPI item (raising ChartDataError for
    the one case that would otherwise omit it — a KPI with no data), so a
    missing key here is this function's own bug, not a shape to paper over.
    """
    kpi_text = item["kpi_text"]
    if kpi_text.get("value"):
        lines.append(f"- value: {kpi_text['value']}")
    if kpi_text.get("support"):
        lines.append(f"- support: {kpi_text['support']}")


#: Cap on columns listed by name before collapsing to a "+N more" tail — this
#: module's whole audience is agent context, and a wide table (60+ columns)
#: would otherwise emit a single line over a thousand bytes long.
_MAX_TABLE_COLUMNS_SHOWN = 20


def _table_columns_summary(
    chart: dict[str, Any],  # type-state: explicit_any — wire dict
) -> str:
    """Build the resolved columns line: each column's label and format name.

    ``chart["columns"]`` is ``ResolvedTableChart.columns`` — the per-column
    display config already merged from ``style.columns`` and table-level
    defaults, in display order, one entry per query column. A hidden column
    (``visible: false``) is skipped, matching what actually renders.
    """
    columns = chart.get("columns")
    if not columns:
        return ""
    parts = []
    for name, config in columns.items():
        if config.get("visible") is False:
            continue
        label = config.get("label") or name
        format_name = _format_spec_name(config.get("format"))
        parts.append(f"{label} ({format_name})" if format_name else label)
    if not parts:
        return ""
    if len(parts) > _MAX_TABLE_COLUMNS_SHOWN:
        shown = parts[:_MAX_TABLE_COLUMNS_SHOWN]
        more = len(parts) - _MAX_TABLE_COLUMNS_SHOWN
        return f"columns: {', '.join(shown)}, +{more} more"
    return f"columns: {', '.join(parts)}"


def _format_spec_name(
    format_input: Any,  # type-state: explicit_any — dumped format field
) -> str | None:
    """Extract the authored format name/spec from a dumped format field."""
    if isinstance(format_input, str):
        return format_input
    if isinstance(format_input, dict):
        spec = format_input.get("spec")
        return spec if isinstance(spec, str) else None
    return None


def render_warnings_section(warnings: list[Diagnostic]) -> str:
    """Render render-time warnings as a compact markdown section.

    One line per warning: code, chart (or "board" when board-level), message.
    """
    if not warnings:
        return ""
    lines = ["", "## Warnings"]
    for warning in warnings:
        label = warning.chart or "board"  # type-state: silent_fallback — board default
        lines.append(f"- {warning.code} ({label}): {warning.message}")
    return "\n".join(lines)


def _field_mappings(chart: dict[str, Any]) -> list[str]:
    """Extract field mapping strings like 'x: month, y: revenue'."""
    mappings = []
    for key in ("x", "y", "color", "size", "theta", "value"):
        val = chart.get(key)
        if val:
            mappings.append(f"{key}: {val}")
    return mappings


def _data_summary(
    chart: dict[str, Any],
    data: list[dict[str, Any]],
    rows_truncated: dict[str, int],
    y_range_display: str | None = None,
) -> str:
    """Build a compact data summary string.

    ``y_range_display`` is the y-role range, pre-formatted by
    ``board_to_dict._cartesian_y_range_display`` (inside that chart's own
    error isolation) the same way the chart's value axis draws it, e.g.
    "$80–$120" instead of the raw "80–120". Unset (the common case — no
    chart-authored format) leaves the range as plain numbers.
    """
    if rows_truncated:
        parts = [f"showing {kept_rows_phrase(rows_truncated)}"]
    else:
        parts = [f"{len(data)} rows"]

    # Collect chart field names by role
    field_roles: dict[str, str] = {}
    for key in ("x", "y", "color", "size", "theta"):
        val = chart.get(key)
        if val:
            field_roles[val] = key

    for col, role in field_roles.items():
        values: list[Any] = [row[col] for row in data if row.get(col) is not None]
        if not values:
            continue
        if all(isinstance(v, (int, float)) for v in values) and role in (
            "y",
            "size",
            "theta",
        ):
            if role == "y" and y_range_display:
                parts.append(f"{col}: {y_range_display}")
            else:
                lo, hi = min(values), max(values)
                parts.append(f"{col}: {lo}–{hi}")
        elif all(isinstance(v, str) for v in values) and role in ("x", "color"):
            distinct = sorted(set(values))
            if len(distinct) <= 5:
                parts.append(f"{col}: {', '.join(distinct)}")
            else:
                parts.append(f"{col}: {len(distinct)} distinct")

    return " | ".join(parts)
