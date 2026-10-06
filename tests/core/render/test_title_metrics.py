"""What the renderer reports about a title, against the title it actually drew."""

from __future__ import annotations

import base64
import dataclasses
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbt_charts.core.compile.image_assets import image_scope
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.compile.resolve.style.markdown_style import title_blocks
from dbt_charts.core.render.sizing import (
    get_theme_style,
    get_title_height,
    title_baseline_offset,
    title_line_box,
)
from dbt_charts.core.render.svg_utils import render_title
from mdsvg.types import Heading, SpanType

from ...conftest import InMemoryProject

_WIDE = 800.0
_TALL = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="120"></svg>'
_CHARTS = "charts"


@pytest.fixture(autouse=True)
def _project() -> Iterator[None]:
    with image_scope(InMemoryProject(Path("/proj"), {"charts/tall.svg": _TALL})):
        yield


_LOGO = base64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">'
    b'<rect width="80" height="80"/></svg>'
).decode()
# An inline image taller than the heading's text moves its baseline down; a
# text-only title cannot tell a real measurement from a fixed heading metric.
TITLES = [
    "Board Title",
    f"![logo](data:image/svg+xml;base64,{_LOGO}) Board Title",
]


def _drawn_baseline(level: int, title: str) -> float:
    """The ``y`` the title's heading is actually painted at, for ``level``."""
    style = resolve_style(get_theme_style())
    svg = render_title(
        title,
        _WIDE,
        level=level,
        resolved_style=style,
        variable_values={},
        base_dir=None,
        error_collector=None,
    )
    match = re.search(r'<text[^>]*\by="([\d.]+)"', svg)
    assert match, svg
    return float(match.group(1))


# Auto level is unbounded — `parent_level + 1` per titled ancestor — so 7 nested
# titled boards reach past the six-rung heading ramp. `board_title_spec` clamps
# there and the ramp does not, which is where the two answers used to part.
@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("level", range(1, 9))
def test_the_reported_baseline_is_the_one_the_title_was_drawn_on(
    level: int, title: str
) -> None:
    style = resolve_style(get_theme_style())

    reported = title_baseline_offset(title, _WIDE, {}, style, level, None)
    assert reported == pytest.approx(_drawn_baseline(level, title), abs=0.01), (
        f"level {level}: reported baseline is not where the heading was painted"
    )


@pytest.mark.parametrize("title", TITLES)
@pytest.mark.parametrize("level", range(1, 9))
def test_the_line_box_holds_the_baseline_it_was_drawn_with(
    level: int, title: str
) -> None:
    """The span and the baseline have to describe one heading, at every level.

    Containment is the whole claim: a span that drifted off the heading would
    still sit inside the block, and one that swallowed the margins would still
    hold the baseline — only both together say it is the text's own line box.
    """
    style = resolve_style(get_theme_style())
    block = get_title_height(
        title, _WIDE, None, level, resolved_style=style, base_dir=None
    )
    top, height = title_line_box(block, level, style)
    baseline = _drawn_baseline(level, title)

    assert top < baseline < top + height, (
        f"level {level}: baseline {baseline:.2f} outside the line box "
        f"{top:.2f}..{top + height:.2f}"
    )


def _painted(title: str, style: ResolvedStyle) -> str:
    return render_title(
        title,
        _WIDE,
        resolved_style=style,
        variable_values={},
        base_dir=_CHARTS,
        error_collector=None,
    )


def test_a_title_with_an_image_reserves_the_height_it_paints() -> None:
    style = resolve_style(get_theme_style())
    title = "![logo](tall.svg) Sales"

    reserved = get_title_height(
        title, _WIDE, {}, resolved_style=style, base_dir=_CHARTS
    )
    svg = _painted(title, style)

    painted = re.search(r'<svg[^>]* height="([\d.]+)"', svg)
    assert painted, svg
    assert "data:image/svg+xml;base64," in svg
    assert reserved == pytest.approx(float(painted.group(1)), abs=0.01)


def test_the_reported_baseline_follows_an_inline_image_down() -> None:
    """An inline image taller than the text grows the title's first line
    upward, so its baseline sits lower -- the controls beside it follow."""
    style = resolve_style(get_theme_style())
    title = "![logo](tall.svg) Board Title"

    reported = title_baseline_offset(title, _WIDE, {}, style, 1, _CHARTS)
    drawn = re.search(r'<text[^>]*\by="([\d.]+)"', _painted(title, style))

    assert drawn
    assert reported == pytest.approx(float(drawn.group(1)), abs=0.01)
    assert reported > _drawn_baseline(1, "Board Title")


def _cased(case: str) -> ResolvedStyle:
    style = resolve_style(get_theme_style())
    font = style.title.font.model_copy(update={"case": case})
    return dataclasses.replace(
        style, title=style.title.model_copy(update={"font": font})
    )


def _cased_words(title: str, case: str) -> list[str]:
    (heading,) = title_blocks(title, {}, 1, _cased(case), _CHARTS, None)
    assert isinstance(heading, Heading)
    return [s.text for s in heading.spans if s.span_type is not SpanType.IMAGE]


class TestTitleCase:
    """``style.title.font.case`` transforms a title's words as one string,
    whatever inline formatting splits them into spans."""

    def test_sentence_case_capitalizes_only_the_first_word(self) -> None:
        assert _cased_words("top 10 **customers**", "sentence") == [
            "Top 10 ",
            "customers",
        ]

    def test_title_case_treats_an_emphasized_small_word_as_mid_title(self) -> None:
        assert _cased_words("revenue **of** the year", "title") == [
            "Revenue ",
            "of",
            " the Year",
        ]

    def test_image_url_keeps_its_case(self) -> None:
        (heading,) = title_blocks(
            "![logo](tall.svg) sales", {}, 1, _cased("upper"), _CHARTS, None
        )
        assert isinstance(heading, Heading)
        image, words = heading.spans
        assert image.url is not None and image.url.startswith("data:image/svg+xml")
        assert words.text == " SALES"

    def test_camel_case_keeps_the_formatting(self) -> None:
        assert _cased_words("top **customers**", "camel") == ["top", "customers"]

    @pytest.mark.parametrize(
        ("title", "words"),
        [
            ("re**venue** growth", ["Re", "venue", " Growth"]),
            ("**Sales**force overview", ["Sales", "force Overview"]),
            ("what it is **for**", ["What It Is ", "For"]),
        ],
    )
    def test_the_default_title_case_reads_across_emphasis(
        self, title: str, words: list[str]
    ) -> None:
        style = resolve_style(get_theme_style())
        (heading,) = title_blocks(title, {}, 1, style, _CHARTS, None)
        assert isinstance(heading, Heading)
        assert [s.text for s in heading.spans] == words

    def test_upper_case_that_lengthens_a_word_keeps_every_letter(self) -> None:
        assert _cased_words("straße **x**", "upper") == ["STRASSE ", "X"]

    @pytest.mark.parametrize("case", ["none", "title", "upper", "sentence", "camel"])
    def test_a_letter_whose_case_changes_its_length_never_raises(
        self, case: str
    ) -> None:
        # "İ".lower() is two code points: an edge counted in one casing is
        # off by one in the other.
        assert _cased_words("İstanbul **sales** x", case)

    def test_upper_case_of_a_dotted_capital_i_keeps_it(self) -> None:
        assert _cased_words("İstanbul sales", "upper") == ["İSTANBUL SALES"]

    def test_title_case_capitalizes_only_the_first_span(self) -> None:
        assert _cased_words("the lord of **the** rings", "title") == [
            "The Lord of ",
            "the",
            " Rings",
        ]

    def test_title_case_leaves_inline_code_alone(self) -> None:
        assert _cased_words("the `sql_query` guide", "title") == [
            "The ",
            "sql_query",
            " Guide",
        ]
