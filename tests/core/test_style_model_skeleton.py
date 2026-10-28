"""Tests for the bare-noun/Patch/Resolved style model skeleton.

TDD tests — written BEFORE implementation. All should fail initially.

Tests prove:
1. FontStyle and BorderStylePatch/BorderStyle leaf types exist with correct fields.
2. Style tree exists with correct nested structure.
3. build_patch_model() generates all-Optional patches from Style.
4. resolve_style() merges base + patches into ResolvedStyle.
5. Cascade inheritance applies (font flows down).
6. ResolvedStyle has required concrete fields.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit

from ._board_utils import merged_patch

# =============================================================================
# Leaf types
# =============================================================================


def test_font_style_fields():
    """FontStyle accepts field values and stores them correctly."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    f = FontStyle(family="Inter", color="#333", size=14.0, weight="400")
    assert f.family == "Inter"
    assert f.color == "#333"
    assert f.size == 14.0
    assert f.weight == "400"


def test_font_style_partial():
    """FontStyle accepts partial field values."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    f = FontStyle(family="Inter", color="#222")
    assert f.family == "Inter"
    assert f.color == "#222"
    assert f.size is None


def test_font_style_rejects_extra_fields():
    """FontStyle is closed (extra='forbid')."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    with pytest.raises(ValidationError):
        FontStyle.model_validate({"family": "Inter", "unknown": True})


def test_border_style_fields():
    """BorderStylePatch field presence is covered by test_border_style_partial."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch(radius=4.0, color="#e0e0e0", width=1.0)
    assert b.radius == 4.0
    assert b.color == "#e0e0e0"
    assert b.width == 1.0


def test_border_style_partial():
    """BorderStylePatch accepts partial field values."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch(radius=4.0, color="#e0e0e0")
    assert b.radius == 4.0
    assert b.color == "#e0e0e0"
    assert b.width is None


def test_border_style_rejects_extra_fields():
    """BorderStylePatch is closed."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    with pytest.raises(ValidationError):
        BorderStylePatch.model_validate({"radius": 4, "unknown": True})


# =============================================================================
# Resolved leaf types — concrete fields
# =============================================================================


def test_resolved_font_style_requires_all_fields():
    """ResolvedFontStyle has required concrete fields (no Optional)."""
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle

    with pytest.raises(ValidationError):
        ResolvedFontStyle()  # all fields required — BaseModel raises ValidationError


def test_resolved_font_style_concrete():
    """ResolvedFontStyle can be constructed when all fields are provided."""
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle

    rf = ResolvedFontStyle(
        family="Inter",
        color="#222",
        size=14.0,
        weight="400",
        style="normal",
        decoration="none",
        case="none",
        line_height=1.25,
        tabular_figures=False,
    )
    assert rf.family == "Inter"
    assert rf.color == "#222"
    assert rf.size == 14.0
    assert rf.weight == "400"
    assert rf.style == "normal"
    assert rf.decoration == "none"
    assert rf.case == "none"
    assert rf.line_height == 1.25


# =============================================================================
# Style tree
# =============================================================================


def test_compiled_style_has_required_sections():
    """Style has all required top-level sections."""
    s = get_theme_style()
    assert s.frame is not None
    assert s.font is not None
    assert s.border is not None
    assert s.charts is not None
    assert s.layout is not None
    assert s.variables is not None
    assert s.title is not None
    assert s.text is not None
    assert s.placeholder is not None
    assert s.charts.callout is not None


def test_compiled_style_board_dimensions():
    """Style.frame has dimension fields with defaults."""

    s = get_theme_style()
    assert s.frame.width is None
    assert isinstance(s.frame.max_width, float)
    assert isinstance(s.frame.min_height, float)
    assert isinstance(s.frame.margin, float)
    assert isinstance(s.frame.card_padding, float)
    assert isinstance(s.frame.card_gap, float)


def test_compiled_style_charts_axis_nested():
    """Style.charts.axis uses nested structure with FontStyle."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    s = get_theme_style()
    # axis is always present on ResolvedChartsStyle
    assert s.charts.axis is not None
    assert s.charts.axis.labels is not None
    assert isinstance(s.charts.axis.labels.font, FontStyle)
    assert s.charts.axis.title is not None
    assert isinstance(s.charts.axis.title.font, FontStyle)


def test_compiled_style_charts_has_palette():
    """Style.charts.color.categorical.palette is a list of color strings."""

    s = get_theme_style()
    assert s.charts.color is not None
    categorical = s.charts.color.categorical
    assert categorical is not None
    assert isinstance(categorical.palette, list)
    assert len(categorical.palette) > 0
    assert all(isinstance(c, str) for c in categorical.palette)


def test_compiled_style_charts_table_nested():
    """Style.charts.table uses nested structure; font is a cascade sentinel."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    s = get_theme_style()
    assert s.charts.table is not None
    assert s.charts.table.font == FontStyle(extends="label")
    assert s.charts.table.header is not None
    assert isinstance(s.charts.table.header.font, FontStyle)
    assert s.charts.table.row is not None


def test_compiled_style_charts_kpi_nested():
    """Style.charts.kpi has value section with font."""

    s = get_theme_style()
    assert s.charts.kpi is not None
    assert s.charts.kpi.value is not None


def test_compiled_style_variables_nested():
    """Style.variables uses nested FontStyle and BorderStyle."""
    from dbt_charts.core.compile.models.primitives import BorderStyle, FontStyle

    s = get_theme_style()
    assert isinstance(s.variables.font, FontStyle)
    assert isinstance(s.variables.border, BorderStyle)
    assert s.variables.input is not None


def test_compiled_style_layout_nested():
    """Style.layout has rows, cols, grid, tabs, details."""

    s = get_theme_style()
    assert s.layout.rows is not None
    assert s.layout.cols is not None
    assert s.layout.grid is not None
    assert s.layout.tabs is not None
    assert s.layout.details is not None


def test_compiled_style_rejects_extra_fields():
    """Style is closed (extra='forbid')."""
    from dbt_charts.core.compile.models.style.theme import Style

    with pytest.raises(ValidationError):
        Style.model_validate({"inventedTopLevel": True})


# =============================================================================
# build_patch_model factory
# =============================================================================


def test_build_patch_model_generates_optional_fields():
    """build_patch_model turns required fields into Optional fields."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.style.theme import Style

    PatchCls = build_patch_model(Style)
    patch = PatchCls()
    # All top-level fields should default to None
    assert patch.frame is None
    assert patch.font is None
    assert patch.charts is None
    assert patch.layout is None
    assert patch.variables is None


def test_build_patch_model_accepts_partial_values():
    """Generated patch model accepts partial values without validation errors."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.style.theme import ChartsStyle

    PatchCls = build_patch_model(ChartsStyle)
    # Should not raise; palette is now under color.categorical.palette
    patch = PatchCls.model_validate(
        {"color": {"categorical": {"palette": ["#ff0000"]}}}
    )
    assert patch.color is not None
    assert patch.color.categorical is not None
    assert patch.color.categorical.palette == ["#ff0000"]
    assert patch.aspect_ratio is None


def test_build_patch_model_is_cached():
    """build_patch_model returns the same class for the same input."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.style.theme import Style

    cls1 = build_patch_model(Style)
    cls2 = build_patch_model(Style)
    assert cls1 is cls2


def test_compiled_style_patch_is_subclass_of_generated():
    """StylePatch is a subclass of build_patch_model(Style) (inherits all its fields)."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.models.style.theme import Style

    assert issubclass(StylePatch, build_patch_model(Style))


def test_patch_model_rejects_extra_fields():
    """Generated patch model is closed (extra='forbid')."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.style.theme import Style

    PatchCls = build_patch_model(Style)
    with pytest.raises(ValidationError):
        PatchCls.model_validate({"inventedField": True})


# =============================================================================
# resolve_style — merge + cascade
# =============================================================================


def test_resolve_style_applies_patch():
    """resolve_style applies patch fields over base; background in patch overrides theme."""
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    patch = StylePatch.model_validate({"background": "#ff0000"})
    resolved = resolve_style(get_theme_style(), patch)
    assert resolved.background == "#ff0000"


def test_resolve_style_last_patch_wins():
    """Later patches override earlier patches."""
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    p1 = StylePatch.model_validate({"background": "#111111"})
    p2 = StylePatch.model_validate({"background": "#222222"})
    resolved = resolve_style(get_theme_style(), merged_patch(p1, p2))
    assert resolved.background == "#222222"


def test_resolve_style_font_cascade(model_copy_at):
    """Root font.color cascades to charts.axis.labels.font.color when not overridden."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    # Null out the axis label font color so cascade has room to fill it.
    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(base, "font.color", "#8b9035"),
        "charts.axis.labels.font.color",
        None,
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.axis.labels.font.color == "#8b9035"


def test_resolve_style_font_override_respected():
    """Explicit font.color at nested level overrides cascade."""
    from dbt_charts.core.compile.models.style.authored import StylePatch
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    base = get_theme_style().model_copy(
        update={"font": get_theme_style().font.model_copy(update={"color": "#63a9f0"})}
    )
    patch = StylePatch.model_validate(
        {"charts": {"axis": {"labels": {"font": {"color": "#433169"}}}}}
    )
    rs, ctx = resolve_style_and_context(base, patch)
    assert ctx.axis.labels.font.color == "#433169"
    assert rs.font.color == "#63a9f0"


# =============================================================================
# ResolvedStyle contract
# =============================================================================


def test_resolved_style_has_concrete_font():
    """ResolvedStyle.font is a ResolvedFontStyle with concrete fields."""
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    resolved = resolve_style(get_theme_style())
    assert isinstance(resolved.font, ResolvedFontStyle)
    assert resolved.font.family is not None
    assert resolved.font.color is not None


def test_resolved_style_charts_axis_label_font_concrete():
    """A fully-resolved axis label carries a ResolvedFontStyle."""
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    ctx = resolve_chart_style_context(get_theme_style())
    # resolved_axis_style() merges all cascade layers and returns a ResolvedAxisStyle.
    axis = resolved_axis_style(
        ctx, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert isinstance(axis.labels.font, ResolvedFontStyle)
    assert axis.labels.font.family is not None


def test_resolved_style_has_background():
    """ResolvedStyle.background is the cascaded canvas color (always a non-empty str)."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    resolved = resolve_style(get_theme_style())
    assert isinstance(resolved.background, str)
    assert resolved.background  # non-empty


# =============================================================================
# Style gap fields: subtitle, discrete dims, bar corner radius
# =============================================================================


def test_compiled_title_style_has_subtitle_block():
    """TitleStyle.subtitle.font: FontStyle — subtitle_font promoted to nested block."""
    from dbt_charts.core.compile.config import (
        get_theme_style,
        reset_config,
    )
    from dbt_charts.core.compile.models.primitives import FontStyle

    reset_config()
    t = get_theme_style().title
    assert isinstance(t.subtitle.font, FontStyle)


def test_compiled_view_style_has_discrete_dimensions():
    """ViewStyle has discrete_width and discrete_height for VL view mapping."""
    from dbt_charts.core.compile.config import (
        get_theme_style,
        reset_config,
    )

    reset_config()
    v = get_theme_style().charts.view
    assert hasattr(v, "discrete_width")
    assert hasattr(v, "discrete_height")
    # These are legitimately optional (None = auto)
    assert v.discrete_width is None or isinstance(v.discrete_width, float)
    assert v.discrete_height is None or isinstance(v.discrete_height, float)


def test_style_to_vega_lite_maps_title_subtitle_fields():
    """style_to_vega_lite maps subtitle.font.{color,family,size,weight} + subtitle_padding to VL."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "title": base.title.model_copy(
                update={
                    "subtitle": base.title.subtitle.model_copy(
                        update={
                            "font": FontStyle(
                                color="#626366", family="Inter", size=14.0, weight=400
                            )
                        }
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    vl = style_to_vega_lite(ctx)
    title = vl.model_dump(exclude_none=True).get("title", {})
    assert title.get("subtitleColor") == "#626366"
    assert title.get("subtitleFont", "").startswith("Inter")
    assert title.get("subtitleFontSize") == 14.0
    assert title.get("subtitleFontWeight") == 400
    assert "subtitlePadding" not in title  # VL-specific, no dbt charts field


def test_style_to_vega_lite_maps_view_fields():
    """style_to_vega_lite maps view stroke, continuous/discrete width/height."""
    from dbt_charts.core.compile.models.style.theme import ViewStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "view": ViewStyle(
                        stroke="#D0D3D9",
                        continuous_width=360.0,
                        continuous_height=300.0,
                        discrete_width=360.0,
                        discrete_height=360.0,
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    vl = style_to_vega_lite(ctx)
    view = vl.model_dump(exclude_none=True).get("view", {})
    assert view.get("stroke") == "#D0D3D9"
    assert view.get("continuousWidth") == 360.0
    assert view.get("continuousHeight") == 300.0
    assert view.get("discreteWidth") == 360.0
    assert view.get("discreteHeight") == 360.0


def test_style_to_vega_lite_maps_text_mark():
    """style_to_vega_lite maps marks.text font.family, font.color (→fill), font.size, align.

    text mark renamed from label to marks.text.
    """
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.theme import TextMarkStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

    base = get_theme_style()
    new_text = TextMarkStyle(
        font=FontStyle(family="Inter", color="#313233", size=11.0), align="center"
    )
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "marks": base.charts.marks.model_copy(update={"text": new_text})
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    vl = style_to_vega_lite(ctx)
    text = vl.model_dump(exclude_none=True).get("text", {})
    assert text.get("font", "").startswith("Inter")
    assert text.get("fill") == "#313233"
    assert text.get("fontSize") == 11.0
    assert text.get("align") == "center"


# =============================================================================
# Task 1f: Border property group consolidation
# =============================================================================


def test_style_to_vega_lite_maps_bar_border_radius():
    """marks.bar.border.radius maps to spec.mark.cornerRadius (not VL config layer).

    bar mark fields live under marks.bar. The VL config layer
    does not emit bar-specific keys — bar.border.radius goes to spec.mark.
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.compile.vega_lite.mapping import style_to_vega_lite

    base = get_theme_style()
    # Set border.radius on the global bar mark tier
    new_bar_mark = base.charts.marks.bar.model_copy(
        update={
            "border": base.charts.marks.bar.border.model_copy(update={"radius": 3.0})
        }
    )
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "marks": base.charts.marks.model_copy(update={"bar": new_bar_mark})
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    vl = style_to_vega_lite(ctx)
    bar = vl.model_dump(exclude_none=True).get("bar", {})
    # bar cornerRadius is at spec.mark, not style_to_vega_lite output
    assert bar is None or bar.get("cornerRadiusEnd") is None
    assert "cornerRadiusTopLeft" not in bar
    assert "cornerRadiusTopRight" not in bar


def test_histogram_has_border_style():
    """charts.marks.bar.border is a BorderStyle (histogram renders bar marks).

    Bar mark geometry (including border) lives in the global
    marks tier under charts.marks.bar. Histogram chart style no longer has a
    flat border field.
    """
    from dbt_charts.core.compile.models.primitives import BorderStyle

    bar_mark = get_theme_style().charts.marks.bar
    assert isinstance(bar_mark.border, BorderStyle)


def test_spark_columns_has_corner_style():
    """SparkColumnsStyle.border is a radius-only CornerStyle (no live stroke)."""
    from dbt_charts.core.compile.models.primitives import CornerStyle

    b = get_theme_style().charts.table.spark.columns
    assert isinstance(b.border, CornerStyle)


def test_spark_bar_has_corner_style():
    """SparkBarCellStyle.border is a radius-only CornerStyle (no live stroke)."""
    from dbt_charts.core.compile.models.primitives import CornerStyle

    p = get_theme_style().charts.table.spark.bar
    assert isinstance(p.border, CornerStyle)


# =============================================================================
# Font field additions + cascade chain
# =============================================================================


def test_compiled_charts_style_has_font():
    """ChartsStyle has font: FontStyle (intermediate cascade node)."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    charts = get_theme_style().charts
    assert hasattr(charts, "font")
    assert isinstance(charts.font, FontStyle)


def test_compiled_kpi_style_has_base_font():
    """KpiStyle has a base ``font: SingleLineFontStyle`` alongside ``value.font``.

    Structural-only — does not pin family/color theme defaults (now
    intentionally set: ``kpi.font.family = 'dbt Sans Tabular'``).
    """
    from dbt_charts.core.compile.models.primitives import SingleLineFontStyle

    kpi = get_theme_style().charts.kpi
    assert hasattr(kpi, "font")
    assert isinstance(kpi.font, SingleLineFontStyle)


def test_compiled_spark_bar_style_has_font():
    """SparkBarCellStyle has font: FontStyle for label text cascade."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    p = get_theme_style().charts.table.spark.bar
    assert hasattr(p, "font")
    assert isinstance(p.font, FontStyle)


def test_cascade_root_to_charts_font(model_copy_at):
    """root.font cascades into charts.font when charts.font is unset."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(base, "font.color", "#492290"),
        "charts.axis.labels.font.color",
        None,
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.axis.labels.font.color == "#492290"


def test_cascade_charts_font_intermediate_override(model_copy_at):
    """Setting charts.font.color overrides root for all chart-level fonts."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(
            model_copy_at(
                model_copy_at(base, "font.color", "#63a9f0"),
                "charts.font",
                FontStyle(color="#cc1889"),
            ),
            "charts.axis.labels.font.color",
            None,
        ),
        "charts.legend.label.font.color",
        None,
    )
    rs, ctx = resolve_style_and_context(seed)
    assert ctx.axis.labels.font.color == "#cc1889"
    assert ctx.legend.label.font.color == "#cc1889"
    assert rs.font.color == "#63a9f0"


def test_cascade_root_to_kpi_font():
    """root.font cascades through charts.font into kpi.font when unset.

    The theme now sets ``kpi.font.family`` to dbt Sans Tabular, so we clear
    it on the seed first to test the cascade-fill behavior on the unset
    branch.
    """
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "font": base.font.model_copy(update={"family": "CustomFont"}),
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={"font": FontStyle()}  # clear theme-set family/weight
                    )
                }
            ),
        }
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.charts.kpi.font.family == "CustomFont"


def test_cascade_kpi_font_to_value_font():
    """kpi.font cascades into kpi.value.font for unset fields.

    `color` fills from kpi.font too: the headline takes the card's ink.
    """
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={"font": FontStyle(weight=771.0)}
                    )
                }
            )
        }
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.charts.kpi.value.font.weight == 771.0
    assert cascaded.charts.kpi.value.font.color == cascaded.charts.kpi.font.color


def test_cascade_charts_font_to_in_cell_spark_bar():
    """charts.font cascades into spark.bar.font (in-cell bar variant)."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={"font": FontStyle(family="SparkFont")}
            )
        }
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.charts.table.spark.bar.font.family == "SparkFont"


def test_cascade_charts_font_to_spark_bar():
    """charts.font cascades into spark_bar.font for unset fields."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={"font": FontStyle(color="#c09708")}
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.spark_bar.font.color == "#c09708"


# =============================================================================
# Missing cascade paths
# =============================================================================


def test_cascade_subtitle_font(model_copy_at):
    """title.subtitle.font inherits from root when unset."""
    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(base, "font.color", "#63a9f0"),
        "title.subtitle.font.color",
        None,
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.title.subtitle.font.color == "#63a9f0"


def test_cascade_charts_callout_fonts(model_copy_at):
    """charts.callout title/message fonts cascade from charts.font, except color:
    the theme sets it to ``by_tone``, which the font fill leaves alone."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = model_copy_at(
        base, "charts.font", FontStyle(family="ChartsFont", color="#cc1889")
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.charts.callout.title.font.color == "by_tone"
    assert cascaded.charts.callout.message.font.color == "by_tone"
    assert cascaded.charts.callout.title.font.family == "ChartsFont"
    assert cascaded.charts.callout.message.font.family == "ChartsFont"


def test_cascade_placeholder_overlay_font(model_copy_at):
    """placeholder.overlay.font inherits from root when unset."""
    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(base, "font.color", "#63a9f0"),
        "placeholder.overlay.font.color",
        None,
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.placeholder.overlay.font.color == "#63a9f0"


def test_cascade_charts_label_font(model_copy_at):
    """charts.marks.text.font cascades from charts.font.

    text mark renamed from label to marks.text.
    """
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = model_copy_at(
        model_copy_at(base, "charts.font", FontStyle(color="#cc1889")),
        "charts.marks.text.font.color",
        None,
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    assert cascaded.charts.marks.text.font.color == "#cc1889"


def test_cascade_charts_table_fonts():
    """charts.table.font and sub-fonts cascade from charts_font (using family — always None in defaults)."""
    style = get_theme_style()
    base = style.model_copy(
        update={"font": style.font.model_copy(update={"family": "RootFont"})}
    )
    cascaded = apply_inherit(base, get_inherit_graph())
    tbl = cascaded.charts.table
    assert tbl.font.family == "RootFont"
    assert (
        tbl.header.font.family == "RootFont"
    )  # header.font.color has an explicit default; family does not
    assert tbl.title_row.font.family == "RootFont"
    assert tbl.more_rows.font.family == "RootFont"
    assert tbl.empty_state.font.family == "RootFont"


def test_cascade_table_two_level():
    """table sub-fonts inherit from table.font (2-level cascade, via family which is None by default)."""
    from dbt_charts.core.compile.models.primitives import FontStyle

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "table": base.charts.table.model_copy(
                        update={"font": FontStyle(family="TableFont")}
                    )
                }
            )
        }
    )
    cascaded = apply_inherit(seed, get_inherit_graph())
    tbl = cascaded.charts.table
    assert tbl.header.font.family == "TableFont"
    assert tbl.title_row.font.family == "TableFont"
    assert tbl.more_rows.font.family == "TableFont"
    assert tbl.empty_state.font.family == "TableFont"


def test_cascade_layout_fonts():
    """layout.tabs.font and layout.details.font cascade from root."""
    style = get_theme_style()
    base = style.model_copy(
        update={"font": style.font.model_copy(update={"color": "#63a9f0"})}
    )
    cascaded = apply_inherit(base, get_inherit_graph())
    assert cascaded.layout.tabs.font.color == "#63a9f0"
    assert cascaded.layout.details.font.color == "#63a9f0"


# =============================================================================
# charts.callout — chart-family style for callout charts
# =============================================================================


def test_charts_callout_defaults():
    """A resolved callout has concrete defaults from theme YAML and its tone."""
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    callout = resolve_chart_style_context(get_theme_style()).callout_error
    # Values come from theme tokens — assert structure/types not literal values
    assert callout.background is not None
    assert callout.border.color is not None
    assert callout.border.width is not None and callout.border.width >= 0
    assert callout.border.radius is not None and callout.border.radius >= 0
    assert callout.title.font.color is not None
    assert callout.title.font.size is not None
    assert callout.title.font.weight is not None
    assert callout.title.y_offset == 0.0
    assert callout.message.font.color is not None
    assert callout.message.font.size is not None
    assert callout.message.y_offset == 0.0


def test_theme_callout_colors_are_by_tone():
    callout = get_theme_style().charts.callout

    assert callout.background == "by_tone"
    assert callout.border.color == "by_tone"
    assert callout.title.font.color == "by_tone"
    assert callout.message.font.color == "by_tone"


def test_resolved_charts_callout_style_padding_from_theme():
    """padding/section_gap propagate from Style override through to resolved type."""
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    # Set distinctive values — these must survive the compile→resolve pipeline.
    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "callout": base.charts.callout.model_copy(
                        update={
                            "padding": PaddingStyle(
                                top=37.0, right=37.0, bottom=37.0, left=37.0
                            ),
                            "section_gap": 5.0,
                        }
                    )
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.callout.padding.top == 37.0
    assert ctx.callout.padding.left == 37.0
    assert ctx.callout.section_gap == 5.0


def test_callout_card_uses_inline_spacing_from_style():
    """Inline callout spacing must come from resolved style, not renderer constants."""
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.engine import render_callout_card

    base = get_theme_style()
    tight_seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "callout": base.charts.callout.model_copy(
                        update={
                            "padding": PaddingStyle(
                                top=24.0, right=24.0, bottom=24.0, left=24.0
                            ),
                            "section_gap": 6.0,
                        }
                    )
                }
            )
        }
    )
    tight_ctx = resolve_chart_style_context(tight_seed)

    loose_seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "callout": base.charts.callout.model_copy(
                        update={
                            "padding": PaddingStyle(
                                top=24.0, right=24.0, bottom=24.0, left=24.0
                            ),
                            "section_gap": 18.0,
                        }
                    )
                }
            )
        }
    )
    loose_ctx = resolve_chart_style_context(loose_seed)
    tight_svg = render_callout_card(
        title="Test chart",
        message="some callout message",
        width=240,
        style=tight_ctx.callout_error,
        chart_id="callout",
    )
    loose_svg = render_callout_card(
        title="Test chart",
        message="some callout message",
        width=240,
        style=loose_ctx.callout_error,
        chart_id="callout",
    )

    def _message_y(svg: str) -> float:
        # Message content is in a <g transform="translate(padding, y)"> group;
        # the last translate group is the message block (title comes first).
        translates = re.findall(r'<g transform="translate\([0-9.]+, ([0-9.]+)\)"', svg)
        assert len(translates) >= 2, f"expected ≥2 translate groups, got {translates!r}"
        return float(translates[-1])

    loose_msg_y = _message_y(loose_svg)
    tight_msg_y = _message_y(tight_svg)
    assert loose_msg_y - tight_msg_y == pytest.approx(12.0, abs=0.01)


# =============================================================================
# ResolvedChartsStyle.spark field
# =============================================================================


def test_resolved_charts_style_has_spark():
    """ChartStyleContext.spark is SparkStyle."""
    from dbt_charts.core.compile.models.style.theme import SparkStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    ctx = resolve_chart_style_context(get_theme_style())
    assert isinstance(ctx.spark, SparkStyle)
    assert ctx.spark.color is not None  # filled by token cascade
