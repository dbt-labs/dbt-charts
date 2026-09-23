"""Compile-time validation that no palette field survives normalization unresolved.

Palette roles (`palette: category`) are resolved exactly once, at
``get_theme_style()`` (``compile/resolve/style/tokens.py``'s
``expand_palette_refs``), where the theme's own ``palettes:`` map is final.
Board- and chart-level ``style:`` never gets that treatment — a role there is
unsupported by design: theme roles resolve against the theme's own map, which
does not exist at board/chart scope. But the same model validators that let a
*theme* defer a role until that map is final
(``ScaleTargetPaletteValidationMixin``, ``CategoricalColorStyle``) cannot tell
a role-shaped board/chart string apart from a typo, so both are deferred the
same way at every layer, not just the theme's.

This walk is the guard that closes the deferral: after normalization, any
palette field still holding a bare ``str`` (instead of the ``list[str]`` a
resolved categorical/single-series palette becomes, or an already-checked
gradient/scale name) is exactly one of a role (unsupported here) or a typo
(always an error) — and both fail the same way, ``ERR-PALETTE-UNKNOWN``.

Reuses ``expand_palette_refs`` itself, called with an *empty* palettes map —
so no name it ever meets can resolve as a role, only as a shipped palette or
a Vega scheme, which is exactly the check this guard needs. A ``Style``
subtree or a chart's own style patch is never a ``Style`` instance itself, so
``expand_palette_refs``'s own theme auto-detection
(``isinstance(node, Style): palettes = node.palettes``) never overrides the
empty map with a theme's real roles — the one thing that would silently
reopen board/chart-level role support this guard exists to close.

Coverage is deliberately scoped, not "the whole compiled tree": a ``Chart``
also carries ``query`` (author data — ``ValuesQuery.rows`` is
``list[dict[str, Any]]``) and, on geo charts, an inline ``Any``-typed ``geo``
config. Handing those to a dict-key-aware walker would treat an ordinary data
column or config key literally named ``palette`` as a style leaf. Only the
palette-bearing surfaces are walked: a chart's own ``style`` patch and a
KPI's ``background`` channel (the one ``Any``-typed field that legitimately
carries a palette — see ``resolve/style/tokens.py``'s key-aware dict branch)
— plus, per board, the board-level style cascade's ``.charts`` subtree (the
only part of a ``Style`` that can hold a palette field; ``.palettes``/
``.roles``/``.formats`` are the resolver's own lookup tables, not styled
content, and are never walked). An overlay layer's own style patch
(``BarLayerStylePatch`` etc.) is not walked — each wraps only ``marks:``, so
none can ever carry a ``palette``/``single_series_palette`` field.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import Chart, KpiChart
from dbt_charts.core.compile.resolve.style.tokens import expand_palette_refs


def validate_board_palette_specs(board: Board) -> None:
    """Walk a normalized Board tree, raising on any unresolved palette field."""
    _validate_board(board, set())


def _validate_board(board: Board, validated: set[int]) -> None:
    """Validate one board's board style and charts.

    Recurses into nested boards first: a nested board's own board-level style
    cascade (``chart_style_context.pre_style``) is reachable only through its
    own ``Board`` object — unlike its charts, which normalization hoists into
    every ancestor's ``board.charts`` too (``_collect_charts_from_layout``), so
    skipping the recursion would silently stop checking a nested board's
    board-level `style:`. Charts are deduped by ``id()`` for that same reason:
    the same hoisted chart object would otherwise be walked once per ancestor.
    """
    for item in board.layout.items:
        if item.type == "board" and item.board is not None:
            _validate_board(item.board, validated)

    expand_palette_refs(
        board.chart_style_context.pre_style.charts, {}, path="style.charts"
    )

    for chart in board.charts.values():
        if id(chart) in validated:
            continue
        validated.add(id(chart))
        _validate_chart(chart)


def _validate_chart(chart: Chart) -> None:
    """Check one chart's palette-bearing surfaces: its own style patch, and,
    for a KPI, its background channel."""
    expand_palette_refs(chart.style, {}, path=f"charts.{chart.id}.style")
    if isinstance(chart, KpiChart):
        expand_palette_refs(chart.background, {}, path=f"charts.{chart.id}.background")
