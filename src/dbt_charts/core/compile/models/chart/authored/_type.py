"""Authored chart type enum and UI display metadata."""

from __future__ import annotations

from enum import Enum


class ChartType(str, Enum):
    """Authorable chart types -- every member maps to an AuthoredChart variant.

    The membership here must stay in sync with the AuthoredChart discriminated union
    in __init__.py.  Raw Vega-Lite mark names (arc, boxplot, circle, errorband,
    errorbar, image, rect, rule, square, tick, trail) are intentionally absent: they
    have no AuthoredChart variant, so including them would make the enum a lie.
    """

    BAR = "bar"
    LINE = "line"
    AREA = "area"
    GEOSHAPE = "geoshape"
    TABLE = "table"
    KPI = "kpi"
    CALLOUT = "callout"

    # Aliases (map to underlying marks)
    SCATTER = "scatter"  # VL mark: point
    HEATMAP = "heatmap"  # -> rect
    PIE = "pie"  # -> arc
    DONUT = "donut"  # -> pie with style.inner_radius=0.6 (normalized alias)
    HISTOGRAM = "histogram"  # -> bar + binning
    MAP = "map"  # -> geoshape (generic map)
    POINT_MAP = "point_map"  # -> circle marks with lat/lng
    BUBBLE_MAP = "bubble_map"  # -> circle marks with size encoding

    # Spark charts (compact inline charts)
    SPARK_BAR = "spark_bar"  # -> compact horizontal bars for profiler cards


# Display metadata for schema_hints / LSP; one entry per ChartType member.
CHART_TYPE_DISPLAY: dict[ChartType, dict[str, str]] = {
    ChartType.LINE: {"label": "Line", "icon": "📈"},
    ChartType.BAR: {"label": "Bar", "icon": "📊"},
    ChartType.AREA: {"label": "Area", "icon": "📉"},
    ChartType.GEOSHAPE: {"label": "Geoshape", "icon": "🗺️"},
    ChartType.TABLE: {"label": "Table", "icon": "📋"},
    ChartType.KPI: {"label": "KPI", "icon": "#️⃣"},
    ChartType.CALLOUT: {"label": "Callout", "icon": "🚨"},
    ChartType.SCATTER: {"label": "Scatter", "icon": "⬡"},
    ChartType.HEATMAP: {"label": "Heatmap", "icon": "🟧"},
    ChartType.PIE: {"label": "Pie", "icon": "🥧"},
    ChartType.DONUT: {"label": "Donut", "icon": "🍩"},
    ChartType.HISTOGRAM: {"label": "Histogram", "icon": "📊"},
    ChartType.MAP: {"label": "Map", "icon": "🗺️"},
    ChartType.POINT_MAP: {"label": "Point Map", "icon": "📍"},
    ChartType.BUBBLE_MAP: {"label": "Bubble Map", "icon": "🫧"},
    ChartType.SPARK_BAR: {"label": "Spark Bar", "icon": "📊"},
}

# Internal chart types that should not be shown in UI dropdowns by default.
_INTERNAL_CHART_TYPES: set[ChartType] = {ChartType.DONUT}
