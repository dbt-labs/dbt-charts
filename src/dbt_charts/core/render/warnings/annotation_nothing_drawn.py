"""Detector: WARN_ANNOTATION_NOTHING_DRAWN: see the code's `doc` in
core/diagnostics/codes_render.py.

The engine records an annotation it placed nowhere
(`ctx.annotation_nothing_drawn`), and resolve records an annotation whose scope
had no rows to compute over (`annotation_gaps`); this detector reports each.
"""

from __future__ import annotations

from collections.abc import Iterator

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedLineChart,
    ResolvedScatterChart,
)
from dbt_charts.core.diagnostics import WARN_ANNOTATION_NOTHING_DRAWN, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def _drawn_nothing(ctx: WarningContext) -> Iterator[tuple[str, int, str]]:
    for chart_id, reasons in ctx.annotation_nothing_drawn.items():
        for index, reason in reasons.items():
            yield chart_id, index, reason
    for chart_id, chart in ctx.board_spec.charts.items():
        if isinstance(
            chart,
            ResolvedLineChart
            | ResolvedAreaChart
            | ResolvedBarChart
            | ResolvedScatterChart,
        ):
            for gap in chart.annotation_gaps:
                yield chart_id, gap.index, gap.reason


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """One Diagnostic per annotation that draws nothing."""
    return [
        Diagnostic.from_code(
            WARN_ANNOTATION_NOTHING_DRAWN,
            chart=chart_id,
            message=WARN_ANNOTATION_NOTHING_DRAWN.message_template.format(
                chart_id=chart_id, index=index, reason=reason
            ),
            fix=WARN_ANNOTATION_NOTHING_DRAWN.fix_template,
        )
        for chart_id, index, reason in _drawn_nothing(ctx)
    ]
