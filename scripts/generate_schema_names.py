#!/usr/bin/env python3
"""Generate schema_names.py — closed name lists baked in as Literal aliases.

Themes, palettes and format aliases are closed sets fixed at build time (read
from YAML packaged inside dbt_charts), but nothing about them reaches the
authored schema. This script snapshots them into a committed Python module so the
JSON Schema generator can introspect a real Literal type instead of a
hand-written enum list.

Re-run after adding/removing a theme or palette YAML file, or an entry in
PREDEFINED_SPECS (predefined_formats.py):

    just generate-schema-names

The drift test (test_schema_names.py::test_schema_names_matches_generator_output)
fails when the committed module diverges from a fresh regeneration.
"""

from __future__ import annotations

import sys
from pathlib import Path

# parents[1] is the dbt-charts package root in both monorepo (dbt-charts/scripts/)
# and standalone export (scripts/ after Copybara core.move("dbt-charts", "")).
_DBT_CHARTS_DIR = Path(__file__).resolve().parents[1]
_SRC = _DBT_CHARTS_DIR / "src"
_OUT = _SRC / "dbt_charts" / "core" / "compile" / "models" / "schema_names.py"

sys.path.insert(0, str(_SRC))


def _literal_block(var_name: str, values: list[str]) -> str:
    # A one-value Literal is written inline. A magic trailing comma does not
    # hold a single-element subscript open, so `ruff format` collapses the
    # exploded form -- and the committed file would then never match a fresh
    # regeneration, failing the drift test on every run.
    if len(values) == 1:
        return f'{var_name} = Literal["{values[0]}"]\n'
    items = ",\n".join(f'    "{v}"' for v in values)
    return f"{var_name} = Literal[\n{items},\n]\n"


def main() -> None:
    from dbt_charts.core.colors import VARIANT_WORDS
    from dbt_charts.core.compile.config import get_theme_style, list_built_in_themes
    from dbt_charts.core.compile.models.primitives import VEGA_SCHEME_NAMES
    from dbt_charts.core.compile.resolve.style.palette import (
        _HARD_FAIL_NAMES,
        _WARN_ALIASES,
        list_palettes,
        palette,
    )
    from dbt_charts.core.text.predefined_formats import (
        PREDEFINED_SPECS,
        PREDEFINED_TIME_SPECS,
    )

    theme_names = sorted(
        name
        for name in list_built_in_themes()
        if not name.startswith("_") and not name.startswith("diagnostics-")
    )
    # Engine-owned: predefined number + time format names offered as completions.
    # Native formatters (Python-only, Vega-invalid) live in PREDEFINED_NATIVE and
    # are intentionally absent from PREDEFINED_SPECS and PREDEFINED_TIME_SPECS, so
    # every list below excludes them without an explicit subtraction.
    #
    # Split by kind: a d3 number spec baked onto a temporal axis renders garbage,
    # so a field that knows its kind (number_format, time_format) offers and
    # accepts only its own half. The union stays for the kind-agnostic slots,
    # where the column's type decides which half applies.
    number_format_aliases = sorted(PREDEFINED_SPECS)
    time_format_aliases = sorted(PREDEFINED_TIME_SPECS)
    format_aliases = sorted({*number_format_aliases, *time_format_aliases})
    # Every categorical palette derives every literal variant tier live
    # (variant(), compile/resolve/style/palette.py) -- no companion file, so
    # every categorical name gets a completion for all four, mechanically,
    # never hand-listed.
    categorical_variant_names = sorted(
        f"{name}.{tier}"
        for name in list_palettes("categorical")
        for tier in VARIANT_WORDS
    )
    palette_names = sorted({*list_palettes(), *categorical_variant_names})
    # A `palette:` field takes stops, and a tone resolves to none: `palette()`
    # raises `ToneAsPaletteError` on every one, so naming a tone there parses
    # and dies at resolve with ERR-PALETTE-UNKNOWN. The role map keeps the whole
    # index (`_base.yaml` binds `info: info`) — a role names a color source,
    # which is what a tone is.
    stops_palette_names = sorted(set(palette_names) - set(list_palettes("tone")))
    # Every slot the `category` role can seat under any built-in theme. A
    # higher index resolves under no shipped theme, so offering it would
    # complete authors straight into an UnknownColorError.
    category_slots = max(
        len(palette(theme_palettes["category"]))
        for name in theme_names
        if (theme_palettes := get_theme_style(name).palettes) is not None
        and "category" in theme_palettes
    )
    # VEGA_SCHEME_NAMES includes "rainbow", which is also a _HARD_FAIL_NAMES
    # anti-pattern (perceptually non-uniform) — offering it in scale-palette
    # completion would promote the exact name the project hard-fails on.
    excluded_scheme_names = _HARD_FAIL_NAMES | set(_WARN_ALIASES)
    scale_palette_names = sorted(
        {*stops_palette_names, *VEGA_SCHEME_NAMES} - excluded_scheme_names
    )

    content = (
        '"""Generated. Do not edit by hand — run `just generate-schema-names`.\n\n'
        "Source: list_built_in_themes() (compile/config.py), PREDEFINED_SPECS\n"
        "(text/predefined_formats.py), list_palettes() and palette()\n"
        "(compile/resolve/style/palette.py), VEGA_SCHEME_NAMES\n"
        "(compile/models/primitives.py), get_theme_style() (compile/config.py).\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Literal\n\n"
        "# Built-in theme stems offered as `theme:`/`extends:` value completions.\n"
        "# Excludes the private `_base` completeness floor and diagnostic-only themes.\n"
        f"{_literal_block('ThemeName', theme_names)}\n"
        "# Named dbt charts palettes (sequential, diverging, categorical, scaffold, tone).\n"
        "# Excludes hard-fail anti-pattern names and warn-alias migration names — neither\n"
        "# is a real palette YAML entry, so list_palettes() already omits them.\n"
        "# The whole index, which only `style.palettes` (the role map) accepts.\n"
        f"{_literal_block('PaletteName', palette_names)}\n"
        "# PaletteName minus the tone family — the names a `palette:` field takes.\n"
        "# A tone is a color-token source and resolves to no stops, so `palette()`\n"
        "# refuses every one: naming a tone there parses and dies at resolve.\n"
        f"{_literal_block('StopsPaletteName', stops_palette_names)}\n"
        "# StopsPaletteName union Vega/Vega-Lite scheme names — the only closed set\n"
        "# ScaleTargetConfig.palette accepts (it also forwards Vega scheme names\n"
        "# straight through to VL's scale.scheme). Hard-fail and warn-alias scheme\n"
        "# names (e.g. 'rainbow') are excluded — see _HARD_FAIL_NAMES/_WARN_ALIASES.\n"
        f"{_literal_block('ScalePaletteName', scale_palette_names)}\n"
        "# Engine-predefined format names (PredefinedNumberFormat + PredefinedTimeFormat).\n"
        "# A predefined name resolves to a d3 spec or native formatter at render time;\n"
        "# a raw d3 spec or user style.formats alias is equally legal, so every field\n"
        "# carrying this reads `FormatAlias | str` — the Literal names the shortcuts,\n"
        "# it does not close the set. Kind-agnostic slots (`format:`) take this one;\n"
        "# a slot that knows its kind takes the matching half below.\n"
        f"{_literal_block('FormatAlias', format_aliases)}\n"
        "# The number half — PredefinedNumberFormat, i.e. every name resolving to a\n"
        "# d3 *number* spec. What `number_format` offers and accepts.\n"
        f"{_literal_block('NumberFormatAlias', number_format_aliases)}\n"
        "# The time half — PredefinedTimeFormat, i.e. every name resolving to a d3\n"
        "# *time* spec. What `time_format` offers and accepts, beside a raw strftime\n"
        "# spec like `%b %Y`.\n"
        f"{_literal_block('TimeFormatAlias', time_format_aliases)}\n"
        "# Category role slot tokens, `category[1]`..`category[N]` — N is the widest\n"
        "# palette any built-in theme binds to the `category` role. Pinning a category\n"
        "# to a slot (rather than a hex) is what lets it re-skin with the theme, so\n"
        "# these are the values worth completing. Like FormatAlias, the field carrying\n"
        "# this reads `CategorySlotToken | str`: dotted palette tokens\n"
        "# (`dbt-grays.muted`) and literal hex stay legal.\n"
        f"{_literal_block('CategorySlotToken', [f'category[{i}]' for i in range(1, category_slots + 1)])}"
    )
    _OUT.write_text(content, encoding="utf-8")
    try:
        out_display = _OUT.relative_to(_DBT_CHARTS_DIR)
    except ValueError:
        out_display = _OUT
    print(
        f"Wrote {len(theme_names)} themes, {len(palette_names)} palettes "
        f"({len(stops_palette_names)} with stops), "
        f"{len(scale_palette_names)} scale palettes, {len(format_aliases)} format "
        f"aliases ({len(number_format_aliases)} number, {len(time_format_aliases)} "
        f"time), {category_slots} category slots → {out_display}"
    )


if __name__ == "__main__":
    main()
