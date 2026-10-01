"""Tests for Noto Emoji appearing in resolved SVG output from the render pipeline.

Guards that:
1. The resolved font-family stacks emitted by the render pipeline include Noto Emoji.
2. Rendered SVG output contains Noto Emoji in font-family attributes on text elements —
   the attribute vl-convert reads for per-character font shaping.
"""

from __future__ import annotations

import dataclasses
import html
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY
from dbt_charts.core.project import Project

from .._prose_plan import full_width_plan

_QUOTED = f"'{NOTO_EMOJI_FONT_FAMILY}'"

_EMOJI_YAML = """
title: "📈 Revenue trend"
queries:
  q1:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  c1:
    query: q1
    type: bar
    x: month
    y: revenue
rows:
  - c1
"""


def setup_function() -> None:
    reset_config()


def teardown_function() -> None:
    reset_config()


class TestSVGFontFamilyContainsNotoEmoji:
    def test_default_theme_root_font(self) -> None:
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        # font_family is what vl-convert uses as the VL `font` property
        assert ctx.font_family is not None
        assert _QUOTED in ctx.font_family

    def test_editorial_cream_title_font(self) -> None:
        compiled = get_theme_style("paper")
        resolved = resolve_style(compiled)
        family = resolved.title.font.family
        assert family is not None
        assert _QUOTED in family

    def test_editorial_cream_primary_wins(self) -> None:
        compiled = get_theme_style("paper")
        resolved = resolve_style(compiled)
        family = resolved.title.font.family
        assert family is not None
        assert family.index("Source Serif 4") < family.index(NOTO_EMOJI_FONT_FAMILY)

    def test_dbt_sans_tabular_stack_also_has_noto_emoji(self) -> None:
        """dbt Sans Tabular font stacks (kpi, axis labels) should also include Noto Emoji."""
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        # KPI font is explicitly dbt Sans Tabular — verify Noto Emoji appended
        kpi_family = ctx.kpi.font.family
        assert kpi_family is not None
        assert _QUOTED in kpi_family

    def test_charts_font_family_propagates_to_vl_font(self) -> None:
        """The font_family that would be emitted as VL `font` includes Noto Emoji."""
        compiled = get_theme_style()
        ctx = resolve_chart_style_context(compiled)
        font_family = ctx.font_family
        assert font_family is not None
        assert _QUOTED in font_family


class TestRenderedSVGContainsNotoEmoji:
    """Guard that the full render pipeline emits Noto Emoji in SVG font-family
    attributes — the attribute vl-convert reads for per-character font shaping."""

    def test_body_text_renderer_applies_emoji_family_to_default_markdown_stack(
        self,
    ) -> None:
        from dbt_charts.core.render.boards import _render_text_svg

        compiled = get_theme_style()
        resolved = resolve_style(compiled)
        font = resolved.text.font.model_copy(
            update={
                "family": "'Inter Variable', Inter, system-ui, sans-serif",
            }
        )
        assert font.family is not None
        assert _QUOTED not in font.family
        text_style = resolved.text.model_copy(update={"font": font})
        resolved_without_emoji_family = dataclasses.replace(resolved, text=text_style)

        svg, _height = _render_text_svg(
            "Emoji body 😀",
            {},
            400.0,
            resolved_without_emoji_family,
            text_style=resolved_without_emoji_family.text,
            painted_canvas=None,
            plan=full_width_plan(resolved_without_emoji_family, 400.0),
        )

        body_rule = re.search(
            r"\.md-[0-9a-f]{8}-text \{ font-family: ((?:&#?\w+;|[^;])+);", svg
        )
        assert body_rule is not None
        assert _QUOTED in html.unescape(body_rule.group(1))

    @pytest.mark.windows
    def test_rendered_svg_font_family_contains_noto_emoji(
        self, local_project: Callable[..., Project]
    ) -> None:
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import render

        result = compile(_EMOJI_YAML)
        assert result.success and result.board is not None, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg_out = render(result.board, executor, format="svg").output
        assert isinstance(svg_out, str)
        # Verify Noto Emoji appears in an actual font-family="..." attribute, not only
        # in the <style> block — vl-convert reads attributes for per-character shaping.
        families = re.findall(r'font-family="([^"]+)"', svg_out)
        assert any(NOTO_EMOJI_FONT_FAMILY in f for f in families), (
            "Expected 'Noto Emoji' in a font-family=\"...\" attribute on an SVG text element; "
            "only found it in the <style> block."
        )
