"""Tests for raw HTML foreignObject passthrough in markdown-svg."""

import re

import pytest

from mdsvg import RawHtmlBlock, parse, render, render_content
from mdsvg.renderer import SVGRenderer
from mdsvg.types import BlockType


class TestRawHtmlParsing:
    """Test that raw HTML blocks are detected by the parser."""

    def test_simple_html_block(self) -> None:
        """A standalone HTML block is parsed as an HTML_BLOCK."""
        doc = parse("<div>hello</div>")
        assert len(doc) == 1
        assert doc[0].block_type == BlockType.HTML_BLOCK

    def test_multiline_html_block(self) -> None:
        """Multi-line HTML blocks are captured as a single block."""
        md = "<div>\n  <p>hello</p>\n</div>"
        doc = parse(md)
        assert len(doc) == 1
        assert doc[0].block_type == BlockType.HTML_BLOCK
        assert isinstance(doc[0], RawHtmlBlock)
        assert "<p>hello</p>" in doc[0].html

    def test_html_block_between_markdown(self) -> None:
        """HTML blocks interspersed with markdown are parsed correctly."""
        md = "# Title\n\n<div>html content</div>\n\nSome text."
        doc = parse(md)
        assert len(doc) == 3
        assert doc[1].block_type == BlockType.HTML_BLOCK
        assert isinstance(doc[1], RawHtmlBlock)
        assert doc[1].html.strip() == "<div>html content</div>"

    def test_non_html_angle_bracket(self) -> None:
        """Text with < that isn't an HTML tag is parsed as a paragraph."""
        doc = parse("5 < 10 is true")
        assert len(doc) == 1
        assert doc[0].block_type == BlockType.PARAGRAPH


class TestRawHtmlRenderingDisabled:
    """When allow_raw_html is False (default), HTML is escaped."""

    def test_html_escaped_by_default(self) -> None:
        """Raw HTML is escaped to safe text when allow_raw_html is off."""
        svg = render("<div>hello</div>")
        assert "foreignObject" not in svg
        assert "&lt;div&gt;" in svg

    def test_render_content_escapes_html(self) -> None:
        """render_content also escapes HTML by default."""
        result = render_content("<div>hello</div>")
        assert "foreignObject" not in result.elements


class TestRawHtmlRenderingEnabled:
    """When allow_raw_html is True, HTML is rendered via foreignObject."""

    def test_html_in_foreign_object(self) -> None:
        """Raw HTML is wrapped in foreignObject when enabled."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<div>hello</div>")
        svg = renderer.render(blocks, width=400)
        assert "<foreignObject" in svg
        assert 'xmlns="http://www.w3.org/1999/xhtml"' in svg
        assert "<div>hello</div>" in svg

    def test_foreign_object_dimensions(self) -> None:
        """foreignObject gets proper width and height attributes."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<div>hello</div>")
        svg = renderer.render(blocks, width=500)
        assert 'width="' in svg
        # foreignObject should exist with dimensions
        fo_start = svg.index("<foreignObject")
        fo_tag = svg[fo_start : svg.index(">", fo_start) + 1]
        assert "width=" in fo_tag
        assert "height=" in fo_tag

    def test_multiline_html_in_foreign_object(self) -> None:
        """Multi-line HTML blocks render correctly in foreignObject."""
        renderer = SVGRenderer(allow_raw_html=True)
        md = "<div>\n  <p>hello</p>\n</div>"
        blocks = parse(md)
        svg = renderer.render(blocks, width=400)
        assert "<foreignObject" in svg
        assert "<p>hello</p>" in svg

    def test_mixed_markdown_and_html(self) -> None:
        """Markdown blocks render normally; HTML blocks use foreignObject."""
        renderer = SVGRenderer(allow_raw_html=True)
        md = "# Title\n\n<div>html</div>\n\nParagraph text."
        blocks = parse(md)
        svg = renderer.render(blocks, width=400)
        assert re.search(r"md-[0-9a-f]{8}-heading", svg) is not None
        assert "<foreignObject" in svg
        assert "Paragraph text." in svg

    def test_convenience_render_with_allow_raw_html(self) -> None:
        """The render() convenience function accepts allow_raw_html."""
        svg = render("<div>hello</div>", allow_raw_html=True)
        assert "<foreignObject" in svg
        assert "<div>hello</div>" in svg

    def test_convenience_render_content_with_allow_raw_html(self) -> None:
        """The render_content() convenience function accepts allow_raw_html."""
        result = render_content("<div>hello</div>", allow_raw_html=True)
        assert "<foreignObject" in result.elements


class TestRawHtmlSecurity:
    """Security: script tags and event handlers are stripped even when enabled."""

    def test_script_tags_stripped(self) -> None:
        """Script tags are removed even with allow_raw_html=True."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<script>alert('xss')</script>")
        svg = renderer.render(blocks, width=400)
        assert "<script" not in svg

    def test_event_handlers_stripped(self) -> None:
        """on* event attributes are removed even with allow_raw_html=True."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse('<div onclick="alert(1)">click</div>')
        svg = renderer.render(blocks, width=400)
        assert "onclick" not in svg
        assert "click" in svg  # content preserved

    def test_style_attribute_preserved(self) -> None:
        """Safe attributes like style are kept."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse('<div style="color: red">red text</div>')
        svg = renderer.render(blocks, width=400)
        assert 'style="color: red"' in svg


class TestRawHtmlSecurityBypasses:
    """Regression tests for XSS payloads blocked by the nh3 parser allowlist.

    A regex over tag soup cannot block these without a real DOM tree walk:
    - javascript: hrefs on allowed tags (<a> was not in the old blocked-tag list)
    - unquoted event handlers (onerror=... with no quotes, bypassing quote-requiring patterns)
    - SVG vectors with no HTML event attribute (<use href="javascript:...">)
    """

    def test_javascript_href_on_anchor_blocked(self) -> None:
        """javascript: href on <a> is stripped — <a> was not in the blocked-tag list."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<a href=\"javascript:fetch('//evil.example/')\">click</a>")
        svg = renderer.render(blocks, width=400)
        assert "javascript:" not in svg

    def test_unquoted_event_handler_blocked(self) -> None:
        """Unquoted onerror= bypasses the quote-requiring regex; must still be stripped."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<img src=x onerror=fetch('//evil.example/')>")
        svg = renderer.render(blocks, width=400)
        assert "onerror" not in svg

    def test_svg_use_javascript_href_blocked(self) -> None:
        """SVG <use href="javascript:..."> is an XSS vector with no HTML event attr."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse('<svg><use href="javascript:alert(1)"/></svg>')
        svg = renderer.render(blocks, width=400)
        assert "javascript:" not in svg


class TestFencedHtmlBlock:
    """Tests for ::: html fenced-block syntax."""

    def test_fenced_html_block_parses_as_raw_html(self) -> None:
        """::: html block is parsed as an HTML_BLOCK."""
        doc = parse("::: html\n<div>hello</div>\n:::")
        assert len(doc) == 1
        assert doc[0].block_type == BlockType.HTML_BLOCK
        assert isinstance(doc[0], RawHtmlBlock)
        assert "<div>hello</div>" in doc[0].html

    def test_fenced_html_multiline_content(self) -> None:
        """Multi-line content inside ::: html is captured as a single block."""
        md = "::: html\n<div>\n  <p>hello</p>\n</div>\n:::"
        doc = parse(md)
        assert len(doc) == 1
        assert isinstance(doc[0], RawHtmlBlock)
        assert "<p>hello</p>" in doc[0].html

    def test_fenced_html_between_markdown(self) -> None:
        """::: html block interspersed with markdown is parsed correctly."""
        md = "# Title\n\n::: html\n<div>html content</div>\n:::\n\nSome text."
        doc = parse(md)
        assert len(doc) == 3
        assert doc[1].block_type == BlockType.HTML_BLOCK
        assert isinstance(doc[1], RawHtmlBlock)
        assert "<div>html content</div>" in doc[1].html

    def test_fenced_html_no_elevation_none_policy(self) -> None:
        """Critical invariant: ::: html block inside html_policy: none (allow_raw_html=False)
        renders as escaped text, not raw HTML. The block cannot elevate the document's policy.
        """
        md = '::: html\n<div class="widget">hello</div>\n:::'
        svg = render(md)  # allow_raw_html=False by default
        assert "foreignObject" not in svg
        # The HTML is escaped as text
        assert "&lt;" in svg or "div" in svg  # content present but escaped

    def test_fenced_html_renders_via_foreign_object_when_allowed(self) -> None:
        """::: html block renders via foreignObject when allow_raw_html=True."""
        renderer = SVGRenderer(allow_raw_html=True)
        doc = parse("::: html\n<div>hello</div>\n:::")
        svg = renderer.render(doc, width=400)
        assert "<foreignObject" in svg
        assert "<div>hello</div>" in svg

    def test_fenced_html_sanitizes_scripts(self) -> None:
        """Script tags in ::: html blocks are stripped even when allow_raw_html=True."""
        renderer = SVGRenderer(allow_raw_html=True)
        doc = parse("::: html\n<script>alert('xss')</script>\n:::")
        svg = renderer.render(doc, width=400)
        assert "<script" not in svg

    def test_fenced_html_convenience_render_no_elevation(self) -> None:
        """render() with default args escapes ::: html block content."""
        result = render_content("::: html\n<div>hello</div>\n:::")
        assert "foreignObject" not in result.elements


class TestRawHtmlXmlWellFormedness:
    """nh3's HTML5 output has two XML-incompatibilities; both must be fixed.

    1. Void elements lack self-closing slashes (<br> is HTML5-valid but not XHTML).
    2. nh3 emits &nbsp; for U+00A0, which XML does not define.

    The <foreignObject> wrapping raw HTML lives inside an SVG document that is
    parsed as XML; any deviation from XHTML causes the whole document to be rejected.
    All tests verify by parsing the full rendered SVG with ET.fromstring().
    """

    def test_br_produces_valid_xml(self) -> None:
        """<br/> survives nh3 sanitization as valid XHTML inside foreignObject."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<p>a<br/>b</p>")
        svg = renderer.render(blocks, width=400)
        # Raises ParseError if void element lost its self-closing slash.
        ET.fromstring(svg)

    def test_img_produces_valid_xml(self) -> None:
        """<img/> survives nh3 sanitization as valid XHTML inside foreignObject."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse('<img src="https://example.com/x.png" alt="x"/>')
        svg = renderer.render(blocks, width=400)
        ET.fromstring(svg)

    def test_hr_produces_valid_xml(self) -> None:
        """<hr/> survives nh3 sanitization as valid XHTML inside foreignObject."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse("<div>above<hr/>below</div>")
        svg = renderer.render(blocks, width=400)
        ET.fromstring(svg)

    def test_nbsp_entity_produces_valid_xml(self) -> None:
        """U+00A0 in content does not produce &nbsp; (undefined in XML)."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        # The non-breaking space character that nh3 serializes as &nbsp;
        blocks = parse("<p>hello world</p>")
        svg = renderer.render(blocks, width=400)
        assert "&nbsp;" not in svg
        ET.fromstring(svg)

    def test_gt_in_attribute_value_produces_valid_xml(self) -> None:
        """A literal > in an attribute value is re-escaped as &gt; for XHTML."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse(
            '<a href="https://example.com/?a=1&amp;b=2" title="a>b">link</a>'
        )
        svg = renderer.render(blocks, width=400)
        ET.fromstring(svg)

    def test_attribute_value_escaped_exactly_once(self) -> None:
        """An & in an attribute value round-trips, not double-escaped."""
        import xml.etree.ElementTree as ET

        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse('<a href="https://example.com/?a=1&amp;b=2">link</a>')
        svg = renderer.render(blocks, width=400)
        root = ET.fromstring(svg)
        anchors = [el for el in root.iter() if el.tag.endswith("}a")]
        assert anchors, f"no <a> element found in {svg!r}"
        assert anchors[0].get("href") == "https://example.com/?a=1&b=2"


# OWASP XSS Filter Evasion Cheat Sheet payload corpus.
# Reference: https://owasp.org/www-community/xss-filter-evasion-cheatsheet
# Sections: #tests (javascript: URLs, event handlers, SVG, blocked tags),
# #character-escape-sequences (case/whitespace variants),
# #methods-to-bypass-waf-cross-site-scripting (data: URIs).
#
# Categories covered: javascript: URLs, event-handler variants (quoted and
# unquoted), SVG vectors, data: URIs, and blocked-tag vectors (script, iframe,
# object, embed, form, meta).
#
# CSS-expression vectors (e.g. style="background:url(javascript:...)") are NOT
# included: nh3 does not parse CSS values — the style attribute passes through
# verbatim when allow_raw_html=True (documented in renderer.py's
# _HTML_ALLOWED_ATTRIBUTES comment). CSS expressions are an IE-only concern
# that modern browsers ignore. When allow_raw_html=False (the default), no
# <foreignObject> is emitted so no raw HTML reaches the browser regardless of
# style content.
_OWASP_PAYLOADS = [
    # javascript: URL vectors — nh3 strips any href/src whose scheme is not in
    # url_schemes={"http","https","mailto"} (#tests, #character-escape-sequences)
    pytest.param(
        '<a href="javascript:alert(1)">click</a>',
        "javascript:",
        id="js-href-anchor-basic",
    ),
    pytest.param(
        '<a href="JAVAscript:alert(1)">click</a>',
        "javascript:",
        id="js-href-anchor-case",
    ),
    pytest.param(
        '<a href="  javascript:alert(1)">click</a>',
        "javascript:",
        id="js-href-anchor-leading-whitespace",
    ),
    pytest.param(
        '<img src="javascript:alert(1)">',
        "javascript:",
        id="js-src-img",
    ),
    # data: URI vectors — data: is not in url_schemes
    # (#methods-to-bypass-waf-cross-site-scripting)
    pytest.param(
        '<a href="data:text/html,<script>alert(1)</script>">click</a>',
        "data:",
        id="data-uri-href-html",
    ),
    pytest.param(
        '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">click</a>',
        "data:",
        id="data-uri-href-base64",
    ),
    # Event-handler variants — quoted (#tests)
    # on* attributes are not in _HTML_ALLOWED_ATTRIBUTES, so nh3 strips them.
    pytest.param(
        '<img src=x onerror="alert(1)">',
        "onerror",
        id="event-onerror-quoted",
    ),
    pytest.param(
        '<div onclick="alert(1)">click</div>',
        "onclick",
        id="event-onclick-quoted",
    ),
    pytest.param(
        '<div onmouseover="alert(1)">text</div>',
        "onmouseover",
        id="event-onmouseover-quoted",
    ),
    pytest.param(
        '<a href="#" onfocus="alert(1)">link</a>',
        "onfocus",
        id="event-onfocus-quoted",
    ),
    # Event-handler variants — unquoted (bypassed the old regex; #tests)
    pytest.param(
        "<img src=x onerror=alert(1)>",
        "onerror",
        id="event-onerror-unquoted",
    ),
    pytest.param(
        "<div onclick=alert(1)>click</div>",
        "onclick",
        id="event-onclick-unquoted",
    ),
    # SVG vectors — svg/use/animate are not in _HTML_ALLOWED_TAGS, so nh3
    # unwraps them and re-emits their text children; on* attributes are stripped
    # by the attribute allowlist (#tests)
    pytest.param(
        "<svg onload=alert(1)>text</svg>",
        "onload",
        id="svg-onload",
    ),
    pytest.param(
        '<svg><use href="javascript:alert(1)"/></svg>',
        "javascript:",
        id="svg-use-javascript-href",
    ),
    pytest.param(
        "<svg><animate onbegin=alert(1) attributeName=x></svg>",
        "onbegin",
        id="svg-animate-onbegin",
    ),
    # Blocked-tag vectors — none of these tags are in _HTML_ALLOWED_TAGS;
    # nh3 unwraps them (strips tags, re-emits children), except <script> whose
    # content is also removed (#tests)
    pytest.param(
        "<script>alert(1)</script>",
        "<script",
        id="blocked-script",
    ),
    pytest.param(
        '<iframe src="javascript:alert(1)"></iframe>',
        "<iframe",
        id="blocked-iframe",
    ),
    pytest.param(
        '<object data="javascript:alert(1)"></object>',
        "<object",
        id="blocked-object",
    ),
    pytest.param(
        '<embed src="javascript:alert(1)">',
        "<embed",
        id="blocked-embed",
    ),
    pytest.param(
        '<form action="javascript:alert(1)"><button>click</button></form>',
        "<form",
        id="blocked-form",
    ),
    pytest.param(
        '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
        "<meta",
        id="blocked-meta",
    ),
]


class TestOwaspPayloadCorpus:
    """OWASP XSS Filter Evasion Cheat Sheet payload corpus against the nh3 sanitizer.

    Each payload is fed through SVGRenderer(allow_raw_html=True) so that
    _sanitize_html() is exercised. When allow_raw_html=False no <foreignObject>
    is emitted, so the sanitizer is a no-op — the corpus must run with it on.
    """

    @pytest.mark.parametrize("payload,blocked", _OWASP_PAYLOADS)
    def test_payload_blocked_at_sanitizer(self, payload: str, blocked: str) -> None:
        """Each OWASP payload is rejected or neutralized by the nh3 allowlist."""
        renderer = SVGRenderer(allow_raw_html=True)
        blocks = parse(payload)
        svg = renderer.render(blocks, width=400)
        assert blocked.lower() not in svg.lower()
