"""Per-chart ``ChartStyleContext`` and the chart's declared-format helpers.

Render code reads only the final per-chart Resolved*Style, never the chart-local
patch or this cascade context; the merge itself lives in ``chart_style``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import declared_format, resolve_format
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
    _BaseChartFields,
)
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatTable,
)
from dbt_charts.core.compile.models.style.authored import ChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_style import (
    merge_chart_style,
    primary_patch,
)
from dbt_charts.core.text.format_d3 import is_time_format
from dbt_charts.core.text.predefined_formats import PREDEFINED_TEXT_NAMES

# The families that carry axes.
CartesianFamilyChart = BarChart | LineChart | AreaChart | ScatterChart | HeatmapChart


def declared_column_format(
    chart: _BaseChartFields,
    columns: str | list[str] | None,
    formats: FormatTable | None,
    *,
    time: bool,
    text: bool = False,
) -> str | None:
    """The format name the chart's query declares for ``columns``, when it is of
    the asked kind (a time format for ``time``, else a number format).

    A text name (``email``, ``url``) is of no numeric or date kind: it comes
    back only for a destination that links text (``text=True``, the KPI), and
    is no format at all to an axis or label.

    A number format on a date axis would paint garbage, and a time format has
    nothing to say about a number, so the other kind is no format at all.
    """
    name = declared_format(chart.query.formats if chart.query else None, columns)
    if name is None:
        return None
    resolved = resolve_format(name, formats)
    if resolved in PREDEFINED_TEXT_NAMES:
        return name if text else None
    return None if is_time_format(resolved) != time else name


def axis_layer_format(
    chart: CartesianFamilyChart,
    channel_type: str,
    columns: str | list[str] | None,
    formats: FormatTable | None,
) -> AuthoredFormat | None:
    """The axis cascade's column layer: the format the chart's query declares for
    the axis column.

    The cascade merges board-level axis formats below this layer and the chart's
    own ``axis_*.labels.format`` above it, which is where the column belongs.
    A band axis prints its category text as it is, so it takes no format.
    """
    if channel_type not in ("quantitative", "temporal"):
        return None
    return declared_column_format(
        chart, columns, formats, time=channel_type == "temporal"
    )


def build_chart_style_context(
    board_context: ChartStyleContext | None,
    chart: Chart,
) -> ChartStyleContext:
    """The board context with the chart's local style merged on top.

    Returns the board context itself when the chart overrides nothing.
    """
    base = board_context or resolve_chart_style_context(get_theme_style())
    overrides = merge_chart_style(base, chart)
    return dataclasses.replace(base, **overrides) if overrides else base


def family_patch_for(style: ChartStylePatch | Any, chart_type: str) -> Any:
    """Return the family sub-patch for chart_type, or None.

    For V1 monolithic ChartStylePatch: look up the nested family slot.
    For per-family patches: the style IS the family patch — return it directly.
    """
    if style is None:
        return None
    if not isinstance(style, ChartStylePatch):
        # Chart per-family patch is already the family sub-patch.
        return style

    from dbt_charts.core.compile.normalize.charts import (  # noqa: PLC0415
        _AUTHORED_FAMILY_TO_MONOLITHIC_KEY,
    )

    key = _AUTHORED_FAMILY_TO_MONOLITHIC_KEY.get(chart_type)
    if key is not None:
        return getattr(style, key, None)
    return primary_patch(style)
