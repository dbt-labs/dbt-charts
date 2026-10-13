"""Each closed name list in schema_names.py matches its canonical source."""

from __future__ import annotations

import typing

from dbt_charts.core.colors import VARIANT_WORDS
from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
from dbt_charts.core.compile.models.palette import (
    HARD_FAIL_PALETTE_NAMES,
    PALETTE_WARN_ALIASES,
)
from dbt_charts.core.compile.models.schema_names import (
    VEGA_SCHEME_NAMES,
    CategorySlotToken,
    ChartWarningCode,
    FormatAlias,
    PaletteName,
    ScalePaletteName,
    StopsPaletteName,
    ThemeName,
    WarningCode,
)
from dbt_charts.core.compile.resolve.style.palette import list_palettes, palette
from dbt_charts.core.diagnostics.codes_retired import RETIRED_WARNING_CODES
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SPECS,
    PREDEFINED_TEXT_NAMES,
    PREDEFINED_TIME_SPECS,
)


def _names(alias: object) -> list[str]:
    return list(typing.get_args(alias))


def test_theme_name_is_built_in_themes_minus_private_and_diagnostics():
    expected = [
        n
        for n in list_built_in_themes()
        if not n.startswith("_") and not n.startswith("diagnostics-")
    ]
    assert _names(ThemeName) == expected
    assert "clarity" in expected


def test_palette_name_is_every_palette_plus_categorical_variants():
    variants = {
        f"{name}.{tier}"
        for name in list_palettes("categorical")
        for tier in VARIANT_WORDS
    }
    assert _names(PaletteName) == sorted({*list_palettes(), *variants})


def test_palette_name_excludes_hard_fail_and_warn_alias_names():
    names = set(_names(PaletteName))
    assert not (names & HARD_FAIL_PALETTE_NAMES)
    assert not (names & set(PALETTE_WARN_ALIASES))


def test_stops_palette_name_holds_only_names_that_resolve_to_stops():
    """`palette()` raises `ToneAsPaletteError` on every tone name, so a
    `palette:` field naming one parses and dies at resolve with
    ERR-PALETTE-UNKNOWN. Offering them was a completion whose every use breaks
    the board — and the design panel turns a completion into a control.

    `PaletteName` keeps the whole index: `style.palettes` binds roles to color
    sources, and `_base.yaml` binds `info: info`.
    """
    tones = set(list_palettes("tone"))
    names = set(_names(StopsPaletteName))
    assert not (names & tones)
    assert set(_names(PaletteName)) - names == tones
    for name in names:
        palette(name)


def test_scale_palette_name_is_union_of_palettes_and_vega_schemes_minus_excluded():
    """VEGA_SCHEME_NAMES contains "rainbow", which is also a hard-fail
    anti-pattern — the union must not resurrect it into ScalePaletteName."""
    excluded = HARD_FAIL_PALETTE_NAMES | set(PALETTE_WARN_ALIASES)
    assert "rainbow" in VEGA_SCHEME_NAMES & excluded
    expected = (set(_names(StopsPaletteName)) | VEGA_SCHEME_NAMES) - excluded
    assert _names(ScalePaletteName) == sorted(expected)


def test_format_aliases_are_the_predefined_spec_and_text_names():
    # Plain strings, not the str-mixin enum members the spec maps are keyed
    # by: pydantic would hand an enum member back, and the docs would print it.
    assert {type(n) for n in _names(FormatAlias)} == {str}
    assert _names(FormatAlias) == sorted(
        {*PREDEFINED_SPECS, *PREDEFINED_TIME_SPECS, *PREDEFINED_TEXT_NAMES}
    )


def test_category_slots_match_widest_category_palette_under_the_real_resolver():
    """The slot count walks raw theme YAML; the theme resolver is the oracle."""
    widest = 0
    for name in _names(ThemeName):
        palettes = get_theme_style(name).palettes
        assert palettes is not None
        widest = max(widest, len(palette(palettes["category"])))
    assert _names(CategorySlotToken) == [f"category[{i}]" for i in range(1, widest + 1)]


def test_warning_codes_are_registry_warnings_plus_retired():
    warnings = REGISTRY.codes(level="warning")
    assert _names(WarningCode) == sorted(warnings | RETIRED_WARNING_CODES)
    chart_scoped = {c for c in warnings if REGISTRY.get(c).domain != "query"}
    assert _names(ChartWarningCode) == sorted(chart_scoped | RETIRED_WARNING_CODES)
