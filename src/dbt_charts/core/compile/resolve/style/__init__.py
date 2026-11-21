"""resolve.style — the style cascade engine.

Stage: COMPILE (pure — no render imports)

Layout: ``tokens.py`` (color/theme-self tokens, emoji font insertion),
``axis_cascade.py`` (the axis cascade, both halves rejoined),
``scale.py`` (build_resolved_scale + ruler digit-ladder math), ``board.py``
(the board-level cascade engine, cache, and entry points),
``chart_context.py`` (per-chart context merge + chart-patch readers),
``palette.py`` / ``typography.py`` / ``inherit_graph.py`` /
``inherit_resolver.py``.

Public surface: only entry points that live in unbanned submodules
(``board.py``, ``tokens.py`` — see the render-boundary guard's comment in
``test_render_boundary.py`` for which submodules are banned and why).
``build_resolved_axis``/``resolved_axis_style`` (``axis_cascade.py``) and
``build_chart_style_context`` (``chart_context.py``) are deliberately NOT
re-exported here: those two submodules are banned from render, and
re-exporting a banned submodule's symbol through this unbanned package root
would silently open a door the guard closes everywhere else. Nothing
outside this package imports them through here anyway — every real
consumer already goes straight to the owning submodule, matching how
``resolve/chart/`` is consumed. Imports are one-way: no module here may
import ``resolve.chart`` (pinned by ``test_no_style_imports_resolve_chart``).
"""

from __future__ import annotations

from dbt_charts.core.compile.resolve.style.board import (
    clear_resolve_style_cache,
    resolve_cascaded_font,
    resolve_chart_style_context,
    resolve_mark,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.tokens import (
    apply_emoji_to_family,
    insert_emoji_family,
)

__all__ = [
    "apply_emoji_to_family",
    "clear_resolve_style_cache",
    "insert_emoji_family",
    "resolve_cascaded_font",
    "resolve_chart_style_context",
    "resolve_mark",
    "resolve_style",
    "resolve_style_and_context",
]
