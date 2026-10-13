"""Closed name lists the authored models expose as ``Literal`` enums.

Each list is derived at import from the package data that defines it — theme
and palette YAML, ``PREDEFINED_SPECS``, the diagnostics ``REGISTRY`` — so the
JSON Schema offers exactly what the engine accepts, with nothing to regenerate.

The derivation runs while the models that use these aliases are still
importing, so it reads only leaf modules and raw YAML: the palette resolver
and ``get_theme_style()`` both import (or validate through) those models.

Static checkers cannot see a ``Literal`` built at runtime, so under
``TYPE_CHECKING`` every alias is ``str``.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Literal, TypeAlias

import yaml
from importlib_resources import files

from dbt_charts.core.colors import VARIANT_WORDS
from dbt_charts.core.compile.models.palette import (
    HARD_FAIL_PALETTE_NAMES,
    PALETTE_WARN_ALIASES,
    PaletteIndexEntry,
    build_palette_index,
)
from dbt_charts.core.diagnostics.codes_retired import RETIRED_WARNING_CODES
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.text.predefined_formats import (
    PREDEFINED_SPECS,
    PREDEFINED_TEXT_NAMES,
    PREDEFINED_TIME_SPECS,
    PredefinedNumberFormat,
    PredefinedTimeFormat,
)
from dbt_charts.core.utils import UniqueKeyLoader

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

_DEFAULTS_DIR = files("dbt_charts.core") / "defaults"
_THEMES_DIR = _DEFAULTS_DIR / "themes"


# Vega/Vega-Lite's built-in named color schemes (https://vega.github.io/vega/docs/schemes/),
# the only strings ``ScaleTargetConfig.palette`` forwards straight through to VL's
# ``scale.scheme``. Sampled-discrete shorthand like "blues-9" is not a real scheme
# name — VL fails deep inside rendering ("Cannot read properties of undefined")
# rather than rejecting it up front, so this is validated at compile time instead.
#
# Empirically verified against this repo's pinned vl-convert (not hand-copied from
# docs alone): every name below was round-tripped through
# ``vlc.vegalite_to_svg`` with a quantitative ``scale.scheme`` encoding and
# confirmed to render — see ``test_vega_scheme_names_parity.py``, which re-runs
# that sweep and fails if this set and the bundled renderer ever diverge.
VEGA_SCHEME_NAMES: frozenset[str] = frozenset(
    {
        # Categorical
        "accent",
        "category10",
        "category20",
        "category20b",
        "category20c",
        "dark2",
        "observable10",
        "paired",
        "pastel1",
        "pastel2",
        "set1",
        "set2",
        "set3",
        "tableau10",
        "tableau20",
        # Sequential (single-hue)
        "blues",
        "tealblues",
        "teals",
        "greens",
        "browns",
        "greys",  # codespell:ignore greys
        "oranges",
        "purples",
        "reds",
        "warmgreys",
        # Sequential (multi-hue)
        "viridis",
        "magma",
        "inferno",
        "plasma",
        "cividis",
        "turbo",
        "bluegreen",
        "bluepurple",
        "goldgreen",
        "goldorange",
        "goldred",
        "greenblue",
        "orangered",
        "purplebluegreen",
        "purpleblue",
        "purplered",
        "redpurple",
        "yellowgreenblue",
        "yellowgreen",
        "yelloworangebrown",
        "yelloworangered",
        # Diverging
        "blueorange",
        "brownbluegreen",
        "purplegreen",
        "pinkyellowgreen",
        "purpleorange",
        "redblue",
        "redgrey",
        "redyellowblue",
        "redyellowgreen",
        "spectral",
        # Cyclical
        "rainbow",
        "sinebow",
        # For dark backgrounds
        "darkblue",
        "darkgold",
        "darkgreen",
        "darkmulti",
        "darkred",
        # For light backgrounds
        "lightgreyred",
        "lightgreyteal",
        "lightmulti",
        "lightorange",
        "lighttealblue",
    }
)


def built_in_theme_stems(themes_dir: Traversable) -> list[str]:
    """Sorted stems of every ``*.yaml`` theme file in ``themes_dir``."""
    return sorted(
        entry.name.removesuffix(".yaml")
        for entry in themes_dir.iterdir()
        if entry.name.endswith(".yaml")
    )


@cache
def _category_binding(theme: str) -> str:
    """The palette ``theme`` binds to the ``category`` role, through ``extends:``."""
    data = yaml.load(
        (_THEMES_DIR / f"{theme}.yaml").read_text(encoding="utf-8"),
        Loader=UniqueKeyLoader,
    )
    palettes = data["style"].get("palettes")
    if palettes is not None and "category" in palettes:
        return palettes["category"]
    parent = data["extends"]
    if not isinstance(parent, str):
        raise ValueError(
            f"theme '{theme}': `extends:` must name one theme to derive the "
            f"category slot count, got {parent!r}"
        )
    return _category_binding(parent)


def _color_count(entry: PaletteIndexEntry) -> int:
    if entry["family"] != "categorical":
        raise ValueError(f"category role bound to non-categorical palette {entry}")
    data = yaml.load(entry["path"].read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    return len(data["colors"])


_palettes = build_palette_index(_DEFAULTS_DIR / "palettes")
_tones = {name for name, entry in _palettes.items() if entry["family"] == "tone"}
_categorical = [n for n, e in _palettes.items() if e["family"] == "categorical"]

# Built-in theme stems minus the private `_base` completeness floor and the
# diagnostic-only themes.
_theme_names = [
    name
    for name in built_in_theme_stems(_THEMES_DIR)
    if not name.startswith("_") and not name.startswith("diagnostics-")
]
# Every categorical palette derives every variant tier live, so each gets a
# completion for all of them.
_palette_names = sorted(
    {*_palettes, *(f"{name}.{tier}" for name in _categorical for tier in VARIANT_WORDS)}
)
# A tone resolves to no stops: `palette()` refuses every one.
_stops_palette_names = sorted(set(_palette_names) - _tones)
# VEGA_SCHEME_NAMES includes "rainbow", a hard-fail anti-pattern; offering it
# would promote the exact name the resolver refuses.
_scale_palette_names = sorted(
    {*_stops_palette_names, *VEGA_SCHEME_NAMES}
    - (HARD_FAIL_PALETTE_NAMES | set(PALETTE_WARN_ALIASES))
)
# A higher slot resolves under no shipped theme, so offering it would complete
# authors straight into an UnknownColorError.
_category_slots = max(
    _color_count(_palettes[_category_binding(name)]) for name in _theme_names
)
# Native formatters (Python-only, Vega-invalid) have no d3 spec, so the spec
# maps exclude them. Their keys are str-mixin enum members; a Literal needs the
# plain values.
_number_formats = sorted(
    m.value for m in PredefinedNumberFormat if m in PREDEFINED_SPECS
)
_time_formats = sorted(
    m.value for m in PredefinedTimeFormat if m in PREDEFINED_TIME_SPECS
)
_warning_codes = REGISTRY.codes(level="warning")
# A query-domain diagnostic never carries a chart id, so a chart-scoped
# `warnings_ignore` naming one would silently suppress nothing.
_chart_warning_codes = {c for c in _warning_codes if REGISTRY.get(c).domain != "query"}

if TYPE_CHECKING:
    ThemeName: TypeAlias = str
    PaletteName: TypeAlias = str
    StopsPaletteName: TypeAlias = str
    ScalePaletteName: TypeAlias = str
    FormatAlias: TypeAlias = str
    CategorySlotToken: TypeAlias = str
    WarningCode: TypeAlias = str
    ChartWarningCode: TypeAlias = str
else:
    # Built-in themes offered as `theme:`/`extends:` value completions.
    ThemeName = Literal[tuple(_theme_names)]
    # The whole palette index, which only `style.palettes` (the role map) accepts.
    PaletteName = Literal[tuple(_palette_names)]
    # PaletteName minus the tone family — the names a `palette:` field takes.
    StopsPaletteName = Literal[tuple(_stops_palette_names)]
    # StopsPaletteName plus the Vega scheme names ScaleTargetConfig.palette
    # forwards to VL's scale.scheme, minus anti-pattern names.
    ScalePaletteName = Literal[tuple(_scale_palette_names)]
    # Engine-predefined format names. A raw d3 spec or a style.formats alias is
    # equally legal, so every field carrying one reads `FormatAlias | str`: the
    # Literal names the shortcuts, it does not close the set. The column's type
    # decides which half applies.
    FormatAlias = Literal[
        tuple(sorted({*_number_formats, *_time_formats, *PREDEFINED_TEXT_NAMES}))
    ]
    # `category[1]`..`category[N]`, N the widest palette any built-in theme
    # binds to the `category` role. The field reads `CategorySlotToken | str`:
    # dotted palette tokens and literal hex stay legal.
    CategorySlotToken = Literal[
        tuple(f"category[{i}]" for i in range(1, _category_slots + 1))
    ]
    # Retired codes stay legal: a board that suppresses one keeps loading, with
    # a warning.
    WarningCode = Literal[tuple(sorted(_warning_codes | RETIRED_WARNING_CODES))]
    ChartWarningCode = Literal[
        tuple(sorted(_chart_warning_codes | RETIRED_WARNING_CODES))
    ]
