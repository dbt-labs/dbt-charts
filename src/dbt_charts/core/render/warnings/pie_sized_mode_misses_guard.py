"""Detector: WARN_PIE_SIZED_MODE_MISSES_GUARD — fires when a pie drawn in a box
other than the one it was sized in keeps a labeled mode whose wheel is below the
dominance floor of the label-free wheel.

Detection rule: ``WarningContext.text_truncations`` records with
``surface == "pie_wheel_starved"``, whose ``authored_text`` is
``"<mode>|<ratio>|<floor>"``. One ``Diagnostic`` fires per chart.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_PIE_SIZED_MODE_MISSES_GUARD, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose sized labeled mode starves its wheel."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.text_truncations.items():
        for record in truncations:
            if record.surface != "pie_wheel_starved":
                continue
            mode, ratio, floor = record.authored_text.split("|")
            warnings.append(
                Diagnostic.from_code(
                    WARN_PIE_SIZED_MODE_MISSES_GUARD,
                    chart=chart_id,
                    field=None,
                    path=f"charts.{chart_id}.height",
                    message=WARN_PIE_SIZED_MODE_MISSES_GUARD.message_template.format(
                        chart_id=chart_id,
                        mode=mode,
                        ratio=float(ratio),
                        floor=float(floor),
                    ),
                    fix=WARN_PIE_SIZED_MODE_MISSES_GUARD.fix_template,
                )
            )
            break
    return warnings
