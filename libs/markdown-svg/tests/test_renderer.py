"""Tests for the SVG renderer."""

import re
from pathlib import Path

import pytest

from mdsvg import (
    DARK_THEME,
    GITHUB_THEME,
    RenderResult,
    Size,
    Style,
    measure,
    parse,
    render,
    render_blocks,
    render_content,
)
from mdsvg.fonts import FontFace, FontFaces, FontMeasurer, _cached_measurer
from mdsvg.renderer import SVGRenderer

# Class names carry a hash-derived scope (see TestClassPrefixScoping) so two
# boards' SVGs sharing one HTML page can't collide on `.md-heading`. Tests
# that only care "did the heading rule render" match on the bare name.
_MD_CLASS = r"md-[0-9a-f]{8}"


def _has_md_class(haystack: str, name: str) -> bool:
    return re.search(rf"{_MD_CLASS}-{name}\b", haystack) is not None


def _md_rule_body(haystack: str, name: str) -> str:
    match = re.search(rf"\.{_MD_CLASS}-{name} \{{([^}}]*)\}}", haystack)
    assert match is not None, f"no .md-*-{name} rule found"
    return match.group(1)


class TestBasicRendering:
    """Test basic rendering functionality."""

    def test_render_simple_text(self) -> None:
        """Test rendering simple text."""
        svg = render("Hello World")
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "Hello World" in svg

    def test_render_heading(self) -> None:
        """Test rendering heading."""
        svg = render("# Hello")
        assert "<svg" in svg
        assert "Hello" in svg
        assert _has_md_class(svg, "heading")

    def test_render_bold(self) -> None:
        """Test rendering bold text."""
        svg = render("**bold**")
        assert "bold" in svg
        assert "font-weight: bold" in svg

    def test_render_italic(self) -> None:
        """Test rendering italic text."""
        svg = render("*italic*")
        assert "italic" in svg
        assert "font-style: italic" in svg

    def test_render_code(self) -> None:
        """Test rendering inline code."""
        svg = render("`code`")
        assert "code" in svg
        assert _has_md_class(svg, "code")

    def test_render_inline_code_renders_as_chip(self) -> None:
        """Inline code renders as a background chip in normal text flow (no brackets)."""
        import re

        svg = render("Inline `code` should stand out", width=400)
        # The prose text and code token are all present
        assert "Inline " in svg
        assert "code" in svg
        assert " should stand out" in svg
        # No brackets around the code token
        assert "[code]" not in svg
        # A background rect chip is emitted
        assert "<rect" in svg
        # The code text element is present
        text = re.search(r"<text[^>]*>(.*?)</text>", svg, re.DOTALL)
        assert text is not None

    def test_render_inline_code_uses_code_background_for_chip(self) -> None:
        """Inline code chip fill matches code_background; fenced-code border does not appear."""
        style = Style(code_background="#eeeeee", code_block_border_color="#cccccc")
        svg = render("Inline `code` example", style=style, width=400)

        # The chip rect uses code_background
        assert 'fill="#eeeeee"' in svg
        # The fenced-code border color is not applied to inline chips
        assert 'stroke="#cccccc"' not in svg
        assert "[code]" not in svg

    def test_render_inline_code_keeps_internal_spaces_styled(self) -> None:
        """Spaces inside a code span stay inside the same chip mono run."""
        svg = render("Use `line_height = 1.25` here", width=400)

        assert "line_height = 1.25" in svg
        assert "[line_height = 1.25]" not in svg

    def test_render_inline_code_wraps_across_lines_as_chips(self) -> None:
        """A wrapped inline code span emits one chip rect per line segment."""
        svg = render("Use `line_height = 1.25` here", width=80)

        # No old-style brackets anywhere
        assert "[lin" not in svg
        assert "25]" not in svg
        # Code text fragments are present (the token wraps but no brackets)
        assert "1.25" in svg
        # At least two rects for the two line fragments
        assert svg.count("<rect") >= 2

    def test_render_heading_inline_code_renders_as_chip(self) -> None:
        """Heading inline code renders as a chip, not brackets."""
        svg = render("# Check `status`")

        assert "[status]" not in svg
        assert "status" in svg
        assert _has_md_class(svg, "code")
        assert "<rect" in svg

    def test_render_link(self) -> None:
        """Test rendering link."""
        svg = render("[link](https://example.com)")
        assert "<a href=" in svg
        assert "example.com" in svg

    def test_render_link_uses_tight_solid_underline(self) -> None:
        """Links use native SVG underline styling."""
        svg = render("[link](https://example.com)")

        assert "text-decoration: underline" in svg
        assert "text-decoration-style: solid" in svg
        assert "text-decoration-thickness: 1px" in svg
        assert "text-underline-offset: 1px" in svg

    def test_render_mixed_style_sentence_keeps_boundary_spaces(self) -> None:
        """Whitespace at run boundaries must survive wrapping tokenization."""
        import re

        svg = render("Hello [link](https://example.com) world", width=400)
        text = re.search(r"<text[^>]*>(.*?)</text>", svg, re.DOTALL)
        assert text is not None
        visible = re.sub(r"<[^>]+>", "", text.group(1))
        assert visible == "Hello link world"


class TestCodeBlocks:
    """Test code block rendering."""

    def test_code_block_has_background(self) -> None:
        """Test code block has background rect."""
        svg = render("```\ncode\n```")
        assert "<rect" in svg

    def test_short_code_block_background_shrinks_to_content(self) -> None:
        """Short fenced code blocks should not occupy full prose width."""
        import re

        svg = render("```\ncode\n```", width=400)
        rect = re.search(r"<rect [^>]*width=\"([^\"]+)\"", svg)

        assert rect is not None
        assert float(rect.group(1)) < 80

    def test_code_block_vertical_padding_is_tighter_than_horizontal(self) -> None:
        """Fenced code blocks use tighter vertical padding than horizontal padding."""
        import re

        style = Style(code_font_size=10, code_block_padding=12)
        svg = render("```\nline_height = 1.25\nparagraph_gap = 0.5\n```", style=style)
        rect = re.search(r"<rect [^>]*height=\"([^\"]+)\"", svg)

        assert rect is not None
        assert float(rect.group(1)) == 44

    def test_long_code_block_background_caps_at_available_width(self) -> None:
        """Long fenced code blocks can still use the available width."""
        import re

        svg = render(f"```\n{'x' * 200}\n```", width=400)
        rect = re.search(r"<rect [^>]*width=\"([^\"]+)\"", svg)

        assert rect is not None
        width = float(rect.group(1))
        assert width <= 400
        assert width > 300

    def test_code_block_preserves_content(self) -> None:
        """Test code block content is preserved."""
        svg = render("```python\nprint('hello')\n```")
        assert "print" in svg

    def test_code_block_preserves_indentation(self) -> None:
        """Code block lines with leading spaces must keep their indentation.

        The text elements must have xml:space="preserve" so SVG renderers
        (browsers, svglib, etc.) don't collapse leading whitespace visually.
        Having spaces in the SVG string is not enough — without the attribute,
        renderers strip them on display.
        """
        md = "```python\ndef foo():\n    return 42\n```"
        svg = render(md)
        assert "    return 42" in svg
        # xml:space="preserve" is required for visual correctness
        assert 'xml:space="preserve"' in svg

    def test_hide_overflow_clip_id_is_deterministic(self) -> None:
        """`code-clip-*` ids must derive from content, not `id()` — a CPython
        memory address is nondeterministic between runs and can collide when
        two renders are composited into one document (address reuse)."""
        style = Style(code_block_overflow="hide")
        md = "```python\nprint('hello')\n```"

        svg1 = render(md, style=style, width=400)
        svg2 = render(md, style=style, width=400)

        clip1 = re.search(r'clipPath id="(code-clip-[^"]+)"', svg1)
        clip2 = re.search(r'clipPath id="(code-clip-[^"]+)"', svg2)
        assert clip1 is not None and clip2 is not None
        assert clip1.group(1) == clip2.group(1)
        # Equality alone would also hold under `id()` whenever CPython happens
        # to reuse the freed block's address, so pin the derived shape too — a
        # memory address is neither 8 chars nor hex-only.
        assert re.fullmatch(r"code-clip-[0-9a-f]{8}", clip1.group(1))


class TestLists:
    """Test list rendering."""

    def test_unordered_list_has_bullets(self) -> None:
        """Test unordered list has bullet points."""
        import re

        svg = render("- Item 1\n- Item 2")
        assert "<circle" in svg  # Bullet points are circles
        assert 'r="1.5"' in svg
        marker = re.search(r'<circle cx="([^"]+)"[^>]*r="([^"]+)"', svg)
        text = re.search(r'<text x="([^"]+)"[^>]*>Item 1</text>', svg)
        assert marker is not None
        assert text is not None
        marker_right = float(marker.group(1)) + float(marker.group(2))
        text_left = float(text.group(1))
        assert text_left - marker_right == 6.5

    def test_ordered_list_has_numbers(self) -> None:
        """Test ordered list has numbers."""
        svg = render("1. First\n2. Second")
        assert "1." in svg
        assert "2." in svg

    def test_multiline_item_renders_full_text(self) -> None:
        """Continuation lines appear in the rendered SVG, not just the first line."""
        svg = render("- first line\n  second line", width=400)
        assert "first line" in svg
        assert "second line" in svg

    def test_nested_child_list_renders(self) -> None:
        """A nested sub-list renders its own bullet and text."""
        svg = render("- parent\n  - child", width=400)
        assert "parent" in svg
        assert "child" in svg
        # Nested list also emits a bullet circle for the child
        assert svg.count("<circle") == 2

    def test_flat_list_height_unchanged(self) -> None:
        """Flat list items with no children produce the same height as before the fix."""
        # Measure single-item flat list; children=() contributes 0.0 height
        single_size = measure("- only item", width=400)
        two_size = measure("- item one\n- item two", width=400)
        assert two_size.height > single_size.height


class TestTables:
    """Test table rendering."""

    def test_table_has_border(self) -> None:
        """Test table has border."""
        md = """| H1 | H2 |
| --- | --- |
| C1 | C2 |"""
        svg = render(md)
        assert "stroke=" in svg  # Border stroke

    def test_table_has_header_background(self) -> None:
        """Test table header has background."""
        md = """| Header |
| --- |
| Cell |"""
        svg = render(md)
        # Should have background rect for header
        assert "<rect" in svg

    def test_table_cell_markdown_link_renders_anchor(self) -> None:
        """GFM table cells must emit <a href> (mdsvg used to flatten spans to plain text)."""
        md = """| Dashboard |
| --- |
| [Account Summary](/compare/?run=1&dashboard_id=1291) |"""
        svg = render(md)
        # `&` is XML-escaped in SVG attribute values
        assert 'href="/compare/?run=1&amp;dashboard_id=1291"' in svg
        assert "<a " in svg

    def test_table_cell_markdown_link_wraps_on_narrow_width(self) -> None:
        """Narrow table cells should wrap linked text instead of overflowing a single line."""
        md = """| Dashboard |
| --- |
| [1. Revenue Performance Executive Summary](/compare/?run=1&dashboard_id=955) |"""
        svg = render(md, width=180, padding=0)
        assert svg.count("<a ") >= 2
        assert measure(md, width=180, padding=0).height > 80
        assert 'href="/compare/?run=1&amp;dashboard_id=955"' in svg

    def test_table_cell_inline_code_renders_as_chip(self) -> None:
        """Table cell inline code renders as a background chip, not brackets."""
        md = """| Field |
| --- |
| `account_id` |"""
        svg = render(md)

        assert "[account_id]" not in svg
        assert "account_id" in svg
        assert _has_md_class(svg, "code")


class TestStyling:
    """Test style customization."""

    def test_custom_text_color(self) -> None:
        """Test custom text color."""
        style = Style(text_color="#ff0000")
        svg = render("Hello", style=style)
        assert "#ff0000" in svg

    def test_custom_font_size(self) -> None:
        """Test custom font size."""
        style = Style(base_font_size=20.0)
        svg = render("Hello", style=style)
        assert 'font-size="20"' in svg

    def test_default_font_weight_not_emitted(self) -> None:
        """Default body weight should not churn SVG snapshots."""
        svg = render("Hello")
        assert _has_md_class(svg, "text")
        assert "font-weight:" not in _md_rule_body(svg, "text")

    def test_custom_font_weight(self) -> None:
        """Test custom body font weight."""
        style = Style(font_weight=600)
        svg = render("Hello", style=style)
        assert "font-weight: 600" in svg

    def test_dark_theme(self) -> None:
        """Test dark theme."""
        svg = render("Hello", style=DARK_THEME)
        assert DARK_THEME.text_color in svg

    def test_github_theme(self) -> None:
        """Test GitHub theme."""
        svg = render("Hello", style=GITHUB_THEME)
        assert GITHUB_THEME.text_color in svg


class TestClassPrefixScoping:
    """`.md-*` class names are scoped to a hash of their own style, so two
    boards' inline SVGs sharing one HTML page (no CSS scope between them)
    can't have the last `.md-heading` rule win for both."""

    def test_different_styles_produce_non_colliding_selectors(self) -> None:
        """A serif-styled render and a sans-styled render must not share a
        `.md-*-heading` selector."""
        serif = render("# Title", style=Style(font_family="'Source Serif 4', serif"))
        sans = render("# Title", style=Style(font_family="Inter, sans-serif"))

        serif_heading = re.search(r"\.(md-[0-9a-f]{8}-heading) \{", serif)
        sans_heading = re.search(r"\.(md-[0-9a-f]{8}-heading) \{", sans)
        assert serif_heading is not None
        assert sans_heading is not None
        assert serif_heading.group(1) != sans_heading.group(1)

    def test_class_attrs_match_selectors_in_own_style_block(self) -> None:
        """Every `class="md-*"` an element emits resolves to a selector
        present in that same render's style block."""
        svg = render("# Heading\n\nBody text.")
        selectors = set(re.findall(r"\.(md-[0-9a-f]{8}-\w+)\s*\{", svg))
        classes = set(re.findall(r'class="(md-[0-9a-f]{8}-\w+)"', svg))
        assert classes
        assert classes <= selectors

    def test_prefix_deterministic_across_renders(self) -> None:
        """The same style content, in two unrelated Style instances, must
        produce the same prefix — the hash is over content, not `id()`."""
        first = render("# Heading", style=Style(font_family="'My Font', serif"))
        second = render("# Heading", style=Style(font_family="'My Font', serif"))

        first_class = re.search(r'class="(md-[0-9a-f]{8}-heading)"', first)
        second_class = re.search(r'class="(md-[0-9a-f]{8}-heading)"', second)
        assert first_class is not None
        assert second_class is not None
        assert first_class.group(1) == second_class.group(1)

    def test_no_unprefixed_md_selector_escapes(self) -> None:
        """Standing guard: every `.md-` selector must carry a hash-prefixed
        scope. Add a seventh rule to `_get_style_block` without routing it
        through the prefix and this trips."""
        svg = render("# Heading\n\n> Quote\n\n`code`")
        bad = re.findall(r"\.md-(?![0-9a-f]{8}-)[\w-]+", svg)
        assert bad == []


class TestDimensions:
    """Test dimension handling."""

    def test_custom_width(self) -> None:
        """Test custom width."""
        svg = render("Hello", width=600)
        assert 'width="600"' in svg

    def test_custom_padding(self) -> None:
        """Test custom padding."""
        svg = render("Hello", width=400, padding=30)
        assert "<svg" in svg  # Just verify it renders


class TestMeasure:
    """Test measurement functionality."""

    def test_measure_returns_size(self) -> None:
        """Test measure returns Size object."""
        size = measure("Hello World")
        assert isinstance(size, Size)
        assert size.width > 0
        assert size.height > 0

    def test_measure_respects_width(self) -> None:
        """Test measure respects width constraint."""
        size = measure("Hello", width=200)
        assert size.width == 200

    def test_longer_text_taller(self) -> None:
        """Test longer text produces taller output."""
        short_size = measure("Hi")
        long_size = measure("Hi\n\nThis is a much longer paragraph that will wrap.")
        assert long_size.height > short_size.height


class TestRenderBlocks:
    """Test render_blocks function."""

    def test_render_blocks_basic(self) -> None:
        """Test render_blocks with parsed blocks."""
        blocks = parse("# Hello\n\nWorld")
        svg = render_blocks(blocks, width=400)
        assert "<svg" in svg
        assert "Hello" in svg
        assert "World" in svg


class TestSVGRenderer:
    """Test SVGRenderer class directly."""

    @pytest.fixture(autouse=True)
    def clear_measurer_cache(self):
        """Prevent unavailable-font entries from leaking into subsequent tests."""
        yield
        _cached_measurer.cache_clear()

    def test_renderer_with_style(self) -> None:
        """Test renderer with custom style."""
        style = Style(text_color="#123456")
        renderer = SVGRenderer(style=style)
        blocks = parse("Hello")
        svg = renderer.render(blocks, width=400)
        assert "#123456" in svg

    def test_renderer_measure(self) -> None:
        """Test renderer measure method."""
        renderer = SVGRenderer()
        blocks = parse("Hello")
        size = renderer.measure(blocks, width=400)
        assert size.width == 400
        assert size.height > 0

    def test_renderer_raises_when_precise_font_measurement_unavailable(self) -> None:
        """Explicit font_path should fail instead of silently degrading."""
        with pytest.raises(RuntimeError, match="Precise text measurement"):
            SVGRenderer(font_path="/definitely/missing/font.ttf")

    def test_renderer_allows_plain_text_without_mono_font(self) -> None:
        from mdsvg import renderer as renderer_module

        original = renderer_module.get_system_mono_font
        renderer_module.get_system_mono_font = lambda: None
        try:
            renderer = SVGRenderer()
            svg = renderer.render(parse("plain prose"), width=400)
            assert "plain prose" in svg
        finally:
            renderer_module.get_system_mono_font = original

    def test_renderer_raises_when_mono_content_has_no_mono_font(self) -> None:
        from mdsvg import renderer as renderer_module

        original = renderer_module.get_system_mono_font
        renderer_module.get_system_mono_font = lambda: None
        try:
            renderer = SVGRenderer()
            with pytest.raises(RuntimeError, match="Precise monospace measurement"):
                renderer.render(parse("`code`"), width=400)
        finally:
            renderer_module.get_system_mono_font = original


class TestFontFaces:
    """SVGRenderer(fonts=...) measures each style against its own real font file."""

    @pytest.fixture(autouse=True)
    def clear_measurer_cache(self):
        yield
        _cached_measurer.cache_clear()

    def test_fonts_and_font_path_mutually_exclusive(self) -> None:
        """fonts= and font_path=/mono_font_path= cannot both be supplied."""
        with pytest.raises(ValueError, match="not both"):
            SVGRenderer(
                font_path="/tmp/regular.ttf",
                fonts=FontFaces(regular=FontFace(path="/tmp/regular.ttf")),
            )

    def test_measure_text_italic_narrower_than_regular_with_real_fontface(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """Regression: mdsvg's ratio-based estimate had italic's sign backwards
        (wider than regular). A real italic font file measures NARROWER."""
        regular_path = str(dbt_charts_fonts_dir / "SourceSerif4Variable.ttf")
        italic_path = str(dbt_charts_fonts_dir / "SourceSerif4-Italic.ttf")
        fonts = FontFaces(
            regular=FontFace(path=regular_path),
            italic=FontFace(path=italic_path),
        )
        renderer = SVGRenderer(fonts=fonts)
        sample = "so it may run to eighty and keep going indefinitely"

        regular_width = renderer._measure_text(sample, 14)
        italic_width = renderer._measure_text(sample, 14, is_italic=True)

        assert italic_width < regular_width

    def test_bold_measurement_uses_supplied_fontface_not_ratio(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """A real bold font file (~2-3% wider) must be used instead of the
        ratio-scaling fallback (~21% wider)."""
        variable_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        fonts = FontFaces(
            regular=FontFace(path=variable_path, weight=400),
            bold=FontFace(path=variable_path, weight=700),
        )
        renderer = SVGRenderer(fonts=fonts)
        sample = "measured bold text width against regular"

        regular_width = renderer._measure_text(sample, 14)
        ratio_scaled = regular_width * (
            renderer.style.bold_char_width_ratio / renderer.style.char_width_ratio
        )
        bold_width = renderer._measure_text(sample, 14, is_bold=True)

        assert regular_width < bold_width < ratio_scaled

    def test_heading_measurement_uses_supplied_heading_fontface(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """A heading measures against fonts.heading, not the body regular face,
        when the two are different font files — the same "measure what gets
        painted" guarantee font_weight already has for heading_font_weight.
        """
        regular_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        heading_path = str(dbt_charts_fonts_dir / "SourceSerif4Variable.ttf")
        fonts = FontFaces(
            regular=FontFace(path=regular_path),
            heading=FontFace(path=heading_path),
        )
        renderer = SVGRenderer(fonts=fonts)
        sample = "measured heading text width against body"

        body_width = renderer._measure_text(sample, 14)
        heading_width = renderer._measure_text(sample, 14, is_heading=True)

        assert body_width != heading_width
        assert "heading" in renderer.used_faces

    def test_heading_measurement_uses_a_static_heading_face_with_a_weight(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """A heading face with no wght axis (a static font) still measures the
        heading, not the body, when a weight is requested — regression for a
        bug where `_measurer_at_weight` returning None (no axis to re-instance)
        fell all the way through to the body-family branch below, instead of
        the un-reweighted heading face `self._heading_measurer` already holds.
        """
        regular_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        # Static (non-variable): no 'fvar' table, so _measurer_at_weight
        # returns None for any requested weight — the exact case that fell
        # through to the regular face before this fix.
        heading_path = str(
            dbt_charts_fonts_dir / "DBTSerifOldstyleProportional-Regular.ttf"
        )
        fonts = FontFaces(
            regular=FontFace(path=regular_path),
            heading=FontFace(path=heading_path),
        )
        renderer = SVGRenderer(fonts=fonts)
        sample = "measured heading text width against body"

        heading_only_measurer = FontMeasurer(heading_path)
        expected = heading_only_measurer.measure(sample, 14)
        body_width = renderer._measure_text(sample, 14)

        # font_weight="bold" forces the weight != None branch this bug lived in.
        actual = renderer._measure_text(sample, 14, is_heading=True, font_weight="bold")

        assert actual == expected
        assert actual != body_width
        assert "heading" in renderer.used_faces

    def test_heading_measurement_falls_back_to_regular_without_a_heading_face(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """No fonts.heading supplied -> is_heading measures identically to a
        plain run, matching heading_font_family's "" default (inherits body)."""
        regular_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        renderer = SVGRenderer(fonts=FontFaces(regular=FontFace(path=regular_path)))
        sample = "measured heading text width against body"

        assert renderer._heading_face is None
        assert renderer._measure_text(
            sample, 14, is_heading=True
        ) == renderer._measure_text(sample, 14)

    def test_bold_run_inside_a_heading_still_uses_the_body_bold_face(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """is_heading + is_bold: no heading-bold combination is measured against
        fonts.heading — the body bold face (or its ratio estimate) is used, per
        the documented scope of the heading face (plain heading runs only)."""
        regular_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        heading_path = str(dbt_charts_fonts_dir / "SourceSerif4Variable.ttf")
        variable_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        fonts = FontFaces(
            regular=FontFace(path=regular_path),
            bold=FontFace(path=variable_path, weight=700),
            heading=FontFace(path=heading_path),
        )
        renderer = SVGRenderer(fonts=fonts)
        sample = "bold word"

        bold_plain = renderer._measure_text(sample, 14, is_bold=True)
        bold_in_heading = renderer._measure_text(
            sample, 14, is_bold=True, is_heading=True
        )

        assert bold_plain == bold_in_heading


class TestMaxContentWidth:
    """RenderResult.max_content_width tracks the widest emitted line."""

    def test_zero_for_empty_content(self) -> None:
        renderer = SVGRenderer()
        result = renderer.render_content(parse(""), width=400)
        assert result.max_content_width == 0.0

    def test_wider_for_longer_paragraph_content(self) -> None:
        short_result = SVGRenderer().render_content(parse("Hi"), width=600)
        long_result = SVGRenderer().render_content(
            parse(
                "A considerably longer sentence that occupies much more "
                "horizontal space on the line."
            ),
            width=600,
        )
        assert long_result.max_content_width > short_result.max_content_width

    def test_includes_heading_text(self) -> None:
        result = SVGRenderer().render_content(
            parse("# A reasonably long heading spanning several words"), width=600
        )
        assert result.max_content_width > 0.0

    def test_includes_table_cell_text(self) -> None:
        md = (
            "| Header |\n"
            "| --- |\n"
            "| A long cell value that stretches the column considerably |\n"
        )
        result = SVGRenderer().render_content(parse(md), width=250)
        assert result.max_content_width > 0.0

    def test_unwrapped_code_overflow_exceeds_the_box(self) -> None:
        """'show' overflow doesn't wrap code lines, so a long line paints past
        the box — max_content_width must report that real overflow, not the
        capped background-rect width."""
        style = Style(code_block_overflow="show")
        renderer = SVGRenderer(style=style)
        long_line = "x" * 200
        result = renderer.render_content(parse(f"```\n{long_line}\n```"), width=100)
        assert result.max_content_width > result.width


class TestEscaping:
    """Test XML/SVG escaping."""

    def test_escapes_angle_brackets(self) -> None:
        """Test angle brackets are escaped."""
        svg = render("<script>alert('xss')</script>")
        assert "<script>" not in svg
        assert "&lt;" in svg

    def test_escapes_ampersand(self) -> None:
        """Test ampersand is escaped."""
        svg = render("A & B")
        assert "&amp;" in svg

    def test_escapes_quotes(self) -> None:
        """Test quotes are escaped in attributes."""
        svg = render('Text with "quotes"')
        # Quotes in text content are escaped
        assert "&quot;" in svg or '"quotes"' not in svg


class TestEdgeCases:
    """Test edge cases in rendering."""

    def test_empty_input(self) -> None:
        """Test rendering empty input."""
        svg = render("")
        assert "<svg" in svg
        assert "</svg>" in svg

    def test_whitespace_only(self) -> None:
        """Test rendering whitespace only."""
        svg = render("   \n\n   ")
        assert "<svg" in svg

    def test_very_long_text(self) -> None:
        """Test rendering very long text wraps."""
        long_text = "word " * 100
        svg = render(long_text, width=200)
        assert "<svg" in svg
        # Should have multiple text elements due to wrapping
        assert svg.count("<text") > 1


class TestTextAlignment:
    """Test text alignment functionality."""

    def test_default_alignment_is_left(self) -> None:
        """Test default text alignment is left (start)."""
        svg = render("Hello")
        assert 'text-anchor="start"' in svg

    def test_center_alignment(self) -> None:
        """Test center text alignment."""
        style = Style(text_align="center")
        svg = render("Hello", style=style)
        assert 'text-anchor="middle"' in svg

    def test_right_alignment(self) -> None:
        """Test right text alignment."""
        style = Style(text_align="right")
        svg = render("Hello", style=style)
        assert 'text-anchor="end"' in svg

    def test_left_alignment_explicit(self) -> None:
        """Test explicit left text alignment."""
        style = Style(text_align="left")
        svg = render("Hello", style=style)
        assert 'text-anchor="start"' in svg

    def test_heading_respects_alignment(self) -> None:
        """Test that headings respect text alignment."""
        style = Style(text_align="center")
        svg = render("# Centered Title", style=style)
        assert 'text-anchor="middle"' in svg

    def test_style_get_text_anchor_method(self) -> None:
        """Test Style.get_text_anchor() helper method."""
        assert Style(text_align="left").get_text_anchor() == "start"
        assert Style(text_align="center").get_text_anchor() == "middle"
        assert Style(text_align="right").get_text_anchor() == "end"


class TestComplexDocuments:
    """Test complex document rendering."""

    def test_full_document(self) -> None:
        """Test rendering a full document."""
        md = """# Main Title

This is a paragraph with **bold** and *italic* text.

## Code Example

```python
def hello():
    print("Hello, World!")
```

## List Example

- First item
- Second item
- Third item

> A wise quote

---

| Column A | Column B |
| -------- | -------- |
| Value 1  | Value 2  |
"""
        svg = render(md, width=600)
        assert "<svg" in svg
        assert "Main Title" in svg
        assert "bold" in svg
        assert "print" in svg


class TestRenderContent:
    """Test render_content function and RenderResult dataclass."""

    def test_render_content_returns_render_result(self) -> None:
        """Test that render_content returns a RenderResult object."""
        result = render_content("# Hello World")
        assert isinstance(result, RenderResult)

    def test_render_result_has_content(self) -> None:
        """Test that RenderResult has content without SVG wrapper."""
        result = render_content("Hello World")
        assert result.content is not None
        # Content should not have the outer SVG tags
        assert not result.content.strip().startswith("<svg")
        assert not result.content.strip().endswith("</svg>")
        # But should have actual content
        assert "Hello World" in result.content

    def test_render_result_has_dimensions(self) -> None:
        """Test that RenderResult has width and height."""
        result = render_content("# Hello", width=400)
        assert result.width == 400
        assert result.height > 0

    def test_render_result_height_matches_measure(self) -> None:
        """Test that RenderResult height matches measure()."""
        markdown = "# Title\n\nSome paragraph text here."
        result = render_content(markdown, width=400, padding=20)
        size = measure(markdown, width=400, padding=20)
        assert result.height == size.height
        assert result.width == size.width

    def test_render_result_to_svg(self) -> None:
        """Test RenderResult.to_svg() method."""
        result = render_content("# Hello World", width=400)
        svg = result.to_svg()

        # Should have SVG wrapper
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg
        assert 'width="400"' in svg

        # Should contain the content
        assert "Hello World" in svg

    def test_to_svg_matches_render(self) -> None:
        """Test that to_svg() produces same result as render()."""
        markdown = "# Hello\n\nThis is a paragraph."
        width = 400
        padding = 20

        result = render_content(markdown, width=width, padding=padding)
        svg_from_result = result.to_svg()

        svg_from_render = render(markdown, width=width, padding=padding)

        # Both should produce valid SVGs with same dimensions
        assert f'width="{width}"' in svg_from_result
        assert f'width="{width}"' in svg_from_render
        # Both should contain the content
        assert "Hello" in svg_from_result
        assert "Hello" in svg_from_render

    def test_render_content_includes_style_block(self) -> None:
        """Test that content includes the CSS style block."""
        result = render_content("Hello")
        assert "<style>" in result.content
        assert _has_md_class(result.content, "text")

    def test_render_content_with_custom_style(self) -> None:
        """Test render_content with custom style."""
        style = Style(text_color="#ff0000")
        result = render_content("Hello", style=style)
        assert "#ff0000" in result.content

    def test_render_content_composability(self) -> None:
        """Test that content can be composed into larger SVG."""
        result1 = render_content("# Section 1", width=300)
        result2 = render_content("# Section 2", width=300)

        # Compose into a larger SVG
        combined_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="700" height="400">
  <g transform="translate(0, 0)">
    {result1.content}
  </g>
  <g transform="translate(350, 0)">
    {result2.content}
  </g>
</svg>"""

        # Should be valid SVG with both sections
        assert 'xmlns="http://www.w3.org/2000/svg"' in combined_svg
        assert "Section 1" in combined_svg
        assert "Section 2" in combined_svg

    def test_svg_renderer_render_content_method(self) -> None:
        """Test SVGRenderer.render_content() method directly."""
        renderer = SVGRenderer()
        blocks = parse("# Hello\n\nWorld")
        result = renderer.render_content(blocks, width=400)

        assert isinstance(result, RenderResult)
        assert result.width == 400
        assert result.height > 0
        assert "Hello" in result.content
        assert "World" in result.content

    def test_render_result_empty_input(self) -> None:
        """Test render_content with empty input."""
        result = render_content("")
        assert isinstance(result, RenderResult)
        assert result.width > 0
        assert result.height >= 0
        # to_svg() should still work
        svg = result.to_svg()
        assert "<svg" in svg

    def test_render_result_has_elements_field(self) -> None:
        """Test that RenderResult has elements field without style block."""
        result = render_content("Hello World")
        assert result.elements is not None
        # Elements should have the actual content
        assert "Hello World" in result.elements
        # Elements should NOT have the style block
        assert "<style>" not in result.elements

    def test_render_result_has_style_block_field(self) -> None:
        """Test that RenderResult has style_block field."""
        result = render_content("Hello World")
        assert result.style_block is not None
        # Style block should have the CSS
        assert "<style>" in result.style_block
        assert _has_md_class(result.style_block, "text")
        assert _has_md_class(result.style_block, "heading")
        # Style block should NOT have content
        assert "Hello World" not in result.style_block

    def test_content_property_combines_style_and_elements(self) -> None:
        """Test that content property is style_block + elements."""
        result = render_content("Hello World")
        # content should be the combination
        assert result.content == result.style_block + "\n" + result.elements
        # Both pieces should be in content
        assert "<style>" in result.content
        assert "Hello World" in result.content

    def test_compose_with_single_style_block(self) -> None:
        """Test composing multiple sections with a single style block."""
        result1 = render_content("# Section 1", width=300)
        result2 = render_content("# Section 2", width=300)

        # Compose using only one style block + elements from both
        combined_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="700" height="400">
  {result1.style_block}
  <g transform="translate(0, 0)">
    {result1.elements}
  </g>
  <g transform="translate(350, 0)">
    {result2.elements}
  </g>
</svg>"""

        # Should be valid SVG with both sections
        assert 'xmlns="http://www.w3.org/2000/svg"' in combined_svg
        assert "Section 1" in combined_svg
        assert "Section 2" in combined_svg
        # Should only have ONE style block
        assert combined_svg.count("<style>") == 1

    def test_style_block_reflects_custom_style(self) -> None:
        """Test that style_block contains custom style values."""
        style = Style(text_color="#abcdef", link_color="#123456")
        result = render_content("Hello", style=style)
        assert "#abcdef" in result.style_block
        assert "#123456" in result.style_block


def test_image_preserve_aspect_ratio_default() -> None:
    # Default style emits the prior hardcoded "xMidYMid meet" so existing
    # consumers (and visual snapshot fleets) are unchanged.
    svg = render("![alt](http://x/img.png){width=200 height=300}")
    assert 'preserveAspectRatio="xMidYMid meet"' in svg


def test_image_preserve_aspect_ratio_custom() -> None:
    # Custom Style.image_preserve_aspect_ratio flows through to the
    # <image> element. Pins the renderer-Style wiring so a future refactor
    # can't silently re-hardcode the attribute.
    style = Style(image_preserve_aspect_ratio="xMidYMin meet")
    result = render_content(
        "![alt](http://x/img.png){width=200 height=300}", style=style
    )
    assert 'preserveAspectRatio="xMidYMin meet"' in result.elements


class TestBlockquoteBackground:
    """blockquote_background: a background rect is rendered behind blockquote content."""

    def test_blockquote_has_background_rect_when_set(self) -> None:
        """When blockquote_background is set, a fill rect appears in the SVG."""
        style = Style(blockquote_background="#f0f0f0")
        svg = render("> A quote", style=style)
        assert "#f0f0f0" in svg

    def test_blockquote_inline_code_renders_as_chip(self) -> None:
        """Blockquote inline code renders as a background chip, not brackets."""
        svg = render("> Quote `status` carefully")

        assert "[status]" not in svg
        assert "status" in svg
        assert _has_md_class(svg, "code")

    def test_blockquote_no_background_rect_when_empty(self) -> None:
        """When blockquote_background is '' (empty/none), only the left-rule rect is
        emitted — exactly one <rect>, vs two when a background is set."""
        bg = render("> A quote", style=Style(blockquote_background="#eeeeee"))
        no_bg = render("> A quote", style=Style(blockquote_background=""))
        assert no_bg.count("<rect") == bg.count("<rect") - 1

    def test_blockquote_background_field_exists_on_style(self) -> None:
        """Style dataclass has a blockquote_background field."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Style)}
        assert "blockquote_background" in fields

    def test_blockquote_background_default_is_empty(self) -> None:
        """Default blockquote_background is empty string (no background)."""
        style = Style()
        assert style.blockquote_background == ""

    def test_blockquote_background_honors_border_radius(self) -> None:
        """The background rect rounds its corners when blockquote_border_radius is set."""
        style = Style(blockquote_background="#f0f0f0", blockquote_border_radius=6)
        svg = render("> A quote", style=style)
        assert 'rx="6"' in svg

    def test_blockquote_background_no_radius_when_zero(self) -> None:
        """No rx attribute is emitted when the radius is 0."""
        style = Style(blockquote_background="#f0f0f0", blockquote_border_radius=0)
        svg = render("> A quote", style=style)
        assert "rx=" not in svg


class TestCodeBlockBorderStroke:
    """code_block_border_color + code_block_border_width: border stroke on code blocks."""

    def test_code_block_border_color_field_exists(self) -> None:
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Style)}
        assert "code_block_border_color" in fields

    def test_code_block_border_width_field_exists(self) -> None:
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Style)}
        assert "code_block_border_width" in fields

    def test_code_block_border_stroke_emitted_when_set(self) -> None:
        """When code_block_border_color is set, a stroke appears on the code rect."""
        style = Style(code_block_border_color="#aabbcc", code_block_border_width=2.0)
        svg = render("```\ncode\n```", style=style)
        assert "#aabbcc" in svg

    def test_code_block_border_default_is_empty(self) -> None:
        """Default code_block_border_color is empty (no stroke)."""
        style = Style()
        assert style.code_block_border_color == ""

    def test_code_block_no_stroke_when_color_empty(self) -> None:
        """When code_block_border_color is empty, the code rect carries no stroke."""
        svg = render("```\ncode\n```", style=Style(code_block_border_color=""))
        assert "<rect" in svg
        assert "stroke=" not in svg

    def test_code_block_stroke_present_when_color_set(self) -> None:
        """A non-empty code_block_border_color draws a stroke on the code rect."""
        svg = render("```\ncode\n```", style=Style(code_block_border_color="#333333"))
        assert 'stroke="#333333"' in svg


class TestCodeFontOverrides:
    """Per-code font-override fields on mdsvg.Style are honored in SVG output."""

    def test_code_font_weight_appears_in_svg(self) -> None:
        """code_font_weight is emitted in the .md-code CSS class."""
        style = Style(code_font_weight="700")
        svg = render("Use `code` here", style=style)
        assert "font-weight: 700" in svg

    def test_code_font_style_italic_appears_in_svg(self) -> None:
        """code_font_style=italic is emitted for code spans."""
        style = Style(code_font_style="italic")
        svg = render("Use `code` here", style=style)
        assert "font-style: italic" in svg

    def test_code_font_family_overrides_mono(self) -> None:
        """code_font_family overrides mono_font_family for code spans."""
        style = Style(code_font_family="'My Code Font', monospace")
        svg = render("Use `code` here", style=style)
        assert "My Code Font" in svg

    def test_code_font_size_override_applies(self) -> None:
        """code_font_size sets the font-size on the .md-code CSS class."""
        style = Style(code_font_size=11.0)
        svg = render("Use `code` here", style=style)
        assert "font-size: 11" in svg

    def test_code_font_decoration_underline_appears(self) -> None:
        """code_font_decoration=underline is emitted for code spans."""
        style = Style(code_font_decoration="underline")
        svg = render("Use `code` here", style=style)
        assert "text-decoration: underline" in svg

    def test_code_font_case_upper_transforms_text(self) -> None:
        """code_font_case=upper transforms code text to uppercase."""
        style = Style(code_font_case="upper")
        svg = render("Use `hello` here", style=style)
        assert "HELLO" in svg
        assert "hello" not in svg

    def test_code_font_case_upper_with_special_chars_stays_valid_xml(self) -> None:
        """Case transform must run before XML escaping — uppercasing an escaped
        string would corrupt entities (&amp; -> &AMP;) and break the whole SVG."""
        import xml.etree.ElementTree as ET

        style = Style(code_font_case="upper")
        svg = render("Code `a < b && c` here", style=style)
        # The document must still parse as XML (no corrupted &AMP;/&LT; entities).
        ET.fromstring(svg)
        assert "&AMP;" not in svg and "&LT;" not in svg

    def test_code_unset_falls_back_to_mono_family(self) -> None:
        """When code_font_family is empty, the mono_font_family is used."""
        mono = "ui-monospace, 'Test Mono', monospace"
        style = Style(mono_font_family=mono, code_font_family="")
        svg = render("Use `code` here", style=style)
        assert "Test Mono" in svg

    def test_code_block_font_weight_applies(self) -> None:
        """code_font_weight is honored in fenced code blocks too."""
        style = Style(code_font_weight="700")
        svg = render("```\nhello\n```", style=style)
        assert "font-weight" in svg

    def test_code_block_font_case_upper_transforms(self) -> None:
        """code_font_case=upper transforms fenced code block text."""
        style = Style(code_font_case="upper")
        svg = render("```\nhello world\n```", style=style)
        assert "HELLO WORLD" in svg

    def test_code_block_font_family_applies(self) -> None:
        """code_font_family overrides the mono family on fenced code blocks too."""
        style = Style(code_font_family="'My Code Font', monospace")
        svg = render("```\nhello\n```", style=style)
        assert "My Code Font" in svg

    def test_code_block_font_size_applies(self) -> None:
        """code_font_size sizes fenced code block text (not just inline)."""
        style = Style(code_font_size=11.0)
        svg = render("```\nhello\n```", style=style)
        assert 'font-size="11"' in svg

    def test_code_block_font_decoration_applies(self) -> None:
        """code_font_decoration is honored on fenced code blocks."""
        style = Style(code_font_decoration="underline")
        svg = render("```\nhello\n```", style=style)
        assert 'text-decoration="underline"' in svg


class TestCodeFontScale:
    """code_font_scale sizes code against the text it sits in, per context.

    Inline code scales off its host block, so code in a heading grows with the
    heading. Block code scales off base_font_size, since a fenced block sits
    inside nothing.
    """

    def test_inline_code_scales_off_body_prose(self) -> None:
        style = Style(base_font_size=14.0, code_font_scale=0.9)
        svg = render("Use `code` here", style=style)
        assert "font-size: 12.5px" in svg

    def test_inline_code_in_heading_scales_off_the_heading(self) -> None:
        """A heading's code tracks the heading, not the body size below it."""
        style = Style(base_font_size=14.0, h2_size=18.0, code_font_scale=0.9)
        svg = render("## Heading with `code`", style=style)
        assert "font-size: 16px" in svg
        assert "font-size: 12.5px" not in svg

    def test_fenced_block_scales_off_base_not_host(self) -> None:
        style = Style(base_font_size=14.0, code_font_scale=0.9)
        svg = render("```\ncode\n```", style=style)
        assert 'font-size="12.5"' in svg

    def test_scaled_size_rounds_to_nearest_half_pixel(self) -> None:
        """13 * 0.9 = 11.7, which lands on 11.5 rather than a ragged decimal."""
        style = Style(base_font_size=13.0, code_font_scale=0.9)
        svg = render("Use `code` here", style=style)
        assert "font-size: 11.5px" in svg

    def test_absolute_code_font_size_still_wins(self) -> None:
        """The absolute override stays the escape hatch, in both contexts."""
        style = Style(base_font_size=14.0, code_font_scale=0.9, code_font_size=11.0)
        svg = render("Use `code` here\n\n```\ncode\n```", style=style)
        assert "font-size: 11px" in svg
        assert "font-size: 12.5px" not in svg

    def test_chip_width_tracks_the_scaled_size(self) -> None:
        """Measurement and paint must read the same size.

        The chip rect is drawn from the measured run width, so if measurement
        ignored the scale the two renders would produce identical rects while
        painting different glyph sizes.
        """
        import re

        def chip_width(scale: float) -> float:
            svg = render(
                "Use `code` here",
                style=Style(base_font_size=14.0, code_font_scale=scale),
            )
            widths = re.findall(r'<rect [^>]*width="([\d.]+)"[^>]*fill="#f3f4f6"', svg)
            assert widths, "expected an inline-code chip rect"
            return float(widths[0])

        assert chip_width(1.2) > chip_width(0.6)

    def test_chip_box_is_sized_off_the_code_size_not_the_host(self) -> None:
        """The chip box must follow the painted code size at the shipping scale.

        Scaling and pinning to the same painted size must produce the same box:
        0.9 x 14 and an absolute 12.5 both paint code at 12.5px. If the geometry
        read the 14px host instead, the scaled chip would come out ~12% taller
        than the pinned one — the over-tall box the cap-height formula exists to
        prevent. The sibling geometry tests pin scale=1.0, where host and code
        size coincide and this cannot be seen.
        """
        import re

        def chip_box(style: Style) -> tuple[str, str]:
            svg = render("Use `code` here", style=style, width=400, padding=20)
            m = re.search(r'<rect [^>]*y="([\d.]+)"[^>]*height="([\d.]+)"', svg)
            assert m is not None, "expected an inline-code chip rect"
            return m.group(1), m.group(2)

        scaled = chip_box(Style(base_font_size=14.0, code_font_scale=0.9))
        pinned = chip_box(Style(base_font_size=14.0, code_font_size=12.5))
        assert scaled == pinned

    def test_table_cell_code_scales_off_the_cell(self) -> None:
        """Table cells thread their own size through to inline code.

        Without this, only the downstream dct golden suite catches a regression
        here — a consumer of the published wheel gets no signal.
        """
        style = Style(base_font_size=14.0, code_font_scale=0.9)
        svg = render("| a | b |\n|---|---|\n| x | `code` |", style=style)
        assert "font-size: 12.5px" in svg


class TestBlockquoteFontOverrides:
    """Per-blockquote font-override fields on mdsvg.Style are honored in SVG output."""

    def test_blockquote_font_weight_appears_in_svg(self) -> None:
        """blockquote_font_weight is emitted in the .md-blockquote CSS class."""
        style = Style(blockquote_font_weight="700")
        svg = render("> A quote", style=style)
        assert "font-weight: 700" in svg

    def test_blockquote_font_style_italic_appears_in_svg(self) -> None:
        """blockquote_font_style=italic is emitted for blockquote text."""
        style = Style(blockquote_font_style="italic")
        svg = render("> A quote", style=style)
        assert "font-style: italic" in svg

    def test_blockquote_font_family_applies(self) -> None:
        """blockquote_font_family is used for blockquote text."""
        style = Style(blockquote_font_family="'My Quote Font', serif")
        svg = render("> A quote", style=style)
        assert "My Quote Font" in svg

    def test_blockquote_font_family_falls_back_to_body_family(self) -> None:
        """Blockquotes declare body font-family when no quote override is set."""
        style = Style(font_family="'Body Serif', serif")
        svg = render("> A quote", style=style)
        assert _has_md_class(svg, "blockquote")
        assert "font-family: 'Body Serif', serif" in svg

    def test_blockquote_font_size_applies(self) -> None:
        """blockquote_font_size sets the font-size in the .md-blockquote CSS class."""
        style = Style(blockquote_font_size=13.0)
        svg = render("> A quote", style=style)
        assert "font-size: 13" in svg

    def test_blockquote_font_decoration_line_through_appears(self) -> None:
        """blockquote_font_decoration=line-through is emitted for blockquote text."""
        style = Style(blockquote_font_decoration="line-through")
        svg = render("> A quote", style=style)
        assert "text-decoration: line-through" in svg

    def test_blockquote_font_case_lower_transforms_text(self) -> None:
        """blockquote_font_case=lower transforms blockquote text to lowercase."""
        style = Style(blockquote_font_case="lower")
        svg = render("> HELLO WORLD", style=style)
        assert "hello world" in svg
        assert "HELLO WORLD" not in svg


class TestHeadingFontFamily:
    """heading_font_family on mdsvg.Style is honored in the .md-heading CSS class."""

    def test_heading_font_family_applies(self) -> None:
        """heading_font_family is used for heading text, not just body text."""
        style = Style(heading_font_family="'My Heading Font', serif")
        svg = render("# A heading", style=style)
        assert "My Heading Font" in svg

    def test_heading_font_family_falls_back_to_body_family(self) -> None:
        """Headings declare the body font-family when no heading override is set."""
        style = Style(font_family="'Body Serif', serif")
        svg = render("# A heading", style=style)
        assert _has_md_class(svg, "heading")
        assert "font-family: 'Body Serif', serif" in svg

    def test_heading_font_family_independent_of_body_font_family(self) -> None:
        """A heading override does not change the body text's own family."""
        style = Style(
            font_family="'Body Serif', serif",
            heading_font_family="'Heading Sans', sans-serif",
        )
        svg = render("# A heading\n\nSome body text.", style=style)
        assert "Heading Sans" in svg
        assert "font-family: 'Body Serif', serif" in svg


class TestBoldFontWeight:
    """bold_font_weight controls **bold** runs and bold table cells (including
    table headers, which are forced bold for measurement) — independent of
    heading_font_weight, which governs H1-H6 markdown headings only. Tests
    that check for a value mdsvg also emits by default (e.g. "bold") scope
    the assertion to the specific tspan/element rather than the whole
    document, since the always-present `.md-heading` CSS rule would
    otherwise satisfy a document-wide check regardless of the run tested.
    """

    def test_default_bold_font_weight_unchanged_on_bold_tspan(self) -> None:
        """Default bold_font_weight stays 'bold' (700) on the bold run's own
        tspan — unchanged published behavior for external consumers."""
        import re

        svg = render("**bold**")
        tspan = re.search(r"<tspan[^>]*>bold</tspan>", svg)
        assert tspan is not None
        assert "font-weight: bold" in tspan.group(0)

    def test_custom_bold_font_weight_applies_to_inline_bold(self) -> None:
        """A custom bold_font_weight is emitted for an inline **bold** span."""
        style = Style(bold_font_weight=600)
        svg = render("A **bold** word", style=style)
        assert "font-weight: 600" in svg

    def test_custom_bold_font_weight_applies_to_table_cell_bold(self) -> None:
        """A custom bold_font_weight is emitted for a bold table-data-cell run.

        Scoped to the data cell's own tspan: the header row is also
        force-bolded, so a document-wide "font-weight: 600" check would pass
        even if the data cell's **Cell** run never got the weight at all.
        """
        import re

        md = """| Header |
| --- |
| **Cell** |"""
        style = Style(bold_font_weight=600)
        svg = render(md, style=style)
        cell_element = re.search(r"<text[^>]*>.*?Cell.*?</text>", svg, re.DOTALL)
        assert cell_element is not None
        assert "font-weight: 600" in cell_element.group(0)

    def test_table_header_uses_bold_font_weight_not_heading_font_weight(self) -> None:
        """Table headers are forced bold for measurement and must render at
        bold_font_weight, not heading_font_weight — distinct sentinels prove
        the two knobs don't collapse into one."""
        import re

        md = """| Header |
| --- |
| Cell |"""
        style = Style(heading_font_weight=900, bold_font_weight=600)
        svg = render(md, style=style)
        header_element = re.search(r"<text[^>]*>.*?Header.*?</text>", svg, re.DOTALL)
        assert header_element is not None
        assert "600" in header_element.group(0)
        assert "900" not in header_element.group(0)

    def test_heading_font_weight_independent_of_bold_font_weight(self) -> None:
        """A real markdown heading's weight (the .md-heading CSS rule) is
        governed by heading_font_weight and must not move when bold_font_weight
        changes — proves headings and bold runs stayed on separate knobs."""
        style = Style(bold_font_weight=999)
        svg = render("# Heading", style=style)
        heading_rule_body = _md_rule_body(svg, "heading")
        assert "font-weight: bold" in heading_rule_body  # unchanged default
        assert "999" not in heading_rule_body


class TestHeadingMarginPxOverrides:
    """heading_margin_top_px / heading_margin_bottom_px override em scaling.

    The em-based ``heading_margin_top`` / ``heading_margin_bottom`` scale
    with the heading's own font size, so an H1 (large) gets a much bigger
    gap than an H2 even at the same nominal multiplier. The px override
    fields let callers set a fixed pixel margin used regardless of the
    heading's font size — useful for body-rhythm-derived spacing where the
    gap should be constant across H levels.
    """

    def test_top_px_override_used_when_set(self) -> None:
        """When heading_margin_top_px is set, it overrides the em multiplier."""
        from mdsvg import Style, measure

        em_only = Style(heading_margin_top=2.0, heading_margin_top_px=None)
        px_override = Style(heading_margin_top=2.0, heading_margin_top_px=40.0)
        # Preceded by a paragraph: an opening heading collapses its top margin,
        # so a lone heading cannot show a top-margin override at all.
        h1 = "Body.\n\n# Heading"

        em_size = measure(h1, width=400, style=em_only)
        px_size = measure(h1, width=400, style=px_override)

        # Different margin → different overall block height.
        assert em_size.height != px_size.height, (
            "heading_margin_top_px did not override heading_margin_top em."
        )

    def test_bottom_px_override_used_when_set(self) -> None:
        from mdsvg import Style, measure

        em_only = Style(heading_margin_bottom=2.0, heading_margin_bottom_px=None)
        px_override = Style(heading_margin_bottom=2.0, heading_margin_bottom_px=40.0)
        h1 = "# Heading"

        em_size = measure(h1, width=400, style=em_only)
        px_size = measure(h1, width=400, style=px_override)

        assert em_size.height != px_size.height

    def test_px_override_gives_constant_gap_across_h_levels(self) -> None:
        """The whole point of px overrides: the after-heading gap is the
        same number of pixels for H1, H2, …, H6 — independent of the
        heading's own font size.
        """
        from mdsvg import Style, measure

        # Same px margin on top and bottom — block heights should differ
        # only by the heading text height, not by scaled margins.
        style = Style(heading_margin_top_px=20.0, heading_margin_bottom_px=20.0)
        # Each heading follows a paragraph, so its top margin is drawn; an
        # opening heading collapses it and would leave only the bottom half
        # of the constant this test is about.
        lead = measure("Body.", width=400, style=style).height
        h1 = measure("Body.\n\n# H", width=400, style=style)
        h2 = measure("Body.\n\n## H", width=400, style=style)
        h6 = measure("Body.\n\n###### H", width=400, style=style)

        # Compute the non-margin portion (= heading text height).
        # block_height - 40px (top+bottom px) = text_height
        h1_text = h1.height - lead - 40
        h2_text = h2.height - lead - 40
        h6_text = h6.height - lead - 40

        # Each heading's text height differs (different font sizes).
        assert h1_text > h2_text > h6_text, (
            "Heading text heights should descend H1 > H2 > H6."
        )
        # The margin contribution itself is 40px in every case.
        # Equivalently: block_height - text_height is constant.
        assert (
            (h1.height - lead - h1_text)
            == (h2.height - lead - h2_text)
            == (h6.height - lead - h6_text)
            == 40
        )

    def test_px_top_none_falls_back_to_em(self) -> None:
        """heading_margin_top_px=None falls back to the em multiplier."""
        from mdsvg import Style, measure

        em_only_3 = Style(heading_margin_top=3.0, heading_margin_top_px=None)
        em_only_1 = Style(heading_margin_top=1.0, heading_margin_top_px=None)

        size_3 = measure("Body.\n\n# H", width=400, style=em_only_3)
        size_1 = measure("Body.\n\n# H", width=400, style=em_only_1)

        # Larger em multiplier → taller block.
        assert size_3.height > size_1.height


class TestHeadingAdjacentSpacingSkipsParagraphSpacing:
    """mdsvg ≥ 1.0 skips ``paragraph_spacing`` on heading-adjacent transitions.

    Heading margins (returned inside the heading's block height) are the
    sole source of pre/post-heading rhythm. Two adjacent headings
    margin-collapse to ``max(prev.margin_bottom, next.margin_top)`` rather
    than summing.

    All assertions use a distinctive ``paragraph_spacing`` value that would
    inflate any heading-adjacent total if it were still added — so a
    regression to the old double-stack would surface immediately.
    """

    def test_paragraph_to_heading_skips_paragraph_spacing(self) -> None:
        """p → h: total = paragraph_height + heading_block_height (no gap added)."""
        from mdsvg import Style, measure

        style = Style(
            heading_margin_top_px=40.0,
            heading_margin_bottom_px=10.0,
            paragraph_spacing=100.0,  # Would inflate if heading-adjacent gap applied.
        )

        text_alone = measure("Para.", width=400, padding=0, style=style)
        h_alone = measure("## H", width=400, padding=0, style=style)
        seq = measure("Para.\n\n## H", width=400, padding=0, style=style)

        # h_alone opens its own document, so it collapsed the 40px top margin
        # the heading in `seq` draws — the joint itself still adds nothing.
        expected = text_alone.height + h_alone.height + 40.0
        assert seq.height == expected, (
            f"p → h should sum the two block heights exactly (heading margins "
            f"are inside h_alone). Got {seq.height} vs {expected} expected. "
            f"Excess implies paragraph_spacing leaked into the joint."
        )

    def test_heading_to_paragraph_skips_paragraph_spacing(self) -> None:
        """h → p: total = heading_block_height + paragraph_height (no gap added)."""
        from mdsvg import Style, measure

        style = Style(
            heading_margin_top_px=40.0,
            heading_margin_bottom_px=10.0,
            paragraph_spacing=100.0,
        )

        h_alone = measure("## H", width=400, padding=0, style=style)
        text_alone = measure("Para.", width=400, padding=0, style=style)
        seq = measure("## H\n\nPara.", width=400, padding=0, style=style)

        assert seq.height == h_alone.height + text_alone.height, (
            f"h → p should sum the two block heights exactly. "
            f"Got {seq.height} vs {h_alone.height + text_alone.height} "
            f"expected. Excess implies paragraph_spacing leaked into the joint."
        )

    def test_heading_to_heading_margin_collapses(self) -> None:
        """h → h: ``prev.margin_bottom`` is collapsed; ``next.margin_top`` remains."""
        from mdsvg import Style, measure

        # margin_bottom = 10, margin_top = 40 — collapse drops the 10.
        style = Style(
            heading_margin_top_px=40.0,
            heading_margin_bottom_px=10.0,
            paragraph_spacing=100.0,
        )

        h_alone = measure("## H", width=400, padding=0, style=style)
        seq = measure("## H\n\n## H", width=400, padding=0, style=style)

        # h_alone collapsed its own top margin; the second heading in `seq`
        # draws its 40, and the collapse drops the first one's trailing 10.
        expected = 2 * h_alone.height + 40.0 - 10.0
        assert seq.height == expected, (
            f"h → h should margin-collapse: 2 * h_alone - margin_bottom = "
            f"{expected}. Got {seq.height}. A summed (10+40) joint OR a "
            f"summed+paragraph_spacing (150) joint would both fail this."
        )

    def test_paragraph_to_paragraph_unchanged(self) -> None:
        """p → p: joint stays at ``paragraph_spacing`` (the un-changed case)."""
        from mdsvg import Style, measure

        style = Style(
            heading_margin_top_px=40.0,
            heading_margin_bottom_px=10.0,
            paragraph_spacing=20.0,
        )

        text_alone = measure("Para.", width=400, padding=0, style=style)
        seq = measure("Para.\n\nPara.", width=400, padding=0, style=style)

        joint = seq.height - 2 * text_alone.height
        assert joint == 20.0, f"p → p joint should be paragraph_spacing=20; got {joint}"


class TestLinkSanitization:
    """Links with unsafe URL schemes must not emit live javascript:/data: hrefs."""

    def test_javascript_href_is_stripped(self) -> None:
        """javascript: links must be replaced with '#' — clickable XSS in inlined SVG."""
        svg = render("[click](javascript:alert(document.cookie))")
        assert "javascript:" not in svg
        assert 'href="#"' in svg

    def test_data_href_is_stripped(self) -> None:
        """data: links are also XSS-capable when SVG is inlined into HTML."""
        svg = render("[img](data:text/html,<script>alert(1)</script>)")
        assert "data:text" not in svg
        assert 'href="#"' in svg

    def test_https_href_is_preserved(self) -> None:
        """Safe https: links must pass through unchanged."""
        svg = render("[docs](https://example.com/guide)")
        assert "https://example.com/guide" in svg

    def test_relative_href_is_preserved(self) -> None:
        """Relative URLs (starting with /) must pass through unchanged."""
        svg = render("[page](/dashboard/index)")
        assert "/dashboard/index" in svg

    def test_anchor_href_is_preserved(self) -> None:
        """Anchor URLs (starting with #) must pass through unchanged."""
        svg = render("[section](#intro)")
        assert "#intro" in svg

    def test_colon_free_relative_href_is_preserved(self) -> None:
        """Relative URLs without a colon (guide/setup) must not be stripped.

        A URL like 'guide/setup' has no scheme — it's a safe relative path.
        The old check only allowed leading /, #, ., ? which excluded sibling-
        page links of the form 'dir/page'.
        """
        svg = render("[guide](guide/setup)")
        assert "guide/setup" in svg
        assert 'href="#"' not in svg


class TestInlineCodeChip:
    """Inline code renders as a background chip (rect + radius), no brackets."""

    def test_no_brackets_in_output(self) -> None:
        """Inline code must not be wrapped in square brackets."""
        svg = render("Use `foo` here", width=400)
        assert "[foo]" not in svg
        assert "foo" in svg

    def test_inline_code_emits_rect(self) -> None:
        """A <rect> is emitted for each inline code run."""
        svg = render("`code`", width=400)
        assert "<rect" in svg

    def test_inline_code_rect_has_code_background(self) -> None:
        """The chip rect fill matches code_background."""
        style = Style(code_background="#aabbcc")
        svg = render("`code`", style=style, width=400)
        assert 'fill="#aabbcc"' in svg

    def test_inline_code_rect_has_border_radius(self) -> None:
        """The chip rect has a non-zero rx matching inline_code_border_radius."""
        style = Style(inline_code_border_radius=5)
        svg = render("`code`", style=style, width=400)
        assert 'rx="5"' in svg

    def test_inline_code_rect_emitted_before_text(self) -> None:
        """The background rect must appear before the <text> element."""
        svg = render("`code`", width=400)
        rect_pos = svg.index("<rect")
        text_pos = svg.index("<text")
        assert rect_pos < text_pos

    def test_inline_code_rect_width_includes_padding(self) -> None:
        """Chip width ≈ run_width + 2 * inline_code_padding (within 2px tolerance)."""
        import re

        padding = 4.0
        style = Style(inline_code_padding=padding, inline_code_border_radius=3)
        svg = render("`abc`", style=style, width=400)

        # Extract the rect width from the first rect in the SVG
        rect_match = re.search(r'<rect[^>]*width="([^"]+)"', svg)
        assert rect_match is not None, "Expected a <rect> in output"
        rect_width = float(rect_match.group(1))

        # The rect width must be larger than zero and include some padding
        assert rect_width > 0
        # padding * 2 must be reflected; rect_width - 2*padding = run_width > 0
        assert rect_width > 2 * padding

    def test_inline_code_rect_height_covers_font_size(self) -> None:
        """Chip height spans the font size plus vertical padding."""
        import re

        padding = 3.0
        font_size = 14.0
        style = Style(
            base_font_size=font_size,
            inline_code_padding=padding,
            inline_code_border_radius=3,
        )
        svg = render("`word`", style=style, width=400)

        rect_match = re.search(r'<rect[^>]*height="([^"]+)"', svg)
        assert rect_match is not None
        rect_height = float(rect_match.group(1))
        # Height must be at least font_size; with padding, strictly greater
        assert rect_height >= font_size

    def test_inline_code_chip_geometry_uses_code_font_size(self) -> None:
        """A code-font-size override resizes the chip as well as its glyphs."""
        import re

        def chip_geometry(code_font_size: float) -> tuple[float, float]:
            svg = render(
                "`code`",
                style=Style(base_font_size=14, code_font_size=code_font_size),
                width=400,
            )
            rect = re.search(r'<rect[^>]*width="([^\"]+)"[^>]*height="([^\"]+)"', svg)
            assert rect is not None
            return float(rect.group(1)), float(rect.group(2))

        smaller_chip = chip_geometry(10)
        larger_chip = chip_geometry(12)

        assert smaller_chip[0] < larger_chip[0]
        assert smaller_chip[1] < larger_chip[1]

    def test_non_code_run_emits_no_rect(self) -> None:
        """Plain prose renders no rect background."""
        svg = render("plain text here", width=400)
        assert "<rect" not in svg

    def test_wrapped_code_run_emits_two_rects(self) -> None:
        """A code run split across two lines emits one rect per line."""
        # Use a very narrow width so the code token wraps
        svg = render("Use `long_variable_name` carefully", width=60)
        # Count rects — each code fragment on each line gets one
        rect_count = svg.count("<rect")
        assert rect_count >= 2

    def test_center_aligned_code_chip_present(self) -> None:
        """Center-aligned text still emits a chip rect for inline code."""
        style = Style(text_align="center")
        svg = render("`foo`", style=style, width=400)
        assert "<rect" in svg
        assert "[foo]" not in svg

    def test_right_aligned_code_chip_present(self) -> None:
        """Right-aligned text still emits a chip rect for inline code."""
        style = Style(text_align="right")
        svg = render("`foo`", style=style, width=400)
        assert "<rect" in svg
        assert "[foo]" not in svg

    def test_style_has_inline_code_padding_field(self) -> None:
        """Style exposes inline_code_padding with a sane default."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Style)}
        assert "inline_code_padding" in fields
        assert Style().inline_code_padding > 0

    def test_style_has_inline_code_border_radius_field(self) -> None:
        """Style exposes inline_code_border_radius with a sane default."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(Style)}
        assert "inline_code_border_radius" in fields
        assert Style().inline_code_border_radius > 0

    def test_inline_code_in_heading_no_brackets(self) -> None:
        """Heading inline code renders as chip, not brackets."""
        svg = render("# Check `status`")
        assert "[status]" not in svg
        assert "status" in svg
        assert "<rect" in svg

    def test_inline_code_in_table_cell_no_brackets(self) -> None:
        """Table cell inline code renders as chip, not brackets."""
        md = """| Field |
| --- |
| `account_id` |"""
        svg = render(md)
        assert "[account_id]" not in svg
        assert "account_id" in svg

    def test_inline_code_in_blockquote_no_brackets(self) -> None:
        """Blockquote inline code renders as chip, not brackets."""
        svg = render("> Quote `status` carefully")
        assert "[status]" not in svg
        assert "status" in svg

    def test_chip_rect_y_aligns_with_cap_height_not_full_em(self) -> None:
        """Chip rect top sits padding above cap height, not padding above full em top.

        Old formula: rect_y = baseline - font_size - padding  (wastes ~28% em above cap)
        New formula: rect_y = baseline - CAP_HEIGHT_RATIO * font_size - padding
        The new formula produces a smaller rect_y value gap to baseline (cap_height < font_size).
        """
        import re

        font_size = 14.0
        padding = 3.0
        style = Style(
            base_font_size=font_size,
            line_height=1.25,  # serif-typical; chip geometry must not depend on line_height
            inline_code_padding=padding,
            inline_code_border_radius=3,
            # This test is about where the chip sits, not how code is sized. Scale
            # 1.0 makes the code size equal font_size, which is what the cap-height
            # arithmetic below assumes; the scale itself is covered by
            # TestCodeFontScale.
            code_font_scale=1.0,
        )
        svg = render("`word`", style=style, width=400, padding=20)

        rect_match = re.search(r'<rect[^>]*y="([^"]+)"', svg)
        text_match = re.search(r'<text[^>]+y="([^"]+)"', svg)
        assert rect_match is not None, "Expected a <rect> in output"
        assert text_match is not None, "Expected a <text> in output"

        rect_y = float(rect_match.group(1))
        baseline_y = float(text_match.group(1))

        # How far above baseline does the rect top sit?
        above_baseline = baseline_y - rect_y  # positive = rect top is above baseline

        # Old formula gives above_baseline == font_size + padding == 17.
        # New formula gives above_baseline ≈ CAP_HEIGHT_RATIO * font_size + padding ≈ 13.
        # Cap height ratio for typical fonts is ~0.72, so cap top is at 10.08px above baseline.
        # With padding=3, rect top should be ~13px above baseline, not 17.
        #
        # Invariant: above_baseline < font_size + padding  (improvement over old formula)
        assert above_baseline < font_size + padding, (
            f"Chip top is {above_baseline:.2f}px above baseline; "
            f"expected < {font_size + padding:.2f} (old formula value). "
            f"Chip extends too far above cap height."
        )
        # And the rect must still cover at least cap height above baseline (plus padding).
        # Cap height ≈ 0.65 * font_size (conservative lower bound for most fonts).
        min_above = 0.65 * font_size + padding
        assert above_baseline >= min_above, (
            f"Chip top is only {above_baseline:.2f}px above baseline; "
            f"must be >= {min_above:.2f} to cover cap height + padding."
        )

    def test_chip_rect_bottom_includes_descender_allowance(self) -> None:
        """Chip rect bottom extends below baseline by padding plus descender room.

        Old formula: chip_height = font_size + 2*padding, so bottom = baseline + padding.
        New formula: bottom = baseline + descender_ratio * font_size + padding.
        The new formula gives extra room so descending glyphs (g, p, y) don't clip.
        """
        import re

        font_size = 14.0
        padding = 3.0
        style = Style(
            base_font_size=font_size,
            line_height=1.25,
            inline_code_padding=padding,
            inline_code_border_radius=3,
            code_font_scale=1.0,  # see the sibling test — isolate anchoring from sizing
        )
        svg = render("`word`", style=style, width=400, padding=20)

        rect_match = re.search(r'<rect[^>]*y="([^"]+)"[^>]*height="([^"]+)"', svg)
        text_match = re.search(r'<text[^>]+y="([^"]+)"', svg)
        assert rect_match is not None, "Expected a <rect> in output"
        assert text_match is not None, "Expected a <text> in output"

        rect_y = float(rect_match.group(1))
        rect_height = float(rect_match.group(2))
        baseline_y = float(text_match.group(1))

        rect_bottom = rect_y + rect_height
        below_baseline = rect_bottom - baseline_y  # positive = below baseline

        # Old formula: below_baseline == padding == 3.
        # New formula: below_baseline == descender_ratio * font_size + padding > padding.
        # Descender ratio for common fonts ≈ 0.2, so below_baseline ≈ 0.2*14 + 3 = 5.8.
        assert below_baseline > padding, (
            f"Chip bottom is only {below_baseline:.2f}px below baseline; "
            f"must be > padding ({padding}) to accommodate descenders."
        )


class TestCodeHighlighting:
    """code_highlight / code_theme: Pygments-driven per-token fenced code colors."""

    PY_SNIPPET = "```python\ndef foo():\n    return 42\n```"

    def test_code_highlight_off_by_default_emits_single_fill(self) -> None:
        """Default style (code_highlight=False) keeps the existing single-fill,
        no-tspan render — zero SVG diff for dashboards that don't opt in."""
        svg = render(self.PY_SNIPPET)
        assert "<tspan" not in svg
        assert 'fill="#be185d"' in svg  # default code_color

    def test_code_highlight_on_with_known_language_emits_multiple_token_fills(
        self,
    ) -> None:
        """code_highlight=True on a recognized language colors tokens individually."""
        import re

        style = Style(code_highlight=True)
        svg = render(self.PY_SNIPPET, style=style)
        fills = set(re.findall(r'<tspan fill="([^"]+)"', svg))
        assert len(fills) > 1

    def test_code_highlight_on_with_unknown_language_falls_back(self) -> None:
        """An unrecognized language is a best-effort miss: fall back to plain text."""
        style = Style(code_highlight=True)
        svg = render("```notareallang\nfoo bar\n```", style=style)
        assert "<tspan" not in svg
        assert "foo bar" in svg

    def test_code_highlight_on_without_pygments_falls_back(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """When Pygments isn't importable, highlighting silently no-ops (opt-in dep)."""
        import sys

        for name in [
            n for n in sys.modules if n == "pygments" or n.startswith("pygments.")
        ]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setitem(sys.modules, "pygments", None)

        style = Style(code_highlight=True)
        svg = render(self.PY_SNIPPET, style=style)
        assert "<tspan" not in svg
        assert "return" in svg

    def test_code_highlight_respects_code_theme(self) -> None:
        """Two different code_theme values produce at least one different token fill."""
        import re

        svg_monokai = render(
            self.PY_SNIPPET, style=Style(code_highlight=True, code_theme="monokai")
        )
        svg_default = render(
            self.PY_SNIPPET, style=Style(code_highlight=True, code_theme="default")
        )
        fills_monokai = set(re.findall(r'<tspan fill="([^"]+)"', svg_monokai))
        fills_default = set(re.findall(r'<tspan fill="([^"]+)"', svg_default))
        assert fills_monokai != fills_default

    def test_code_highlight_with_bad_theme_raises(self) -> None:
        """A typo'd code_theme is a config error, not a fall-through condition."""
        from pygments.util import ClassNotFound

        style = Style(code_highlight=True, code_theme="not-a-theme")
        with pytest.raises(ClassNotFound):
            render(self.PY_SNIPPET, style=style)

    def test_code_highlight_yaml_lexer_specific_token_does_not_raise(self) -> None:
        """Regression: YAML's Literal.Scalar.Plain token isn't in every theme's
        style table and must fall back to a parent token type instead of
        raising KeyError out of pygments.style.Style.style_for_token."""
        import re

        style = Style(code_highlight=True)
        svg = render("```yaml\nkey: value\n```", style=style)
        fills = set(re.findall(r'<tspan fill="([^"]+)"', svg))
        assert len(fills) > 1


class TestBaselinePlacement:
    """Where the first baseline lands inside its line box.

    CSS puts it at ``half_leading + ascent``, splitting the leading above and
    below the text. Assuming the ascent is one em and dropping the whole leading
    under the baseline sets every line low in the box reserved for it, by an
    amount that grows with the font size.
    """

    @staticmethod
    def _baselines(svg: str) -> list[float]:
        found = re.findall(r'<text[^>]*\by="([\d.]+)"', svg)
        assert found, "no text element in the rendered SVG"
        return [float(y) for y in found]

    @classmethod
    def _first_baseline(cls, svg: str) -> float:
        return cls._baselines(svg)[0]

    @staticmethod
    def _expected(measurer, font_size: float, line_height: float) -> float:
        ascent = measurer.ascent_em * font_size
        descent = measurer.descent_em * font_size
        return (font_size * line_height - ascent - descent) / 2 + ascent

    def test_paragraph_baseline_splits_the_leading(self) -> None:
        style = Style(base_font_size=14, line_height=1.4)
        renderer = SVGRenderer(style=style)
        svg = renderer.render(parse("Body copy."), width=400, padding=0.0)

        assert self._first_baseline(svg) == pytest.approx(
            self._expected(renderer._measurer, 14, 1.4), abs=0.01
        )

    def test_heading_baseline_splits_the_leading_below_its_margin(self) -> None:
        """A heading's own top margin offsets the line box; the split is the same.

        Its leading is negative — the shipped heading line heights are tighter
        than a face's ascent-to-descender — so this is also the case that pins
        the model holding when the text overflows the box it is allotted.
        """
        style = Style(
            base_font_size=14,
            heading_line_height=1.1,
            heading_margin_top_px=20.0,
            heading_margin_bottom_px=10.0,
        )
        renderer = SVGRenderer(style=style)
        # After a paragraph, so the heading draws the margin this test offsets by.
        lead = renderer.measure(parse("Body."), width=400, padding=0.0).height
        svg = renderer.render(parse("Body.\n\n# Heading"), width=400, padding=0.0)

        font_size = style.get_heading_size(1)
        assert self._baselines(svg)[1] == pytest.approx(
            lead + 20.0 + self._expected(renderer._measurer, font_size, 1.1), abs=0.01
        )

    def test_line_advance_is_unchanged_by_where_the_baseline_sits(self) -> None:
        """Only the baseline moves — the leading it splits is the same leading.

        Consecutive baselines stay exactly one advance apart, so blocks keep
        their heights and nothing stacked below a text block has to move. That
        is what keeps this a typography fix rather than a layout one.
        """
        style = Style(base_font_size=14, line_height=1.4)
        renderer = SVGRenderer(style=style)
        # Narrow enough to wrap, so the two baselines are lines of one paragraph.
        svg = renderer.render(parse("One two three four five"), width=60, padding=0.0)

        baselines = [float(m) for m in re.findall(r'<text[^>]*\by="([\d.]+)"', svg)]
        assert len(baselines) >= 2, svg
        assert baselines[1] - baselines[0] == pytest.approx(14 * 1.4, abs=0.01)

    def test_heading_baseline_reports_the_line_the_heading_was_drawn_on(self) -> None:
        """Including at a zero heading line height, which is a reachable style.

        ``heading_line_height`` is optional with no validator, so ``0.0`` is a
        value a consumer of this library can set. Resolving it with ``or`` treats
        it as unset and falls back to the body line height, which reports a
        baseline ~19px away from the one drawn — on the one method whose whole
        contract is "the number the heading was actually drawn with".
        """
        for multiplier in (0.0, 1.1, 1.5):
            style = Style(
                base_font_size=14,
                heading_line_height=multiplier,
                heading_margin_top_px=20.0,
                heading_margin_bottom_px=10.0,
            )
            renderer = SVGRenderer(style=style)
            svg = renderer.render(parse("# Heading"), width=400, padding=0.0)

            drawn = re.search(r'<text[^>]*\by="([\d.]+)"', svg)
            assert drawn, svg
            assert renderer.heading_baseline(1) == pytest.approx(
                float(drawn.group(1)), abs=0.01
            ), f"heading_line_height={multiplier}"

    def test_heading_line_box_is_the_block_without_its_margins(self) -> None:
        """The bottom margin is rhythm; what is left is the text.

        The top margin is not in the block at all — an opening heading collapses
        it — so the line box starts at the block's own top edge.
        """
        style = Style(
            base_font_size=14, heading_margin_top_px=20.0, heading_margin_bottom_px=10.0
        )
        renderer = SVGRenderer(style=style)
        block = renderer.measure(parse("# Heading"), width=400, padding=0.0).height

        top, height = renderer.heading_line_box(1, block)
        assert top == pytest.approx(0.0)
        assert height == pytest.approx(block - 10.0)
        assert top <= renderer.heading_baseline(1) < top + height

    def test_vertical_metrics_come_from_the_face(self) -> None:
        """Both are em fractions of the loaded font, and both refuse to guess."""
        renderer = SVGRenderer(style=Style())
        measurer = renderer._measurer

        assert 0.0 < measurer.ascent_em <= 2.0
        assert 0.0 < measurer.descent_em <= 2.0
        with pytest.raises(RuntimeError):
            _ = FontMeasurer("/nonexistent/path/that/cannot/load.ttf").ascent_em

    def test_a_list_marker_sits_on_its_item_s_own_baseline(self) -> None:
        """A number labels the words beside it, so it shares their baseline.

        The two used to be the same expression, so this held by construction and
        nothing pinned it. Moving the text's baseline without moving the marker
        left ordered lists with their numbers riding above the words, growing
        with the font size and invisible to every golden — none renders a list.
        """
        for font_size in (14, 24):
            renderer = SVGRenderer(style=Style(base_font_size=font_size))
            svg = renderer.render(parse("1. First item"), width=400, padding=0.0)

            ys = re.findall(r'<text[^>]*\by="([\d.]+)"', svg)
            assert len(ys) == 2, svg
            assert float(ys[0]) == pytest.approx(float(ys[1]), abs=0.01), (
                f"at {font_size}px the marker sits at y={ys[0]} and its item at "
                f"y={ys[1]}"
            )

    def test_a_bullet_is_centered_on_its_item_s_text(self) -> None:
        """A bullet sits at the middle of its item's text.

        It gets there via the line box's middle, which is the same point: the
        half-leading above the ascent equals the half-leading below the descent,
        so the two midpoints coincide at any line height. Stated in font terms
        here so that a baseline model which broke the equality would be caught.
        """
        style = Style(base_font_size=14, line_height=1.8)
        renderer = SVGRenderer(style=style)
        svg = renderer.render(parse("- First item"), width=400, padding=0.0)

        bullet = re.search(r'<circle[^>]*\bcy="([\d.]+)"', svg)
        text = re.search(r'<text[^>]*\by="([\d.]+)"', svg)
        assert bullet and text, svg
        cy, baseline = float(bullet.group(1)), float(text.group(1))
        measurer = renderer._measurer
        expected = baseline - (measurer.ascent_em - measurer.descent_em) / 2 * 14
        assert cy == pytest.approx(expected, abs=0.01)


class TestOpeningBlockMarginCollapse:
    """A block opening a document draws no leading margin.

    That whitespace separates a heading from text above it. At the top of the
    box there is no text above it, so there is nothing to separate from -- the
    same rule ``BlockMetrics.leading_margin`` exists to let a column packer
    apply, applied to the box the renderer draws for itself.
    """

    @staticmethod
    def _baselines(svg: str) -> list[float]:
        return [float(y) for y in re.findall(r'<text[^>]*\by="([\d.]+)"', svg)]

    def test_a_heading_opening_a_document_sits_at_the_top_of_the_box(self) -> None:
        style = Style(
            base_font_size=14, heading_margin_top_px=20.0, heading_margin_bottom_px=10.0
        )
        renderer = SVGRenderer(style=style)
        svg = renderer.render(parse("# Heading"), width=400, padding=0.0)

        assert self._baselines(svg)[0] == pytest.approx(
            renderer.heading_baseline(1), abs=0.01
        )
        wider = SVGRenderer(
            style=Style(
                base_font_size=14,
                heading_margin_top_px=200.0,
                heading_margin_bottom_px=10.0,
            )
        )
        assert self._baselines(
            wider.render(parse("# Heading"), width=400, padding=0.0)
        )[0] == pytest.approx(self._baselines(svg)[0], abs=0.01), (
            "the opening heading is still carrying its own top margin"
        )

    def test_a_heading_after_a_paragraph_keeps_its_leading_margin(self) -> None:
        """Only the opener collapses -- mid-document rhythm is untouched."""
        style = Style(
            base_font_size=14, heading_margin_top_px=20.0, heading_margin_bottom_px=10.0
        )
        renderer = SVGRenderer(style=style)

        opener = renderer.measure(parse("# Heading"), width=400, padding=0.0).height
        with_paragraph = renderer.measure(
            parse("Body text.\n\n# Heading"), width=400, padding=0.0
        ).height
        paragraph = renderer.measure(parse("Body text."), width=400, padding=0.0).height

        assert with_paragraph == pytest.approx(paragraph + opener + 20.0, abs=0.01)

    def test_the_collapsed_margin_comes_off_the_block_s_height(self) -> None:
        """The box shrinks with the ink -- otherwise the gap moves to the bottom."""
        margin_top = 20.0
        drawn = Style(
            base_font_size=14,
            heading_margin_top_px=margin_top,
            heading_margin_bottom_px=10.0,
        )
        none = Style(
            base_font_size=14, heading_margin_top_px=0.0, heading_margin_bottom_px=10.0
        )
        blocks = parse("# Heading\n\nBody text.")

        assert SVGRenderer(style=drawn).measure(
            blocks, width=400, padding=0.0
        ).height == pytest.approx(
            SVGRenderer(style=none).measure(blocks, width=400, padding=0.0).height,
            abs=0.01,
        )

    def test_a_heading_opening_a_blockquote_collapses_too(self) -> None:
        """A blockquote is a box, and the rule is the box's, not the document's."""
        style = Style(
            base_font_size=14, heading_margin_top_px=20.0, heading_margin_bottom_px=10.0
        )
        renderer = SVGRenderer(style=style)

        quoted = renderer.measure(parse("> # Heading"), width=400, padding=0.0).height
        after = renderer.measure(
            parse("> Body text.\n> # Heading"), width=400, padding=0.0
        ).height
        body = renderer.measure(parse("> Body text."), width=400, padding=0.0).height

        assert after - body - quoted == pytest.approx(20.0, abs=0.01)
