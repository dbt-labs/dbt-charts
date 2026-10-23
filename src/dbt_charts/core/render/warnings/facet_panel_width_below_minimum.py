"""Detector: WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it when a small-multiples panel is narrower than
``FACET_MIN_PANEL_PX``. Panels are never floored back up to that minimum (the
card boundary always wins): a high column-facet cardinality against a narrow
card produces sub-floor panels rather than push painted content past the card's
edge. This is the author's only signal that happened.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per faceted chart whose panel width undercuts the floor."""
    return [
        Diagnostic.from_code(
            WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM,
            chart=chart_id,
            path=f"charts.{chart_id}.multiples",
            message=WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM.message_template.format(
                **params
            ),
            fix=WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM.fix_template,
        )
        for chart_id, params in engine_warnings(
            ctx, WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM
        )
    ]
