"""Tests for inline image spans: an image mid-sentence, in a heading, list
item, or table cell renders a real <image> box, not its alt text.

Standalone image blocks (an image alone on its own line) are unaffected --
see test_images.py, whose tests are pinned untouched.
"""

from __future__ import annotations

import base64
import re

import pytest

from mdsvg import parse
from mdsvg.renderer import SVGRenderer
from mdsvg.style import Style

SVG_WITH_DIMENSIONS = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 412 128" '
    'width="412" height="128"><rect width="412" height="128"/></svg>'
)
# A small square so it never needs to be scaled down to fit a test's width.
SVG_SQUARE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" '
    'width="40" height="40"><rect width="40" height="40"/></svg>'
)


def data_uri(payload: str, media_type: str = "image/svg+xml") -> str:
    encoded = base64.b64encode(payload.encode()).decode()
    return f"data:{media_type};base64,{encoded}"


def _text_y(svg: str, content: str) -> float:
    match = re.search(rf'<text x="[\d.]+" y="([\d.]+)"[^>]*>{re.escape(content)}<', svg)
    assert match is not None, f"no <text> holding {content!r}"
    return float(match.group(1))


def _bullet_cy(svg: str) -> float:
    match = re.search(r'<circle cx="[\d.]+" cy="([\d.]+)"', svg)
    assert match is not None
    return float(match.group(1))


class TestInlineImageInParagraph:
    def test_image_mid_paragraph_renders_as_an_image_not_alt_text(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer().render(
            list(parse(f"Before ![logo]({url}) after")), width=400, padding=0
        )
        assert "<image" in svg
        assert not re.search(r"<text[^>]*>[^<]*logo", svg)
        assert "Before" in svg
        assert "after" in svg

    def test_inline_image_carries_its_alt_text_like_a_block_image(self) -> None:
        url = data_uri(SVG_SQUARE)
        with_alt = SVGRenderer().render(
            list(parse(f"Before ![company logo]({url}) after")), width=400, padding=0
        )
        without_alt = SVGRenderer().render(
            list(parse(f"Before ![]({url}) after")), width=400, padding=0
        )
        assert re.search(r"<image [^>]*/>\s*<title>company logo</title>", with_alt)
        assert "<title>" not in without_alt

    def test_inline_image_is_sized_from_intrinsic_dimensions(self) -> None:
        """A small inline image keeps its natural size -- it is not
        stretched to the container width the way a standalone image block
        is."""
        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer().render(
            list(parse(f"Before ![logo]({url}) after")), width=400, padding=0
        )
        assert 'width="40" height="40"' in svg

    def test_paragraph_wraps_around_a_wide_inline_image(self) -> None:
        """An inline image too wide for the remaining line still fits the
        container (scaled down like a standalone block would be), and the
        paragraph keeps flowing around it rather than erroring."""
        url = data_uri(SVG_WITH_DIMENSIONS)  # 412x128, wider than a 200px column
        text = "word " * 20
        svg = SVGRenderer().render(
            list(parse(f"{text}![logo]({url}) {text}")), width=200, padding=0
        )
        image = re.search(r'<image [^>]*width="([\d.]+)" height="([\d.]+)"', svg)
        assert image is not None
        # Scaled down to fit the 200px column, preserving aspect ratio.
        assert float(image.group(1)) == pytest.approx(200)
        assert float(image.group(2)) == pytest.approx(200 * 128 / 412, abs=0.01)
        assert "word" in svg

    def test_explicit_width_height_attrs_on_an_inline_image(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer().render(
            list(parse(f"See ![logo]({url}){{width=60 height=30}} here")),
            width=400,
            padding=0,
        )
        assert 'width="60" height="30"' in svg


def _image_box(svg: str) -> tuple[float, float]:
    match = re.search(r'<image [^>]*width="([\d.]+)" height="([\d.]+)"', svg)
    assert match is not None, "no <image> in the render"
    return float(match.group(1)), float(match.group(2))


class TestInlineImageSizeTiers:
    """Each tier of ``_inline_image``'s sizing, for a known and an unknown
    image. dct renders with ``fetch_image_sizes=False``, so a remote URL is
    always the unknown-size case."""

    REMOTE = "https://example.invalid/logo.png"

    def _box(
        self, markdown: str, renderer: SVGRenderer | None = None
    ) -> tuple[float, float]:
        renderer = renderer or SVGRenderer(fetch_image_sizes=False)
        return _image_box(renderer.render(list(parse(markdown)), width=600, padding=0))

    def test_height_only_derives_the_width_from_the_measured_aspect(self) -> None:
        url = data_uri(SVG_WITH_DIMENSIONS)
        width, height = self._box(f"Signed by ![logo]({url}){{height=16}} today")
        assert height == 16
        assert width == pytest.approx(16 * 412 / 128, abs=0.01)

    def test_height_only_on_an_unknown_image_uses_the_fallback_aspect(self) -> None:
        width, height = self._box(f"Signed by ![logo]({self.REMOTE}){{height=16}} x")
        assert height == 16
        assert width == pytest.approx(16 * 16 / 9, abs=0.01)

    def test_an_unknown_image_with_no_size_fills_the_line_at_the_fallback_aspect(
        self,
    ) -> None:
        width, height = self._box(f"Signed by ![logo]({self.REMOTE}) today")
        assert width == 600
        assert height == pytest.approx(600 * 9 / 16, abs=0.01)

    def test_style_image_size_sizes_an_unknown_image(self) -> None:
        renderer = SVGRenderer(
            style=Style(image_width=48, image_height=20), fetch_image_sizes=False
        )
        assert self._box(f"See ![logo]({self.REMOTE}) here", renderer) == (48, 20)

    def test_style_image_width_alone_keeps_the_fallback_aspect(self) -> None:
        renderer = SVGRenderer(style=Style(image_width=48), fetch_image_sizes=False)
        width, height = self._box(f"See ![logo]({self.REMOTE}) here", renderer)
        assert width == 48
        assert height == pytest.approx(48 * 9 / 16, abs=0.01)


class TestInlineImageInBlockquote:
    def test_case_transform_keeps_explicit_image_size(self) -> None:
        from mdsvg import Style

        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer(style=Style(blockquote_font_case="upper")).render(
            list(parse(f"> see ![logo]({url}){{width=60 height=30}} here")),
            width=400,
            padding=0,
        )
        assert 'width="60" height="30"' in svg


class TestInlineImageInHeading:
    def test_image_in_heading_renders_as_an_image(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer().render(
            list(parse(f"## Release ![badge]({url})")), width=400, padding=0
        )
        assert "<image" in svg
        assert not re.search(r"<text[^>]*>[^<]*badge", svg)
        assert "Release" in svg

    def test_first_baseline_is_the_painted_baseline_of_an_image_line(self) -> None:
        url = data_uri(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="120">'
            '<rect width="40" height="120"/></svg>'
        )
        blocks = list(parse(f"# ![badge]({url}) Release"))
        renderer = SVGRenderer()
        svg = renderer.render(blocks, width=400, padding=0)
        drawn = re.search(r'<text[^>]*\by="([\d.]+)"', svg)
        assert drawn is not None
        assert float(drawn.group(1)) > 100, "the image line did not grow"
        assert renderer.first_baseline(blocks, 400) == pytest.approx(
            float(drawn.group(1)), abs=0.01
        )


class TestInlineImageInListItem:
    def test_image_in_list_item_renders_as_an_image(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = SVGRenderer().render(
            list(parse(f"- ![thumb]({url}) Item one\n- Item two")),
            width=400,
            padding=0,
        )
        assert "<image" in svg
        assert not re.search(r"<text[^>]*>[^<]*thumb", svg)
        assert "Item one" in svg
        assert "Item two" in svg

    def test_ordered_marker_sits_on_the_baseline_of_an_image_line(self) -> None:
        url = data_uri(SVG_SQUARE)  # 40px: taller than one text line
        svg = SVGRenderer().render(
            list(parse(f"1. ![thumb]({url}) Item one")), width=400, padding=0
        )
        marker_y = _text_y(svg, "1.")
        assert marker_y == _text_y(svg, "Item one")

    def test_bullet_stays_centered_on_the_text_of_an_image_line(self) -> None:
        """The bullet keeps the same offset from its item's baseline whether
        or not an inline image made that line taller."""
        url = data_uri(SVG_SQUARE)
        renderer = SVGRenderer()
        plain = renderer.render(list(parse("- Item one")), width=400, padding=0)
        with_image = renderer.render(
            list(parse(f"- ![thumb]({url}) Item one")), width=400, padding=0
        )
        assert _text_y(with_image, "Item one") > _text_y(plain, "Item one")
        assert _text_y(with_image, "Item one") - _bullet_cy(
            with_image
        ) == pytest.approx(_text_y(plain, "Item one") - _bullet_cy(plain), abs=0.01)


class TestInlineImageInTableCell:
    def test_image_in_table_cell_renders_as_an_embedded_image(self) -> None:
        url = data_uri(SVG_SQUARE)
        table = f"| Logo | Name |\n| --- | --- |\n| ![logo]({url}) | Acme |\n"
        svg = SVGRenderer().render(list(parse(table)), width=400, padding=0)
        assert "<image" in svg
        assert not re.search(r"<text[^>]*>[^<]*logo", svg)
        assert "Acme" in svg

    def test_row_grows_to_its_tallest_cell(self) -> None:
        url = data_uri(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="120">'
            '<rect width="40" height="120"/></svg>'
        )
        with_image = f"| Logo | Name |\n| --- | --- |\n| ![logo]({url}) | Acme |\n"
        text_only = "| Logo | Name |\n| --- | --- |\n| logo | Acme |\n"
        renderer = SVGRenderer()
        tall = renderer.render_content(list(parse(with_image)), width=200, padding=0)
        short = renderer.render_content(list(parse(text_only)), width=200, padding=0)
        # The image line is the image's height tall; the text line it
        # replaces was one font size of text.
        growth = 120 - renderer.style.base_font_size
        assert tall.height == pytest.approx(short.height + growth, abs=0.01)

    def test_an_image_in_one_cell_leaves_the_next_cells_lines_evenly_spaced(
        self,
    ) -> None:
        """Each cell lays out its own lines -- an image in one cell makes the
        row taller, it does not stretch line N of a sibling cell."""
        url = data_uri(
            '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="120">'
            '<rect width="40" height="120"/></svg>'
        )
        words = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        table = f"| A | B |\n| --- | --- |\n| ![x]({url}) | {words} |\n"
        svg = SVGRenderer().render(list(parse(table)), width=240, padding=0)

        baselines = [
            float(y)
            for y, content in re.findall(
                r'<text x="[\d.]+" y="([\d.]+)"[^>]*>([^<]*)<', svg
            )
            if content and content.split()[0] in words.split()
        ]
        assert len(baselines) >= 3, "fixture must wrap the text cell"
        gaps = {round(b - a, 2) for a, b in zip(baselines, baselines[1:])}
        assert len(gaps) == 1, f"uneven line spacing in the text cell: {baselines}"


class TestMeasureRenderParity:
    """measure_blocks (the look-ahead pass dct's column packer uses) must
    agree with the real paint pass on how tall an inline image makes a line
    -- a mismatch means multi-column prose clips the image."""

    def test_single_line_image_paragraph_measures_the_same_height_it_renders(
        self,
    ) -> None:
        url = data_uri(SVG_SQUARE)  # 40x40, taller than one text line
        # "logo: " keeps this a Paragraph with an inline IMAGE span, not a
        # standalone ImageBlock (an image alone on its own line) -- the
        # measure/render pair only special-cases Paragraph.
        markdown = f"logo: ![logo]({url})"
        blocks = list(parse(markdown))
        renderer = SVGRenderer()

        metrics = renderer.measure_blocks(blocks, width=300.0)[0]
        assert len(metrics.line_advances) == 1
        measured_height = sum(metrics.line_advances)

        result = renderer.render_block_window(blocks[0], width=300.0, start=0, count=1)

        assert result.height == measured_height

    def test_multiline_paragraph_measures_each_line_exactly(self) -> None:
        """The real bug: BlockMetrics used to report one line_advance for the
        whole block, so a paragraph with one tall image line among several
        plain text lines measured as if EVERY line were that tall --
        drastically over-counting. line_advances must be exact per line, not
        just an upper bound, or the column packer breaks in the wrong place
        and a caller placing an exact-height window gets a mismatch."""
        url = data_uri(SVG_WITH_DIMENSIONS)  # 412x128 -- much taller than text
        words = "word " * 20
        markdown = f"{words}![logo]({url}) {words}"
        blocks = list(parse(markdown))
        renderer = SVGRenderer()
        width = 200.0

        metrics = renderer.measure_blocks(blocks, width=width)[0]
        assert len(metrics.line_advances) > 2, "fixture must wrap to several lines"
        # Not every line is the same height -- proves this isn't secretly
        # back to one block-wide value repeated per line.
        assert len(set(metrics.line_advances)) > 1
        measured_total = sum(metrics.line_advances)

        result = renderer.render_block_window(
            blocks[0], width=width, start=0, count=len(metrics.line_advances)
        )

        assert result.height == measured_total


class TestTextAfterImageHasNoStrayLeadingSpace:
    """A text run right after an inline image can carry the wrap
    algorithm's word-separator as a literal leading space (_wrap_runs
    prepends it via TextRun.append when merging same-style runs). SVG
    collapses a <text> element's leading whitespace for rendering but not
    for layout, which already measured it as part of the run's width --
    left in, the space silently vanishes on screen ("dotafter" instead of
    "dot after") and the visible text sits one space-width off from where
    it was measured to be."""

    def test_text_segment_after_an_image_strips_the_leading_space(self) -> None:
        url = data_uri(SVG_SQUARE)
        renderer = SVGRenderer()
        svg = renderer.render(
            list(parse(f"before ![dot]({url}) after the image")),
            width=400,
            padding=0,
        )

        image_match = re.search(r'<image x="([\d.]+)"[^>]*width="([\d.]+)"', svg)
        assert image_match is not None
        image_x = float(image_match.group(1))
        image_width = float(image_match.group(2))

        text_elements = re.findall(r'<text x="([\d.]+)"[^>]*>([^<]*)</text>', svg)
        after = [
            (float(x), content) for x, content in text_elements if float(x) > image_x
        ]
        assert after, "no text segment found positioned after the image"
        after_x, after_content = after[0]

        space_width = renderer._measure_text(" ", renderer.style.base_font_size)
        # abs=0.01: format_number rounds every emitted coordinate to 2
        # decimal places, so the SVG's own x can differ from the raw float
        # by up to half a cent of a pixel.
        assert after_x == pytest.approx(image_x + image_width + space_width, abs=0.01)
        assert not after_content.startswith(" "), (
            f"emitted text {after_content!r} still carries the leading space "
            "SVG would silently drop on screen"
        )
        assert after_content.startswith("after")


class TestImageSizeAttributes:
    """``{width=X height=Y}`` after an image sizes it; any other braces are
    the author's text and stay in the document."""

    @staticmethod
    def _texts(markdown: str) -> str:
        from mdsvg.types import Paragraph

        (block,) = parse(markdown)
        assert isinstance(block, Paragraph)
        return "".join(s.text for s in block.spans if s.url is None)

    def test_other_braces_after_an_inline_image_stay_as_text(self) -> None:
        assert "{see note}" in self._texts("Price ![x](u.png){see note} here")

    def test_other_braces_after_a_block_image_stay_as_text(self) -> None:
        assert "{see note}" in self._texts("![x](u.png){see note}")

    def test_a_non_numeric_size_stays_as_text(self) -> None:
        assert "{width=abc}" in self._texts("Price ![x](u.png){width=abc} here")

    def test_size_attributes_still_size_the_image(self) -> None:
        from mdsvg.types import ImageBlock, Paragraph, SpanType

        (block,) = parse("![x](u.png){width=40 height=20}")
        assert isinstance(block, ImageBlock)
        assert (block.width, block.height) == (40, 20)
        (para,) = parse("a ![x](u.png){width=40 height=20} b")
        assert isinstance(para, Paragraph)
        (image,) = [s for s in para.spans if s.span_type is SpanType.IMAGE]
        assert (image.width, image.height) == (40, 20)


class TestInlineImageEscapesHostileInput:
    """The shared line emitter and the inline <image> emitter escape every
    authored or style string they interpolate, like the block paths do."""

    HOSTILE = '"/><script>alert(1)</script><x a="'

    def _render(self, markdown: str) -> str:
        from xml.etree import ElementTree

        from mdsvg import Style

        style = Style(image_preserve_aspect_ratio="xMinYMin slice")
        svg = SVGRenderer(style=style).render(
            list(parse(markdown)), width=400, padding=0
        )
        assert "<script" not in svg
        ElementTree.fromstring(svg)
        return svg

    def test_hostile_alt_text_and_style_in_a_paragraph_line(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = self._render(
            f"Before ![{self.HOSTILE}]({url}) `code` [link](https://x.test) after"
        )
        assert "<image" in svg
        assert "<title>" in svg

    def test_hostile_alt_text_and_style_in_a_table_cell_line(self) -> None:
        url = data_uri(SVG_SQUARE)
        svg = self._render(
            f"| a |\n| --- |\n| ![{self.HOSTILE}]({url}) [link](https://x.test) |"
        )
        assert "<image" in svg
