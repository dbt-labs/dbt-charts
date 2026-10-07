"""resolve — normalized chart → ResolvedChart.

Stage: COMPILE (pure — no render imports)
Purpose: Bake style cascade and channel resolution into the discriminated
resolved model.  Row-sample enrichments are also done here (e.g. bar
orientation from data types); VL-specific type inference (infer_vega_type_from_data)
is foreign-format translation and happens later in the render layer.

Architecture:
- Callable: resolve(normalized, data, chart_style_context) → ResolvedChart
- Style cascade: chart_style_context (ChartStyleContext, built once per board) +
  chart's family patch → Resolved<Family>ChartStyle slice + resolved axes.
  Each family resolver builds its own per-chart ChartStyleContext via
  build_chart_style_context(chart_style_context, normalized), then applies the
  family patch with merge_onto_base and passes axis overrides via
  AxisOverrides.
- Channels baked via normalize_chart_channels (compile.channel)
- Bar orientation inferred here from data column types
- No render imports — compile boundary is enforced by test_no_render_imports_in_resolve_package

Layered per family, mirroring `render/chart/emitters/`: underscore-prefixed modules
hold substrate shared across families (axes, domain, kwargs, palette, marks, layers,
links, channels, chart-row types, the cartesian frame prelude/postlude in `_plan`,
and table resolution used by pie attachments); one module per remaining chart family
(`bar`, `line`, `area`, `scatter`, `heatmap`, `pie`, `geo`, `simple`); `_dispatch`
holds the public `resolve()` entry point. Imports are one-way: shared modules never
import a family module, and no family module imports another family module — only
`_dispatch` imports family modules.
"""

from ._dispatch import resolve
from ._domain import resolve_y_zero
from ._kwargs import (
    AutomaticLinkCandidate,
    default_chart_width,
    preferred_chart_width,
)
from ._links import (
    auto_link_excludes_x,
    should_fetch_table_fk_links,
    should_synthesize_auto_link,
)
from ._table import infer_pivot_measure_names

__all__ = [
    "AutomaticLinkCandidate",
    "auto_link_excludes_x",
    "default_chart_width",
    "infer_pivot_measure_names",
    "preferred_chart_width",
    "resolve",
    "resolve_y_zero",
    "should_fetch_table_fk_links",
    "should_synthesize_auto_link",
]
