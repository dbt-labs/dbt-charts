"""Style values are written into CSS, so a value must not be able to break out."""

from __future__ import annotations

import re
from dataclasses import fields, replace

import pytest

from mdsvg import Style, render

_EVIL = "x;} svg{background:url(https://evil)} .a{"


def _rule_openers(svg: str) -> list[str]:
    css = re.search(r"<style>(.*?)</style>", svg, re.S)
    assert css is not None
    return re.findall(r"([^{}]*)\{", css.group(1))


def test_font_family_cannot_open_new_css_rules() -> None:
    svg = render("hello", style=Style(font_family=_EVIL, mono_font_family=_EVIL))
    assert all(sel.strip().startswith(".md-") for sel in _rule_openers(svg))


def test_font_family_in_raw_html_style_attribute_is_escaped() -> None:
    svg = render("<b>hi</b>", style=Style(font_family=_EVIL), allow_raw_html=True)
    div = svg.split("<foreignObject", 1)[1].split("</div>", 1)[0]
    assert "url(https://evil)" not in div.split(">", 2)[1]
    assert ";}" not in div


def test_font_family_in_inline_code_style_attribute_is_escaped() -> None:
    svg = render("`code`", style=Style(mono_font_family=_EVIL))
    assert not re.search(r'style="[^"]*;\}', svg)


_VALIDATED = [
    f.name
    for f in fields(Style)
    if isinstance(getattr(Style(), f.name), str) and not f.name.endswith("font_family")
]


@pytest.mark.parametrize("field", _VALIDATED)
def test_css_breaking_value_is_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        replace(Style(), **{field: "red;} svg{background:url(https://evil)} .b{"})


def test_optional_and_numeric_fields_are_validated_when_str() -> None:
    with pytest.raises(ValueError, match="font_weight"):
        Style(font_weight="bold;}")
    with pytest.raises(ValueError, match="heading_color"):
        Style(heading_color="red;}")


@pytest.mark.parametrize("color", ["oklch(0.7 0.1 250)", "var(--bg)", "#14532d", ""])
def test_ordinary_color_values_construct(color: str) -> None:
    assert Style(text_color=color).text_color == color


def test_with_updates_revalidates() -> None:
    with pytest.raises(ValueError):
        Style().with_updates(hr_color="red;}")


def test_safe_font_stack_renders_unchanged() -> None:
    stack = "'Source Serif 4', Georgia, serif"
    svg = render("hello", style=Style(font_family=stack))
    assert f"font-family: {stack.replace(chr(39), '&#x27;')};" in svg
