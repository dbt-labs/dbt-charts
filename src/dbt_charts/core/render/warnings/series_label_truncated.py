"""Detector: WARN_SERIES_LABEL_TRUNCATED — see its ``doc`` in
``core/diagnostics/codes_render.py`` for what this fires on.

Detection rule:
  ``WarningContext.series_label_truncations`` is non-empty for a chart — the
  endpoint-label feature capped the rail (``measure_label_pane_width`` in
  ``render/chart/emitters/_endpoint_rail.py``) on a layout that honors that
  width, so Vega will ellipsize the recorded names.

One ``Diagnostic`` fires per (chart, authored field): a chart with thirty long
categories has one problem, not thirty, and every sibling detector folds a
count into a single message. The path points at the key the author actually
wrote — ``color`` for a series-color chart, ``y`` for a wide-form area, which
has no color key at all.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_SERIES_LABEL_TRUNCATED, Diagnostic
from dbt_charts.core.render.chart.series_label_truncation import SeriesLabelTruncation
from dbt_charts.core.render.warnings.base import WarningContext, listed_phrase


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per (chart, authored field) whose labels were cut."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.series_label_truncations.items():
        by_field: dict[str, list[SeriesLabelTruncation]] = {}
        for trunc in truncations:
            by_field.setdefault(trunc.authored_field, []).append(trunc)
        for authored_field, cut in by_field.items():
            warnings.append(
                Diagnostic.from_code(
                    WARN_SERIES_LABEL_TRUNCATED,
                    chart=chart_id,
                    field=None,
                    path=f"charts.{chart_id}.{authored_field}",
                    message=WARN_SERIES_LABEL_TRUNCATED.message_template.format(
                        chart_id=chart_id,
                        count=len(cut),
                        labels_noun="label" if len(cut) == 1 else "labels",
                        were="was" if len(cut) == 1 else "were",
                        authored_field=authored_field,
                        labels=listed_phrase([t.series_name for t in cut]),
                    ),
                    fix=WARN_SERIES_LABEL_TRUNCATED.fix_template.format(
                        authored_field=authored_field
                    ),
                )
            )
    return warnings
