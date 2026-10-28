"""Text render output format for AI agents.

Templates the dict produced by board_to_dict into compact markdown text.
"""

from collections.abc import Mapping
from typing import Any

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_to_dict import (
    MAX_TABLE_COLUMNS_SHOWN,
    NO_ROW_CAP,
    TABLE_PREVIEW_ROW_CAP,
    board_to_dict,
    is_numeric_cell,
    kept_rows_phrase,
)
from dbt_charts.core.render.chart.table_static_pagination import StaticPagination
from dbt_charts.core.text.row_table import row_table

#: Longest board text block the summary quotes verbatim; a longer one is cut
#: with an explicit note, because the summary is agent context, not a document.
TEXT_EXCERPT_CHARS = 2000


def render_board_text(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
    include_data: bool = False,
    *,
    static_paginations: Mapping[str, StaticPagination],
) -> str:
    """Render a compiled board to compact markdown text for AI agents.

    ``include_data`` appends a ``## Data`` section: every query's rows,
    grouped by query name (not per chart, so a query shared by several
    charts is dumped once), capped by ``max_rows_per_query`` the same way
    every other row-embedding format is (head + tail, never silent).

    ``static_paginations`` is what the draw recorded per paginated table
    (keyed by chart id); each such table gets a line stating its row and page
    counts and how many pages a static export carries.
    """
    d = board_to_dict(
        board,
        executor,
        variables,
        error_collector,
        max_rows_per_query,
        include_text_format=True,
    )
    lines: list[str] = []
    _render_board(d, lines, 1, static_paginations)
    if include_data:
        data_lines = _render_include_data(d)
        if data_lines:
            lines.append("")
            lines.extend(data_lines)
    return "\n".join(lines)


#: (rows, rows_truncated) pair _collect_query_rows accumulates per query.
_Rows = tuple[list[dict[str, Any]], dict[str, int]]  # type-state: explicit_any — rows


def _collect_query_rows(
    board: dict[str, Any],  # type-state: explicit_any — wire dict
    out: dict[str, _Rows],
) -> None:
    """Walk the board tree, collecting each query name's rows exactly once.

    Mirrors ``data_format.py``'s ``_flatten`` traversal: a query shared by
    several charts (same ``query_name``) is recorded on first sight and
    skipped thereafter, so the appended data section never repeats a row set.
    Carries the item's own ``rows_truncated`` record alongside its rows —
    those rows are already head+tail-capped by ``max_rows_per_query``
    upstream in ``board_to_dict``.
    """
    for item in board.get("items", []):
        if item["type"] == "board":
            _collect_query_rows(item["board"], out)
            continue
        if "_error" in item:
            continue
        chart = item["chart"]
        query_name = chart.get("query_name")
        if not query_name or query_name in out:
            continue
        # A non-error chart item always carries "data" (board_to_dict always
        # sets it); [] only guards a shape the "_error" branch above already
        # filters out. rows_truncated is absent (not {}) on an untruncated
        # item — board_to_dict's documented "not truncated" sentinel.
        out[query_name] = (
            item.get("data", []),  # type-state: silent_fallback — see above
            item.get("rows_truncated", {}),  # type-state: silent_fallback — see above
        )


def _render_include_data(
    board: dict[str, Any],  # type-state: explicit_any — wire dict
) -> list[str]:
    """Build the ``## Data`` section: one compact markdown table per query,
    with an explicit truncation note and a gap marker between the kept head
    and tail whenever the query's rows were capped — never a silent cut.
    """
    queries: dict[str, _Rows] = {}
    _collect_query_rows(board, queries)
    if not queries:
        return []
    lines = ["## Data"]
    for name, (rows, truncated) in queries.items():
        lines.append("")
        lines.append(f"### {name}")
        if truncated:
            lines.append(f"showing {kept_rows_phrase(truncated)}")
            gap_after = truncated["head"] if truncated["tail"] else None
            lines.extend(_markdown_table(rows, gap_after=gap_after))
        else:
            lines.extend(_markdown_table(rows))
    return lines


def _render_board(
    board: dict[str, Any],  # type-state: explicit_any — wire dict
    lines: list[str],
    depth: int,
    static_paginations: Mapping[str, StaticPagination],
) -> bool:
    """Render a board dict to markdown lines at the given heading depth.

    Returns whether anything was appended. Only an authored title makes a
    heading: a titleless container is a row/col grouping with a synthetic id,
    so it renders its text and children in place under its parent's heading.
    """
    # A board wire dict always carries "items" (board_to_dict always sets it,
    # even to []); [] only guards a layout with no children, the genuine
    # empty case.
    items = board.get("items", [])  # type-state: silent_fallback — see above
    title = board.get("title")
    start_len = len(lines)
    if title:
        lines.append(f"{'#' * depth} {title}")
    # A titleless container takes no heading level, so its children sit at
    # its own depth rather than skipping one.
    child_depth = depth + 1 if title else depth
    if text := board.get("text"):
        if title:
            lines.append("")
        lines.extend(_text_excerpt(text))
    for item in items:
        if item["type"] == "chart":
            _render_chart(item, lines, child_depth, static_paginations)
        elif item["type"] == "board":
            child_lines: list[str] = []
            if _render_board(
                item["board"], child_lines, child_depth, static_paginations
            ):
                # A chart block opens with its own blank line.
                if child_lines[0]:
                    lines.append("")
                lines.extend(child_lines)
    return len(lines) > start_len


def _text_excerpt(text: str) -> list[str]:
    """The board's text, cut to ``TEXT_EXCERPT_CHARS`` with a note saying so."""
    body = text.strip()
    if len(body) <= TEXT_EXCERPT_CHARS:
        return body.split("\n")
    return [
        *body[:TEXT_EXCERPT_CHARS].split("\n"),
        f"[text cut: showing the first {TEXT_EXCERPT_CHARS} of {len(body)} characters]",
    ]


def _render_chart(
    item: dict[str, Any],  # type-state: explicit_any — wire dict
    lines: list[str],
    depth: int,
    static_paginations: Mapping[str, StaticPagination],
) -> None:
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
    title = chart.get("title") or chart.get("label") or chart["id"]
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
        pagination = static_paginations.get(chart["id"])
        if pagination is not None:
            lines.append(f"- {_pagination_line(pagination)}")
        if data:
            lines.extend(_table_row_preview(item, data))

    # Field mappings
    fields = _field_mappings(chart)
    if fields:
        lines.append(f"- {', '.join(fields)}")

    # Data summary (a table's row count is in its preview, above)
    if data and chart_type != "table":
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
    if len(parts) > MAX_TABLE_COLUMNS_SHOWN:
        shown = parts[:MAX_TABLE_COLUMNS_SHOWN]
        more = len(parts) - MAX_TABLE_COLUMNS_SHOWN
        return f"columns: {', '.join(shown)}, +{more} more"
    return f"columns: {', '.join(parts)}"


def _pagination_line(pagination: StaticPagination) -> str:
    """State a paginated table's row and page counts and what a static file keeps."""
    carried = (
        f"the first {pagination.rendered_pages} pages"
        if pagination.rendered_pages < pagination.total_pages
        else f"all {pagination.total_pages} pages"
    )
    return (
        f"{pagination.total_rows:,} rows, {pagination.page_rows} a page, "
        f"{pagination.total_pages} pages; the static file carries {carried}"
    )


def _markdown_table(
    rows: list[dict[str, Any]],  # type-state: explicit_any — wire rows
    *,
    gap_after: int | None = None,
) -> list[str]:
    """Render rows as a markdown table, columns taken from the first row.

    No rows means no table: the columns are unknown.
    """
    if not rows:
        return []
    return row_table(rows, list(rows[0].keys()), gap_after=gap_after).split("\n")


def _table_row_preview(
    item: dict[str, Any],  # type-state: explicit_any — wire dict
    data: list[dict[str, Any]],  # type-state: explicit_any — wire rows
) -> list[str]:
    """Build the table chart's unconditional row preview: the table's one row
    count, then the first few rows, each cell as the column's format paints it
    (``board_to_dict._table_text_rows``, which also drops hidden columns and
    caps wide tables), with an explicit note when more rows exist — never a
    silent truncation.
    """
    # ``data`` is already capped by ``max_rows_per_query``; the total is the
    # query's, so every count on a table agrees with the pagination line.
    total = item["rows_truncated"]["total"] if "rows_truncated" in item else len(data)
    lines = [
        f"- showing first {TABLE_PREVIEW_ROW_CAP} of {total} rows"
        if total > TABLE_PREVIEW_ROW_CAP
        else f"- {total} rows"
    ]
    lines.extend(_markdown_table(item["table_text"]))
    return lines


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


def _field_display(
    node: dict[str, Any],  # type-state: explicit_any — wire dict
    key: str,
) -> str | None:
    """A wire channel's value; a wide ``y`` names its authored measures, not
    the synthetic fold column the resolver baked into ``y``."""
    if key == "y" and node.get("wide_measures"):
        return ", ".join(node["wide_measures"])
    value = node.get(key)
    return None if value is None else str(value)


def _field_mappings(chart: dict[str, Any]) -> list[str]:
    """Extract field mapping strings like 'x: month, y: revenue'.

    Walks ``chart.get("layers")`` too — a cartesian chart's overlay layers
    (bar/line/area/scatter) each carry their own ``y`` (and occasionally
    ``x``/``color``), which the base chart's fields alone would drop for a
    multi-series/overlay chart like a "created vs. solved" combo.
    """
    mappings = []
    for key in ("x", "y", "color", "size", "theta", "value"):
        val = _field_display(chart, key)
        if val:
            mappings.append(f"{key}: {val}")
    # A non-layered chart carries no "layers" key at all; [] is the genuine
    # "no overlays" case, not a shape guard.
    for layer in chart.get("layers") or []:  # type-state: silent_fallback — see above
        for key in ("x", "y", "color"):
            val = _field_display(layer, key)
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
    error isolation) with the axis's own format, e.g. "$80–$120" instead of
    the raw "80–120". None when the axis has no format (a non-cartesian
    family, or a non-numeric y column); the range then falls back to plain
    numbers.
    """
    if rows_truncated:
        parts = [f"showing {kept_rows_phrase(rows_truncated)}"]
    else:
        parts = [f"{len(data)} rows"]

    # Collect chart field names by role — base chart first, then overlay
    # layers (setdefault: a layer sharing the base's column keeps that
    # column's original role rather than being reassigned).
    field_roles: dict[str, str] = {}
    for key in ("x", "y", "color", "size", "theta"):
        val = chart.get(key)
        if val:
            field_roles[val] = key
    # A non-layered chart carries no "layers" key at all; [] is the genuine
    # "no overlays" case, not a shape guard.
    for layer in chart.get("layers") or []:  # type-state: silent_fallback — see above
        for key in ("x", "y", "color"):
            val = _field_display(layer, key)
            if val:
                field_roles.setdefault(val, key)

    base_y = chart.get("y")
    for col, role in field_roles.items():
        values: list[Any] = [row[col] for row in data if row.get(col) is not None]
        if not values:
            continue
        if all(is_numeric_cell(v) for v in values) and role in (
            "y",
            "size",
            "theta",
        ):
            if role == "y" and col == base_y and y_range_display:
                parts.append(f"{col}: {y_range_display}")
            else:
                lo, hi = min(values), max(values)
                parts.append(f"{col}: {lo}–{hi}")
        elif all(is_numeric_cell(v) for v in values) and role in (
            "x",
            "color",
        ):
            lo, hi = min(values), max(values)
            parts.append(f"{col}: {lo}–{hi}")
        elif all(isinstance(v, str) for v in values) and role in ("x", "color"):
            distinct = sorted(set(values))
            if len(distinct) <= 5:
                parts.append(f"{col}: {', '.join(distinct)}")
            else:
                parts.append(f"{col}: {len(distinct)} distinct")

    return " | ".join(parts)
