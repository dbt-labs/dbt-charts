"""Padding and title-offset constants shared by the sizing and title-band code."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.theme import PaddingStyle


def additive_padding(card_pad: float, chart_padding: PaddingStyle) -> dict[str, float]:
    """Add card_padding to per-family chart padding on each side independently.

    ``chart_padding`` is the per-family resolved padding — typically
    ``resolved_chart.layout_padding``, baked in at construction time. It
    already carries board → family fill via InheritSlot and any chart-local
    ``style.<family>.padding`` override merged in by ``build_chart_style_context``.
    Each side stacks ON TOP of card_padding so author-specified insets compose
    with the global card layout rather than replacing it. With no chart-local
    override and board default {0,0,0,0}, this collapses to a uniform
    card_pad on all four sides.
    """
    return {
        "left": card_pad + chart_padding.left,
        "right": card_pad + chart_padding.right,
        "top": card_pad + chart_padding.top,
        "bottom": card_pad + chart_padding.bottom,
    }


# Vega's own default for title.offset, which applies when neither the title block
# nor config.title sets one.
VEGA_TITLE_OFFSET = 4
