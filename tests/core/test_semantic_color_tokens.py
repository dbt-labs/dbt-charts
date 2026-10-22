"""Tests for semantic color tokens at root Style level.

Task 1d: Introduce accent + muted root tokens so components inherit
instead of repeating hex values.

NOTE: resolved.charts.table is the LEGACY TableChartStyle (no spark sub-field).
Inline-spark cascade tests use apply_inherit(..., get_inherit_graph())
directly; spark_bar/tick/rule and focus_color are tested via the public resolve_style API.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit
from dbt_charts.core.compile.resolve.style.palette import color as resolve_palette_color

# =============================================================================
# Cascade: accent/muted propagate via apply_inherit(..., get_inherit_graph())
# ResolvedChartsStyle.table is legacy TableChartStyle — no spark field.
# We test cascade correctness via the internal cascade directly.
# =============================================================================


def test_cascade_fills_spark_color_from_single_series_palette():
    """cascade fills charts.table.spark.color from color.categorical.single_series_palette[0]."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    existing_cat = base.charts.color.categorical
    new_cat = (
        existing_cat.model_copy(update={"single_series_palette": ["#3b82f6"]})
        if existing_cat is not None
        else None
    )
    assert new_cat is not None
    updated = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "color": base.charts.color.model_copy(
                        update={"categorical": new_cat}
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(updated)
    assert ctx.table.spark.color == "#3b82f6"


def test_cascade_fills_spark_bar_color_from_single_series_palette():
    """cascade fills charts.table.spark.bar.color from color.categorical.single_series_palette[0]."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    existing_cat = base.charts.color.categorical
    new_cat = (
        existing_cat.model_copy(update={"single_series_palette": ["#3b82f6"]})
        if existing_cat is not None
        else None
    )
    assert new_cat is not None
    updated = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "color": base.charts.color.model_copy(
                        update={"categorical": new_cat}
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(updated)
    assert ctx.table.spark.bar.color == "#3b82f6"


def test_cascade_keeps_the_theme_pinned_spark_bar_track():
    """_base pins the spark-bar track on the grid step; muted does not override it."""
    base = get_theme_style().model_copy(update={"muted": "#123456"})
    cascaded = apply_inherit(base, get_inherit_graph())
    assert cascaded.charts.table.spark.bar.background == resolve_palette_color(
        "dbt-grays.3"
    )


def test_cascade_respects_explicit_spark_color():
    """resolve does not overwrite spark.color when explicitly set."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style().model_copy(
        update={
            "charts": get_theme_style().charts.model_copy(
                update={
                    "table": get_theme_style(
                        get_default_theme_name()
                    ).charts.table.model_copy(
                        update={
                            "spark": get_theme_style(
                                get_default_theme_name()
                            ).charts.table.spark.model_copy(update={"color": "#e2a330"})
                        }
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(base)
    assert ctx.table.spark.color == "#e2a330"


# =============================================================================
# Cascade: accent/muted propagate to spark_bar (accessible via ResolvedChartsStyle)
# =============================================================================


def test_resolve_style_spark_bar_bar_color_inherits_accent():
    """After resolve, spark_bar.bar.color is set (non-None)."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    ctx = resolve_chart_style_context(get_theme_style())
    assert ctx.spark_bar.bar.color is not None


def test_resolve_style_spark_bar_bar_background_inherits_muted():
    """After resolve, spark_bar.bar.background is set (non-None)."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    ctx = resolve_chart_style_context(get_theme_style())
    assert ctx.spark_bar.bar.background is not None


def test_resolve_style_input_focus_color_inherits_accent():
    """After resolve, variables.input.focus_color is set (non-None)."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    resolved = resolve_style(get_theme_style())
    assert resolved.variables.input.focus_color is not None


# =============================================================================
# Cascade: font.color propagates to rule.stroke
# =============================================================================


def test_resolve_style_rule_stroke_inherits_font_color():
    """After resolve, marks.rule.stroke.color equals root font.color.

    rule moved from charts.rule to charts.marks.rule under ADR-015.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    rs, ctx = resolve_style_and_context(get_theme_style())
    assert ctx.marks.rule.stroke is not None
    assert ctx.marks.rule.stroke.color == rs.font.color


# =============================================================================
# Theme-level override: changing accent cascades everywhere
# =============================================================================


def test_single_series_palette_override_propagates_to_spark_bar_color():
    """Setting color.categorical.single_series_palette on Style flows to spark_bar.bar.color after resolve."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    existing_cat = base.charts.color.categorical
    new_cat = (
        existing_cat.model_copy(update={"single_series_palette": ["#ff0000"]})
        if existing_cat is not None
        else None
    )
    assert new_cat is not None
    updated_charts = base.charts.model_copy(
        update={"color": base.charts.color.model_copy(update={"categorical": new_cat})}
    )
    ctx = resolve_chart_style_context(
        base.model_copy(update={"charts": updated_charts})
    )
    assert ctx.spark_bar.bar.color == "#ff0000"


def test_muted_override_does_not_touch_spark_bar_track():
    """muted is the secondary-TEXT token; bar tracks are theme-pinned fills.

    The old muted → spark_bar.bar.background Inherit conflated fill-grade
    and text-grade grays (invisible KPI support rows).
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    base = get_theme_style()
    rs, ctx = resolve_style_and_context(base.model_copy(update={"muted": "#cccccc"}))
    assert rs.muted == "#cccccc"
    assert ctx.spark_bar.bar.background == (base.charts.spark_bar.bar.background)


def test_font_color_override_propagates_to_rule_stroke():
    """font.color cascades to marks.rule.stroke.color when rule.stroke.color is unset.

    rule moved to charts.marks.rule under ADR-015.
    stroke is now FontColorStrokeStyle (non-nullable); clear stroke.color to test inherit.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    # Clear stroke.color so the Inherit system has room to fill it from font.color.
    base = get_theme_style()
    marks = base.charts.marks
    new_rule = marks.rule.model_copy(
        update={"stroke": marks.rule.stroke.model_copy(update={"color": None})}
    )
    seed = base.model_copy(
        update={
            "font": base.font.model_copy(update={"color": "#aabbcc"}),
            "charts": base.charts.model_copy(
                update={"marks": marks.model_copy(update={"rule": new_rule})}
            ),
        }
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.marks.rule.stroke is not None
    assert ctx.marks.rule.stroke.color == "#aabbcc"


def test_accent_override_propagates_to_focus_color():
    """Setting accent on Style flows to variables.input.focus_color."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    resolved = resolve_style(get_theme_style().model_copy(update={"accent": "#ff5500"}))
    assert resolved.variables.input.focus_color == "#ff5500"


# =============================================================================
# Patch-level override: explicit component value wins over cascade
# =============================================================================


def test_explicit_spark_bar_color_overrides_accent_cascade():
    """An explicit spark_bar.bar.color in a patch wins over the accent cascade."""
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style().model_copy(update={"accent": "#3b82f6"})
    patch = StylePatch.model_validate(
        {"charts": {"spark_bar": {"bar": {"color": "#8b9035"}}}}
    )
    ctx = resolve_chart_style_context(base, patch)
    assert ctx.spark_bar.bar.color == "#8b9035"


# =============================================================================
# Semantic tokens are patchable via StylePatch
# =============================================================================


def test_accent_is_patchable():
    """accent can be patched via StylePatch and flows downstream to UI affordances.

    Spark mark color is now driven by single_series_palette (see
    test_single_series_palette_override_propagates_to_spark_bar_color).
    accent remains the UI-affordance token — variables input focus, link,
    etc.
    """
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    patch = StylePatch.model_validate({"accent": "#ff5500"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.variables.input.focus_color == "#ff5500"


def test_muted_is_patchable():
    """muted can be patched via StylePatch and survives resolve."""
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    patch = StylePatch.model_validate({"muted": "#dddddd"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.muted == "#dddddd"


# =============================================================================
# Real-world path: theme-loaded base + board patch
# (The critical cascade-ordering tests — these verify single-place cascade.)
# =============================================================================


class TestCascadeWithThemeLoadedBase:
    """Cascade must propagate board-level accent/muted overrides even when
    the base Style was loaded via get_theme_style() (real path).

    These tests FAIL if the token cascade is applied at theme-load time
    (filling sub-fields to concrete values that _fill_none then skips).
    They pass only when cascade runs exclusively inside resolve_style().
    """

    def test_board_single_series_palette_override_propagates_through_theme_loaded_base(
        self,
    ):
        """Board-level color.categorical.single_series_palette patch flows to spark_bar.bar.color
        via resolve on a theme-loaded base."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "color": {"categorical": {"single_series_palette": ["#ff0000"]}}
                }
            }
        )
        ctx = resolve_chart_style_context(base, patch)
        assert ctx.spark_bar.bar.color == "#ff0000"

    def test_board_muted_override_propagates_through_theme_loaded_base(self):
        """Board muted override survives resolve on a theme-loaded base,
        without leaking into the theme-pinned spark_bar track fill."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_style_and_context,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate({"muted": "#cccccc"})
        rs, ctx = resolve_style_and_context(base, patch)
        assert rs.muted == "#cccccc"
        assert ctx.spark_bar.bar.background == (base.charts.spark_bar.bar.background)

    def test_board_single_series_palette_propagates_to_spark_inline_via_cascade(self):
        """Board color.categorical.single_series_palette override flows to inline spark.color via resolve."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        base = get_theme_style()
        patch = StylePatch.model_validate(
            {
                "charts": {
                    "color": {"categorical": {"single_series_palette": ["#ff0000"]}}
                }
            }
        )
        ctx = resolve_chart_style_context(base, patch)
        assert ctx.table.spark.color == "#ff0000"

    def test_theme_default_tokens_resolve_correctly(self):
        """Default theme resolves spark_bar.color to color.categorical.single_series_palette[0]
        and preserves the theme-pinned track fill."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        base = get_theme_style()
        ctx = resolve_chart_style_context(base)
        assert base.charts.color.categorical is not None
        assert base.charts.color.categorical.single_series_palette is not None
        assert (
            ctx.spark_bar.bar.color
            == base.charts.color.categorical.single_series_palette[0]
        )
        assert ctx.spark_bar.bar.background == (base.charts.spark_bar.bar.background)

    def test_explicit_component_override_survives_board_accent_patch(self):
        """Explicit spark_bar.bar.color patch is not clobbered by board accent."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.resolve.style.board import (
            resolve_chart_style_context,
        )

        base = get_theme_style()
        # Patch sets both accent AND an explicit bar.color — explicit wins.
        patch = StylePatch.model_validate(
            {
                "accent": "#ff0000",
                "charts": {"spark_bar": {"bar": {"color": "#e2a330"}}},
            }
        )
        ctx = resolve_chart_style_context(base, patch)
        assert ctx.spark_bar.bar.color == "#e2a330"
