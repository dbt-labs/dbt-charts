"""Detector: WARN_VARIABLE_BUTTONS_OVERFLOW — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

``WarningContext.variable_buttons_overflows`` holds each button row the render drew as
its menu form; this turns each into a ``Diagnostic``.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_VARIABLE_BUTTONS_OVERFLOW, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per button row that did not fit its slot."""
    return [
        Diagnostic.from_code(
            WARN_VARIABLE_BUTTONS_OVERFLOW,
            path=record.path,
            message=WARN_VARIABLE_BUTTONS_OVERFLOW.message_template.format(
                variable=record.variable,
                needed=record.needed,
                available=record.available,
            ),
            fix=WARN_VARIABLE_BUTTONS_OVERFLOW.fix_template,
        )
        for record in ctx.variable_buttons_overflows
    ]
