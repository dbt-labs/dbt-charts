"""Detector: WARN_TABLE_TEXT_TRUNCATED — fires when table header labels or cell
values were clipped with an ellipsis during SVG rendering.

Detection rule: ``WarningContext.text_truncations`` contains records with
``surface == "table_cell"``.  One ``Diagnostic`` fires per
table, with one ``related`` location per truncated column at its
``style.columns`` entry (nearest authored ancestor when it is not authored).
"""

from __future__ import annotations

from collections import Counter

from dbt_charts.core.diagnostics import (
    WARN_TABLE_TEXT_TRUNCATED,
    Diagnostic,
    RelatedLocation,
)
from dbt_charts.core.render.warnings.base import WarningContext


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per table with truncated text."""
    warnings: list[Diagnostic] = []
    for chart_id, truncations in ctx.text_truncations.items():
        counts: Counter[str] = Counter(
            t.authored_field for t in truncations if t.surface == "table_cell"
        )
        if not counts:
            continue
        warnings.append(
            Diagnostic.from_code(
                WARN_TABLE_TEXT_TRUNCATED,
                chart=chart_id,
                field=None,
                path=f"charts.{chart_id}",
                related=tuple(
                    RelatedLocation(
                        path=f"charts.{chart_id}.style.columns.{col}",
                        message=f"{col}: {count} value(s) cut",
                    )
                    for col, count in counts.items()
                ),
                message=WARN_TABLE_TEXT_TRUNCATED.message_template.format(
                    chart_id=chart_id,
                    authored_field=", ".join(counts),
                    truncation_count=counts.total(),
                ),
                fix=WARN_TABLE_TEXT_TRUNCATED.fix_template,
            )
        )
    return warnings
