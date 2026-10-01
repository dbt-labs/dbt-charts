"""Compact horizontal spark bar chart SVG rendering."""

from __future__ import annotations

import html as html_module
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.authored import ChartSort
from dbt_charts.core.compile.models.chart.resolved.spark_bar import (
    ResolvedSparkBarChart,
)
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedStyle,
)
from dbt_charts.core.compile.models.style.theme import SparkBarChartStyle
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND,
    ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
)
from dbt_charts.core.render.chart.spark import _signed_fraction
from dbt_charts.core.render.chart.text_truncation import record_text_truncation
from dbt_charts.core.render.svg_utils import authored_kind_attr, escape_attr
from dbt_charts.core.render.utils import normalize_data_types
from dbt_charts.core.text.case import apply_case


def _auto_detect_spark_bar_fields(
    data: list[dict[str, Any]],
    x_field: str | None,
    y_field: str | None,
) -> tuple[str | None, str | None]:
    """Auto-detect x (frequency) and y (category) fields for spark bar charts.

    Args:
        data: List of data rows
        x_field: Explicitly specified x field (frequency)
        y_field: Explicitly specified y field (category)

    Returns:
        Tuple of (x_field, y_field) with auto-detected values if not specified

    """
    if not data:
        return x_field, y_field

    # Auto-detect y field (category)
    if not y_field:
        for key in data[0]:
            if key.lower() in ("value", "label", "category", "name"):
                y_field = key
                break
        if not y_field:
            # Use first string column as y
            for key, val in data[0].items():
                if isinstance(val, str):
                    y_field = key
                    break

    # Auto-detect x field (frequency)
    if not x_field:
        for key in data[0]:
            if key.lower() in ("frequency", "count", "freq", "n", "total"):
                x_field = key
                break
        if not x_field:
            # Use first numeric column as x
            for key, val in data[0].items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    x_field = key
                    break

    return x_field, y_field


def _validate_spark_bar_value_field(
    chart_id: str,
    x_field: str | None,
    data: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> None:
    """Raise unless the resolved x (magnitude) field is a real numeric column.

    spark_bar reverses the cartesian x/y convention every other family uses —
    x is the magnitude, y is the label. Four routes lead to the same silent
    all-zero chart if left unguarded: (1) x names a column that is not in the
    query result at all (a typo), (2) x names a wholly non-numeric column
    (the cartesian-order authoring mistake), (3) x names a column that is
    numeric for some rows and something else for others (a data-shape bug,
    not sparse NULLs), (4) no x was authored and auto-detection found no
    numeric column at all — there is no legitimate spark_bar with no
    magnitude field. NULL cells are legitimate sparse data and are skipped,
    counted as neither numeric nor invalid.

    Case (1) gets its own code. A missing column makes every ``row.get()``
    return None, which is indistinguishable from an all-NULL column by value
    alone — so without the key check a typo would be told to swap x and y,
    confidently prescribing a reordering of already-correct YAML.

    Takes the FULL query result, not the max_bars-limited slice the chart
    displays — "is this column numeric?" is a property of the dataset, not
    of a display cap. The verdict must be identical at any max_bars value.
    """
    if not data:
        return
    if not x_field:
        raise ChartDataError.from_code(
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
            chart_id=chart_id,
            field="<none>",
            reason="no x is authored and no numeric column could be auto-detected",
        )
    if not any(x_field in row for row in data):
        raise ChartDataError.from_code(
            ERR_SPARK_BAR_VALUE_FIELD_NOT_FOUND,
            chart_id=chart_id,
            field=x_field,
            available=sorted({key for row in data for key in row}),
        )
    has_numeric_value = False
    for row in data:
        value = row.get(x_field)
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            has_numeric_value = True
            continue
        raise ChartDataError.from_code(
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
            chart_id=chart_id,
            field=x_field,
            reason=f"row value {value!r} is not numeric",
        )
    if not has_numeric_value:
        raise ChartDataError.from_code(
            ERR_SPARK_BAR_VALUE_NOT_NUMERIC,
            chart_id=chart_id,
            field=x_field,
            reason="the column holds no numeric values",
        )


def _render_spark_bar_row(
    row: dict[str, Any],
    row_index: int,
    row_y: float,
    x_field: str | None,
    y_field: str | None,
    bar_height: int,
    bar_area_width: float,
    max_value: float,
    signed_layout: bool,
    left_padding: int,
    chart_width: float,
    text_color: str,
    bar_color: str,
    bar_background: str,
    labels_visible: bool,
    counts_visible: bool,
    spark_config: SparkBarChartStyle,
    spark_rendering: Any,
    font: FontStyle,
    chart_id: str = "",
) -> list[str]:
    """Render a single bar row for spark bar chart.

    Args:
        row: Data row dict
        row_index: Index of this row (0-based)
        row_y: Y position for this row
        x_field: Field name for frequency/count values
        y_field: Field name for category labels
        bar_height: Height of each bar (may be overridden from default)
        bar_area_width: Width of the bar area
        max_value: Maximum magnitude for scaling bars
        signed_layout: Whether bars grow from a shared midpoint
        left_padding: Left padding before bar starts
        chart_width: Total chart width
        text_color: Color for text labels
        bar_color: Fill color for bars (may be overridden from default)
        bar_background: Background color for bar track
        labels_visible: Whether to show category labels
        counts_visible: Whether to show count labels
        spark_config: Config object for non-overridable values (border_radius, font sizes, label.width)
        font: FontStyle for text elements (reads .family)

    Returns:
        List of SVG element strings for this row

    """
    svg_parts: list[str] = []

    # Get values. A NULL value cell is legitimate sparse data (0 bar width);
    # the caller (_validate_spark_bar_value_field) has already walked every
    # visible row and rejected any non-null, non-numeric value, so a non-None
    # value here is guaranteed numeric.
    label_value = str(row.get(y_field, "")) if y_field else f"Item {row_index + 1}"
    raw_count = row.get(x_field) if x_field else None
    count_value = raw_count if raw_count is not None else 0

    signed = _signed_fraction(float(count_value), max_value, signed_layout)
    if signed_layout:
        half_width = bar_area_width / 2
        bar_width = signed.fraction * half_width
        fill_x = left_padding + (
            half_width - bar_width if signed.is_negative else half_width
        )
    else:
        bar_width = signed.fraction * bar_area_width
        fill_x = left_padding

    # Truncate label if too long (use config for label.width)
    max_label_chars = int(spark_config.label.width / spark_rendering.avg_char_width_px)
    display_label = label_value
    if len(display_label) > max_label_chars:
        display_label = display_label[: max_label_chars - 2] + "..."
        if chart_id:
            record_text_truncation(chart_id, "spark_label", label_value, "y")
    escaped_label = html_module.escape(display_label)

    # Format count
    if isinstance(count_value, float) and count_value.is_integer():
        display_count = str(int(count_value))
    elif isinstance(count_value, float):
        display_count = f"{count_value:,.1f}"
    else:
        display_count = f"{count_value:,}"

    # Render label
    if labels_visible:
        label_y = row_y + (bar_height / 2) + spark_rendering.text_baseline_offset
        svg_parts.append(
            f'<text x="0" y="{label_y:.1f}" '
            f'font-size="{escape_attr(spark_config.font.size)}" fill="{escape_attr(text_color)}" '
            f'font-family="{escape_attr(font.family)}">'
            f"{escaped_label}</text>",
        )

    # Render bar background
    bar_x = left_padding
    svg_parts.append(
        f'<rect x="{escape_attr(bar_x)}" y="{row_y:.1f}" '
        f'width="{escape_attr(bar_area_width)}" height="{escape_attr(bar_height)}" '
        f'fill="{escape_attr(bar_background)}" rx="{escape_attr(spark_config.border.radius)}"/>',
    )

    # Render bar fill
    if bar_width > 0:
        svg_parts.append(
            f'<rect x="{fill_x:.1f}" y="{row_y:.1f}" '
            f'width="{bar_width:.1f}" height="{escape_attr(bar_height)}" '
            f'fill="{escape_attr(bar_color)}" rx="{escape_attr(spark_config.border.radius)}"/>',
        )

    # Render count
    if counts_visible:
        count_x = chart_width - spark_rendering.side_padding
        count_y = row_y + (bar_height / 2) + spark_rendering.text_baseline_offset
        svg_parts.append(
            f'<text x="{count_x:.1f}" y="{count_y:.1f}" '
            f'font-size="{escape_attr(spark_config.font.size)}" fill="{escape_attr(text_color)}" text-anchor="end" '
            f'font-family="{escape_attr(font.family)}" '
            f'style="font-variant-numeric: tabular-nums lining-nums;">'
            f"{display_count}</text>",
        )

    return svg_parts


def render_spark_bar_svg(
    chart: ResolvedSparkBarChart,
    data: list[dict[str, Any]],
    width: float | None = None,
    height: float | None = None,
    is_placeholder: bool = False,
    *,
    board_style: ResolvedStyle,
    title_shift: TitleShift = NO_TITLE_SHIFT,
) -> str:
    """Render a ``ResolvedSparkBarChart`` as SVG.

    spark_bar is custom SVG, not a Vega-Lite emitter; this is the typed
    renderer routed at the session level (Wave 1), not through
    ``get_emitter``. Maps ``chart.style.spark_bar`` (the family style slice,
    same shape as v1's ``chart.resolved_style.spark_bar``) and the shared
    chart fields onto the family-agnostic core.

    Args:
        chart: Resolved spark_bar chart.
        data: List of dicts containing bar data.
        width: Optional explicit width in pixels.
        height: Optional explicit height in pixels.
        is_placeholder: If True, render with placeholder styling.
        board_style: Board-level ResolvedStyle for theme color reads.

    Returns:
        SVG string representing the spark bar chart.

    """
    return _render_spark_bar_svg_core(
        spark_style=chart.style.spark_bar,
        board_style=board_style,
        chart_id=chart.id,
        title=chart.title,
        subtitle=chart.subtitle,
        x=chart.x,
        y=chart.y,
        color=chart.color,
        sort=chart.sort,
        width=width,
        height=height,
        is_placeholder=is_placeholder,
        data=data,
        title_font=chart.style.title_font,
        title_shift=title_shift,
    )


def _render_spark_bar_svg_core(
    *,
    spark_style: SparkBarChartStyle | None,
    board_style: ResolvedStyle,
    chart_id: str,
    title: str | None,
    subtitle: str | None,
    x: str | None,
    y: str | None,
    color: str | None,
    sort: ChartSort | None,
    width: float | None,
    height: float | None,
    is_placeholder: bool,
    data: list[dict[str, Any]],
    title_font: ResolvedFontStyle | None,
    title_shift: TitleShift,
) -> str:
    """Render spark bar SVG from family-agnostic primitives.

    Core SVG body so callers stay byte-identical by
    construction. ``color``/``sort`` are accepted for parity with the
    resolved chart models — the spark bar mark is single-series and does
    not branch on either.

    Args:
        spark_style: Resolved spark_bar style slice (v1: ``chart.resolved_style.spark_bar``;
            ``chart.style.spark_bar``).
        board_style: Board-level ResolvedStyle for theme color reads.
        chart_id: Chart id (unused by current rendering; kept for parity/future error context).
        title: Chart title.
        subtitle: Chart subtitle.
        x: Frequency/count column.
        y: Category/label column.
        color: Color-encoding column (unused; spark_bar has no color channel).
        sort: Sort config (unused; spark_bar renders rows in data order).
        width: Optional explicit width in pixels.
        height: Optional explicit height in pixels.
        is_placeholder: If True, render with placeholder styling.
        data: List of dicts containing bar data.

    Returns:
        SVG string representing the spark bar chart.

    """
    del color, sort  # accepted for signature parity; not read by this mark
    assert spark_style is not None, "style.spark_bar must be resolved by the cascade"
    spark_config = spark_style
    data = normalize_data_types(data)

    spark_rendering = (
        get_chart_rendering().spark_bar
    )  # designer-tunable layout constants

    assert spark_config.font.color is not None, (
        "style.spark_bar.font.color must be set by theme"
    )
    text_color: str = spark_config.font.color
    secondary_color = board_style.muted
    subtitle_text = subtitle or ""

    # Extract overridable values (post-patch)
    bar_height = spark_config.bar.height
    max_bars = spark_config.max_bars
    labels_visible = spark_config.label.visible
    counts_visible = spark_config.count.visible
    assert spark_config.bar.color is not None, (
        "style.spark_bar.bar.color must be set by theme"
    )
    bar_color: str = spark_config.bar.color
    bar_background = spark_config.bar.background

    # Get field names from data, auto-detecting if not specified
    x_field, y_field = _auto_detect_spark_bar_fields(data, x, y)

    # "Is x a usable magnitude column?" is a question about the query result,
    # not about how many bars max_bars happens to paint — validate the full
    # dataset so the verdict never flips with a display-only style value.
    _validate_spark_bar_value_field(chart_id, x_field, data)

    # Limit data to max_bars
    visible_data = data[:max_bars] if data else []

    # Calculate dimensions
    num_bars = len(visible_data)
    row_height = bar_height + spark_config.bar.padding
    chart_width = spark_config.preferred_width if width is None else width
    chart_width = max(chart_width, spark_config.min_width)
    assert title_font is not None, (
        "render_spark_bar_svg_v2 requires chart.style.title_font; "
        "resolve() always sets it — construct via resolve(), not ResolvedSparkBarChart() directly"
    )
    chart_title_weight: int | str = int(title_font.weight)
    chart_title_size = int(title_font.size)
    chart_title_family = title_font.family
    title_height = spark_rendering.title_height if title else 0
    if title and subtitle_text:
        title_height += chart_title_size
    # The shifted title band is added to the card: bars keep their own room.
    title_height += title_shift.body_dy
    chart_height = (
        height + title_shift.body_dy
        if height
        else title_height + (num_bars * row_height) + spark_config.bar.padding
    )

    # Calculate bar area dimensions
    left_padding = (
        spark_config.label.width if labels_visible else spark_rendering.side_padding
    )
    right_padding = (
        spark_config.count.width if counts_visible else spark_rendering.side_padding
    )
    bar_area_width = max(chart_width - left_padding - right_padding, 20)

    finite_values = (
        [
            float(value)
            for row in visible_data
            if (value := row.get(x_field)) is not None and math.isfinite(float(value))
        ]
        if x_field
        else []
    )
    signed_layout = any(value < 0 for value in finite_values)
    max_value = max((abs(value) for value in finite_values), default=0.0)
    if max_value == 0:
        max_value = 1.0  # Prevent division by zero

    # Build SVG elements
    svg_parts: list[str] = []
    current_y = 0.0

    # Title
    if title:
        # Apply style.title.font.case so the spark title matches the chart and
        # table renderers' case transform (audit: object titles must agree).
        # Chart-local style patches have no title sub-key, so board_style.title
        # is identical to what v1's chart-merged resolved_style.title carries.
        _case = board_style.title.font.case
        _display_title = (
            apply_case(str(title), _case) if _case and _case != "none" else str(title)
        )
        escaped_title = html_module.escape(_display_title)
        svg_parts.append(
            f'<text x="0" y="{escape_attr(spark_rendering.title_baseline_y + title_shift.title_dy)}" '
            f'font-size="{escape_attr(chart_title_size)}" font-weight="{escape_attr(chart_title_weight)}" fill="{escape_attr(text_color)}" '
            f'font-family="{escape_attr(chart_title_family)}"{authored_kind_attr("title")}>'
            f"{escaped_title}</text>",
        )
        if subtitle_text:
            escaped_subtitle = html_module.escape(subtitle_text)
            assert spark_config.subtitle.font.size is not None, (
                "theme must supply spark_bar.subtitle.font.size"
            )
            subtitle_font_size = float(spark_config.subtitle.font.size)
            svg_parts.append(
                f'<text x="0" y="{escape_attr(spark_rendering.title_baseline_y + title_shift.title_dy + chart_title_size)}" '
                f'font-size="{escape_attr(subtitle_font_size)}" fill="{escape_attr(secondary_color)}" '
                f'font-family="{escape_attr(spark_config.font.family)}"{authored_kind_attr("subtitle")}>'
                f"{escaped_subtitle}</text>",
            )
        current_y = title_height

    # Render bars
    for i, row in enumerate(visible_data):
        row_y = current_y + (i * row_height)
        svg_parts.extend(
            _render_spark_bar_row(
                row=row,
                row_index=i,
                row_y=row_y,
                x_field=x_field,
                y_field=y_field,
                bar_height=int(bar_height),
                bar_area_width=bar_area_width,
                max_value=max_value,
                signed_layout=signed_layout,
                left_padding=int(left_padding),
                chart_width=chart_width,
                text_color=text_color,
                bar_color=bar_color,
                bar_background=bar_background,
                labels_visible=labels_visible,
                counts_visible=counts_visible,
                spark_config=spark_config,
                spark_rendering=spark_rendering,
                font=spark_config.font,
                chart_id=chart_id,
            ),
        )

    # Show "more" indicator if data was truncated
    if len(data) > len(visible_data):
        more_count = len(data) - len(visible_data)
        more_y = (
            current_y + (num_bars * row_height) + spark_rendering.more_rows_offset_y
        )
        svg_parts.append(
            f'<text x="{escape_attr(chart_width / 2)}" y="{more_y:.1f}" '
            f'font-size="{escape_attr(spark_rendering.more_rows_font_size)}" fill="{escape_attr(secondary_color)}" text-anchor="middle" font-style="italic" '
            f'font-family="{escape_attr(spark_config.font.family)}" '
            f'style="font-variant-numeric: tabular-nums lining-nums;">'
            f"+ {more_count} more</text>",
        )
        chart_height = more_y + spark_rendering.more_rows_bottom_padding

    # Wrap in SVG
    svg_result = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{escape_attr(chart_width)}" height="{escape_attr(chart_height)}" viewBox="0 0 {escape_attr(chart_width)} {escape_attr(chart_height)}">
{"".join(svg_parts)}
</svg>"""

    # Apply placeholder styling if needed
    if is_placeholder:
        from dbt_charts.core.render.placeholder import (
            add_placeholder_overlay,
            apply_placeholder_opacity,
        )

        svg_result = apply_placeholder_opacity(svg_result, resolved_style=board_style)
        svg_result = add_placeholder_overlay(
            svg_result,
            chart_width,
            chart_height,
            font=spark_config.font,
            resolved_style=board_style,
        )

    return svg_result
