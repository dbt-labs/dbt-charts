"""Final pie-label and attached-table policy used before model construction."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal, NamedTuple

from dbt_charts.core.colors import sanitize_color
from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.compile.models.primitives import (
    ResolvedFontStyle,
    ResolvedFormat,
)
from dbt_charts.core.compile.models.style.theme import (
    LegendAlign,
    LegendEdge,
    SliceLabelsStyle,
    TableChartStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.chart.label_data import (
    LABEL_FIELD,
    ChartRows,
    prepare_pie_label_data,
    project_pie_table_rows,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import settled
from dbt_charts.core.constants.pie import (
    ATTACHED_TABLE_GAP_PX,
    RIGHT_PLACEMENT_MAX_WHEEL_PX,
    RIGHT_PLACEMENT_MIN_WIDTH_FRACTION,
    RIGHT_PLACEMENT_MIN_WIDTH_PX,
    WEDGE_LABEL_MIN_SHARE,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_PIE_NEGATIVE_THETA,
    ERR_PIE_NULL_THETA,
)
from dbt_charts.core.font_measure import get_weighted_font_measurer
from dbt_charts.core.fonts import font_is_tabular
from dbt_charts.core.text.format_d3 import d3_format
from dbt_charts.core.utils import CellValue

AttachmentMode = Literal["hybrid", "full_table"]


def validate_theta_values(chart_id: str, theta_field: str, data: ChartRows) -> None:
    """Reject NULL or negative theta values before share math or label templates.

    A NULL theta silently coerces to a zero share in ``compute_shares``, then
    paints a null placeholder in the default label template's value line. A
    negative theta produces a share a pie slice's angle cannot represent. Both
    are query-grain problems the author must fix, not values the renderer can
    guess a substitute for.

    Scoped to rows where ``theta_field`` is present with a ``None`` value —
    a theta naming a column absent from the data entirely is a different,
    pre-existing shape (``compute_shares`` already treats it as "no numeric
    slices" and renders an empty pie) that this guard must not touch.
    """
    null_rows = sum(
        1 for row in data if theta_field in row and row[theta_field] is None
    )
    if null_rows:
        raise ChartDataError.from_code(
            ERR_PIE_NULL_THETA,
            chart_id=chart_id,
            theta_field=theta_field,
            null_rows=null_rows,
        )
    negative_rows = sum(
        1
        for row in data
        if isinstance(row.get(theta_field), (int, float, Decimal))
        and row[theta_field] < 0
    )
    if negative_rows:
        raise ChartDataError.from_code(
            ERR_PIE_NEGATIVE_THETA,
            chart_id=chart_id,
            theta_field=theta_field,
            negative_rows=negative_rows,
        )


def infer_implicit_color_field(theta_field: str, data: ChartRows) -> str | None:
    """The pie's implicit identity column, when the author omits `color:`.

    `color:` is a pie/donut's only channel besides `theta` -- so without it,
    a multi-row pie has no field bound to name its wedges: the default
    no-color label template renders share and value only, and the attached
    table's name column has nothing to key on. But the query's row shape
    often already carries the name (e.g. ``[segment, value]``) -- it's just
    not bound to any channel.

    When exactly one non-theta column is present, there is nothing to guess:
    it's the only field left that could possibly identify the row. Use it to
    name (not paint) the wedge -- fill stays `palette[0]` for every row, the
    same single-series contract as before. Two or more remaining columns are
    genuinely ambiguous (which one is "the" category?), so nothing is
    inferred rather than picking one at random.
    """
    if not data:
        return None
    other_fields = [key for key in data[0] if key != theta_field]
    return other_fields[0] if len(other_fields) == 1 else None


def compute_shares(theta_field: str, data: ChartRows) -> list[float]:
    if not data:
        return []
    try:
        values = [float(row.get(theta_field, 0) or 0) for row in data]
    except (TypeError, ValueError):
        return []
    total = sum(values)
    if not math.isfinite(total):
        raise ChartDataError(
            f"pie: theta {theta_field!r} sums to a non-finite total ({total})"
        )
    if total <= 0:
        return []
    return [value / total for value in values]


class TablePlacement(NamedTuple):
    edge: LegendEdge
    # The author's side edge, when the card was too narrow to hold it beside
    # the wheel and the table went below instead.
    overridden_edge: LegendEdge | None


def choose_table_placement(
    width: float, table_width: float, authored_edge: LegendEdge | None
) -> TablePlacement:
    """Where the attached table sits: the authored edge, else the width decides.

    A table beside the wheel (left or right) needs the wheel region it leaves
    to stay wide enough; a card too narrow for that puts it below, naming the
    side the author asked for so the swap is reported rather than silent.
    """
    wheel_region = width - table_width - ATTACHED_TABLE_GAP_PX
    beside_fits = (
        wheel_region >= RIGHT_PLACEMENT_MIN_WIDTH_FRACTION * width
        and wheel_region >= RIGHT_PLACEMENT_MIN_WIDTH_PX
    )
    if authored_edge in ("top", "bottom"):
        return TablePlacement(authored_edge, None)
    if authored_edge is None:
        return TablePlacement("right" if beside_fits else "bottom", None)
    if beside_fits:
        return TablePlacement(authored_edge, None)
    return TablePlacement("bottom", authored_edge)


def decide_table_align(
    placement: LegendEdge, authored_align: LegendAlign | None
) -> LegendAlign:
    """Authored align, else start beside the wheel and center above or below."""
    if authored_align is not None:
        return authored_align
    return "start" if placement in ("left", "right") else "center"


class AttachmentPlan(NamedTuple):
    """The companion table a mode implies, plus the widths it leaves behind."""

    row_indices: tuple[int, ...]
    rows: list[dict[str, CellValue]]
    columns: dict[str, TableColumnConfig]
    placement: LegendEdge
    align: LegendAlign
    overridden_edge: LegendEdge | None
    table_width: float
    wheel_width: float


def plan_attachment(
    mode: AttachmentMode,
    shares: list[float],
    data: ChartRows,
    palette: Sequence[str],
    color_field: str | None,
    theta_field: str,
    value_format: ResolvedFormat | None,
    table_style: TableChartStyle,
    width: float,
    category_colors: tuple[CategoryColorScale, ...],
    authored_edge: LegendEdge | None,
    authored_align: LegendAlign | None,
) -> AttachmentPlan:
    """Measure the companion table and split the card between it and the wheel.

    ``category_colors`` doesn't change the measured swatch column width (a
    fixed constant, see ``build_attached_table_columns``); it colors the
    swatches ``project_pie_table_rows`` bakes into ``rows``, the same
    wedge-fill resolution the engine paints the wedges with.
    """
    row_indices = tuple(
        range(len(data))
        if mode == "full_table"
        else (
            index
            for index, share in enumerate(shares)
            if share <= WEDGE_LABEL_MIN_SHARE
        )
    )
    rows = project_pie_table_rows(
        data, shares, palette, color_field, theta_field, row_indices, category_colors
    )
    columns, natural_width = build_attached_table_columns(
        rows, value_format, table_style
    )
    placement, overridden_edge = choose_table_placement(
        width, natural_width, authored_edge
    )
    align = decide_table_align(placement, authored_align)
    if placement in ("left", "right"):
        wheel_width = min(
            width - natural_width - ATTACHED_TABLE_GAP_PX,
            RIGHT_PLACEMENT_MAX_WHEEL_PX,
        )
        return AttachmentPlan(
            row_indices,
            rows,
            columns,
            placement,
            align,
            overridden_edge,
            natural_width,
            wheel_width,
        )
    return AttachmentPlan(
        row_indices,
        rows,
        columns,
        placement,
        align,
        overridden_edge,
        min(natural_width, width),
        width,
    )


def _format_value_for_width(
    value: str | int | float | bool, value_format: ResolvedFormat | None
) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    spec = value_format.raw if value_format is not None else None
    prefix = value_format.prefix if value_format is not None else ""
    suffix = value_format.suffix if value_format is not None else ""
    body = d3_format(spec if spec in (".0%", "$,.0f") else ",.0f")(numeric)
    return f"{prefix}{body}{suffix}"


def build_attached_table_columns(
    rows: list[dict[str, CellValue]],
    value_format: ResolvedFormat | None,
    table_style: TableChartStyle,
) -> tuple[dict[str, TableColumnConfig], float]:
    font_family = settled(table_style.font.family)
    font_size = settled(table_style.font.size)
    measurer = get_weighted_font_measurer(font_family, settled(table_style.font.weight))
    cell_pad = table_style.column_layout.cell_padding

    def column_width(strings: list[str], floor: int) -> int:
        widest = max(
            (measurer.measure(value, font_size) for value in strings), default=0
        )
        return max(floor, math.ceil(widest + cell_pad * 2 + 3))

    swatch_width = 24
    share_width = column_width([str(row["share"]) for row in rows], 36)
    name_width = column_width([str(row["name"] or "") for row in rows], 60)
    value_width = column_width(
        [_format_value_for_width(row["value"], value_format) for row in rows], 50
    )
    columns = {
        "swatch": TableColumnConfig(swatch=True, width=swatch_width),
        "share": TableColumnConfig(align="right", width=share_width),
        "name": TableColumnConfig(width=name_width),
        "value": TableColumnConfig(
            format=value_format,
            align="right",
            width=value_width,
        ),
    }
    return columns, float(swatch_width + share_width + name_width + value_width)


def resolve_hybrid_heading_font(table_style: TableChartStyle) -> ResolvedFontStyle:
    color_override = table_style.color.static if table_style.color is not None else None
    color = sanitize_color(color_override, table_style.font.color)
    return ResolvedFontStyle(
        **(
            table_style.font.model_dump(exclude={"extends"})
            | {
                "weight": table_style.header.font.weight,
                "color": color,
                "tabular_figures": font_is_tabular(table_style.font.family),
            }
        )
    )


def finalize_pie_label_lines(
    theta_field: str,
    color_field: str | None,
    data: ChartRows,
    labels: SliceLabelsStyle,
    shares: list[float],
) -> tuple[tuple[str, ...], ...]:
    if not shares:
        return tuple(() for _ in data)
    rendered = prepare_pie_label_data(theta_field, color_field, data, labels)
    finalized: list[tuple[str, ...]] = []
    for row, share in zip(rendered, shares, strict=True):
        rendered_label = row.get(LABEL_FIELD)
        if share > WEDGE_LABEL_MIN_SHARE and isinstance(rendered_label, list):
            finalized.append(tuple(str(line) for line in rendered_label))
        else:
            finalized.append(())
    return tuple(finalized)
