"""ChartFeature implementations and the DEFAULT_FEATURES list."""

from __future__ import annotations

from dbt_charts.core.render.chart.feature import ChartFeature
from dbt_charts.core.render.chart.features.click_interactivity import (
    ClickInteractivityFeature,
)
from dbt_charts.core.render.chart.features.native_tooltip import (
    NativeTooltipAffixFeature,
)

# List order IS application order (FeaturePipeline does not sort) — load-bearing.
DEFAULT_FEATURES: list[ChartFeature] = [
    ClickInteractivityFeature(),
    NativeTooltipAffixFeature(),
]

__all__ = [
    "DEFAULT_FEATURES",
    "ClickInteractivityFeature",
    "NativeTooltipAffixFeature",
]
