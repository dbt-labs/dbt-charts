"""Compile-time check that a table row rule's glyph ink has a glyph to color.

A row rule's ``columns.<col>`` may set ``tone`` or ``glyph_color``, which color
the cell's glyph; the glyph comes from the same ``columns.<col>`` or from the
chart's ``style.columns.<col>``. The two halves live in different parts of the
chart's style, so no single model can state the rule.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_TABLE_GLYPH_INK_WITHOUT_GLYPH,
)


def validate_table_row_rule_glyphs(
    chart_styles: Mapping[
        str, BaseModel | None
    ],  # type-state: optional — None when the chart authors no style
) -> None:
    """Raise at the first row rule cell that sets ``tone`` or ``glyph_color``
    with no glyph from the cell or its column.

    ``chart_styles`` maps chart id to the style patch the chart itself authored.
    """
    for chart_id, style in chart_styles.items():
        row = style.row if isinstance(style, TableChartStylePatch) else None
        rules = row.conditional if row is not None else None
        if not rules or not isinstance(style, TableChartStylePatch):
            continue
        columns = style.columns
        for tested, column_rules in rules.items():
            for rule in column_rules:
                if rule.columns is None:
                    continue
                for target, cell in rule.columns.items():
                    ink = (
                        "tone"
                        if cell.tone
                        else "glyph_color"
                        if cell.glyph_color
                        else None
                    )
                    column = columns.get(target) if columns is not None else None
                    has_glyph = cell.glyph or (column is not None and column.glyph)
                    if ink and not has_glyph:
                        raise CompilationError.from_code(
                            ERR_TABLE_GLYPH_INK_WITHOUT_GLYPH,
                            chart_id=chart_id,
                            tested=tested,
                            ink=ink,
                            target=target,
                            field_path=f"charts.{chart_id}.style.row.conditional.{tested}",
                        )
