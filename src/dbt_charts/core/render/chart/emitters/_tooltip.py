"""Chart-axes LUT + structured tooltip builder for render-v2.

The chart-axes LUT maps each in-scope chart family to its tooltip ROLES —
identity (header), dependent (value), and whether a color dimension can be
promoted to a series row — never a VL screen axis. Orientation is resolved
downstream by each family's own emitter (see bar.py's vertical/horizontal
split); this table only ever reads authored semantic channels (``chart.x``/
``chart.y``/``color``), which stay independent/dependent regardless of
orientation.

``build_structured_tooltip_expr`` is the single function that turns those
roles into a per-datum expression string wired to VL's native ``description``
channel — not an appended ``encoding.tooltip`` array. ``description`` is the
only mechanism that gives exact, order-controlled aria-label content with no
risk of Vega-Lite's tooltip-merge duplicating a field already carried by its
own titled channel (the historical #2308 dedup bug guarded by
``test_tooltip_dedup.py``): an explicit ``tooltip`` array only ever *adds* to
channel-derived content, it never replaces it, so any field left off the
array (or covered by an already-titled channel) can still resurface — which
is exactly how #2308 happened. ``description`` fully replaces the mark's
aria-label instead of merging with it.

``translate.py::_apply_structured_tooltip`` wires the expression as a
``{"value": {"expr": ...}}`` def, deliberately NOT a ``{"field": ...}``
reference bound through a ``calculate`` transform — a field-bound description
is a genuine encoded channel, and Vega-Lite folds every encoded nominal
channel into the automatic ``impute`` transform's ``groupby`` for line/area
marks. Since the header always embeds the datum's own x/time value, every row
gets a distinct description, exploding that groupby to one group per row and
fragmenting the stacked area/line path with phantom imputed points. See
``_apply_structured_tooltip``'s docstring for the full mechanism.
"""

from __future__ import annotations

import json
import math

from dbt_charts.core.compile.models.primitives import ResolvedFormat
from dbt_charts.core.render.chart.vl_field_maps import (
    _affixed_digits_expr,
)
from dbt_charts.core.render.numeral_expr import numeral_vega_expr
from dbt_charts.core.text.predefined_formats import VEGA_ARMS

# Zero-width Unicode "format" characters (general category Cf — the same
# family as U+200B ZERO WIDTH SPACE) mark each tooltip entry's role so
# chart_interactivity.js can render the header/rows/footer hierarchy (bold
# header, swatched series, label:value dependent rows, emphasized footer
# total) from one flat aria-label string. Unlike C0 control characters these
# are valid XML/SVG attribute content, and browsers/screen readers neither
# render nor vocalize them — the accessible name still reads as clean prose,
# just without a redundant field-name prefix on the header/series rows.
# chart_interactivity.js mirrors these exact codepoints; keep the two in sync.
ROLE_HEADER = "⁡"  # FUNCTION APPLICATION
ROLE_HEADER_SWATCHED = "⁤"  # INVISIBLE PLUS -- header IS the color dim
ROLE_SERIES = "⁢"  # INVISIBLE TIMES
ROLE_TOTAL = "⁣"  # INVISIBLE SEPARATOR
# A precomputed display-order rank for this mark's series -- the SAME order
# that already drives the color scale's reordered domain (and, via
# apply_legend_entry_order, legend.values). chart_interactivity.js prefers
# this over reading the rendered legend's DOM order, which is unavailable
# whenever no legend renders (e.g. a line using endpoint labels instead of a
# legend, the common default) -- see features/structured_tooltip.py.
ROLE_ORDER = "‌"  # ZERO WIDTH NON-JOINER
# Emphasis modifier, orthogonal to the role markers above: a value/total row
# prefixed with this renders its value at the low-contrast label color rather
# than the loud value color, for a row that is only companion context to the
# row it follows. Composes with a role marker (a muted total is MUTED +
# ROLE_TOTAL). chart_interactivity.js mirrors it.
MUTED = "⁠"  # WORD JOINER
# Brackets a value row's swatch color: SWATCH + "#hex" + SWATCH + label.

COUNT_TITLE = "#"


_NO_FORMAT = ResolvedFormat(spec="")


# Chart families with structured-tooltip support. An unlisted chart_type
# KeyErrors in build_structured_tooltip_expr — fail fast rather than guess a
# role shape. "scatter" and "scatter_colored" are two role shapes for the
# SAME authored chart_type ("scatter") — the LUT key a caller passes depends
# on whether a color channel is bound (see features/structured_tooltip.py's
# _scatter_roles), not on any new authored discriminator.


def format_quantitative_expr(value_expr: str, fmt: ResolvedFormat) -> str:
    """A quantitative Vega expression's number format; an affix composes through
    ``_affixed_digits_expr`` like the axis labelExpr. ``value_expr`` may be any
    expression."""
    if fmt.has_affix:
        return _affixed_digits_expr(value_expr, fmt.spec, fmt, anchored=False)
    if fmt.spec in VEGA_ARMS:
        return numeral_vega_expr(value_expr, fmt.spec)
    return f"format({value_expr}, {json.dumps(fmt.spec)})"


# A duration reads in the coarsest unit that keeps it a readable number:
# days up to three weeks, weeks up to eight, months beyond.
_DURATION_UNITS: tuple[tuple[str, float, float, str], ...] = (
    ("day", 1, 21, ".0f"),
    ("week", 7, 56, ".1~f"),
    ("month", 30.4375, math.inf, ".1~f"),
)


__all__ = [
    "MUTED",
    "ROLE_HEADER",
    "ROLE_HEADER_SWATCHED",
    "ROLE_ORDER",
    "ROLE_SERIES",
    "ROLE_TOTAL",
]
