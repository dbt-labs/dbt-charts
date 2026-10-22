"""Tests for tooltip box style cascade resolution and Python→JS injection.

TDD anchors:
- TooltipStyle must reject construction without required scalars (ValidationError).
- All built-in production themes resolve tooltip box fields.
- The board root publishes a parseable data-dbt-tooltip-style JSON blob.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Pydantic contract: required scalar raises ValidationError when absent
# ---------------------------------------------------------------------------


def test_tooltip_style_requires_background():
    """TooltipStyle must raise ValidationError when background is absent."""
    from dbt_charts.core.compile.models.style.theme import TooltipStyle

    with pytest.raises(ValidationError, match="background"):
        TooltipStyle(
            format=",.2f",
            # background intentionally omitted
            line_height=1.5,
            max_width=280.0,
            gap=16.0,
            font={},
            padding={"top": 8.0, "bottom": 8.0, "left": 12.0, "right": 12.0},
            label={"font": {}},
            value={"font": {}},
            border={"color": "rgba(0,0,0,0.5)", "width": 1.0, "radius": 4.0},
            shadow={"visible": True},
        )


def test_tooltip_border_style_requires_color():
    """TooltipBorderStyle must raise ValidationError when color is absent."""
    from dbt_charts.core.compile.models.style.theme import TooltipBorderStyle

    with pytest.raises(ValidationError, match="color"):
        TooltipBorderStyle(width=1.0, radius=4.0)


def test_tooltip_shadow_style_requires_visible():
    """TooltipShadowStyle must raise ValidationError when visible is absent."""
    from dbt_charts.core.compile.models.style.theme import TooltipShadowStyle

    with pytest.raises(ValidationError):
        TooltipShadowStyle()


def test_tooltip_slot_style_has_font_default():
    """TooltipSlotStyle can be constructed with no args (all sub-fields optional)."""
    from dbt_charts.core.compile.models.style.theme import TooltipSlotStyle

    slot = TooltipSlotStyle()
    # font is a FontStyle with default_factory; sub-fields are all None
    assert slot.font is not None


# ---------------------------------------------------------------------------
# Cascade resolution: all built-in production themes must have tooltip fields
# ---------------------------------------------------------------------------


def test_all_built_in_themes_resolve_tooltip_padding(compiled_themes):
    """Every production built-in theme must resolve tooltip.padding.top through cascade."""
    errors: list[str] = []
    for name, style in compiled_themes.items():
        try:
            top = style.charts.tooltip.padding.top
            assert isinstance(top, float), f"{name}: padding.top is not float"
        except Exception as exc:  # noqa: BLE001 — collect all failures
            errors.append(f"{name}: {exc}")
    assert not errors, "Themes missing tooltip.padding.top:\n" + "\n".join(errors)


def test_all_built_in_themes_resolve_tooltip_border(compiled_themes):
    """Every production built-in theme must resolve tooltip.border.color through cascade."""
    errors: list[str] = []
    for name, style in compiled_themes.items():
        try:
            color = style.charts.tooltip.border.color
            assert isinstance(color, str) and color, f"{name}: border.color is empty"
        except Exception as exc:  # noqa: BLE001 — collect all failures
            errors.append(f"{name}: {exc}")
    assert not errors, "Themes missing tooltip.border.color:\n" + "\n".join(errors)


def test_all_built_in_themes_resolve_tooltip_shadow(compiled_themes):
    """Every production built-in theme must resolve tooltip.shadow.visible."""
    errors: list[str] = []
    for name, style in compiled_themes.items():
        try:
            visible = style.charts.tooltip.shadow.visible
            assert isinstance(visible, bool), f"{name}: shadow.visible is not bool"
        except Exception as exc:  # noqa: BLE001 — collect all failures
            errors.append(f"{name}: {exc}")
    assert not errors, "Themes missing tooltip.shadow.visible:\n" + "\n".join(errors)


def test_stark_resolves_triangle_marker_other_themes_resolve_fill(
    compiled_themes,
):
    """stark's tooltip active_marker is 'triangle'; every other theme 'fill'.

    Guards the leak: the 'triangle' stark sets for itself must not reach any
    other theme. stark itself is excluded from the comparison loop below --
    it is the baseline being compared against.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    stark_marker = resolve_chart_style_context(
        get_theme_style("stark")
    ).tooltip.active_marker
    assert stark_marker == "triangle"

    errors: list[str] = []
    for name, style in compiled_themes.items():
        if name == "stark":
            continue
        actual = resolve_chart_style_context(style).tooltip.active_marker
        if actual != "fill":
            errors.append(f"{name}: expected active_marker='fill', got {actual!r}")
    assert not errors, "\n".join(errors)


def test_soft_tooltip_themes_differ_from_stark_tooltip_box(
    compiled_themes,
):
    """clarity/paper/vivid/neon keep a soft tooltip box, unlike stark's.

    stark sets border.width=1.5, shadow.visible=False, and swatch.radius=0
    for its own utilitarian look (and is excluded from the comparison loop
    below -- it is the baseline).
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    stark_tooltip = resolve_chart_style_context(get_theme_style("stark")).tooltip
    errors: list[str] = []
    for name, style in compiled_themes.items():
        if name == "stark":
            continue
        tooltip = resolve_chart_style_context(style).tooltip
        if tooltip.border.width == stark_tooltip.border.width:
            errors.append(
                f"{name}: border.width leaked stark's {stark_tooltip.border.width}"
            )
        if tooltip.shadow.visible == stark_tooltip.shadow.visible:
            errors.append(
                f"{name}: shadow.visible leaked stark's {stark_tooltip.shadow.visible}"
            )
        if tooltip.swatch.radius == stark_tooltip.swatch.radius:
            errors.append(
                f"{name}: swatch.radius leaked stark's {stark_tooltip.swatch.radius}"
            )
    assert not errors, "\n".join(errors)


def test_tooltip_cascade_propagates_via_override():
    """Distinctive-value override propagates through cascade (not pinning theme literals)."""
    from dbt_charts.core.compile.config import get_theme_style

    base = get_theme_style("stark")
    # Apply a distinctive override to max_width
    updated_tooltip = base.charts.tooltip.model_copy(update={"max_width": 999.0})
    updated_charts = base.charts.model_copy(update={"tooltip": updated_tooltip})
    updated = base.model_copy(update={"charts": updated_charts})
    assert updated.charts.tooltip.max_width == 999.0
    # Original unaffected
    assert base.charts.tooltip.max_width != 999.0


# ---------------------------------------------------------------------------
# Published on the board root: data-dbt-tooltip-style is the JSON the runtime reads
# ---------------------------------------------------------------------------


def _tooltip_style_blob(resolved_style):
    """The tooltip style as the board root publishes it for the hover runtime."""
    import html

    from dbt_charts.core.render.chart_interactivity import hover_runtime_attributes

    attrs = hover_runtime_attributes(resolved_style)
    match = re.search(r'data-dbt-tooltip-style="([^"]*)"', attrs)
    assert match, "board root does not publish data-dbt-tooltip-style"
    return json.loads(html.unescape(match.group(1)))


def _get_resolved_style():
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    return resolve_style(get_theme_style("stark"))


def test_tooltip_style_blob_parses_as_json():
    """The published value must be valid JSON with the expected top-level keys."""
    blob = _tooltip_style_blob(_get_resolved_style())
    expected_keys = {
        "background",
        "lineHeight",
        "maxWidth",
        "gap",
        "font",
        "padding",
        "label",
        "value",
        "border",
        "shadow",
    }
    missing = expected_keys - blob.keys()
    assert not missing, f"tooltip style missing keys: {missing}"


def test_tooltip_style_blob_shadow_is_bool():
    blob = _tooltip_style_blob(_get_resolved_style())
    assert isinstance(blob["shadow"]["visible"], bool)


def test_tooltip_style_blob_padding_has_four_sides():
    blob = _tooltip_style_blob(_get_resolved_style())
    assert {"top", "bottom", "left", "right"} <= blob["padding"].keys()
