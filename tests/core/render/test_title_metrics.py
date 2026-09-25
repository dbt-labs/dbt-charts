"""What the renderer reports about a title, against the title it actually drew."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.sizing import (
    get_theme_style,
    get_title_height,
    title_baseline_offset,
    title_line_box,
)
from dbt_charts.core.render.svg_utils import render_title

_WIDE = 800.0


def _drawn_baseline(level: int) -> float:
    """The ``y`` the title's heading is actually painted at, for ``level``."""
    style = resolve_style(get_theme_style())
    svg = render_title("Board Title", _WIDE, level=level, resolved_style=style)
    match = re.search(r'<text[^>]*\by="([\d.]+)"', svg)
    assert match, svg
    return float(match.group(1))


# Auto level is unbounded — `parent_level + 1` per titled ancestor — so 7 nested
# titled boards reach past the six-rung heading ramp. `board_title_spec` clamps
# there and the ramp does not, which is where the two answers used to part.
@pytest.mark.parametrize("level", range(1, 9))
def test_the_reported_baseline_is_the_one_the_title_was_drawn_on(level: int) -> None:
    style = resolve_style(get_theme_style())

    assert title_baseline_offset(style, level) == pytest.approx(
        _drawn_baseline(level), abs=0.01
    ), f"level {level}: reported baseline is not where the heading was painted"


@pytest.mark.parametrize("level", range(1, 9))
def test_the_line_box_holds_the_baseline_it_was_drawn_with(level: int) -> None:
    """The span and the baseline have to describe one heading, at every level.

    Containment is the whole claim: a span that drifted off the heading would
    still sit inside the block, and one that swallowed the margins would still
    hold the baseline — only both together say it is the text's own line box.
    """
    style = resolve_style(get_theme_style())
    block = get_title_height("Board Title", _WIDE, None, level, resolved_style=style)
    top, height = title_line_box(block, level, style)
    baseline = _drawn_baseline(level)

    assert top < baseline < top + height, (
        f"level {level}: baseline {baseline:.2f} outside the line box "
        f"{top:.2f}..{top + height:.2f}"
    )
