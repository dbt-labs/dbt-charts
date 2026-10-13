"""Detector: WARN_TABLE_CRAMPED — see its `doc` in core/diagnostics/codes_render.py.

Tables are a custom SVG renderer with no spec to inspect, so the renderer
records what it actually did into ``WarningContext.table_crampings`` (see
``render/chart/table_overflow.py``). This detector is policy-only: it reads
those captured counts, so the warning describes the render rather than a
second guess at it.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_TABLE_CRAMPED, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one warning per table whose columns wrapped under a width shortfall."""
    warnings: list[Diagnostic] = []
    for chart_id, cramping in ctx.table_crampings.items():
        # A table that paints past its slot is WARN-TABLE-COLUMNS-OVERFLOW's
        # case — the physical rung above this one. One defect, one warning.
        # A non-positive budget (a slot narrower than its own row-number
        # column) is the same territory: no width arithmetic below is
        # meaningful, and today's captures of it always overflow too — but
        # this detector must not depend on another sink to cover the range.
        if chart_id in ctx.table_overflows or cramping.available_width <= 0:
            continue
        shortfall = cramping.required_width - cramping.available_width
        # Only a budget the columns could not fit into, read out as wrapped
        # headers, is cramping the author can fix with width. Wrapped headers
        # without a shortfall are not: a long label over short values wraps at
        # any board width (spare budget only grows a column toward its header,
        # it does not guarantee reaching it), so warning on that would hand
        # out a width that does not clear the warning. Below one pixel the
        # message would read out two equal rounded widths — measurement noise,
        # not a defect an author can see or act on. A slot cutting the
        # rows-per-page down is the height axis — WARN-TABLE-PAGE-SQUEEZED's,
        # whose grow-the-slot fix matches that cause.
        if shortfall < 1.0 or not cramping.wrapped_headers:
            continue
        suggested = cramping.suggested_width
        fix = WARN_TABLE_CRAMPED.fix_template.format(suggested_width=suggested)
        warnings.append(
            Diagnostic.from_code(
                WARN_TABLE_CRAMPED,
                chart=chart_id,
                message=WARN_TABLE_CRAMPED.message_template.format(
                    chart_id=chart_id,
                    wrapped_headers=cramping.wrapped_headers,
                    column_count=cramping.column_count,
                    needed_width=cramping.required_width,
                    available_width=cramping.available_width,
                ),
                fix=fix,
            )
        )

    return warnings
