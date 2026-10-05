"""Tests for unified BorderStylePatch — TDD-first for the unify-borderstyle refactor."""

from dbt_charts.core.compile.config import get_theme_style


def test_from_css_sets_width_color():
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch.from_css("2px solid #333333")
    assert b.width == 2.0
    assert b.color == "#333333"
    assert b.radius is None


def test_from_css_none_returns_empty_border():
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch.from_css(None)
    assert b.width is None
    assert b.color is None
    assert b.radius is None


def test_from_css_named_color():
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch.from_css("2px solid red")
    assert b.width == 2.0
    assert b.color == "red"


def test_from_css_does_not_set_radius():
    """from_css must never set radius — radius is only via nested YAML struct."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    b = BorderStylePatch.from_css("2px solid #333")
    assert b.radius is None


def test_style_patch_nested_border_struct():
    """border: {radius: 8, color: '#ccc', width: 1} via nested dict."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate(
        {"border": {"width": 1, "color": "#ccc", "radius": 8}}
    )
    assert patch.border is not None
    assert patch.border.width == 1
    assert patch.border.color == "#ccc"
    assert patch.border.radius == 8


def test_style_patch_border_string_coercion():
    """border: CSS string coerces via BorderStylePatch.model_validator."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate({"border": "1px solid #aaa"})
    assert patch.border is not None
    assert patch.border.width == 1.0
    assert patch.border.color == "#aaa"
    assert patch.border.radius is None


def test_border_shorthand_tracks_only_values_present_in_the_string():
    """CSS parsing must not turn unmentioned properties into explicit clears."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    assert BorderStylePatch.model_validate("2px").model_fields_set == {"width"}
    assert BorderStylePatch.model_validate("red").model_fields_set == {"color"}
    assert BorderStylePatch.model_validate("1px solid #aaa").model_fields_set == {
        "width",
        "color",
    }


def test_border_shorthand_merge_inherits_unmentioned_radius():
    from dbt_charts.core.compile.merge import merge_onto_base
    from dbt_charts.core.compile.models.primitives import BorderStyle, BorderStylePatch

    base = BorderStyle(width=3, color="black", radius=8)
    patch = BorderStylePatch.model_validate("1px solid #aaa")

    assert merge_onto_base(base, patch) == BorderStyle(
        width=1,
        color="#aaa",
        radius=8,
    )


def test_style_patch_unknown_border_radius_key_rejected():
    """border_radius flat key is not in StylePatch — extra="forbid" rejects it."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError):
        StylePatch.model_validate({"border_radius": "8px"})


# =============================================================================
# BorderStyle — new concrete type, all required fields
# =============================================================================


def test_compiled_border_style_no_defaults():
    """BorderStyle rejects construction without all fields."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.primitives import BorderStyle

    with pytest.raises(ValidationError):
        BorderStyle()  # missing required fields


def test_compiled_border_style_rejects_extra():
    """BorderStyle uses extra='forbid'."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.primitives import BorderStyle

    with pytest.raises(ValidationError):
        BorderStyle(width=0, color="transparent", radius=0, unknown=1)


# =============================================================================
# BorderStylePatch — generated via build_patch_model
# =============================================================================


def test_compiled_border_style_patch_partial():
    """BorderStylePatch accepts partial field values."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch(radius=4.0)
    assert p.radius == 4.0
    assert p.width is None


# =============================================================================
# BorderStylePatch.from_css() — CSS parser on the patch type
# =============================================================================


def test_border_patch_from_css_sets_fields():
    """from_css('2px solid #abc') → BorderStylePatch(width, color)."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch.from_css("2px solid #abcdef")
    assert p.width == 2.0
    assert p.color == "#abcdef"
    assert p.radius is None


def test_border_patch_from_css_none_returns_empty_patch():
    """from_css(None) returns all-None patch."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch.from_css(None)
    assert isinstance(p, BorderStylePatch)
    assert p.width is None
    assert p.color is None
    assert p.radius is None


def test_border_patch_from_css_never_sets_radius():
    """BorderStylePatch.from_css never sets radius — radius is YAML-only."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch.from_css("2px solid red")
    assert p.radius is None


# =============================================================================
# resolve_style produces BorderStyle (no None fields) on ResolvedStyle
# — uses theme-loaded config so all 4 border fields are populated from YAML
# =============================================================================


def test_resolve_style_border_is_compiled_border():
    """resolve_style().border is BorderStyle — all fields concrete, no None."""
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.primitives import BorderStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    reset_config()
    try:
        rs = resolve_style(get_theme_style())
        assert isinstance(rs.border, BorderStyle)
        assert rs.border.width is not None
        assert rs.border.color is not None
        assert rs.border.radius is not None
    finally:
        reset_config()


def test_resolve_style_charts_callout_border_is_compiled_border():
    """A resolved callout's border is BorderStyle."""
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.primitives import BorderStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    reset_config()
    try:
        rs = resolve_chart_style_context(get_theme_style())
        assert isinstance(rs.callout_error.border, BorderStyle)
    finally:
        reset_config()


def test_all_built_in_themes_resolve_without_assertion_error(compiled_themes):
    """Every production theme must resolve without error."""
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    for _name, style in compiled_themes.items():
        resolve_style(style)


# =============================================================================
# dash_array / line_cap / dash_offset — SVG-native dash primitives.
# No style: solid|dashed|dotted enum (Dave, 2026-05-07) — dash_array unset
# means solid.
# =============================================================================


def test_border_style_dash_fields_default_to_none():
    """dash_array/line_cap/dash_offset default to None (solid border)."""
    from dbt_charts.core.compile.models.primitives import BorderStyle

    b = BorderStyle(width=1, color="#333", radius=0)
    assert b.dash_array is None
    assert b.line_cap is None
    assert b.dash_offset is None


def test_border_style_accepts_dash_fields():
    from dbt_charts.core.compile.models.primitives import BorderStyle

    b = BorderStyle(
        width=1,
        color="#333",
        radius=0,
        dash_array=[4, 4],
        line_cap="round",
        dash_offset=2.0,
    )
    assert b.dash_array == [4, 4]
    assert b.line_cap == "round"
    assert b.dash_offset == 2.0


def test_border_style_rejects_invalid_line_cap():
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.primitives import BorderStyle

    with pytest.raises(ValidationError):
        BorderStyle(width=1, color="#333", radius=0, line_cap="dashed")


def test_border_style_patch_accepts_dash_fields():
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch(dash_array=[8, 4, 1, 4], line_cap="square", dash_offset=1.0)
    assert p.dash_array == [8, 4, 1, 4]
    assert p.line_cap == "square"
    assert p.dash_offset == 1.0


def test_border_style_patch_dash_fields_default_to_none():
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch()
    assert p.dash_array is None
    assert p.line_cap is None
    assert p.dash_offset is None


def test_border_patch_from_css_never_sets_dash_fields():
    """CSS shorthand parsing never sets dash fields — YAML-struct-only, like radius."""
    from dbt_charts.core.compile.models.primitives import BorderStylePatch

    p = BorderStylePatch.from_css("2px solid #abcdef")
    assert p.dash_array is None
    assert p.line_cap is None
    assert p.dash_offset is None


def test_border_shorthand_merge_inherits_dash_fields_from_base():
    """Merging a width-only patch onto a dashed base preserves the base's dash fields."""
    from dbt_charts.core.compile.merge import merge_onto_base
    from dbt_charts.core.compile.models.primitives import BorderStyle, BorderStylePatch

    base = BorderStyle(
        width=3, color="black", radius=8, dash_array=[4, 4], line_cap="round"
    )
    patch = BorderStylePatch(width=1)

    merged = merge_onto_base(base, patch)
    assert merged.width == 1
    assert merged.dash_array == [4, 4]
    assert merged.line_cap == "round"


def test_style_patch_nested_border_struct_with_dash_array():
    """border: {width, color, radius, dash_array, line_cap} via nested dict."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate(
        {
            "border": {
                "width": 1,
                "color": "#ccc",
                "radius": 8,
                "dash_array": [4, 4],
                "line_cap": "round",
            }
        }
    )
    assert patch.border is not None
    assert patch.border.dash_array == [4, 4]
    assert patch.border.line_cap == "round"


def test_resolve_style_border_dash_fields_default_to_none():
    """resolve_style().border has dash fields unset when no theme sets them —
    unlike width/color/radius, these are not required-by-theme."""
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    reset_config()
    try:
        rs = resolve_style(get_theme_style())
        assert rs.border.dash_array is None
    finally:
        reset_config()
