"""Tests for the Inter default typography rollout."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageChops

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.chart_interactivity import (
    hover_runtime_attributes,
    hover_runtime_source,
)
from dbt_charts.core.render.converters.pdf import to_pdf
from dbt_charts.core.render.converters.png import to_png
from dbt_charts.core.render.placeholder import add_placeholder_overlay

from .._run import Run

TEST_YAML = """
title: Test Dashboard
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
    """Reset config before each test."""
    reset_config()


def teardown_function() -> None:
    """Reset config after each test."""
    reset_config()


def _render_svg(
    local_project: Callable[..., FilesystemProject], yaml_content: str = TEST_YAML
) -> str:
    result = compile(yaml_content)
    assert result.success
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    return render(result.board, executor.ctx, executor.ledger, format="svg").output


_FONT_FAMILY = "'Inter Variable', Inter, system-ui, sans-serif"

_FONT_FAMILY_PRIMARY = _FONT_FAMILY.split(",")[0].strip()


def test_default_font_family_is_inter() -> None:
    assert get_theme_style().font.family.startswith("'Inter Variable'")
    # The resolved VL config has Noto Emoji inserted after the primary font.
    vl = resolve_style(get_theme_style("stark")).vega_config
    assert vl.get("font", "").startswith(_FONT_FAMILY_PRIMARY)


def test_rendered_chart_svg_uses_inter(
    local_project: Callable[..., FilesystemProject],
) -> None:
    svg_output = _render_svg(local_project)
    assert f'font-family="{_FONT_FAMILY_PRIMARY},' in svg_output


def test_svg_timestamp_uses_inter_and_tabular_figures(
    local_project: Callable[..., FilesystemProject],
) -> None:
    svg_output = _render_svg(local_project)
    assert f'font-family="{_FONT_FAMILY_PRIMARY},' in svg_output
    assert "font-variant-numeric: tabular-nums lining-nums;" in svg_output


def test_placeholder_overlay_uses_inter() -> None:
    from dbt_charts.core.compile.models.primitives import FontStyle

    svg = '<svg width="200" height="100"></svg>'
    _style = resolve_style(get_theme_style())
    result = add_placeholder_overlay(svg, 200, 100, FontStyle(family="Inter"), _style)
    assert 'font-family="Inter"' in result


def test_chart_tooltip_script_uses_inter_and_tabular_values() -> None:
    import html

    from dbt_charts.core.compile.resolve.style.board import resolve_style

    attrs = html.unescape(
        hover_runtime_attributes(resolved_style=resolve_style(get_theme_style()))
    )
    script = hover_runtime_source()
    assert f'data-dbt-font-family="{_FONT_FAMILY_PRIMARY},' in attrs
    assert "tooltip.style.fontFamily = active.fontFamily;" in script
    assert "font-variant-numeric:tabular-nums lining-nums;" in script


def test_svg_exports_to_pdf_and_png_with_inter() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="50">'
        '<text x="10" y="30" font-family="Inter" font-size="20">12345</text>'
        "</svg>"
    )
    pdf = to_pdf(svg)
    png = to_png(svg)
    assert png[:4] == b"\x89PNG", f"Expected PNG magic bytes, got {png[:4]!r}"
    assert pdf[:4] == b"%PDF", f"Expected PDF magic bytes, got {pdf[:4]!r}"


def test_png_export_renders_visible_text_for_inter_via_vl_convert_boundary_fix() -> (
    None
):
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="60">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<text x="10" y="38" font-family="Inter" font-size="24">Visible</text>'
        "</svg>"
    )

    png = to_png(svg)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    blank = Image.new("RGB", image.size, (255, 255, 255))
    non_white_pixels = ImageChops.difference(image, blank).getbbox()

    assert non_white_pixels is not None
