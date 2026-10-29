"""A root board title is drawn exactly as a leading ``# H1`` with the same words.

Both sit on the body line grid, so title baseline to first body baseline is two
body lines (one blank line), and titles in one row share a baseline whatever
their sizes.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style

from ._prose_heading_board import body_line, render_board_svg_text
from ._svg_texts import SvgText, svg_texts


def _find(texts: list[SvgText], words: str) -> SvgText:
    return next(t for t in texts if t.text.lower() == words.lower())


def test_title_key_and_leading_h1_draw_identically() -> None:
    via_title = svg_texts(
        render_board_svg_text("title: Same Words\ntext: |\n  Body paragraph.\n")
    )
    via_h1 = svg_texts(
        render_board_svg_text("text: |\n  # Same Words\n\n  Body paragraph.\n")
    )

    title, h1 = _find(via_title, "Same Words"), _find(via_h1, "Same Words")
    body_after_title = _find(via_title, "Body paragraph.")
    body_after_h1 = _find(via_h1, "Body paragraph.")

    assert title.y == pytest.approx(h1.y, abs=0.01)
    assert title.font_size == h1.font_size
    # Group origins are snapped to whole pixels, so the title's body can land
    # up to half a pixel off the grid the H1's does not leave.
    assert body_after_title.y - title.y == pytest.approx(2 * body_line(), abs=0.5)
    assert body_after_h1.y - h1.y == pytest.approx(2 * body_line(), abs=0.01)


_ROW = """
queries:
  q:
    columns: [month, revenue]
    values:
      - ["2026-01-01", 100]
      - ["2026-02-01", 150]
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue by month
rows:
  - cols:
      - text: "{heading} Key takeaways\\n\\nNorth region is leading."
      - bar1
"""


@pytest.mark.parametrize("heading", ["#", "##", "###"])
def test_a_text_card_heading_shares_a_baseline_with_the_chart_title_beside_it(
    heading: str,
) -> None:
    texts = svg_texts(render_board_svg_text(_ROW.format(heading=heading)))
    heading = _find(texts, "Key Takeaways")
    chart_title = _find(texts, "Revenue by month")

    assert heading.y == pytest.approx(chart_title.y, abs=0.01)


_TALL_TEXT_CARD_BESIDE_PADDED_NEIGHBOR = """
queries:
  q:
    columns: [m, v]
    values:
      - ["a", 1]
      - ["b", 2]
charts:
  c1:
    query: q
    type: bar
    x: m
    y: v
rows:
  - cols:
      - text: "### Small heading\\n\\n{prose}"
      - title: Big Section Title
        style:
          frame:
            card_padding: 36
        rows:
          - c1
"""


def _prose_extent(yaml_text: str) -> tuple[float, float]:
    """The board's height and the baseline of the last line of prose in it."""
    svg = render_board_svg_text(yaml_text)
    match = re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg)
    assert match is not None
    height = float(match.group(1))
    prose = [
        t for t in svg_texts(svg) if t.text.startswith(("Long prose", "prose line"))
    ]
    return height, max(t.y for t in prose)


def test_a_shifted_text_card_reserves_the_shift_in_its_height() -> None:
    """The tallest card of a row, pushed down onto its neighbor's baseline, grows
    by the push: its last line of prose stays inside the card."""
    text = _TALL_TEXT_CARD_BESIDE_PADDED_NEIGHBOR.format(
        prose="Long prose line. " * 150
    )
    level_height, level_last = _prose_extent(
        text.replace(
            "        style:\n          frame:\n            card_padding: 36\n", ""
        )
    )
    pushed_height, pushed_last = _prose_extent(text)

    assert pushed_last - level_last == pytest.approx(20.0, abs=0.5)
    assert pushed_height - level_height >= pushed_last - level_last - 0.01


_WIDE_CELL_ROW = """
queries:
  q:
    columns: [month, revenue]
    values:
      - ["2026-01-01", 100]
      - ["2026-02-01", 150]
charts:
  bar1:
    query: q
    type: bar
    x: month
    y: revenue
    title: Revenue by month
rows:
  - cols:
      - width: 70%
        text: "## {heading}\\n\\nNorth region is leading."
      - bar1
"""


def test_a_wide_text_cell_heading_shares_the_baseline_it_is_drawn_on() -> None:
    """The prose wraps at its readable measure, not at the slot it sits in, so the
    band has to be measured at the measure or it drags the neighbor's title off."""
    heading = "A deliberately long heading that wraps at the readable measure " * 2
    texts = svg_texts(render_board_svg_text(_WIDE_CELL_ROW.format(heading=heading)))
    h2_size = float(resolve_style(get_theme_style()).chart_defaults.section_sizes[0])
    first = next(t for t in texts if t.font_size == h2_size)
    chart_title = _find(texts, "Revenue by month")

    assert first.y == pytest.approx(chart_title.y, abs=0.01)


_LOCKED_LEVEL_SECTION = """
queries:
  q:
    columns: [m, v]
    values:
      - ["a", 1]
      - ["b", 2]
charts:
  c1:
    query: q
    type: bar
    x: m
    y: v
style:
  title:
    level: 1
  text:
    font:
      size: {body_size}
title: Root title
rows:
  - title: Section title
    style:
      title:
        level: 1
    rows:
      - c1
"""


def test_a_section_title_keeps_its_chrome_spacing_under_a_locked_title_level() -> None:
    """``style.title.level: 1`` makes every title level 1; only the root title is
    set on the body grid, so the space a section title takes must not follow the
    body size."""

    def section_title_to_chart(body_size: int) -> float:
        texts = svg_texts(
            render_board_svg_text(_LOCKED_LEVEL_SECTION.format(body_size=body_size))
        )
        section = _find(texts, "Section Title")
        axis_label = next(t for t in texts if t.text == "a" and t.y > section.y)
        return axis_label.y - section.y

    assert section_title_to_chart(14) == pytest.approx(
        section_title_to_chart(20), abs=0.01
    )
