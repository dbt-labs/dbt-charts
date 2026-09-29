"""Regression tests for dbt-labs/dbt-charts#35.

``style.charts.title`` used to require every ``TitleStyle`` field on any
partial override. ``ChartsStyle.title`` now carries its own
``InheritSlot(from_path="Style.title")`` (see ``charts.py``), so
``apply_inherit`` merges an override onto the board title as part of the
ordinary declarative cascade; ``resolve/style/board.py`` only re-validates
that already-merged result (``merge.to_title_style``). Per-family overrides
(``style.charts.kpi.title``, ...) keep ``_ChartStyleBase.title``'s
``SkipInheritSlots()`` and stay sparse patches at this board-cascade stage —
they reach every chart of that family at chart-resolve time instead, via
``build_chart_style_context``'s board -> family-theme -> chart-local title
merge (``resolve/style/chart_context.py``; see
``test_per_family_theme_title_patch.py``).

The first two tests assert the merged, actually-consumed value
(``ChartStyleContext.title``), not just that the patch parses.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


def test_charts_title_partial_font_override_reaches_resolved_title():
    patch = StylePatch.model_validate(
        {"charts": {"title": {"font": {"family": "Georgia"}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    # The resolved family carries an appended emoji fallback (_append_emoji_family);
    # assert the authored family leads it rather than pinning the emoji suffix.
    assert ctx.title.font.family.startswith("Georgia")


def test_charts_title_with_no_override_keeps_theme_default():
    default_ctx = resolve_chart_style_context(get_theme_style())
    patch = StylePatch.model_validate({"charts": {"title": {}}})
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    assert ctx.title.font.family == default_ctx.title.font.family


def test_charts_title_override_leaving_font_entirely_unset_still_resolves():
    """A patch that sets some other title field and leaves the whole `font`
    sub-object unset must still resolve font.* from the board title --
    apply_inherit can't fill a leaf through a None intermediate container,
    so `font` needs its own container-level copy link (emitted because it's
    nested inside the charts.title slot), not just leaf-level links."""
    default_ctx = resolve_chart_style_context(get_theme_style())
    patch = StylePatch.model_validate({"charts": {"title": {"min_height": 40}}})
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    assert ctx.title.min_height == 40
    assert ctx.title.font.family == default_ctx.title.font.family


def test_charts_family_title_partial_override_does_not_raise():
    """Per-family ``style.charts.<type>.title`` (e.g. ``charts.bar.title``) was
    never rejected by this bug — it inherits the field through the
    "AllOptional" base, not directly from ``_ChartStyleBase`` — so this only
    pins that the type change above doesn't regress it at the board-cascade
    stage. ``charts_board_overrides`` is the raw, still-sparse authored patch;
    ``test_per_family_theme_title_patch.py`` covers the merged value every
    chart of the family actually resolves to.
    """
    patch = StylePatch.model_validate(
        {"charts": {"bar": {"title": {"font": {"family": "Georgia"}}}}}
    )
    ctx = resolve_chart_style_context(get_theme_style(), patch)
    assert ctx.charts_board_overrides.bar.title.font.family == "Georgia"
