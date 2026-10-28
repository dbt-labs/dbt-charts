"""Tests for placeholder config living under style.placeholder.

Placeholder is a visual/authorable style (overlay text, colors, font, opacity),
so it lives in the theme under `style.placeholder` — not as a top-level engine
config block. Color nests inside `overlay.font.color`.
"""

from dbt_charts.core.compile.config import (
    get_config,
    get_theme_style,
    reset_config,
)


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


def test_placeholder_lives_under_style():
    """Placeholder config is reachable at get_theme_style().placeholder."""
    ph = get_theme_style().placeholder
    assert 0 < ph.opacity < 1
    assert ph.overlay.text is not None
    assert len(ph.overlay.text) > 0


def test_placeholder_color_nests_inside_overlay_font():
    """overlay.font carries color/size/weight — color stays inside font."""
    font = get_theme_style().placeholder.overlay.font
    assert font.color is not None
    assert font.size is not None and font.size > 0
    assert font.weight is not None and font.weight > 0


def test_placeholder_not_at_top_level():
    """Top-level config.placeholder is gone — single home under style.placeholder."""
    cfg = get_config()
    assert "placeholder" not in dict(cfg)


def test_get_placeholder_config_getter_is_removed():
    """The top-level narrow getter was dropped — callers read style.placeholder."""
    from dbt_charts.core.compile import config as config_module

    assert not hasattr(config_module, "get_placeholder_config")


def test_add_placeholder_overlay_uses_style_placeholder():
    """Renderer reads style.placeholder.overlay.font.color, not the muted tone."""
    import dataclasses

    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.placeholder import add_placeholder_overlay

    svg = '<svg width="100" height="100"><rect width="100" height="100"/></svg>'
    style = resolve_style(get_theme_style())
    overlay = style.placeholder.overlay
    pinned_font = overlay.font.model_copy(update={"color": "#123456"})
    pinned = style.placeholder.model_copy(
        update={"overlay": overlay.model_copy(update={"font": pinned_font})}
    )
    style = dataclasses.replace(style, placeholder=pinned)
    result = add_placeholder_overlay(
        svg,
        width=100,
        height=100,
        font=FontStyle(family="sans-serif"),
        resolved_style=style,
    )
    assert "add data" in result
    assert "#123456" in result
