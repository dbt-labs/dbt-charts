"""Detector: WARN_LOW_TEXT_CONTRAST — fires when resolved text ink and its
background clear too little WCAG contrast to read.

Detection rule: ``WarningContext.contrast_warnings`` holds every
``ContrastRecord`` the render pass captured
(``check_text_contrast``/``check_markdown_contrast``,
``render/contrast_warning.py``) — one per (kind, ink, background) pair
actually painted below the configured floor. The recorder already applies
the floor and skips unparseable/non-opaque colors, so this detector only
turns each record into a ``Diagnostic``.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per low-contrast ink/background pair captured."""
    warnings: list[Diagnostic] = []
    for record in ctx.contrast_warnings:
        size = "large" if record.large_text else "normal"
        warnings.append(
            Diagnostic.from_code(
                WARN_LOW_TEXT_CONTRAST,
                path=record.path,
                message=WARN_LOW_TEXT_CONTRAST.message_template.format(
                    element=record.element,
                    ink=record.ink,
                    background=record.background,
                    ratio=record.ratio,
                    floor=record.floor,
                    size=size,
                ),
                fix=WARN_LOW_TEXT_CONTRAST.fix_template.format(
                    key=record.key, background=record.background
                ),
            )
        )
    return warnings
