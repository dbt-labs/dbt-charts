"""Header ink for a layered chart's support_table `source:` columns.

A hidden legend leaves each series unnamed; a `source:` column that reads a
mark's own `y` measure takes that mark's ink instead, so every series is still
named once. A scatter chart carries no `series_label` (no companion-ink
concept), so only the bar, line and area resolvers call this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarLayer,
    ResolvedLayer,
)
from dbt_charts.core.compile.resolve.style.palette import mark_ink


def _seen_color(layer: ResolvedLayer) -> str | None:
    """The color a reader sees on a layer's mark: an outline-only bar reads by its border."""
    if isinstance(layer, ResolvedBarLayer):
        border = layer.bar_mark.border
        if layer.bar_mark.opacity == 0 and border is not None:
            return border.color
    return layer.ink


def series_header_inks(
    *,
    base_y: str | list[str] | None,
    base_fill: str | None,
    layers: Sequence[ResolvedLayer],
    legend_visible: bool,
    canvas: str,
) -> Mapping[str, str]:
    """Each measure's header ink, by the mark that reads it.

    Empty when the legend is shown, the base is colored by a channel or reads
    several measures, or any layer has no single color to read. A measure two
    marks claim stays uncolored: either header would name the wrong series.
    """
    if legend_visible or not layers or not isinstance(base_y, str) or base_fill is None:
        return {}
    seen: dict[str, str | None] = {base_y: base_fill}
    for layer in layers:
        color = _seen_color(layer)
        if layer.y is None or color is None:
            return {}
        seen[layer.y] = None if layer.y in seen else color
    return {
        y: mark_ink(color, canvas) for y, color in seen.items() if color is not None
    }
