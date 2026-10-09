"""Regenerate tests/fixtures/locale_parity.json from Vega's bundled d3-format.

Run from the repo root: uv run python libs/d3-format/scripts/regenerate_locale_fixture.py
"""

import html
import json
import pathlib
import re
from typing import Any

import vl_convert as vlc

# vl_convert types its locale names as one Literal; this list is a plain subset.
_vlc: Any = vlc

OUTPUT = pathlib.Path(__file__).parents[1] / "tests" / "fixtures" / "locale_parity.json"
LOCALES = ["fr-FR", "de-CH", "de-DE", "en-IN", "ar-EG", "en-US"]
SPECS = [",.2f", ",d", "$,.2f", ".1%", "~s", ".3s", "08,d", "(,.1f", ".2e", ",.0f"] + [
    "+,.1f",
    ",g",
    "n",
]
VALUES = ["1234567.891", "-9876.5", "0.123", "1e+21", "nan"]


def _render(locale: str, spec: str, value: str) -> str:
    expr = "NaN" if value == "nan" else value
    vl = {
        "data": {"values": [{}]},
        "transform": [{"calculate": f"format({expr}, {json.dumps(spec)})", "as": "s"}],
        "mark": "text",
        "encoding": {"text": {"field": "s", "type": "nominal"}},
    }
    svg = _vlc.vegalite_to_svg(vl, format_locale=locale)
    return html.unescape(re.findall(r'graphics-symbol"[^>]*>([^<]*)</text>', svg)[0])


# Vega bundles an older d3-format whose SI type renders NaN as "NaNZ"; the npm
# reference (d3_reference.json) is the authority there, so those cases are out.
cases = [
    [loc, spec, value, _render(loc, spec, value)]
    for loc in LOCALES
    for spec in SPECS
    for value in VALUES
    if not (value == "nan" and spec.endswith("s"))
]
locales = {loc: _vlc.get_format_locale(loc) for loc in LOCALES}
OUTPUT.write_text(
    json.dumps({"locales": locales, "cases": cases}, ensure_ascii=False, indent=1)
    + "\n"
)
