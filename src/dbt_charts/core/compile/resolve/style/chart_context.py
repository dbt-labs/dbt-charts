"""Per-chart ``ChartStyleContext`` and the chart's declared-format helpers.

Render code reads only the final per-chart Resolved*Style, never the chart-local
patch or this cascade context; the merge itself lives in ``chart_style``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import declared_column_format
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.primitives import (
    AuthoredFormat,
    FormatTable,
)
from dbt_charts.core.compile.models.style.authored import ChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.compile.resolve.style.chart_style import (
    merge_chart_style,
    primary_patch,
)

# The families that carry axes.
CartesianFamilyChart = BarChart | LineChart | AreaChart | ScatterChart | HeatmapChart


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
        chart.query.formats if chart.query else None,
        columns,
        formats,
        time=channel_type == "temporal",
    )


def build_chart_style_context(
    board_context: ChartStyleContext | None,
    chart: Chart,
) -> ChartStyleContext:
    """The board context with the chart's local style merged on top.

    Returns the board context itself when the chart overrides nothing. A context
    that carries a memo (``with_chart_memo``) remembers its results.
    """
    base = board_context or resolve_chart_style_context(get_theme_style())
    if board_context is None or _MEMO_KEY not in board_context.__dict__:
        return _merged(base, chart)
    # The chart is held and compared by identity because render paths
    # `model_copy` charts (row truncation) and an id or equal copy can carry a
    # different style.
    memo: ChartMemo = board_context.__dict__[_MEMO_KEY]
    cached = memo.get(chart.id)
    if cached is not None and cached[0] is chart:
        return cached[1]
    merged = _merged(base, chart)
    memo[chart.id] = (chart, merged)
    return merged


# The memo is an instance attribute, not a dataclass field: it must stay out of
# the Pydantic schema, the dump and equality, and `dataclasses.replace` starts a
# new context without it. It holds caller charts, so it is only ever installed on
# a context exactly one board owns, never on one shared through the theme or
# cascade caches (process-wide state).
_MEMO_KEY = "_chart_memo"
ChartMemo = dict[str, tuple[Chart, ChartStyleContext]]


def with_chart_memo(context: ChartStyleContext) -> ChartStyleContext:
    """A fresh copy of ``context`` with an empty memo, for one board to own."""
    owned = dataclasses.replace(context)
    owned.__dict__[_MEMO_KEY] = {}
    return owned


def context_with_category_colors(
    context: ChartStyleContext, scales: tuple[CategoryColorScale, ...]
) -> ChartStyleContext:
    """``context`` with its planned category scales, and its per-chart memo.

    The per-chart merge never reads ``category_colors``, so each remembered
    result is the same context with the scales set. The memo is rebuilt, never
    shared with ``context``.
    """
    updated = dataclasses.replace(context, category_colors=scales)
    if _MEMO_KEY not in context.__dict__:
        return updated
    memo: ChartMemo = context.__dict__[_MEMO_KEY]
    updated.__dict__[_MEMO_KEY] = {
        chart_id: (
            chart,
            updated
            if merged is context
            else dataclasses.replace(merged, category_colors=scales),
        )
        for chart_id, (chart, merged) in memo.items()
    }
    return updated


def _merged(base: ChartStyleContext, chart: Chart) -> ChartStyleContext:
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
