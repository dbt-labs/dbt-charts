"""Pins VEGA_SCHEME_NAMES against the renderer this repo actually ships.

The frozenset is a hand-maintained list (vl-convert exposes no queryable
scheme enum at the Python API level), so this test is what keeps it honest:
every name must round-trip through vl-convert's bundled Vega-Lite → SVG
pipeline. A name that stops rendering, or a real scheme vl-convert accepts
but this set omits, should fail here — not surface as a compile-time
rejection of a previously-working board (see codes_compile.py's ERR-*
provenance and `dbt-charts/AGENTS.md`'s "no hand-written parallel schema" rule).
"""

from __future__ import annotations

import json

import vl_convert as vlc

from dbt_charts.core.compile.models.schema_names import VEGA_SCHEME_NAMES


def _spec_for(scheme: str) -> str:
    return json.dumps(
        {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]},
            "mark": "rect",
            "encoding": {
                "x": {"field": "x", "type": "ordinal"},
                "y": {"field": "y", "type": "ordinal"},
                "color": {
                    "field": "y",
                    "type": "quantitative",
                    "scale": {"scheme": scheme},
                },
            },
        }
    )


def test_every_vega_scheme_name_renders() -> None:
    """Every name in VEGA_SCHEME_NAMES must be a real, renderable VL scheme."""
    failures: dict[str, str] = {}
    for name in sorted(VEGA_SCHEME_NAMES):
        try:
            vlc.vegalite_to_svg(_spec_for(name))
        except Exception as exc:  # noqa: BLE001 — sweeping every candidate name
            failures[name] = str(exc).splitlines()[0]
    assert not failures, (
        f"VEGA_SCHEME_NAMES contains {len(failures)} name(s) the bundled "
        f"vl-convert rejects: {failures}"
    )


def test_vega_scheme_names_has_no_dbt_charts_palette_collisions() -> None:
    """The two accepted string vocabularies (Vega schemes, dbt charts named
    palettes) must stay disjoint, or ScaleTargetConfig's palette validator
    can't tell which resolution path a name means."""
    from dbt_charts.core.compile.resolve.style.palette import list_palettes

    overlap = VEGA_SCHEME_NAMES & set(list_palettes())
    assert not overlap, f"Vega scheme / dbt charts palette name collision: {overlap}"
