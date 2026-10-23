"""Detector: WARN_VALUE_LABELS_CROWD_WIDTH — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

The engine raises it when the widest value label is wider than the slot each
mark gets (the panel's plot width over its distinct categorical x values), so
labels overflow their mark and collide with their neighbors. Scope: vertical
bar, line and area. Horizontal bars run the label along the bar's length (a
different axis) and are out of scope.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.diagnostics import WARN_VALUE_LABELS_CROWD_WIDTH, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext, engine_warnings


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per chart whose widest value label overflows its slot."""
    warnings: list[Diagnostic] = []
    for chart_id, params in engine_warnings(ctx, WARN_VALUE_LABELS_CROWD_WIDTH):
        chart = ctx.board_spec.charts[chart_id]
        # `mark_key` is the authored `marks.<key>` the labels came from, so the
        # warning marks where they were switched on. Area labels ride the
        # overlaid *line* mark, which is why it is not always the family name.
        if isinstance(chart, ResolvedBarChart):
            mark_key = "bar"
            labels = chart.style.mark.labels
        else:
            assert isinstance(chart, ResolvedLineChart | ResolvedAreaChart)
            mark_key = "line"
            labels = chart.style.line_mark.labels
        field = labels.field if labels.field is not None else chart.y
        assert isinstance(field, str)
        warnings.append(
            Diagnostic.from_code(
                WARN_VALUE_LABELS_CROWD_WIDTH,
                chart=chart_id,
                # Chart-local style takes no family segment — `style.bar.…` is
                # the board-level spelling and does not compile on a chart.
                path=f"charts.{chart_id}.style.marks.{mark_key}.labels",
                field=field,
                message=WARN_VALUE_LABELS_CROWD_WIDTH.message_template.format(**params),
                fix=WARN_VALUE_LABELS_CROWD_WIDTH.fix_template,
            )
        )
    return warnings
