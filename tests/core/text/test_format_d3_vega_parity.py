"""Parity: every VEGA_SAFE_TIME_DIRECTIVES letter must render byte-identical
to real d3 (Vega's ``utcFormat``), bare and under every padding modifier.

``VEGA_SAFE_TIME_DIRECTIVES`` is the allowlist the compile-time gate
(``compile/validate/formats.py``) trusts to reject any directive it does not
cover -- the whole measure-vs-paint guarantee lives in this set being
correct. This test is the drift guard: it renders each letter through real
Vega (``vl_convert``) rather than hand-pinning expected strings, so an
allowlisted letter whose modifier form (or bare form) diverges from d3 fails
here instead of shipping silently, the way the ``%_L``/``%f``/``%g`` gaps
did.

Reads the computed value via the rendered mark's ``aria-label`` attribute,
not the inner ``<text>`` element content: ``vl_convert``'s SVG writer trims
leading/trailing whitespace from a text mark's literal glyph content (a
generic SVG-serialization behavior, reproducible with a hardcoded
``"  5"`` text value with no ``utcFormat`` involved at all), which would
silently mask a real space-padding divergence between ``portable_strftime``
and the value ``utcFormat`` actually computed. ``aria-label`` carries that
pre-trim string, matching what ``portable_strftime`` -- a general-purpose
strftime substitute, also used outside Vega where no such trim ever applies
(table columns, the footer timestamp) -- must reproduce.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
import vl_convert as vlc

from dbt_charts.core.text.format_d3 import VEGA_SAFE_TIME_DIRECTIVES, portable_strftime

_ARIA_LABEL_RE = re.compile(r'aria-label="label: (.*?)"')

_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

# A handful of UTC instants chosen to exercise both edges of every
# directive's padding: single-digit vs. double-digit day/hour/month/minute/
# second, zero vs. non-zero milliseconds, Q1 vs. Q2 quarter, and an ISO
# week-year that diverges from the calendar year (2027-01-01 is week 53 of
# 2026).
_PARITY_INSTANTS_MS = [
    1684328645006,  # 2023-05-17T13:04:05.006Z -- Q2, mixed single/double digits
    1672880523007,  # 2023-01-05T01:02:03.007Z -- Q1, single-digit day/hour/min/sec
    1798761600000,  # 2027-01-01T00:00:00.000Z -- ISO week-year boundary, zero ms
]


def _utc_format_via_vega(ms: int, fmt: str) -> str:
    """The string ``utcFormat(ms, fmt)`` computes, read via ``aria-label`` so
    an unrelated SVG-serialization whitespace trim on the rendered glyphs
    doesn't corrupt the comparison (see module docstring).
    """
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": [{"value": ms}]},
        "transform": [
            {"calculate": f"utcFormat(datum.value, {json.dumps(fmt)})", "as": "label"}
        ],
        "mark": "text",
        "encoding": {"text": {"field": "label", "type": "nominal"}},
    }
    svg = vlc.vegalite_to_svg(spec)
    match = _ARIA_LABEL_RE.search(svg)
    assert match, f"no aria-label found in rendered SVG: {svg}"
    return match.group(1)


@pytest.mark.parametrize("instant_ms", _PARITY_INSTANTS_MS)
@pytest.mark.parametrize("modifier", ["", "-", "_", "0"])
@pytest.mark.parametrize("letter", sorted(VEGA_SAFE_TIME_DIRECTIVES))
def test_allowlisted_directive_matches_vega(
    letter: str, modifier: str, instant_ms: int
) -> None:
    fmt = f"%{modifier}{letter}"
    dt = _EPOCH_UTC + timedelta(milliseconds=instant_ms)
    expected = _utc_format_via_vega(instant_ms, fmt)
    actual = portable_strftime(dt, fmt)
    assert actual == expected, (
        f"{fmt!r} at {dt.isoformat()}: d3 computed {expected!r}, "
        f"portable_strftime measured {actual!r}"
    )
