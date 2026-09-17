"""Evaluate per-row chart label templates during final resolution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from pydantic_core import to_jsonable_python

from dbt_charts.core.compile.errors import TemplateOutputTooLargeError
from dbt_charts.core.compile.models.style.theme import SliceLabelsStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
    category_scale_for,
    color_at,
)
from dbt_charts.core.compile.template.labels_env import (
    label_jinja_env,
    strip_jinja_braces,
)
from dbt_charts.core.compile.template.output_budget import (
    TemplateOutputBudgetExceeded,
    render_with_budget,
)
from dbt_charts.core.utils import CellValue

LABEL_FIELD = "__dbt_label"
ChartRow = dict[str, CellValue]
ChartRows = list[ChartRow]


def pie_presentation_fingerprint(data: ChartRows) -> str:
    """Fingerprint the exact query rows whose pie policy was finalized."""
    canonical = json.dumps(
        to_jsonable_python(data, inf_nan_mode="null"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def project_pie_table_rows(
    data: ChartRows,
    shares: list[float],
    palette: Sequence[str],
    color_field: str | None,
    theta_field: str,
    row_indices: tuple[int, ...],
    category_colors: tuple[CategoryColorScale, ...],
) -> list[dict[str, CellValue]]:
    """Project frozen row indices into the attached table's mechanical shape.

    The table is the pie's legend, so each row's swatch must be the exact
    fill its own wedge paints -- ``color_at`` keyed by the row's own value,
    same as the wedge (see ``PieEmitter.emit``), never a row-position lookup.
    ``category_colors`` is this chart's own board-wide binding, already
    narrowed to the fields it draws (``chart.category_colors`` at render,
    ``_bound_scales(...)`` at compile) -- empty for an unbound pie (no
    board-wide binding for ``color_field``, or no color channel at all),
    which keeps today's positional fallback: a single-series pie already
    paints every wedge ``palette[0]`` uniformly regardless of row position.
    """

    def scalar(value: CellValue) -> str | int | float | bool:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (str, int, float, bool)):
            return value
        if value is None:
            return ""
        return str(value)

    category_scale = (
        category_scale_for(category_colors, color_field) if color_field else None
    )

    def swatch(index: int) -> str:
        if category_scale is not None and color_field:
            value = data[index].get(color_field)
            if value is not None:
                return color_at(category_scale, str(value), palette)
        return palette[index % len(palette)]

    return [
        {
            "swatch": swatch(index),
            "share": f"{round(shares[index] * 100)}%",
            "name": scalar(data[index].get(color_field)) if color_field else "",
            "value": scalar(data[index].get(theta_field)),
        }
        for index in row_indices
    ]


def _coerce_numeric(value: CellValue) -> CellValue:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return value


def prepare_label_data(
    rows: ChartRows,
    labels: Any,
    *,
    context_extras: Callable[[ChartRow, int], ChartRow],
) -> ChartRows:
    """Return copies of rows carrying their evaluated label text."""
    env = label_jinja_env()
    template = env.from_string(labels.template)
    where_expr = (
        env.compile_expression(strip_jinja_braces(labels.where))
        if labels.where
        else None
    )

    out: ChartRows = []
    total_rows = len(rows)
    for index, row in enumerate(rows):
        context = {
            **row,
            **context_extras(row, index),
            "index": index,
            "is_first": index == 0,
            "is_last": index == total_rows - 1,
        }
        new_row = dict(row)
        if where_expr is not None and not where_expr(**context):
            new_row[LABEL_FIELD] = None
        else:
            try:
                rendered = render_with_budget(template, context)
            except TemplateOutputBudgetExceeded as exc:
                raise TemplateOutputTooLargeError(
                    emitted_bytes=exc.emitted_bytes, ceiling=exc.ceiling
                ) from exc
            new_row[LABEL_FIELD] = (
                rendered.split("\n") if "\n" in rendered else [rendered]
            )
        out.append(new_row)
    return out


def prepare_pie_label_data(
    theta_field: str,
    color_field: str | None,
    data: ChartRows,
    labels: SliceLabelsStyle,
) -> ChartRows:
    """Evaluate pie labels identically during resolution and emission."""
    values = [float(row.get(theta_field, 0) or 0) for row in data]
    total = sum(values)
    shares = [value / total if total else 0.0 for value in values]
    return prepare_label_data(
        data,
        labels,
        context_extras=lambda row, index: {
            "percent": shares[index],
            "value": _coerce_numeric(row.get(theta_field)),
            "total": total,
            "color": row.get(color_field) if color_field else None,
        },
    )


def render_pie_label_lines(
    theta_field: str,
    color_field: str | None,
    data: ChartRows,
    labels: SliceLabelsStyle,
    visible_indices: tuple[int, ...],
) -> tuple[tuple[str, ...], ...]:
    """Mechanically evaluate finalized label policy for matching runtime rows."""
    rendered = prepare_pie_label_data(theta_field, color_field, data, labels)
    visible = set(visible_indices)
    return tuple(
        (
            tuple(str(line) for line in row[LABEL_FIELD])
            if index in visible and isinstance(row.get(LABEL_FIELD), list)
            else ()
        )
        for index, row in enumerate(rendered)
    )
