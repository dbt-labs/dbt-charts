"""Detector: WARN_ENDPOINT_LABEL_GAP_OVERFLOW, WARN_ENDPOINT_LABEL_RAIL_TIED,
and WARN_ENDPOINT_LABEL_RAIL_OVERFLOW — see their `doc` entries in
`core/diagnostics/codes_render.py`.

Detection rule:
  ``WarningContext.endpoint_label_gap_overflows`` is non-empty for a chart —
  ``recascade_endpoint_labels`` (``render/chart/features/endpoint_labels.py``),
  called from the post-probe correction pass in
  ``render/converters/chart.py``, could not honor the intended pixel gap and
  either distributed labels evenly, or (``rail_overflow``) dropped the labels
  that would not fit, instead.

  The record's ``cause`` picks the code, because the three have different
  remedies: ``gap_did_not_fit`` is fixable with height or fewer series;
  ``no_slope`` is a property of the data and height cannot touch it;
  ``rail_overflow`` means some labels were dropped from the rail entirely.
  Emitting one code for all three told authors to grow a chart that was
  already tall enough, or hid a dropped label behind "labels are cramped."
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import (
    WARN_ENDPOINT_LABEL_GAP_OVERFLOW,
    WARN_ENDPOINT_LABEL_RAIL_OVERFLOW,
    WARN_ENDPOINT_LABEL_RAIL_TIED,
    Diagnostic,
)
from dbt_charts.core.render.warnings.base import WarningContext

_CODE_BY_CAUSE = {
    "gap_did_not_fit": WARN_ENDPOINT_LABEL_GAP_OVERFLOW,
    "no_slope": WARN_ENDPOINT_LABEL_RAIL_TIED,
    "rail_overflow": WARN_ENDPOINT_LABEL_RAIL_OVERFLOW,
}


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per chart whose endpoint-label rail was degraded."""
    warnings: list[Diagnostic] = []
    for chart_id, overflow in ctx.endpoint_label_gap_overflows.items():
        code = _CODE_BY_CAUSE[overflow.cause]
        warnings.append(
            Diagnostic.from_code(
                code,
                chart=chart_id,
                path=f"charts.{chart_id}.style.key.direct",
                message=code.message_template.format(
                    chart_id=chart_id,
                    series_count=overflow.series_count,
                    gap_px=overflow.gap_px,
                    dropped_count=len(overflow.dropped_series),
                ),
                fix=code.fix_template,
            )
        )
    return warnings
