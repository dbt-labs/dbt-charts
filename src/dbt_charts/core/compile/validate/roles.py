"""Compile-time validation that every ``style.roles`` address resolves.

A role (`ink: chrome[10]`) is a bare name for a color address. Nothing else
reads one until a field uses it, so an address no palette satisfies would
compile clean and fail only where it is consumed. The final per-board role
table (theme, project and board layers merged) is resolved here, once per
board, through the same ``color_from_theme`` every color token uses.
"""

from __future__ import annotations

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.resolve.style.palette import (
    UnknownColorError,
    color_from_theme,
)
from dbt_charts.core.diagnostics.codes_compile import ERR_COLOR_TOKEN


def validate_board_roles(board: Board) -> None:
    """Raise ``ERR-COLOR-TOKEN`` for the first role on *board* or a nested board
    whose address resolves to no color."""
    for item in board.layout.items:
        if item.type == "board" and item.board is not None:
            validate_board_roles(item.board)
    context = board.chart_style_context
    for name, address in context.roles.items():
        try:
            color_from_theme(
                name,
                palettes=context.palettes,
                roles=context.roles,
                single_series_palette=context.single_series_palette,
            )
        except UnknownColorError as exc:
            raise CompilationError.from_code(
                ERR_COLOR_TOKEN,
                scope=f"style.roles.{name} ({address})",
                reason=str(exc),
            ) from exc
