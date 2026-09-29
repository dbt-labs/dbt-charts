"""Font registration helpers for consistent rendering/export."""

from __future__ import annotations

import re
from functools import partial

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


def _replace_family_value(
    match: re.Match[str], base_family: str, target_family: str
) -> str:
    return (
        match.group(1)
        + match.group(2).replace(base_family, target_family)
        + match.group(3)
    )


def _rewrite_weight_face_in_tag(tag: str) -> str:
    weight_match = re.search(r'font-weight\s*=\s*"(\d+)"', tag)
    if weight_match is None:
        return tag
    weight = weight_match.group(1)
    for base_family, by_weight in WEIGHT_FACE_ALIASES.items():
        target_family = by_weight.get(weight)
        if target_family is not None and base_family in tag:
            return _FONT_FAMILY_VALUE_PATTERN.sub(
                partial(
                    _replace_family_value,
                    base_family=base_family,
                    target_family=target_family,
                ),
                tag,
            )
    return tag


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
    passes through unchanged, same as any weight outside the covered set.
    """
    if not any(
        f'font-weight="{escape_attr(w)}"' in svg_content for w in _COVERED_WEIGHTS
    ):
        return svg_content
    return _TAG_PATTERN.sub(
        lambda m: _rewrite_weight_face_in_tag(m.group(0)), svg_content
    )
