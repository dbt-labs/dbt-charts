"""Line-level block measurement and windowed rendering.

The public contract a column packer needs: ask what a block's lines are, then
render an arbitrary run of them. mdsvg owns line breaking; it knows nothing
about columns, boards or pages -- the caller decides where the breaks go.
"""

from __future__ import annotations

import pytest

from mdsvg import parse
from mdsvg.renderer import SVGRenderer

LONG = (
    "North America's primary issue is insufficient MQL supply, not a regional "
    "Stage 0 target miss or a broad loss of selling capacity. Event sources "
    "explain most of the decline, while target-aligned Stage 0 remained above "
    "target and the funnel table stays the single source for the counts."
)


@pytest.fixture
def renderer() -> SVGRenderer:
    return SVGRenderer()


class TestMeasureBlocks:
    def test_reports_one_entry_per_block(self, renderer: SVGRenderer) -> None:
        blocks = list(parse(f"# Heading\n\n{LONG}\n\n{LONG}"))
        metrics = renderer.measure_blocks(blocks, width=300.0)
        assert len(metrics) == len(blocks) == 3

    def test_line_count_tracks_available_width(self, renderer: SVGRenderer) -> None:
        blocks = list(parse(LONG))
        narrow = renderer.measure_blocks(blocks, width=200.0)[0]
        wide = renderer.measure_blocks(blocks, width=600.0)[0]
        assert len(narrow.line_advances) > len(wide.line_advances) > 1

    def test_headings_keep_with_next(self, renderer: SVGRenderer) -> None:
        blocks = list(parse(f"## Heading\n\n{LONG}"))
        heading, paragraph = renderer.measure_blocks(blocks, width=300.0)
        assert heading.keep_with_next is True
        assert paragraph.keep_with_next is False

    def test_paragraphs_split_but_code_does_not(self, renderer: SVGRenderer) -> None:
        blocks = list(parse(f"{LONG}\n\n```\nx = 1\ny = 2\n```"))
        paragraph, code = renderer.measure_blocks(blocks, width=300.0)
        assert paragraph.splittable is True
        assert code.splittable is False


class TestRenderBlockWindow:
    def test_window_renders_only_the_requested_lines(
        self, renderer: SVGRenderer
    ) -> None:
        block = list(parse(LONG))[0]
        total = len(renderer.measure_blocks([block], width=300.0)[0].line_advances)
        assert total >= 4, "fixture must wrap to several lines to be meaningful"

        head = renderer.render_block_window(block, width=300.0, start=0, count=2)
        tail = renderer.render_block_window(
            block, width=300.0, start=2, count=total - 2
        )
        assert head.elements.count("<text") == 2
        assert tail.elements.count("<text") == total - 2

    def test_window_lines_concatenate_to_the_whole_block(
        self, renderer: SVGRenderer
    ) -> None:
        import re

        block = list(parse(LONG))[0]
        total = len(renderer.measure_blocks([block], width=300.0)[0].line_advances)

        def words(svg: str) -> str:
            return " ".join(re.sub(r"<[^>]+>", " ", svg).split())

        whole = words(renderer.render_block_window(block, 300.0, 0, total).elements)
        split = (
            words(renderer.render_block_window(block, 300.0, 0, 2).elements)
            + " "
            + words(renderer.render_block_window(block, 300.0, 2, total - 2).elements)
        )
        assert split == whole

    def test_window_starts_at_the_top_of_its_own_box(
        self, renderer: SVGRenderer
    ) -> None:
        """A continuation must not inherit the y-offset of the lines it follows."""
        import re

        block = list(parse(LONG))[0]
        total = len(renderer.measure_blocks([block], width=300.0)[0].line_advances)
        tail = renderer.render_block_window(block, 300.0, total - 2, 2)
        first = re.search(r'<text[^>]*\by="([\d.]+)"', tail.elements)
        assert first is not None, "the window must render text"
        first_y = float(first.group(1))
        assert first_y < 40.0, f"continuation should start near y=0, got {first_y}"


class TestHeadingLeadingMargin:
    """`leading_margin` must be the margin the renderer actually draws.

    The margin is an em measure scaled by the heading's own font size, so it
    differs per level. A caller lifting a box-opening heading by this value
    places it wrong for every level whose scale is not 1.0 -- and mis-accounts
    the same amount at the foot of the block.
    """

    def test_margin_tracks_the_heading_size(self) -> None:
        renderer = SVGRenderer()  # default Style(): the em branch, not the px one
        margins = [
            renderer.measure_blocks(list(parse(f"{'#' * level} Heading")), width=300.0)[
                0
            ].leading_margin
            for level in (1, 2, 3)
        ]
        assert margins[0] > margins[1] > margins[2], (
            f"h1/h2/h3 leading margins are {margins} -- a level-blind value means "
            f"the caller lifts every heading by the same amount"
        )

    def test_margin_matches_what_the_renderer_draws(self) -> None:
        renderer = SVGRenderer()
        block = list(parse("# Heading"))[0]
        reported = renderer.measure_blocks([block], width=300.0)[0].leading_margin
        drawn = renderer._heading_margins(block)[0]
        assert reported == pytest.approx(drawn)
