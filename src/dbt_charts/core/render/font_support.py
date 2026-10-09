"""Font registration helpers for consistent rendering/export."""

from __future__ import annotations

import html
import re

from dbt_charts.core.fonts import (
    INTER_FONT_FAMILY,
    INTER_VARIABLE_FONT_FAMILY,
    WEIGHT_FACE_ALIASES,
    get_fonts_dir,
)
from dbt_charts.core.render.svg_utils import escape_attr

_vl_convert_registered = False


def register_vl_convert_fonts(vlc_module: object) -> None:
    """Register vendored fonts for vl-convert backed chart rendering."""
    global _vl_convert_registered

    if _vl_convert_registered:
        return

    register_font_directory = getattr(vlc_module, "register_font_directory", None)
    if callable(register_font_directory):
        register_font_directory(str(get_fonts_dir()))
    _vl_convert_registered = True


_FONT_FAMILY_ATTR_PATTERN = re.compile(
    rf"""(font-family\s*=\s*["']){re.escape(INTER_FONT_FAMILY)}(["'])"""
)
_FONT_FAMILY_STYLE_PATTERN = re.compile(
    rf"""(font-family\s*:\s*["']?){re.escape(INTER_FONT_FAMILY)}(["']?\s*[;"])"""
)
_CSS_VAR_STYLE_PATTERN = re.compile(
    rf"""(--dbt-font-family\s*:\s*["']?){re.escape(INTER_FONT_FAMILY)}(["']?\s*;)"""
)


def _replace_inter_family(match: re.Match[str]) -> str:
    return f"{match.group(1)}{INTER_VARIABLE_FONT_FAMILY}{match.group(2)}"


def normalize_svg_font_families_for_vl_convert(svg_content: str) -> str:
    """Map public font family names to vendored font names for vl-convert only.

    dbt charts keeps emitting the public family name ``Inter`` in its SVG/CSS
    contract. The vendored variable font rasterizes reliably through
    ``vl-convert`` only when referenced by its internal family name,
    ``Inter Variable``. Normalize only the SVG payload sent into vl-convert so
    authored SVG output remains unchanged.

    ``Source Serif 4`` needs no rewrite: exactly one vendored file now claims that
    family, and its internal name already matches the public one. (Until the font
    registry landed, two files in the registered directory both claimed it — a static
    build and a variable one — and which of them vl-convert bound to was undefined.)
    """
    normalized = svg_content
    if INTER_FONT_FAMILY in svg_content:
        normalized = _FONT_FAMILY_ATTR_PATTERN.sub(_replace_inter_family, normalized)
        normalized = _FONT_FAMILY_STYLE_PATTERN.sub(_replace_inter_family, normalized)
        normalized = _CSS_VAR_STYLE_PATTERN.sub(_replace_inter_family, normalized)
    # dbt Sans Tabular, the dbt serif oldstyle faces, and Source Serif 4 need no alias
    # rewrite: their public family names already match the vendored files' internal
    # family names, which vl-convert discovers from the registered font directory.
    # The weight rewrite below applies regardless of which family is present.
    return normalize_svg_font_weights_for_vl_convert(normalized)


_TAG_PATTERN = re.compile(r"<[a-zA-Z][^>]*>")
_FONT_FAMILY_VALUE_PATTERN = re.compile(r'(font-family\s*=\s*")([^"]*)(")')
_COVERED_WEIGHTS = {w for by_weight in WEIGHT_FACE_ALIASES.values() for w in by_weight}
_COVERED_WEIGHT_PATTERN = re.compile(
    rf"""font-weight\s*[=:]\s*"?(?:{"|".join(sorted(_COVERED_WEIGHTS))})\b"""
)
_STYLE_FONT_WEIGHT_PATTERN = re.compile(r"font-weight\s*:\s*(\d+)")
_CLASS_FAMILY_RULE_PATTERN = re.compile(
    r"\.([\w-]+)\s*\{[^}]*?font-family\s*:\s*((?:&#?\w+;|[^;}])+)"
)
_CLASS_ATTR_PATTERN = re.compile(r'class\s*=\s*"([^"]*)"')
_TEXT_ELEMENT_PATTERN = re.compile(r"(<text\b[^>]*>)(.*?)(</text>)", re.DOTALL)
_TSPAN_OPEN_PATTERN = re.compile(r"<tspan\b[^>]*>")


def _weight_aliased_family(family: str, weight: str) -> str | None:
    for base_family, by_weight in WEIGHT_FACE_ALIASES.items():
        target_family = by_weight.get(weight)
        if target_family is not None and base_family in family:
            return family.replace(base_family, target_family)
    return None


def _rewrite_weight_face_in_tag(tag: str) -> str:
    weight_match = re.search(r'font-weight\s*=\s*"(\d+)"', tag)
    if weight_match is None:
        return tag

    def alias_family(match: re.Match[str]) -> str:
        aliased = _weight_aliased_family(match.group(2), weight_match.group(1))
        if aliased is None:
            return match.group(0)
        return match.group(1) + aliased + match.group(3)

    return _FONT_FAMILY_VALUE_PATTERN.sub(alias_family, tag)


def _rewrite_inline_weight_tspans(svg_content: str) -> str:
    """Give a ``<tspan style="font-weight: W">`` the static face for its weight.

    mdsvg paints bold as an inline style on a tspan and takes the family from a
    ``<style>`` class rule on the enclosing ``<text>``, so the weight and the
    family never share a tag. The family is read from the text element and written
    onto the tspan as an attribute.
    """
    class_families = {
        match.group(1): match.group(2).strip()
        for match in _CLASS_FAMILY_RULE_PATTERN.finditer(svg_content)
    }

    def text_family(open_tag: str) -> str | None:
        attribute = _FONT_FAMILY_VALUE_PATTERN.search(open_tag)
        if attribute is not None:
            return attribute.group(2)
        class_attr = _CLASS_ATTR_PATTERN.search(open_tag)
        classes = class_attr.group(1).split() if class_attr is not None else []
        return next((class_families[c] for c in classes if c in class_families), None)

    def rewrite_text(match: re.Match[str]) -> str:
        open_tag, body, close_tag = match.groups()
        family = text_family(open_tag)
        if family is None:
            return match.group(0)

        def rewrite_tspan(tspan: re.Match[str]) -> str:
            tag = tspan.group(0)
            weight_match = _STYLE_FONT_WEIGHT_PATTERN.search(tag)
            if weight_match is None:
                return tag
            aliased = _weight_aliased_family(family, weight_match.group(1))
            if aliased is None:
                return tag
            return (
                f'<tspan font-family="{escape_attr(html.unescape(aliased))}"'
                f"{tag[len('<tspan') :]}"
            )

        return open_tag + _TSPAN_OPEN_PATTERN.sub(rewrite_tspan, body) + close_tag

    return _TEXT_ELEMENT_PATTERN.sub(rewrite_text, svg_content)


def normalize_svg_font_weights_for_vl_convert(svg_content: str) -> str:
    """Rewrite a vendored family + cascaded weight to its dedicated static face.

    vl-convert (resvg) cannot bind a variable font's ``wght`` axis from a numeric
    ``font-weight`` attribute — every weight below resvg's synthetic-bold threshold
    (~600) collapses to Regular, and even above that threshold it applies a fixed
    embolden pass rather than the font's real SemiBold cut. The browser has no such
    limitation: it interpolates the served variable woff2 correctly at any weight, so
    only the vl-convert-bound copy needs this rewrite.

    Scoped per start-tag (not a blanket string substitution) because the rewrite is
    conditional on a second, co-occurring ``font-weight`` attribute — a family/weight
    pair with no row in ``WEIGHT_FACE_ALIASES`` (no static face registered for it)
    passes through unchanged, same as any weight outside the covered set. The one
    cross-tag case is a tspan weighted by inline style, whose family is the
    enclosing text's.
    """
    if _COVERED_WEIGHT_PATTERN.search(svg_content) is None:
        return svg_content
    return _TAG_PATTERN.sub(
        lambda m: _rewrite_weight_face_in_tag(m.group(0)),
        _rewrite_inline_weight_tspans(svg_content),
    )
