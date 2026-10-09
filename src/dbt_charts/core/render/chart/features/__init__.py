"""ChartFeature implementations and the DEFAULT_FEATURES list."""

from __future__ import annotations

from dbt_charts.core.render.chart.feature import ChartFeature
from dbt_charts.core.render.chart.features.bar_hover_band import BarHoverBandFeature
from dbt_charts.core.render.chart.features.baseline import BaselineFeature
from dbt_charts.core.render.chart.features.click_interactivity import (
    ClickInteractivityFeature,
)
from dbt_charts.core.render.chart.features.endpoint_labels import EndpointLabelFeature
from dbt_charts.core.render.chart.features.facet import FacetFeature
from dbt_charts.core.render.chart.features.mirror_axis import MirrorAxisFeature
from dbt_charts.core.render.chart.features.native_tooltip import (
    NativeTooltipAffixFeature,
)
from dbt_charts.core.render.chart.features.structured_tooltip import (
    StructuredTooltipFeature,
)
from dbt_charts.core.render.chart.features.value_labels import ValueLabelFeature
from dbt_charts.core.render.chart.features.zero_value_label import ZeroValueLabelFeature

# List order IS application order (FeaturePipeline does not sort) — load-bearing.
DEFAULT_FEATURES: list[ChartFeature] = [
    # Board-wide category-color binding is no longer a post-pass here: each
    # emitter indexes its own color/companion palettes by the value's board
    # slot as it builds the encoding (see compile/models/style/theme/
    # category_colors.py's category_scale_for).
    # Appends a "0" text label for a genuine-zero bar row, but only
    # where ValueLabelFeature wouldn't otherwise label that row (both gate on
    # labels.visible is not True — ownership is exclusive by construction,
    # not by running order), so position in this list isn't load-bearing for
    # that coordination.
    ZeroValueLabelFeature(),
    # Runs before BaselineFeature so that BaselineFeature's zero-rule layer
    # (which must be the last layer for correct paint order — rendered above all
    # fills) still lands last when the hover band is also appended.  The band
    # appends first; the rule appends on top.
    BarHoverBandFeature(),
    BaselineFeature(),
    EndpointLabelFeature(),
    ValueLabelFeature(),
    ClickInteractivityFeature(),
    StructuredTooltipFeature(),
    # After StructuredTooltipFeature: writes only the cards it leaves unwritten.
    NativeTooltipAffixFeature(),
    # LATE: runs after EndpointLabelFeature so it can detect the endpoint
    # composition and refuse the incompatible mirror+endpoint combination.
    MirrorAxisFeature(),
    # LAST: records facet intent; must run after EndpointLabelFeature (to refuse
    # the facet+endpoint combo) and after MirrorAxisFeature (whose ghost overlay
    # becomes part of the unit spec the facet wraps).
    FacetFeature(),
]

__all__ = [
    "DEFAULT_FEATURES",
    "BarHoverBandFeature",
    "BaselineFeature",
    "EndpointLabelFeature",
    "FacetFeature",
    "ValueLabelFeature",
    "ZeroValueLabelFeature",
    "ClickInteractivityFeature",
    "MirrorAxisFeature",
    "NativeTooltipAffixFeature",
    "StructuredTooltipFeature",
]
