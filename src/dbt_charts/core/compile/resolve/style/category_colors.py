"""Board-wide category→color planning.

One data value gets one swatch across every chart on a board, and one rule
decides which slot each value takes (see ``plan_category_colors``). Without a
board-wide plan, each chart would slot only the values *it* happens to see, so
the same category would land on a different swatch in every chart.

The planner is pure: per-chart observed values in, one scale per bound field
out. Enumerating those values needs executed rows, so the caller runs after
execute — see ``execute/category_colors.py``.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.chart.normalized.geoshape import GeoshapeChart
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
    CategoryColorScale,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED,
    ERR_CATEGORY_COLOR_PIN_DUPLICATE,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.normalized._base import (
        _BaseChartFields,
    )

# An authored `color:` channel names a category on any family — that is what
# the channel means. A positional channel only does when the family paints one
# discrete mark per value: promoting `x` on a line adds a color field, which
# partitions the path into one-point groups and draws nothing.
_COLOR_CHANNEL = "color"

# `chart.type` is the discriminator this module reads for
# `_BINDING_CAPABLE_CHART_TYPES` below — a module constant (not an inline
# string literal) so `getattr` dodges Ruff B009 the same way `_COLOR_CHANNEL`
# does: a field only some family classes declare statically, always present at
# runtime on any concrete chart this module is called with.
_TYPE_FIELD = "type"

# The single source of truth for which chart types actually paint from a board
# scale. Declared by `type:` TAG, not Python class — `BarChart` backs both
# "bar" and "histogram", but only "bar"'s emitter calls `category_scale_for`
# (`_emit_histogram` in `bar.py` never does). This list and each emitter's own
# choice to read `chart.category_colors` are two independently-maintained
# lists, so a family declared here whose emitter ignores the scale (or the
# reverse) is caught by
# `test_binding_capability_matches_every_authored_chart_family` in
# `dbt-charts/tests/core/render/chart/test_render_emitters.py`.
#
# A tag NOT in this set neither binds nor widens the board's domain — the
# safe default for any family whose emitter hasn't been converted yet, so an
# unconverted family is harmless rather than drift-producing.
#
# This set is HAND-MAINTAINED, not derived from the emitters, and that is
# deliberate rather than an oversight: the real fact this frozenset encodes
# ("does this family's emit path read the board scale") only exists inside
# `render/chart/emitters/`, but this planner lives in `compile/`, which must
# never import `render/` — `tach.toml`'s `dbt_charts.core.compile` module
# declares an explicit `depends_on` list that omits both `render` and
# `execute`, and `tests/core/test_layering.py::test_compile_does_not_import_render`
# is a second, dependency-free AST guard over every file under `core/compile/`
# asserting the same thing. Importing an emitter-side registry here to derive
# this set would invert that boundary. The only way to keep a single
# declaration site without breaking it would be moving the set itself into
# `render/` — but the planner needs it during compile, before
# any emitter runs, so `render/` isn't reachable from here either. The
# exhaustive `test_binding_capability_matches_every_authored_chart_family`
# above is the drift guard in place of a mechanical derivation: it fails the
# moment this set and an emitter's real behavior disagree.
_BINDING_CAPABLE_CHART_TYPES: frozenset[str] = frozenset(
    {
        "bar",
        "line",
        "area",
        "scatter",
        "heatmap",
        "pie",
        "donut",
        "map",
        "geoshape",
        "point_map",
        "bubble_map",
    }
)


def categorical_channel_fields(chart: _BaseChartFields) -> tuple[str, ...]:
    """Return the data field this chart names a category with, if any.

    Only an authored `color:` channel counts. A field on `x`/`y` identifies a
    position, and turning it into a fill is a *new* color channel the author
    never asked for — which broke authored `conditional_formatting` and
    a mark color, split line marks into one-point groups, and leaked
    into label layers. The binding now re-scales a channel that already
    exists; it never invents one.

    One helper, two callers: the execute-side walk that enumerates values and
    the resolve-side projection that narrows the board plan to this chart.
    They must agree, or a chart would count toward the threshold, widen the
    domain, and then emit no scale.
    """
    # The type TAG, not the class, decides binding capability — see
    # `_BINDING_CAPABLE_CHART_TYPES`'s docstring for why (histogram vs bar).
    if getattr(chart, _TYPE_FIELD) not in _BINDING_CAPABLE_CHART_TYPES:
        return ()

    # A choropleth authoring `value:` fills from that measure, not from
    # `color:` — `_resolve_choropleth_value_field` resolves `value or color`.
    # Its color field therefore names nothing the chart paints, so counting it
    # would push a sibling over the threshold and re-seat colors this chart
    # then ignores. GeoshapeChart, not the shared geo base: `value:` is inert
    # on a point map, which paints from `color:` and must keep counting.
    if isinstance(chart, GeoshapeChart) and chart.value:
        return ()

    # Not every family declares the channel — a callout has no encodings.
    if _COLOR_CHANNEL not in type(chart).model_fields:
        return ()
    value = getattr(chart, _COLOR_CHANNEL)
    if isinstance(value, str) and value:
        return (value,)
    return ()


@dataclass(frozen=True)
class FieldObservation:
    """What one chart draws for one color field.

    ``totals`` maps a value to the sum of the chart's measure over its rows
    (``series_totals``); a chart with no additive measure has none, and weighs
    nothing. ``stacked`` is the order the author explicitly asked of this chart
    (``stack_order: data`` or ``alphabetical``), empty when none.
    """

    values: tuple[str, ...]
    totals: Mapping[str, float]
    stacked: tuple[str, ...] = ()


# Per-chart observations: field name → what that chart draws for it.
ChartFieldValues = Mapping[str, FieldObservation]

# A label carrying an order of its own: a stem and a trailing number (P1, Tier 3, Q4).
_RANKED_LABEL = re.compile(r"(\D*?)\s*(\d+)")


def _ranked_number(value: str) -> tuple[str, int] | None:
    match = _RANKED_LABEL.fullmatch(value)
    return None if match is None else (match[1].casefold(), int(match[2]))


def _ranked(
    values: Sequence[str], observations: Sequence[FieldObservation]
) -> list[str]:
    """``values`` in the order that has no author behind it: their own, else size.

    Values that are all one stem and a number (``P1``..``P10``) go by the number.
    Otherwise the largest total takes the first slot, by magnitude: a series
    summing to -100 draws larger than one summing to 10. Each chart's totals are
    first turned into shares so charts on different measures weigh equally.
    Ties go alphabetical.
    """
    numbers: dict[str, tuple[str, int]] = {}
    for value in values:
        number = _ranked_number(value)
        if number is None:
            break
        numbers[value] = number
    else:
        if len({stem for stem, _ in numbers.values()}) == 1:
            return sorted(values, key=lambda v: (numbers[v][1], v))
    weight = dict.fromkeys(values, 0.0)
    for chart in observations:
        grand_total = sum(abs(total) for total in chart.totals.values())
        if grand_total == 0:
            continue
        for value, total in chart.totals.items():
            if value in weight:
                weight[value] += abs(total) / grand_total
    return sorted(values, key=lambda v: (-weight[v], v))


def _discover(
    options: Sequence[str],
    stacked: Sequence[str],
    drawn: Sequence[str],
    observations: Sequence[FieldObservation],
) -> list[str]:
    """Every value the field seats, in slot order: what the author said (the
    variable's ``options``, then explicit stack orders), then the rest ranked."""
    said = dict.fromkeys([*options, *stacked])
    rest = list(dict.fromkeys(v for v in drawn if v not in said))
    return [*said, *_ranked(rest, observations)]


def plan_category_colors(
    observations: Sequence[ChartFieldValues],
    authored: Mapping[str, CategoryColorBinding],
    palette: Sequence[str],
    seeded: Mapping[str, Sequence[str]] = MappingProxyType({}),
) -> dict[str, CategoryColorScale]:
    """Return one scale per field some chart colors by.

    One rule decides which slot each value of a field takes, on every chart type
    and whether one chart or many color by the field:

    1. What the author said, in this order: pins under
       ``style.charts.category_colors``; the options of a variable a chart's
       link sets from the field (``seeded``, in option order, so a series keeps
       its swatch whichever options a filter leaves rows for); then an explicit
       ``stack_order: data`` or ``alphabetical`` on a chart.
    2. The values' own order, when they are all one stem and a number
       (``P1``..``P10``, ``Tier 1``..``Tier 3``).
    3. Size: the largest total first (see ``_ranked``), ties alphabetical.

    Slot assignment never reads row order, so it is stable across charts and
    across which rows arrive first. It moves only when the data's ranking does;
    pinning is what freezes an assignment.

    This decides *which slot a value owns*, never the order charts display
    their values in — display order stays wherever each emitter already put
    it. Reordering a domain is what desynchronizes the legend pin, the
    tooltip rank, and the aria labels that read from it.

    Two categories must never share a swatch, so a field with more values than
    the palette can seat does not bind — except when the author named it,
    which raises instead (see ``_build_scale``). Seeding never costs a binding:
    options the palette cannot seat alongside the pins are left unseeded.
    """
    per_field: dict[str, list[FieldObservation]] = {field: [] for field in authored}
    for chart in observations:
        for field, observation in chart.items():
            per_field.setdefault(field, []).append(observation)

    def seats_for(
        field: str, values: Sequence[str]
    ) -> tuple[dict[str, str], dict[str, str], list[str], int]:
        pinned = dict(authored[field].values) if field in authored else {}
        seatable = _seatable_pins(pinned, values)
        domain = _domain_for(seatable, values)
        unseen = {v: c for v, c in pinned.items() if v not in seatable}
        return seatable, unseen, domain, _seats_needed(seatable, domain, palette)

    scales: dict[str, CategoryColorScale] = {}
    for field, field_observations in per_field.items():
        is_authored = field in authored
        options = list(seeded[field]) if field in seeded else []
        stacked = [v for o in field_observations for v in o.stacked]
        drawn = [v for o in field_observations for v in o.values]
        if not drawn and not options:
            # Nothing on screen encodes this field, so there is no scale to
            # build and no observed values to check the pins against.
            # `dct render --chart X` narrows the layout without narrowing the
            # board's pins, which is the ordinary way to get here.
            continue

        pinned, unseen, domain, seats = seats_for(
            field, _discover(options, stacked, drawn, field_observations)
        )
        if seats > len(palette) and options:
            # Seeding is an enhancement on top of binding: options that do not
            # fit leave the field to the values the data holds, as before.
            pinned, unseen, domain, seats = seats_for(
                field, _discover([], stacked, drawn, field_observations)
            )
        if seats > len(palette):
            # Binding is an enhancement the engine volunteers; when the
            # palette cannot seat every value needing a swatch it declines
            # rather than turning a board that rendered fine before this
            # feature existed into an error. Colors staying distinct matters
            # more than binding this one field.
            if not is_authored:
                continue
            # An authored field is different: the author named it, so quietly
            # not honoring the pins would be the wrong kind of silence.
            raise ChartDataError.from_code(
                ERR_CATEGORY_COLOR_PALETTE_EXHAUSTED,
                field=field,
                value_count=seats,
                swatch_count=len(palette),
            )
        scales[field] = _build_scale(field, pinned, domain, palette, unseen)
    return scales


def _seatable_pins(
    pinned: Mapping[str, str], discovered: Sequence[str]
) -> dict[str, str]:
    """Keep only the pins naming a value the field actually draws right now.

    A pin naming anything else used to be seated anyway: it claimed a palette
    slot for a category nothing draws and pushed every real value along one,
    so adding a second such pin recolored the board again. Not seating it
    fixes that completely — nothing is claimed, so nothing shifts.

    It cannot be an error, though it reads like one. "The field never takes
    this value" is not knowable here: the planner sees one render's rows, and
    a value goes missing for reasons that have nothing to do with a typo — a
    variable filter excluding it, or a sibling chart whose query failed and
    was skipped. Raising would blank a whole board (this runs above the
    per-chart failure collector) and blame a typo that does not exist, while
    `dct validate` — no database, no rows — could never catch the real one.
    Same policy as the two guards around it: decline rather than turn a board
    that rendered fine into an error.
    """
    return {value: color for value, color in pinned.items() if value in discovered}


def _domain_for(pinned: Mapping[str, str], discovered: Sequence[str]) -> list[str]:
    """Authored order first, then the remaining values found in the data.

    Every pin names a discovered value by the time this runs
    (``_seatable_pins``), so leading with them only reorders the domain: it
    never introduces a value no chart draws.
    """
    return list(pinned) + [v for v in discovered if v not in pinned]


def _seats_needed(
    pinned: Mapping[str, str], domain: Sequence[str], palette: Sequence[str]
) -> int:
    """Palette swatches the field consumes: palette-member pins plus unpinned values.

    A pin naming a color outside the palette paints its own literal, so it
    needs no swatch.
    """
    in_palette = {color.casefold() for color in palette}
    claimed = {c.casefold() for c in pinned.values() if c.casefold() in in_palette}
    return len(claimed) + sum(1 for v in domain if v not in pinned)


def _build_scale(
    field: str,
    pinned: Mapping[str, str],
    domain: Sequence[str],
    palette: Sequence[str],
    unseen_pins: Mapping[str, str],
) -> CategoryColorScale:
    """Give every value a palette slot; pins claim theirs, the rest take the next free.

    A pin naming a color the palette contains resolves to *that slot*, so a
    nested board repaints it from its own palette instead of being overpainted.
    A pin naming any other color is a literal the author chose: it lands in
    ``overrides`` and burns a free slot while one can be spared, so nothing
    else claims the companion ink that sits alongside it. Once the free slots
    are only enough for the unpinned values, an override shares a slot (and so
    a companion ink) with another value.
    """
    # Two pins naming the same color (case-insensitively) would seat two
    # categories on one swatch — the one thing CategoryColorScale guarantees
    # never happens to a fill, whether that color is a palette member (both claim the
    # same slot below) or an arbitrary literal (both would render the
    # identical override fill regardless of slot). An authored field is
    # explicit intent, so this raises rather than silently letting the
    # second pin win — same policy as the over-capacity guard in
    # ``plan_category_colors``.
    color_owner: dict[str, str] = {}
    for value, color in pinned.items():
        key = color.casefold()
        if key in color_owner:
            raise ChartDataError.from_code(
                ERR_CATEGORY_COLOR_PIN_DUPLICATE,
                field=field,
                first_value=color_owner[key],
                second_value=value,
                color=color,
            )
        color_owner[key] = value

    # Case-insensitive: a pin of "#ABCDEF" and a palette stop of "#abcdef" are
    # the same swatch, and an exact-case compare would hand the stop to a
    # second category — the one thing this guarantees never happens.
    slot_of_color = {color.casefold(): i for i, color in enumerate(palette)}
    pinned_slots = {
        value: slot_of_color.get(color.casefold()) for value, color in pinned.items()
    }
    claimed = {slot for slot in pinned_slots.values() if slot is not None}
    free = iter([i for i in range(len(palette)) if i not in claimed])
    spare = len(palette) - len(claimed) - sum(1 for v in domain if v not in pinned)
    shared = itertools.cycle(range(len(palette)))

    slots: dict[str, int] = {}
    overrides: dict[str, str] = {}
    for value in domain:
        slot = pinned_slots.get(value)
        if slot is None:
            if value in pinned:
                overrides[value] = pinned[value]
                if spare > 0:
                    spare -= 1
                    slot = next(free)
                else:
                    slot = next(shared)
            else:
                slot = next(free)
        slots[value] = slot
    return CategoryColorScale(
        field=field, slots=slots, overrides=overrides, unseen_pins=unseen_pins
    )
