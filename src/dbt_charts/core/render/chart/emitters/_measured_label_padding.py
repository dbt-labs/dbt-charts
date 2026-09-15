"""Measured `labelPadding` for axis labels whose align is flipped toward the plot.

Vega-Lite's ``autosize: fit`` sizing correctly reserves gutter width for the
per-orient smart default (labels growing AWAY from the plot), but reserves
~0 gutter once ``labelAlign`` flips the anchor's growth direction back toward
the plot — regardless of actual label text width (empirically probed against
vl-convert: a 5-digit and a 7-digit label reserve the identical, near-zero
gutter). That same probe found ``labelPadding`` widens the reserved gutter
1:1 regardless of align direction — so computing the real label width in
Python and feeding it as ``labelPadding`` closes the gap without bypassing
``autosize: fit``.

Geometry: with ``text-anchor: end`` (align == the axis's own orient side),
Vega-Lite anchors text at ``(tickSize if ticks are visible else 0) +
labelPadding`` and grows leftward — Vega-Lite already adds the tick's own
length before applying ``labelPadding``, confirmed empirically via vl-convert
(varying ``tickSize``/``labelPadding``/``ticks`` independently). So
``labelPadding`` only ever needs ``max_label_width + breathing_room`` — adding
tick length again here would double it whenever ticks are visible, and add
unearned dead space when they aren't. With that, the widest label's near edge
lands just outside the tick and never crosses it — every shorter label lands
further from the tick, never closer. Digit/text alignment at a shared edge
falls out of this for free.

Two tick-content scopes are safe to measure, both covered here:

1. **Baked** — ``tick_values`` + a concrete ``format`` (quantitative axes on
   a theme that sets ``axis.ticks.count``). dbt charts already knows the exact
   values VL will render (``axis.scale.values`` is emitted explicitly); measuring
   is exact, not an estimate. ``quantitative_tick_labels``.
2. **Unbaked** — no ``tick_values`` (e.g. the ``stark``/``plain`` themes,
   which leave ``axis.ticks.count`` unset so VL picks its own "nice" ticks).
   dbt charts doesn't know VL's actual pick, but it can still bound the widest
   label VL could plausibly render: run the *data's own domain* through the
   same ``nice_tick_values`` algorithm dbt charts uses for the baked case, at a
   spread of plausible tick counts, and take the widest formatted candidate.
   This is an upper-bound estimate, not a prediction of VL's exact choice —
   VL still auto-generates its real ticks; this only sizes the gutter for the
   worst plausible one. Never a shadow implementation of VL's actual tick
   selection (that stays out of scope — see the module's own scope note
   below). ``estimated_quantitative_tick_labels``.

Never safe for scales where Vega-Lite's own choice is unbounded by any data
domain we can read (continuous temporal, or no numeric data at all — e.g. a
bar chart's `aggregate: count` axis, whose values VL computes client-side).
Each call site (``vl_field_maps.py``, ``features/mirror_axis.py``,
``emitters/bar.py``) gates on scope itself (own-side align, and either baked
ticks or a non-empty data domain) before calling in, so this module never has
to model the "not applicable" state as an Optional parameter.

``quantitative_tick_labels`` no longer renders every tick straight from the
literal ``format`` spec. When the axis carries a baked ``ruler`` decision
(``ResolvedAxisStyle.ruler`` — see ``core/compile/models/style/resolved/_base.py``),
VL doesn't paint from the bare spec either: ``inject_axis_numeral_expr``
(``vl_field_maps.py``) composes a ``labelExpr`` from the same decision that
divides by the magnitude, gates the suffix on the ladder's anchor/repeat
mode, and pads every non-suffix tick with ``ruler.reservation`` -- a padding
string composed (``font_measure.compose_suffix_reservation``, baked once at
resolve) from measured Unicode space characters, not a whole number of
digit-width units. This module reads the same ``ruler`` field rather than
re-deriving whether it applies — a gutter measured against a stale or
partial predicate under-measures a compacting ladder, and the labels this
module exists to align invade the plot.

The actual width measurement + Vega-Lite ``labelLimit`` cap this module's own
docstring describes (``measured_label_padding`` / ``cap_padding_to_label_limit``
/ ``DEFAULT_VL_LABEL_LIMIT``) live in ``core/utils.py`` — a leaf module a
compile-side caller (the horizontal bar's categorical-gutter estimate,
``compile/resolve/chart/bar.py``) can reach too. This module keeps only the
label-*text* generation those functions measure.
"""

from __future__ import annotations

from decimal import Decimal

from d3_format import format as d3_format
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedRulerAxis,
    ResolvedTickLabel,
)
from dbt_charts.core.numeric import nice_tick_values
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.text.numeral_scale import (
    SUB_UNIT_SCIENTIFIC_FLOOR,
    SuffixMode,
    decimal_pad_for,
    with_symbol,
)

# Plausible spread of tick counts Vega-Lite's own axis layout might land on
# when dbt charts hasn't baked explicit tick values. Not an attempt to predict
# VL's actual choice for a specific chart (that would need its real panel
# height, which isn't known at this point in the pipeline) — a defensible
# bracket around d3-scale's own default tick target (10) and the low end
# typical of a cramped axis (3), so the nice-rounded candidates cover the
# domain-nicing behavior regardless of where VL actually lands. Widening this
# range only ever grows the gutter estimate (the safe direction); narrowing
# it risks a real miss.
_ESTIMATE_TARGET_COUNTS: tuple[int, ...] = (3, 4, 5, 6, 8, 10, 12)


def quantitative_tick_labels(
    tick_values: tuple[float, ...],
    format_spec: str,
    ruler: ResolvedRulerAxis | None,
    tick_label: ResolvedTickLabel | None = None,
) -> list[str]:
    """Render each baked tick label exactly as the axis will paint it.

    ``format_spec`` is the raw d3-format string dbt charts passes verbatim to
    VL's ``axis.format``. When ``ruler`` is None (the axis's resolved
    ``ResolvedAxisStyle.ruler`` — the ladder doesn't compact, the format
    isn't SI-shaped, or ``label.expr`` is authored), VL renders straight
    from the literal spec and so does this — dbt charts' analytic/narrative
    display extension does not apply here; VL renders axis ticks with its
    own d3-format implementation, not dbt charts'.

    When ``tick_label.prefix`` is set (the ``ruler is None`` non-compacting
    case), the anchor tick additionally gets the currency prefix -- or, when
    ``tick_label.anchor_at_start`` is ``None`` (non-column-forming), every
    non-zero tick does (mirroring ``inject_axis_numeral_expr``'s labelExpr) --
    so the gutter is sized for the widest label.

    When ``ruler`` is not None, VL does **not** paint from the literal spec
    at all -- ``inject_axis_numeral_expr`` composes a ``labelExpr`` from the
    same decision instead. Mirror that composition exactly: divide by the
    magnitude, gate the suffix on ``ruler.mode`` (already the *effective*
    mode -- a non-column-forming axis was baked to REPEAT regardless of
    what the bare ladder would otherwise select), gate the currency prefix
    on ``ruler.prefix_repeats`` instead (independent of ``mode`` -- a
    column-forming axis can land in REPEAT mode for its own, magnitude-driven
    reason and still anchor the prefix) -- the anchor position, for either,
    is ``ruler.anchor_at_start`` (which END of the ladder is the
    magnitude-extreme, not the tick's raw value). A non-suffix tick trails
    with ``ruler.reservation`` when ``ruler.reserve``.

    ``ruler`` is required, not Optional-with-a-default: every real caller
    always has a concrete value (``None`` is itself the concrete "no ruler"
    state, passed explicitly) -- a silently-omittable default here would let
    a future call site under-measure a compacting ladder without noticing.
    """
    if ruler is None:
        if tick_label is None:
            return [d3_format(format_spec, v) for v in tick_values]
        prefix = tick_label.prefix
        anchor_at_start = tick_label.anchor_at_start
        pad_table = tick_label.decimal_pad_table

        if prefix:
            # Mirrors inject_axis_numeral_expr's labelExpr (see this
            # function's docstring). Use with_symbol so d3-format's
            # sign-before-symbol ordering applies (format("$,.0f")(-500) ->
            # "-$500", not "$-500").
            anchor_spec = with_symbol(format_spec, prefix)
            anchor_pos = None
            if anchor_at_start is not None:
                anchor_pos = 0 if anchor_at_start else len(tick_values) - 1
            prefixed_labels = []
            for i, v in enumerate(tick_values):
                is_prefixed = v != 0 and (anchor_pos is None or i == anchor_pos)
                spec = anchor_spec if is_prefixed else format_spec
                digits = d3_format(spec, v)
                if pad_table:
                    digits += decimal_pad_for(pad_table, digits)
                prefixed_labels.append(digits)
            return prefixed_labels

        if pad_table:
            return [
                (s := d3_format(format_spec, v)) + decimal_pad_for(pad_table, s)
                for v in tick_values
            ]
        return [d3_format(format_spec, v) for v in tick_values]

    prefix, digit_spec = ruler.prefix, ruler.digit_spec
    suffix_text = ruler.suffix_string
    magnitude = 10.0**ruler.exponent
    anchor_position = 0 if ruler.anchor_at_start else len(tick_values) - 1
    pad_table = ruler.decimal_pad_table

    anchor_digit_spec = with_symbol(digit_spec, prefix) if prefix else digit_spec

    labels: list[str] = []
    for position, value in enumerate(tick_values):
        is_anchor = position == anchor_position
        # Suffix follows mode, prefix follows prefix_repeats -- independent
        # gates, see this function's docstring.
        carries_suffix = value != 0 and (ruler.mode is SuffixMode.REPEAT or is_anchor)
        carries_prefix = value != 0 and (ruler.prefix_repeats or is_anchor)
        # Use the symbol-inclusive spec on any prefix-carrying tick so
        # d3-format's sign-before-symbol ordering applies
        # (format("$,.1~f")(-500) -> "-$500", not "$-500").
        scaled = value / magnitude
        digits = d3_format(anchor_digit_spec if carries_prefix else digit_spec, scaled)
        if pad_table:
            digits += decimal_pad_for(pad_table, digits)
        tail = suffix_text if carries_suffix else ruler.reservation
        labels.append(digits + tail)
    return labels


def numeric_values(data: list[VLDict], fields: tuple[str, ...]) -> list[float]:
    """Finite numeric values across one or more fields (skips bools/None/non-numeric).

    ``fields`` plural: a multi-metric line/area chart shares one y-axis across
    several fields, so the domain must span all of them combined.
    """
    return [
        float(row[field])
        for field in fields
        for row in data
        if field in row
        and isinstance(row[field], (int, float, Decimal))
        and not isinstance(row[field], bool)
    ]


def estimated_quantitative_tick_labels(
    values: list[float],
    format_spec: str,
    si_format: str | None = None,
    scientific_format: str | None = None,
) -> list[str]:
    """Upper-bound candidate tick labels for a quantitative axis whose real
    Vega-Lite ticks aren't known (dbt charts hasn't baked ``tick_values``).

    ``values`` should already include any authored ``scale.domain`` bounds
    (via ``dbt_charts.core.compile.resolve.chart.tick_values.numeric_domain_bounds``)
    alongside the real data values — an authored domain is a first-class
    field Vega-Lite renders ticks up to regardless of the actual data range,
    so estimating from data alone would under-reserve whenever the authored
    domain is wider (e.g. a fixed ``[0, 2_000_000]`` domain over data that
    only reaches 500). This function only runs the *given* domain through
    ``nice_tick_values``, it does not know about authored overrides itself.

    Runs the domain (plus zero, since a zero-anchored scale is common and
    cheap to cover) through the same ``nice_tick_values`` algorithm dbt charts
    uses when it *does* bake ticks, at ``_ESTIMATE_TARGET_COUNTS`` — not to
    predict VL's actual pick, but to bound the widest label it could
    plausibly render. Returns every candidate's formatted string; callers
    measure all of them and take the max width. Returns ``[]`` for no values
    (nothing to estimate from).

    ``si_format``/``scientific_format`` mirror ``ResolvedTickLabel``'s own
    pair (set together or not at all — the caller threads them straight from
    there): when set, a candidate doesn't format through ``format_spec``
    unconditionally — it goes through the exact same per-tick guard
    ``inject_axis_numeral_expr`` composes into the axis's real ``labelExpr``
    (magnitude >= 1 -> ``si_format``; below 1 and below
    ``SUB_UNIT_SCIENTIFIC_FLOOR`` -> ``scientific_format``; otherwise ->
    ``format_spec``; zero always through ``format_spec``, never scientific).
    Passing ``format_spec`` alone here for a ladder-less axis would measure a
    string the axis never paints (e.g. "1500000" for a tick VL actually
    renders "1.5M").
    """
    if not values:
        return []
    domain_min = min(min(values), 0.0)
    domain_max = max(max(values), 0.0)
    candidates = {domain_min, domain_max}
    for count in _ESTIMATE_TARGET_COUNTS:
        candidates.update(nice_tick_values(domain_min, domain_max, count))
    if si_format is None:
        return [d3_format(format_spec, v) for v in candidates]
    return [
        _sub_unit_guarded_format(v, format_spec, si_format, scientific_format)
        for v in candidates
    ]


def _sub_unit_guarded_format(
    value: float,
    format_spec: str,
    si_format: str,
    scientific_format: str | None,
) -> str:
    """The exact per-tick choice ``inject_axis_numeral_expr`` composes into a
    ladder-less axis's ``labelExpr``, applied in Python so the gutter
    estimate above measures what the axis actually paints. See that
    function's docstring for the full rationale.
    """
    if abs(value) >= 1:
        return d3_format(si_format, value)
    if (
        scientific_format is not None
        and value != 0
        and abs(value) < SUB_UNIT_SCIENTIFIC_FLOOR
    ):
        return d3_format(scientific_format, value)
    return d3_format(format_spec, value)
