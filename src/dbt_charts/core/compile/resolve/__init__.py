"""resolve — normalized chart → ResolvedChart.

Re-exports the public surface of `resolve.chart` (see its docstring).
`resolve.style` (per-chart and board-level style cascade) is a sibling
package, imported directly (`dbt_charts.core.compile.resolve.style`) — not
re-exported here.
"""

from .chart import (
    AutomaticLinkCandidate,
    automatic_link_excludes_x,
    default_chart_width,
    infer_pivot_measure_names,
    layers_own_y_scale,
    preferred_chart_width,
    resolve,
    resolve_y_zero,
    should_fetch_table_fk_links,
    should_synthesize_automatic_link,
)

__all__ = [
    "AutomaticLinkCandidate",
    "automatic_link_excludes_x",
    "default_chart_width",
    "infer_pivot_measure_names",
    "layers_own_y_scale",
    "preferred_chart_width",
    "resolve",
    "resolve_y_zero",
    "should_fetch_table_fk_links",
    "should_synthesize_automatic_link",
]
