"""Chart rendering subpackage.

Holds the per-family Vega-Lite emitters (``emitters/``), the cross-family
feature pipeline (``features/``), Vega-Lite assembly (``translate.py``), and the
custom-SVG renderers for the non-VL families (KPI, table, spark bar, callout).
"""

from dbt_charts.core.render.chart.emitter import ChartEmitter, ResolvedChartT
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.chart.feature import ChartFeature, FeaturePipeline
from dbt_charts.core.render.chart.features import DEFAULT_FEATURES
from dbt_charts.core.render.chart.rendering import render_chart_item, render_layout_item
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import ChartSpec, EndpointLabelData
from dbt_charts.core.render.chart.translate import assemble_final_vl
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec, render_chart
from dbt_charts.core.render.utils import slug_to_text

__all__ = [
    "generate_vega_lite_spec",
    "render_chart",
    "render_chart_item",
    "render_layout_item",
    "slug_to_text",
    "ChartEmitter",
    "ChartFeature",
    "ChartSpec",
    "EndpointLabelData",
    "DEFAULT_FEATURES",
    "BoardRenderSession",
    "FeaturePipeline",
    "ResolvedChartT",
    "assemble_final_vl",
    "get_emitter",
]
