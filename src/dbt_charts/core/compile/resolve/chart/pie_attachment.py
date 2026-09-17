"""Final pie-label and attached-table policy used before model construction."""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal, NamedTuple

from dbt_charts.core.colors import sanitize_color
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.authored import TableColumnConfig
from dbt_charts.core.compile.models.chart.resolved import FormatState
from dbt_charts.core.compile.models.primitives import (
    FormatConfig,
    ResolvedFontStyle,
)
from dbt_charts.core.compile.models.style.theme import SliceLabelsStyle, TableChartStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.chart.label_data import (
    LABEL_FIELD,
    ChartRows,
    prepare_pie_label_data,
    project_pie_table_rows,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_PIE_NEGATIVE_THETA,
    ERR_PIE_NULL_THETA,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.fonts import font_is_tabular
from dbt_charts.core.utils import CellValue

ArcRenderMode = Literal["direct", "hybrid", "full_table"]


def validate_theta_values(chart_id: str, theta_field: str, data: ChartRows) -> None:
    """Reject NULL or negative theta values before share math or label templates.

    A NULL theta silently coerces to a zero share in ``compute_shares``, then
    crashes formatting ``None`` with the default label template's
    ``'{:,.0f}'.format(value)``. A negative theta produces a share a pie
    slice's angle cannot represent. Both are query-grain problems the author
    must fix, not values the renderer can guess a substitute for.

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
    if total <= 0:
        return []
    return [value / total for value in values]


def arc_disk_width(
    label_lines: list[str],
    font_family: str,
    font_size: float,
    label_offset: float,
    width: float,
) -> float:
    """Width left for the arc disk after slice labels reserve their reach.

    Under autosize fit the leader-line label layer shrinks the view. This is
    the one model of that reach — shared by render-mode classification here
    and the center-total overflow detector, so the two can't disagree about
    how much width the labels consume.
    """
    pie_cfg = get_chart_rendering().pie
    measurer = get_font_measurer(font_family)
    reach = pie_cfg.label_reach_coefficient * max(
        (measurer.measure(line, font_size) for line in label_lines),
        default=0.0,
    )
    return max(0.0, width - 2.0 * (label_offset + reach))


def classify_arc_render_mode(
    shares: list[float],
    label_lines: list[str],
    font_family: str,
    font_size: float,
    label_offset: float,
    width: float,
) -> ArcRenderMode:
    if not shares:
        return "direct"
    pie_cfg = get_chart_rendering().pie
    visible = sum(share > pie_cfg.wedge_label_min_share for share in shares)
    if visible == 0:
        return "full_table"
    labeled = arc_disk_width(label_lines, font_family, font_size, label_offset, width)
    ratio = min(1.0, labeled / (width * pie_cfg.outer_fraction))
    if ratio < pie_cfg.wheel_dominance_min_ratio:
        return "full_table"
    return "hybrid" if min(shares) < pie_cfg.invisible_slice_share else "direct"


def choose_table_placement(
    width: float, table_width: float
) -> Literal["below", "right"]:
    pie_cfg = get_chart_rendering().pie
    wheel_region = width - table_width - pie_cfg.attached_table_gap_px
    if (
        wheel_region >= pie_cfg.right_placement_min_width_fraction * width
        and wheel_region >= pie_cfg.right_placement_min_width_px
    ):
        return "right"
    return "below"


class AttachmentPlan(NamedTuple):
    """The companion table a mode implies, plus the widths it leaves behind."""

    row_indices: tuple[int, ...]
    rows: list[dict[str, CellValue]]
    columns: dict[str, TableColumnConfig]
    placement: Literal["below", "right"]
    table_width: float
    wheel_width: float


def plan_attachment(
    mode: ArcRenderMode,
    shares: list[float],
    data: ChartRows,
    palette: Sequence[str],
    color_field: str | None,
    theta_field: str,
    value_format: FormatState,
    table_style: TableChartStyle,
    width: float,
    category_colors: tuple[CategoryColorScale, ...],
) -> AttachmentPlan:
    """Measure the companion table and split the card between it and the wheel.

    ``category_colors`` doesn't change the measured swatch column width (it's
    a fixed constant, see ``build_attached_table_columns``) -- it's threaded
    through so the swatch this compile-time plan bakes into ``rows`` stays
    consistent with the one the render path (``prepare_pie_render_rows``)
    projects from the same wedge-fill resolution, rather than the two paths
    quietly diverging.
    """
    pie_cfg = get_chart_rendering().pie
    row_indices = tuple(
        range(len(data))
        if mode == "full_table"
        else (
            index
            for index, share in enumerate(shares)
            if share <= pie_cfg.wedge_label_min_share
        )
    )
    rows = project_pie_table_rows(
        data, shares, palette, color_field, theta_field, row_indices, category_colors
    )
    columns, natural_width = build_attached_table_columns(
        rows, value_format, table_style
    )
    placement = choose_table_placement(width, natural_width)
    if placement == "right":
        wheel_width = min(
            width - natural_width - pie_cfg.attached_table_gap_px,
            pie_cfg.right_placement_max_wheel_px,
        )
        return AttachmentPlan(
            row_indices, rows, columns, placement, natural_width, wheel_width
        )
    return AttachmentPlan(
        row_indices, rows, columns, placement, min(natural_width, width), width
    )


def _format_value_for_width(
    value: str | int | float | bool, value_format: FormatState
) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    prefix = ""
    suffix = ""
    if isinstance(value_format, FormatConfig):
        spec = value_format.spec
        prefix = value_format.prefix or ""
        suffix = value_format.suffix or ""
    else:
        spec = value_format if isinstance(value_format, str) else None
    if spec == "$,.0f":
        body = f"${numeric:,.0f}"
    elif spec == ".0%":
        body = f"{int(round(numeric * 100))}%"
    else:
        body = f"{numeric:,.0f}"
    return f"{prefix}{body}{suffix}"


def build_attached_table_columns(
    rows: list[dict[str, CellValue]],
    value_format: FormatState,
    table_style: TableChartStyle,
) -> tuple[dict[str, TableColumnConfig], float]:
    font_family = table_style.font.family
    font_size = table_style.font.size
    if font_family is None or font_size is None:
        raise ValueError("attached table font is incomplete after cascade")
    measurer = get_font_measurer(font_family)
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
            table_style.font.model_dump()
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
    threshold = get_chart_rendering().pie.wedge_label_min_share
    finalized: list[tuple[str, ...]] = []
    for row, share in zip(rendered, shares, strict=True):
        rendered_label = row.get(LABEL_FIELD)
        if share > threshold and isinstance(rendered_label, list):
            finalized.append(tuple(str(line) for line in rendered_label))
        else:
            finalized.append(())
    return tuple(finalized)
