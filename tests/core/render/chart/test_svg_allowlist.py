"""Allowlist checking of custom-chart template output: violations raise, never strip.

What ships is the checked tree re-serialized, never the template's raw string, so a
construct XML and HTML parse differently cannot smuggle markup past the check.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.render.chart.svg_allowlist import (
    SvgViolation,
    checked_svg_content,
)

ALLOWED = [
    '<g><rect x="1" y="2" width="3" height="4" fill="#fff"/><text>a &amp; b</text></g>',
    '<defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs>'
    '<rect fill="url(#g)" width="1" height="1"/>',
    '<defs><path id="p" d="M0 0L1 1"/></defs><use href="#p"/><use xlink:href="#p"/>',
    '<clipPath id="c"><rect width="1" height="1"/></clipPath><g clip-path="url(\'#c\')"/>',
    "<title>t</title><desc>d</desc>",
    "",
]


@pytest.mark.parametrize("content", ALLOWED)
def test_allowed_content_passes(content: str) -> None:
    checked_svg_content(content)


def test_output_is_the_checked_tree_reserialized() -> None:
    out = checked_svg_content(
        '<g class="a"><text x=\'1\'>a &amp; &lt;b&gt;</text><use xlink:href="#p"/></g>'
    )
    assert out == (
        '<g class="a"><text x="1">a &amp; &lt;b&gt;</text><use href="#p"/></g>'
    )


def test_attribute_values_are_reescaped() -> None:
    out = checked_svg_content('<text x="&quot;&gt;&lt;script&gt;">t</text>')
    assert out == '<text x="&quot;&gt;&lt;script&gt;">t</text>'


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ("<script>alert(1)</script>", "script"),
        (
            '<foreignObject><div xmlns="http://www.w3.org/1999/xhtml"/></foreignObject>',
            "foreignObject",
        ),
        ("<style>rect{fill:red}</style>", "style"),
        ('<image href="#x"/>', "image"),
        ('<a href="#x"><rect/></a>', "<a>"),
        ('<rect><animate attributeName="x"/></rect>', "animate"),
        ('<set attributeName="x"/>', "set"),
        ("<svg><rect/></svg>", "svg"),
        ('<div xmlns="http://www.w3.org/1999/xhtml"/>', "div"),
        ('<rect onclick="x()"/>', "onclick"),
        ('<rect onLoad="x()"/>', "onLoad"),
        ('<use href="https://evil.test/x.svg#a"/>', "href"),
        ('<use xlink:href="data:image/svg+xml,x"/>', "href"),
        ('<rect fill="url(https://evil.test/a.svg#f)"/>', "url()"),
        ('<rect style="fill:url(http://x/y)"/>', "url()"),
        ('<rect fill="url(#ok) url(//x)"/>', "url()"),
        ('<text x="javascript:alert(1)">t</text>', "javascript:"),
        ('<rect fill="JaVa script:x"/>', "javascript:"),
        ("<!DOCTYPE svg><g/>", "DOCTYPE"),
        ('<!DOCTYPE g [<!ENTITY a "b">]><g>&a;</g>', "DOCTYPE"),
        ("<!ENTITY a 'b'><g/>", "ENTITY"),
        # HTML ends these at the first ">", XML does not: whatever follows would
        # reach the page as live markup.
        ("<g><!--><img src=x onerror=alert(1)>--></g>", "comment"),
        ("<!--><script>alert(1)</script>-->", "comment"),
        ("<!-- a comment --><g/>", "comment"),
        ("<?x ><svg onload=alert(2)>?>", "processing instruction"),
        ("<g><![CDATA[<script>alert(1)</script>]]></g>", "CDATA"),
        # HTML lowercases attribute names, so case must not dodge a check.
        ('<use HREF="https://evil.test/x.svg#a"/>', "href"),
        ('<use xlink:HREF="https://evil.test/x.svg#a"/>', "href"),
        ('<rect ONCLICK="x()"/>', "ONCLICK"),
        # CSS escapes and URL-taking functions other than url().
        ('<rect style="fill:u\\72l(https://evil.test/a)"/>', "backslash"),
        (
            "<rect style=\"mask-image:image-set('https://evil.test/a' 1x)\"/>",
            "image-set",
        ),
        (
            "<rect style=\"mask-image:-webkit-image-set('https://x/a' 1x)\"/>",
            "image-set",
        ),
        (
            "<rect style=\"background:cross-fade(url(#a), 'https://x/b')\"/>",
            "cross-fade",
        ),
        ('<rect xml:space="preserve" data-x="1"/>', "space"),
        ("<g>", "well-formed"),
        ("<g></h>", "well-formed"),
    ],
)
def test_violation_raises_and_names_the_construct(content: str, needle: str) -> None:
    with pytest.raises(SvgViolation, match=needle):
        checked_svg_content(content)
