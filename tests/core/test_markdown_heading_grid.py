"""Markdown headings sit on the body line grid and follow the heading typography.

One body line is ``fonts.body.size * fonts.body.line_height``. Heading margins
are whole body lines, each heading line box is one body line, headings are never
smaller than the body, and they follow ``fonts.heading.case``. The shared
``fonts.heading.sizes`` ramp is untouched: chart titles read it.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.markdown_style import (
    compact_style_kwargs,
    style_prose_headings,
)
from mdsvg import parse
from mdsvg.types import Heading, Paragraph, SpanType


def _kwargs(
    heading_margin_scale: Literal["prose", "chrome"] = "prose",
    h1_size: float | None = None,
) -> dict[str, Any]:
    return compact_style_kwargs(
        resolve_style(get_theme_style("clarity")),
        heading_margin_scale=heading_margin_scale,
        h1_size=h1_size,
    )


def _body_line() -> float:
    rs = resolve_style(get_theme_style("clarity"))
    return float(rs.text.font.size) * float(rs.text.font.line_height)


def test_prose_heading_margins_are_whole_body_lines() -> None:
    kw = _kwargs()
    line = _body_line()
    assert kw["heading_margin_top_px"] == pytest.approx(2 * line)
    assert kw["minor_heading_margin_top_px"] == pytest.approx(line)
    assert kw["heading_margin_bottom_px"] == 0.0
    assert kw["h1_margin_bottom_px"] == pytest.approx(line)
    assert kw["heading_grid_px"] == pytest.approx(line)


def test_no_prose_heading_is_smaller_than_the_body() -> None:
    kw = _kwargs()
    body = kw["base_font_size"]
    sizes = [kw[f"h{n}_size"] for n in range(1, 7)]
    assert all(size >= body for size in sizes)
    # H1 reads the heading ramp, H2/H3 the section ramp, H4-H6 are body size.
    heading_ramp = get_theme_style("clarity").heading_ramp()
    section_ramp = get_theme_style("clarity").section_ramp()
    assert sizes == [
        float(heading_ramp[0]),
        float(section_ramp[0]),
        float(section_ramp[1]),
        body,
        body,
        body,
    ]


def test_the_prose_mapping_leaves_the_shared_heading_ramp_alone() -> None:
    rs = resolve_style(get_theme_style("clarity"))
    compact_style_kwargs(rs)
    assert list(rs.chart_defaults.heading_sizes) == list(
        get_theme_style("clarity").heading_ramp()
    )


def test_board_titles_keep_chrome_spacing_and_the_unclamped_ramp() -> None:
    kw = _kwargs(heading_margin_scale="chrome", h1_size=30.0)
    rs = resolve_style(get_theme_style("clarity"))
    chrome_line = float(rs.font.size) * float(rs.font.line_height)
    assert kw["heading_margin_bottom_px"] == pytest.approx(chrome_line)
    assert kw["minor_heading_margin_top_px"] is None
    assert kw["h1_margin_bottom_px"] is None
    assert kw["heading_grid_px"] is None
    assert kw["h6_size"] == float(rs.chart_defaults.heading_sizes[5])


def test_headings_follow_the_heading_case() -> None:
    rs = resolve_style(get_theme_style("clarity"))
    assert rs.title.font.case == "title"
    blocks = style_prose_headings(
        list(parse("## the quick brown fox\n\nleave this alone")), rs
    )
    heading, paragraph = blocks
    assert isinstance(heading, Heading) and isinstance(paragraph, Paragraph)
    assert "".join(s.text for s in heading.spans) == "The Quick Brown Fox"
    assert paragraph.spans[0].text == "leave this alone"


@pytest.mark.parametrize(
    ("level", "bold"), [(1, False), (2, False), (3, False), (4, True), (6, True)]
)
def test_body_size_headings_use_the_bold_weight(level: int, bold: bool) -> None:
    rs = resolve_style(get_theme_style("clarity"))
    (heading,) = style_prose_headings(list(parse(f"{'#' * level} Heading")), rs)
    assert isinstance(heading, Heading)
    assert all((s.span_type is SpanType.BOLD) is bold for s in heading.spans)


@pytest.mark.parametrize(("level", "lines"), [(1, 2), (2, 2), (3, 1)])
def test_heading_hugs_the_body_line_below_it(level: int, lines: int) -> None:
    from dbt_charts.core.compile.resolve.style.markdown_style import get_compact_style
    from dbt_charts.core.font_measure import markdown_font_faces
    from mdsvg.renderer import SVGRenderer

    rs = resolve_style(get_theme_style("clarity"))
    style = get_compact_style(rs)
    renderer = SVGRenderer(
        style=style, fonts=markdown_font_faces(style.font_family, style)
    )
    line = _body_line()
    blocks = list(parse(f"{'#' * level} Heading\n\nBody."))
    heading_baseline = renderer.first_baseline(blocks, 400)
    body_baseline = renderer.first_baseline(blocks[1:], 400)
    block_height = renderer.measure(blocks[:1], width=400, padding=0).height
    below = style.h1_margin_bottom_px if level == 1 else style.heading_margin_bottom_px
    assert below is not None
    assert block_height - below == pytest.approx(lines * line)
    assert block_height + body_baseline - heading_baseline == pytest.approx(
        line + below
    )


def test_a_section_ramp_too_short_for_prose_headings_is_a_clear_error() -> None:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    patch = StylePatch.model_validate({"fonts": {"section": {"sizes": [20]}}})
    rs = resolve_style(get_theme_style("clarity"), patch)

    with pytest.raises(ValueError, match="style.fonts.section.sizes"):
        compact_style_kwargs(rs)


def _patched(patch: dict[str, Any]) -> Any:
    from dbt_charts.core.compile.models.style.authored import StylePatch

    return resolve_style(get_theme_style("clarity"), StylePatch.model_validate(patch))


def test_no_prose_heading_is_smaller_than_a_larger_body() -> None:
    kw = compact_style_kwargs(_patched({"text": {"font": {"size": 20}}}))
    theme = get_theme_style("clarity")

    assert [kw[f"h{n}_size"] for n in range(1, 7)] == [
        float(theme.heading_ramp()[0]),
        float(theme.section_ramp()[0]),
        20.0,
        20.0,
        20.0,
        20.0,
    ]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("upper", "THE QUICK BROWN FOX"),
        ("lower", "the quick brown fox"),
        ("none", "the Quick brown fox"),
    ],
)
def test_heading_case_override_reaches_markdown_headings(
    case: str, expected: str
) -> None:
    rs = _patched({"fonts": {"heading": {"case": case}}})
    (heading,) = style_prose_headings(list(parse("## the Quick brown fox")), rs)

    assert isinstance(heading, Heading)
    assert "".join(s.text for s in heading.spans) == expected


def test_section_ramp_override_reaches_h2_and_h3() -> None:
    kw = compact_style_kwargs(
        _patched({"fonts": {"section": {"sizes": [30, 26, 16, 12, 11]}}})
    )

    assert (kw["h2_size"], kw["h3_size"]) == (30.0, 26.0)
