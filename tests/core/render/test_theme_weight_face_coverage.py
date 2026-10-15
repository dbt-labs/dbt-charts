"""Regression guard: every weight a built-in theme cascades onto a vendored
variable family must have a matching static face in ``fonts.WEIGHT_FACE_ALIASES``.

vl-convert cannot bind a variable font's ``wght`` axis from a numeric
``font-weight`` request (see fonts/README.md "Select figure style by family",
weight section) — a family/weight pair with no row there silently renders as
Regular in every PNG/PDF export. This test walks the resolved style tree of
every production theme and fails the moment one starts cascading a weight
outside the currently-covered closed set, instead of that showing up as a
quiet visual regression in an export nobody diffed.

"-extreme" diagnostic themes intentionally probe out-of-range values by design
(their own YAML files document this) and are excluded — a face author who
explicitly opts into one accepts the fallback.
"""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel

from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
from dbt_charts.core.compile.models.style.theme import font_weight_as_css
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.fonts import WEIGHT_FACE_ALIASES

_PRODUCTION_THEMES = [
    name
    for name in list_built_in_themes()
    if name != "_base" and not name.startswith("diagnostics-")
]
_TARGET_FAMILIES = set(WEIGHT_FACE_ALIASES)


def _primary_family(family: object) -> str | None:
    if not isinstance(family, str) or not family:
        return None
    return family.split(",", 1)[0].strip().strip("'\"")


def _field_names(obj: object) -> list[str] | None:
    if isinstance(obj, BaseModel):
        return list(type(obj).model_fields)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return [f.name for f in dataclasses.fields(obj)]
    return None


def _collect_family_weight_pairs(obj: object, seen: set[int]) -> set[tuple[str, str]]:
    """Walk a resolved style tree for every (vendored family, weight) pair.

    Resolved style nodes are a mix of a top-level dataclass (``ResolvedStyle``)
    and pydantic ``BaseModel`` leaves/branches — both are walked generically via
    their declared field names, since neither is a plain dict/list container.
    """
    if obj is None or isinstance(obj, str | int | float | bool):
        return set()
    if id(obj) in seen:
        return set()

    names = _field_names(obj)
    if names is None:
        if isinstance(obj, list | tuple):
            return {
                pair
                for item in obj
                for pair in _collect_family_weight_pairs(item, seen)
            }
        if isinstance(obj, dict):
            return {
                pair
                for value in obj.values()
                for pair in _collect_family_weight_pairs(value, seen)
            }
        return set()

    seen.add(id(obj))
    pairs: set[tuple[str, str]] = set()
    family = (
        _primary_family(getattr(obj, "family", None)) if "family" in names else None
    )
    weight = getattr(obj, "weight", None) if "weight" in names else None
    if family is not None and family in _TARGET_FAMILIES and weight is not None:
        pairs.add((family, font_weight_as_css(weight)))
    for name in names:
        pairs |= _collect_family_weight_pairs(getattr(obj, name), seen)
    return pairs


def test_every_theme_cascaded_weight_has_a_registered_static_face() -> None:
    missing: set[tuple[str, str, str]] = set()
    for theme in _PRODUCTION_THEMES:
        resolved = resolve_style(get_theme_style(theme))
        for family, weight in _collect_family_weight_pairs(resolved, set()):
            if weight == "400":
                continue  # the vendored file's own default instance — always fine
            if weight not in WEIGHT_FACE_ALIASES[family]:
                missing.add((theme, family, weight))

    assert not missing, (
        "Theme(s) cascade a (family, weight) pair with no static face in "
        "fonts.WEIGHT_FACE_ALIASES — vl-convert will silently render it as "
        "Regular. Add a static face (see fonts/README.md 'Select figure style "
        f"by family') for: {sorted(missing)}"
    )


def test_every_theme_covers_the_footer_brand_weight() -> None:
    """The footer brand run sets a heavier weight than the theme's body weights.

    ``_collect_family_weight_pairs`` above walks the font weights a theme
    cascades, so it cannot see ``footer.brand_weight`` — the footer sets
    its brand run heavier without any font slot asking for it. A theme whose body family has no
    row for that weight renders the phrase heavier in a browser and flat in
    every PNG/PDF export, the same silent failure this file exists to catch.
    """
    missing: set[tuple[str, str]] = set()
    for theme in _PRODUCTION_THEMES:
        style = get_theme_style(theme)
        weight = str(style.footer.brand_weight)
        family = _primary_family(style.font.family)
        if family is None or family not in _TARGET_FAMILIES:
            continue
        if weight not in WEIGHT_FACE_ALIASES[family]:
            missing.add((theme, family))

    assert not missing, (
        f"Theme(s) whose body family has no static face at the footer brand weight, "
        f"which the footer brand run requests: {sorted(missing)}"
    )
