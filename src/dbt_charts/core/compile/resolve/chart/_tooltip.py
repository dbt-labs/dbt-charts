"""Resolve a chart's `tooltip:` entries into the rows its hover card adds."""

from __future__ import annotations

import decimal
from typing import Any

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import column_format, declared_column_format
from dbt_charts.core.compile.format_value import is_temporal_value
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    LineChart,
    PieChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.chart.resolved._base import ResolvedTooltipField
from dbt_charts.core.compile.models.primitives import FormatTable
from dbt_charts.core.text.case import field_title

_NUMBER = (int, float, decimal.Decimal)


def resolve_tooltip(
    normalized: AreaChart | BarChart | LineChart | PieChart | ScatterChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
    formats: FormatTable | None,
) -> tuple[ResolvedTooltipField, ...]:
    """Bake each entry's label and format. The column's values pick the kind:
    all numbers, all dates, or text (no format)."""
    query_formats = normalized.query.formats if normalized.query else None
    fields: list[ResolvedTooltipField] = []
    for entry in normalized.tooltip:
        present = [
            value for row in data if (value := row.get(entry.column)) is not None
        ]
        fmt = None
        if present and all(
            isinstance(v, _NUMBER) and not isinstance(v, bool) for v in present
        ):
            fmt = column_format(
                "number",
                formats,
                entry.format,
                query_formats=query_formats,
                columns=entry.column,
                values=[float(v) for v in present],
            )
        elif present and all(is_temporal_value(v) for v in present):
            fmt = column_format(
                "time",
                formats,
                entry.format,
                query_formats=query_formats,
                columns=entry.column,
            )
        elif present and (
            entry.format is not None
            or any(
                declared_column_format(query_formats, entry.column, formats, time=time)
                for time in (False, True)
            )
        ):
            raise CompilationError(
                f"Chart {normalized.id!r} tooltip column {entry.column!r} holds "
                "text or mixed values, so a format has nothing to format "
                "(authored, or declared in the query's formats:). Drop the "
                "format, or tooltip a numeric or date column."
            )
        label = (
            entry.label
            if entry.label is not None
            else field_title(entry.column, normalized.field_labels)
        )
        if ":" in label or ";" in label:
            raise CompilationError(
                f"Chart {normalized.id!r} tooltip column {entry.column!r} has the "
                f"label {label!r}; a tooltip label cannot contain ':' or ';'."
            )
        fields.append(
            ResolvedTooltipField(column=entry.column, label=label, format=fmt)
        )
    return tuple(fields)


def reject_tooltip_beside_date_y(
    normalized: ScatterChart,
    data: list[dict[str, Any]],  # type-state: explicit_any — query-row boundary values
) -> None:
    """A scatter whose y is a date draws no structured hover card to add rows to."""
    if not normalized.tooltip or not isinstance(normalized.y, str):
        return
    values = [row[normalized.y] for row in data if row.get(normalized.y) is not None]
    if values and all(is_temporal_value(v) for v in values):
        raise CompilationError(
            f"Chart {normalized.id!r}: tooltip is not supported beside a "
            f"date-valued y ({normalized.y!r}); a scatter draws no hover card "
            "for it. Remove tooltip, or plot the date on x."
        )
