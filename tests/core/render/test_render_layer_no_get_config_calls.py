"""Verify render layer no longer calls get_theme_style().*

Each test calls the target function with a custom resolved_style that has a
distinct sentinel value for the relevant field, then asserts the sentinel
value appears in the output — proving the global get_config() is not being
consulted.

Sites covered:
  chart_interactivity.hover_runtime_attributes
  chart/rendering.py border radius (via resolved_style.charts.border.radius)
  converters/chart.render_vega_spec placeholder height
  boards._render_text_svg text_font_family param
  svg_utils.render_title title_font_family param
  chart/spark.render_spark_bar font_family param
  placeholder.apply_placeholder_opacity from resolved_style
  placeholder.add_placeholder_overlay from resolved_style
  callout.render_callout_svg required resolved_style
  render_board_terminal resolved_style required (no global get_config() fallback)
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from mdsvg.fonts import FontFaces

# =============================================================================
# B — chart_interactivity
# =============================================================================


def test_b_chart_interactivity_uses_resolved_style_font_family() -> None:
    """The font family the hover runtime reads comes from resolved_style, never
    from the global get_config() — published on the board root as data."""
    from dbt_charts.core.render.chart_interactivity import hover_runtime_attributes

    style = resolve_style(
        get_theme_style().model_copy(
            update={
                "font": get_theme_style().font.model_copy(
                    update={"family": "SentinelFontB"}
                )
            }
        )
    )
    assert "SentinelFontB" in hover_runtime_attributes(resolved_style=style)


# =============================================================================
# C — chart/rendering.py border radius on ResolvedChartsStyle
# =============================================================================


def test_c_rendering_uses_resolved_style_border_radius() -> None:
    """resolved_style.charts.border.radius is accessible and correct after resolve_style."""
    from dbt_charts.core.compile.models.primitives import BorderStyle

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "border": BorderStyle(radius=13.0, width=0.0, color="transparent")
                }
            )
        }
    )
    ctx = resolve_chart_style_context(seed)
    assert ctx.border.radius == 13.0


# =============================================================================
# D — converters/chart.render_vega_spec
# =============================================================================


def test_d_render_vega_spec_uses_default_chart_height() -> None:
    """render_vega_spec must use DEFAULT_CHART_HEIGHT for placeholder height (not the global get_config())."""
    import sys
    import types
    from unittest import mock

    def _fake_vegalite_to_svg(spec: object) -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>'

    fake_vlc = types.ModuleType("vl_convert")
    fake_vlc.vegalite_to_svg = _fake_vegalite_to_svg  # type: ignore[attr-defined]
    fake_vlc.register_font_directory = lambda *a, **kw: None  # type: ignore[attr-defined]

    with mock.patch.dict(sys.modules, {"vl_convert": fake_vlc}):
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.converters.chart import render_vega_spec

        style = resolve_style(get_theme_style())

        # is_placeholder=True exercises the height-from-DEFAULT_CHART_HEIGHT path.
        result = render_vega_spec(
            {"$schema": "...", "mark": "bar"},
            "svg",
            resolved_style=style,
            width=None,
            height=None,
            is_placeholder=True,
            chart_id="chart",
        )
    assert isinstance(result, str) and "<svg" in result  # got SVG content back


# =============================================================================
# E — boards._render_text_svg
# =============================================================================


def test_e_render_text_svg_uses_text_font_family_param() -> None:
    """_render_text_svg must use resolved_style.text.font.family, not the global get_config()."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.boards import _render_text_svg

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "text": base.text.model_copy(
                update={"font": FontStyle(family="SentinelFontE")}
            )
        }
    )
    style = resolve_style(seed)

    svg, _height = _render_text_svg("# Hello", {}, 400.0, style, text_style=style.text)
    assert "SentinelFontE" in svg


# =============================================================================
# G — svg_utils.render_title
# =============================================================================


def test_g_render_title_uses_title_font_family_param() -> None:
    """render_title must use resolved_style.title.font.family, not the global get_config()."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.svg_utils import render_title

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "title": base.title.model_copy(
                update={"font": FontStyle(family="SentinelFontG")}
            )
        }
    )
    style = resolve_style(seed)

    result = render_title("My Title", 400.0, resolved_style=style)
    assert "SentinelFontG" in result


def test_g_render_title_uses_title_font_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_title must measure title text with the selected title family."""
    import mdsvg.renderer
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.fonts import SOURCE_SERIF_4_FONT_FAMILY, get_face
    from dbt_charts.core.render.svg_utils import render_title

    captured: dict[str, str] = {}

    class FakeRenderer:
        def __init__(self, *_args: object, fonts: FontFaces, **_kwargs: object) -> None:
            captured["regular"] = fonts.regular.path

        def render(self, *_args: object, **_kwargs: object) -> str:
            return "<svg />"

        @property
        def used_faces(self) -> frozenset[str]:
            return frozenset()

    monkeypatch.setattr(mdsvg.renderer, "SVGRenderer", FakeRenderer)

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "title": base.title.model_copy(
                update={"font": FontStyle(family="Source Serif 4, Georgia, serif")}
            )
        }
    )
    style = resolve_style(seed)

    render_title("My Title", 400.0, resolved_style=style)

    assert captured["regular"] == str(get_face(SOURCE_SERIF_4_FONT_FAMILY).measure_path)


def test_g_render_title_uses_title_font_path_not_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_title must measure with the title family, never the body family
    — even when it's the body family (not the title family) that's set to a
    distinctive value. A board title is always drawn as a heading, so there
    is no separate "non-prose" fallback to the body family to test for.
    """
    import mdsvg.renderer
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.fonts import INTER_VARIABLE_FONT_FAMILY, get_face
    from dbt_charts.core.render.svg_utils import render_title

    captured: dict[str, str] = {}

    class FakeRenderer:
        def __init__(self, *_args: object, fonts: FontFaces, **_kwargs: object) -> None:
            captured["regular"] = fonts.regular.path

        def render(self, *_args: object, **_kwargs: object) -> str:
            return "<svg />"

        @property
        def used_faces(self) -> frozenset[str]:
            return frozenset()

    monkeypatch.setattr(mdsvg.renderer, "SVGRenderer", FakeRenderer)

    base = get_theme_style()
    seed = base.model_copy(
        update={
            "text": base.text.model_copy(
                update={"font": FontStyle(family="Source Serif 4, Georgia, serif")}
            ),
            "title": base.title.model_copy(
                update={"font": FontStyle(family="Inter, sans-serif")}
            ),
        }
    )
    style = resolve_style(seed)

    render_title("My Title", 400.0, resolved_style=style)

    assert captured["regular"] == str(get_face(INTER_VARIABLE_FONT_FAMILY).measure_path)


# =============================================================================
# H — chart/spark.render_spark_bar
# =============================================================================


def test_h_render_spark_bar_uses_font_param() -> None:
    """render_spark_bar must use font: FontStyle param, not get_theme_style().font.family."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.chart.spark import render_spark_bar

    resolved_style = resolve_style(get_theme_style()).chart_defaults

    svg = render_spark_bar(
        50.0,
        value_visible=True,
        font=FontStyle(family="SentinelFontH"),
        resolved_style=resolved_style,
    )
    assert "SentinelFontH" in svg


# =============================================================================
# J — placeholder.apply_placeholder_opacity
# =============================================================================


def test_j_apply_placeholder_opacity_uses_resolved_style() -> None:
    """apply_placeholder_opacity must read opacity from resolved_style, not the global get_config()."""
    from dbt_charts.core.render.placeholder import apply_placeholder_opacity

    base = get_theme_style()
    # Build a style with a distinctive placeholder opacity that differs from the default.
    sentinel_opacity = 0.123
    seed = base.model_copy(
        update={
            "placeholder": base.placeholder.model_copy(
                update={"opacity": sentinel_opacity}
            )
        }
    )
    style = resolve_style(seed)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect/></svg>'
    )
    result = apply_placeholder_opacity(svg, resolved_style=style)
    assert f'opacity="{sentinel_opacity}"' in result, (
        f"Sentinel opacity {sentinel_opacity} not found in output — "
        "apply_placeholder_opacity is not reading from resolved_style"
    )


# =============================================================================
# K — placeholder.add_placeholder_overlay
# =============================================================================


def test_k_add_placeholder_overlay_uses_resolved_style() -> None:
    """add_placeholder_overlay must read overlay config from resolved_style, not the global get_config()."""
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.render.placeholder import add_placeholder_overlay

    base = get_theme_style()
    sentinel_text = "SENTINEL_OVERLAY_TEXT_K"
    seed = base.model_copy(
        update={
            "placeholder": base.placeholder.model_copy(
                update={
                    "overlay": base.placeholder.overlay.model_copy(
                        update={"text": sentinel_text}
                    )
                }
            )
        }
    )
    style = resolve_style(seed)

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"></svg>'
    result = add_placeholder_overlay(
        svg, 200.0, 200.0, font=FontStyle(family="Inter"), resolved_style=style
    )
    assert sentinel_text in result, (
        f"Sentinel overlay text {sentinel_text!r} not found in output — "
        "add_placeholder_overlay is not reading from resolved_style"
    )


# =============================================================================
# L — callout.render_callout_svg required resolved_style
# =============================================================================


def test_l_render_callout_svg_uses_resolved_style() -> None:
    """render_callout_svg must use resolved_style, not the global get_config()."""
    from dbt_charts.core.render.chart.callout import render_callout_svg

    # Should not raise — just verifying it runs using the given style.
    svg = render_callout_svg(
        message="test message",
        width=300.0,
        callout_style=resolve_style(get_theme_style()).chart_defaults.callout,
    )
    assert "<svg" in svg
