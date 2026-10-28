"""The role markers an aria-label carries for the hover runtime.

The engine writes them into each mark's aria-label; tests read them back through
these constants.
"""

from __future__ import annotations

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
# that already drives the color scale's reordered domain. chart_interactivity.js prefers
# this over reading the rendered legend's DOM order, which is unavailable
# whenever no legend renders (e.g. a line using endpoint labels instead of a
# legend, the common default).
ROLE_ORDER = "‌"  # ZERO WIDTH NON-JOINER
# Emphasis modifier, orthogonal to the role markers above: a value/total row
# prefixed with this renders its value at the low-contrast label color rather
# than the loud value color, for a row that is only companion context to the
# row it follows. Composes with a role marker (a muted total is MUTED +
# ROLE_TOTAL). chart_interactivity.js mirrors it.
MUTED = "⁠"  # WORD JOINER
# Prefixes a row the author added with the chart's ``tooltip:`` -- a
# ``label: value`` row after the mark's own value rows (the engine writes it;
# chart_interactivity.js mirrors it).
ROLE_EXTRA = "\u200d"  # ZERO WIDTH JOINER
# Names a (category, series) slot a sparse stack imputes: identity only, no datum.
ROLE_IMPUTED = "\u034f"  # COMBINING GRAPHEME JOINER
# Stands for a ``;`` inside an authored value; rows are split on ``;``.
SEMICOLON_ESCAPE = "\ue000"  # PRIVATE USE AREA

__all__ = [
    "MUTED",
    "ROLE_EXTRA",
    "ROLE_HEADER",
    "ROLE_HEADER_SWATCHED",
    "ROLE_IMPUTED",
    "ROLE_ORDER",
    "ROLE_SERIES",
    "ROLE_TOTAL",
    "SEMICOLON_ESCAPE",
]
