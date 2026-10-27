"""Shared cartesian encoding primitives for render-v2 emitters.

These helpers extract verbatim-identical logic from the line and area emitters.
Each primitive is genuinely shared (2+ call sites), accepts explicit args, and
never takes board_style — preserving the no-board_style emitter invariant.

Convention: every primitive here is a pure builder — it returns a value and
never mutates an argument in place. Exception: ``nest_zero_rule`` mutates
``entry`` in place (appends to ``entry.layers`` when already ``mark="layered"``,
and always pins ``nice: false`` on its own scale dict when a headroom bound is
present) — every call site already treats its return value as authoritative,
so the mutation is harmless, but it is a real exception to this convention.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedAxisStyle,
)
from dbt_charts.core.compile.resolve.chart.tick_values import (
    numeric_domain_bounds,
)
from dbt_charts.core.render.chart.type_inference import (
    is_zero_anchored,
    zero_anchor_pinned_floor,
)

# dbt charts ChartSort.order ("asc"/"desc") → Vega-Lite sort.order. VL silently
# falls back to ascending on the raw dbt charts form, so translate at emit (mirrors
# V1 profile._SORT_ORDER_VL).

# VL scale types on which an explicit [low, high] domain is a valid continuous
# range override. Every other cartesian-x type (ordinal/nominal band scales)
# reads a 2-element domain as exactly two category values instead.

# The complement: cartesian-x types that resolve to a discrete band scale.


# Smallest outer padding that lifts a band scale's first tick off the exact
# range start. Expressed as a fraction of the step, so at any realistic plot
# width it moves a band edge by well under a thousandth of a pixel (measured:
# 0.0001px at 480px across 7/13/31 bands) — below the SVG coordinate precision
# we emit, and orders of magnitude below anything visible.


# The families that build a dimension x through these helpers — narrower than
# ``ResolvedChart`` (which the facet-narrowing predicate takes) but concrete,
# where ``_CartesianResolvedChartFields`` is the shared field protocol.

_CATEGORICAL_TYPES = ("nominal", "ordinal")


# An axis title Vega-Lite will render: a plain string, or one entry per line
# when dbt charts pre-wrapped it to fit the axis (VL renders a list as lines but
# never computes the breaks itself).


# Title block + view padding outside the plot along one axis. Used to size
# horizontal bars, and to budget how much of an axis its own title may occupy.
# Deliberately a little larger than the chrome actually measures (~66px), so
# the budget it leaves is under the real space rather than over it.


# Facet panel key (one cell value per active facet dimension) -> distinct
# values of one field seen within that panel.


def authored_measure_domain(ay: ResolvedAxisStyle) -> tuple[float, float] | None:
    """The authored numeric ``scale.continuous.domain`` on *ay*, or None.

    The single spelling of "did the author pin this axis's domain", so the
    consumers below — the VL scale emitter and the baseline gate — cannot
    disagree about what counts as authored.
    """
    cont = ay.scale.continuous if ay.scale is not None else None
    return numeric_domain_bounds(cont.domain if cont is not None else None)


def effective_measure_domain(
    axis: ResolvedAxisStyle, data_extent: tuple[float, float] | None
) -> tuple[float, float] | None:
    """The ``(lo, hi)`` this measure axis actually renders.

    On a y-axis this mirrors ``resolve_measure_y_scale``'s branches in the
    same order, so the gate that reads this and the emitter that builds the
    y scale cannot disagree about where the axis starts and ends:

    1. **Authored domain** wins outright, and suppresses everything below it by
       design — resolve leaves the baked bounds None whenever one is set.
    2. **Baked ``domain_min`` / ``domain_max``** — the pinned edges,
       taken per-edge, because None means "auto-fit THIS edge" and the two are
       decided separately (a zero-anchored axis bakes only the top).
    3. **The zero-anchor floor**, for a low edge resolve left unbaked:
       ``ladder[0]`` else ``0.0``, byte for byte what ``y_zero_scale`` pins as
       ``domainMin`` on such an axis. The ladder is filtered through
       ``zero_anchor_pinned_floor``, the emitter's own decision, so this
       cannot drift from what is really written; an authored ``scale.values``
       list may not source a domain bound, and when no rung supplies a floor
       the axis reports where ``zero: true`` auto-fits instead.
    4. **The data extent**, which is what Vega-Lite auto-fits from.

    On an unauthored x-axis, branch 2 never fires: resolve bakes no
    ``domain_min``/``domain_max`` for x. Branch 3 fires only for a
    percent-formatted quantitative x, whose zero anchor resolve bakes
    (``_bake_percent_x_zero``); any other x reduces to "authored domain, else
    the data extent" — exactly what Vega-Lite auto-fits an unpinned x-axis
    to. An x-side headroom bake added later would start populating the
    baked edges and invalidate that reduction.

    **Every value here is one the emitter actually pins, or the extent VL fits
    to — never a prediction of where VL's ``nice`` will land.** An earlier
    revision read the whole tick ladder as if it were the domain, and it does
    not hold: on a ``headroom: 0.05`` percent axis the engine pins
    ``domainMax 0.9785`` while the ladder's top rung is ``1.0``, so the rung
    sits outside the rendered domain and VL clips it. Reading it made this
    function non-monotonic in headroom — a *tighter* fit reported more range —
    and let a parity rule paint on a chart whose domain excluded it, where the
    rule's own datum then stretched the domain to cover the mark that should
    never have been drawn. A tick list is not a domain; keep this function
    reading only pinned facts.

    ``data_extent`` is None when the measure column carries no numeric rows
    under the field the caller asked about — including a synthetic folded field
    that is absent from unfolded rows, where resolve may still have baked real
    bounds. Returns None when nothing pins the axis at all.
    """
    authored = authored_measure_domain(axis)
    if authored is not None:
        return min(authored), max(authored)

    if data_extent is None:
        return None
    data_lo, data_hi = data_extent
    hi = axis.domain_max if axis.domain_max is not None else data_hi
    if axis.domain_min is not None:
        lo = axis.domain_min
    elif is_zero_anchored(axis.scale):
        # Exactly what the emitter pins as domainMin for this axis — read from
        # the same decision, never re-derived here. An inline copy of it is
        # what let this function keep answering 0.0 after the emitter stopped
        # pinning that, reporting an inverted range on an all-negative axis.
        pinned = zero_anchor_pinned_floor(axis)
        # No rung, so the emitter pins nothing and `zero: true` leaves the
        # floor to Vega-Lite's own fit: 0 for data that stays above it, the
        # data floor once anything goes below. Not `pinned or 0.0` — a real
        # 0.0 rung is a pin, not an absence.
        lo = pinned if pinned is not None else min(0.0, data_lo)
    else:
        lo = data_lo
    return lo, hi


def distinct_series_values(data: list[dict[str, Any]], series_field: str) -> list[str]:
    """Alphabetically sorted distinct non-null values of *series_field*.

    This is the ground-truth series set for palette assignment: Vega-Lite's
    own default color-scale domain, matching ``config.range.category`` slot
    *i* to the *i*-th alphabetically-sorted series.

    Nulls stay filtered here. Admitting them would put the string ``"None"`` in
    the scale domain while the rows themselves hold ``null`` — a phantom legend
    entry for a series that paints nothing, which is worse than omitting it.
    Callers that must not silently lose a series run ``validate_color_series``
    first; families that don't yet (scatter, pie/arc, overlay layers) keep the
    quiet drop until that guard is extended to them.
    """
    return sorted(
        {str(row[series_field]) for row in data if row.get(series_field) is not None}
    )


# x is a row's raw x-field value (str/date/int, whatever the query returned);
# only ever compared with >= below, never introspected.


__all__ = [
    "distinct_series_values",
]
