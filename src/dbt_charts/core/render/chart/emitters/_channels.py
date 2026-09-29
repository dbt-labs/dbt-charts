"""Channel-to-encoding helpers for emitters."""

from __future__ import annotations

import math
import re
from typing import Any

from dbt_charts.core.colors import interpolate_hcl
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
from dbt_charts.core.compile.models.primitives import (
    ResolvedNamedPaletteScaleTargetConfig,
    ResolvedScaleTarget,
    ScaleTargetConfig,
)
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAxisStyle,
    ResolvedLegendStyle,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    PanelRows,
    map_panels,
)
from dbt_charts.core.numeric import nice_tick_values
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.table_support import (
    diverging_arm_denominators,
    resolve_hinge,
)
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    canonicalize_and_sort_ordinal_x,
    complete_ordinal_time_series,
    detect_time_unit,
)
from dbt_charts.core.render.chart.type_inference import (
    DetectedTimeUnit,
    infer_vega_type_from_data,
    resolve_authored_x_type,
    resolve_cartesian_x_type,
)
from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl, legend_to_vl
from dbt_charts.core.text.case import default_axis_title
from dbt_charts.core.utils import is_vega_numeric_value


def _numeric_extent(
    data: list[dict[str, Any]], field: str
) -> tuple[float, float] | None:
    """Real (min, max) of ``field`` across ``data``, using the exact same
    numeric-value rule (``is_vega_numeric_value``) ``infer_vega_type_from_data``
    uses to decide the color encoding's VL type — a domain must never be
    computed for a field VL is rendering ordinal/nominal (that divergence
    once baked a numeric *string* column's domain onto a scale VL treated as
    nominal, since a looser string-coercing rule computed the domain while a
    stricter one decided the type; every rect painted with no fill). Excludes
    non-finite values (NaN, ±Infinity) so they never reach ``nice_tick_values``,
    which raises on them.
    """
    values: list[float] = []
    for row in data:
        value = row.get(field)
        if value is None or not is_vega_numeric_value(value):
            continue
        coerced = float(value)
        if math.isfinite(coerced):
            values.append(coerced)
    if not values:
        return None
    return min(values), max(values)


def _nice_domain_ticks(
    scale: ScaleTargetConfig,
    data: list[dict[str, Any]] | None,
    field: str | None,
) -> list[float] | None:
    """Nice-widened tick ladder for a gradient's data-derived domain.

    None when nice-widening doesn't apply: an author set ``min`` and/or
    ``max`` (a single-sided bound is honored as that edge exactly — see
    ``gradient_scale_to_vl``'s ``domainMin``/``domainMax`` fallback and
    ``apply_gradient_legend_endpoint_labels``'s matching fallback — never
    folded into a nice-widened domain that could silently move it past what
    was typed), ``scale.nice`` is False (author opt-out), or there's no
    numeric data to derive an extent from. Callers that must never derive a
    domain from data (geoshape's pre-lookup-join path) opt out by omitting
    ``data``/``field`` at the call site (this function requires both
    truthy) — see ``gradient_scale_to_vl``'s docstring and
    ``apply_geo_choropleth_legend_endpoint_labels``, geoshape's client-side
    equivalent of the legend-labeling half of this.

    Why this is computed here in Python rather than wired through VL's own
    ``scale.nice`` (the native option `render/chart/AGENTS.md` otherwise
    requires reaching for first): ``scale.nice`` resolves dynamically inside
    the Vega runtime at render time — compiling a spec with
    ``scale: {"nice": true}`` produces a VL scale carrying ``"nice": true``
    and a data-driven domain reference, not a resolved numeric domain Python
    can read back at compile time. Baking the legend's labeled ticks from
    that unresolved reference would decouple "what domain actually renders"
    from "what Python computes for the legend labels" — reintroducing the
    exact label-disagrees-with-color bug (label says 100, swatch stops at 97)
    this feature exists to prevent. Computing the domain explicitly here and
    baking it into VL ``scale.domain`` keeps the two in sync by construction.
    """
    if scale.min is not None or scale.max is not None:
        return None
    if not (scale.nice and data and field):
        return None
    extent = _numeric_extent(data, field)
    if extent is None:
        return None
    tick_count = get_chart_rendering().gradient.nice_tick_count
    return nice_tick_values(extent[0], extent[1], tick_count)


def _raw_diverging_bounds(
    scale: ScaleTargetConfig,
    data: list[dict[str, Any]] | None,  # type-state: explicit_any — row dicts
    field: str | None,
) -> tuple[float, float] | None:
    """Effective [lo, hi] domain, NEVER nice-widened: an authored bound wins
    per edge, the free edge falls back to the raw data extent.

    This is what ``resolve_hinge``'s ``"auto"`` decision (zero-crossing vs.
    midpoint) must use even when the *rendered* domain (``_diverging_bounds``
    below) is nice-widened — table/KPI's own ``resolve_hinge`` call
    (``table_support.compute_scale_domain``) is always fed the raw extent,
    never a nice-widened one, so the two must agree here too. Nice-widening
    can shift or invent an edge that isn't really in the data: data
    ``[-97, -3]`` nice-widens to ``[-100, 0]``, which would make the
    zero-crossing rule fire (every value landing on one arm) even though the
    real data never reaches zero — table's own auto-hinge on the same data
    correctly picks the raw extent's midpoint instead.
    """
    if scale.min is not None and scale.max is not None:
        return float(scale.min), float(scale.max)
    if not (data and field):
        return None
    extent = _numeric_extent(data, field)
    if extent is None:
        return None
    lo = float(scale.min) if scale.min is not None else extent[0]
    hi = float(scale.max) if scale.max is not None else extent[1]
    return lo, hi


def _diverging_bounds(
    scale: ScaleTargetConfig,
    data: list[dict[str, Any]] | None,  # type-state: explicit_any — row dicts
    field: str | None,
) -> tuple[float, float] | None:
    """Effective [lo, hi] domain a diverging hinge is *rendered* within.

    Reuses the same nice-widening precedence as the sequential path below
    (``_nice_domain_ticks``): both authored → that exact pair; neither
    authored and ``scale.nice`` → the nice-widened extent; otherwise
    ``_raw_diverging_bounds``. Sharing this precedence (rather than always
    using the raw extent) keeps a heatmap/geo gradient's baked domain in
    agreement with ``apply_gradient_legend_endpoint_labels``, which
    nice-widens under the same conditions — a hinge domain narrower than its
    own legend's labeled ticks would paint colors that stop short of where
    the legend says they do. Returns ``None`` when neither an authored bound
    nor a usable data extent is available — the caller's signal that hinge
    cannot be resolved. This is the *rendered* domain only — see
    ``_raw_diverging_bounds`` for what decides an ``"auto"`` hinge's pivot.
    """
    if scale.min is not None and scale.max is not None:
        return float(scale.min), float(scale.max)
    nice_ticks = _nice_domain_ticks(scale, data, field)
    if nice_ticks is not None:
        return nice_ticks[0], nice_ticks[-1]
    return _raw_diverging_bounds(scale, data, field)


def _resolve_hinge_value(
    scale: ScaleTargetConfig,
    data: list[dict[str, Any]] | None,  # type-state: explicit_any — row dicts
    field: str | None,
) -> float | None:
    """The diverging pivot value, decided from the RAW (never nice-widened)
    extent — see ``_raw_diverging_bounds``'s docstring for why "auto" must
    never see a nice-widened edge. ``None`` when hinge is unset or bounds
    can't be resolved at all (e.g. geoshape's pre-lookup-join opt-out with
    no authored bounds either). Shared by ``gradient_scale_to_vl`` (which
    then EXTENDS its rendered bounds to include this value) and
    ``apply_gradient_legend_endpoint_labels`` (which does the same for its
    labeled endpoints) so the two can never resolve a different pivot for
    the same scale.
    """
    if scale.hinge is None:
        return None
    raw_bounds = _raw_diverging_bounds(scale, data, field)
    if raw_bounds is None:
        return None
    return resolve_hinge(scale, raw_bounds[0], raw_bounds[1], None)


def _diverging_domain(
    n: int, hinge: float, neg_denom: float, pos_denom: float
) -> list[float]:
    """``n`` domain breakpoints pivoting a diverging gradient at ``hinge``,
    given each arm's own interpolation-distance denominator.

    Requires an odd ``n >= 3``: the middle index (``mid = n // 2``) always
    sits at EXACTLY ``hinge``, a single breakpoint, never a half-integer
    offset. An even-length palette has no such single center index — its one
    caller, ``_diverging_breakpoints_truncated``, synthesizes one extra
    center stop (an HCL 50/50 blend of the two original center stops) first,
    making the effective count odd, rather than this function trying to
    place two independently-spaced center breakpoints itself. That
    alternative was tried and reverted: with unequal arm widths, two
    independently-spaced center points put the visual 50% pivot measurably
    off the real hinge (equal only by coincidence when both arms happen to
    have the same width) — synthesizing a shared center color first and
    reusing the exact-hinge odd-length placement is the only design that
    keeps the pivot exact regardless of arm width.

    Callers choose ``neg_denom``/``pos_denom`` via
    ``diverging_arm_denominators``'s ``arm_mode`` argument: ``"symmetric"``
    (each arm's own real width — every breakpoint this produces is then
    provably within ``[lo, hi]``, the most extreme breakpoint on either side
    landing exactly on that side's own bound) or ``"asymmetric"`` (one
    shared, wider-arm denominator — can legitimately place a breakpoint past
    the real bound on the narrower arm; see
    ``_diverging_breakpoints_truncated``, which truncates that back to
    ``[lo, hi]``).

    VL/Vega's linear scale natively supports a multi-point ``domain``/
    ``range`` pair — the same mechanism as a 2-point one, just with more
    breakpoints, so no custom interpolation runs at render time for an
    untruncated result. ``n`` is the caller's stop count for a discrete
    palette (colors must be paired 1:1, in order, with these breakpoints).
    """
    if n < 3 or n % 2 == 0:
        raise ValueError(
            f"_diverging_domain requires an odd breakpoint count >= 3, got {n}"
        )
    mid = n // 2
    domain: list[float] = []
    for i in range(n):
        if i <= mid:
            frac = (mid - i) / mid
            domain.append(hinge - frac * neg_denom)
        else:
            frac = (i - mid) / mid
            domain.append(hinge + frac * pos_denom)
    return domain


def _diverging_scheme_extent(hinge: float, lo: float, hi: float) -> tuple[float, float]:
    """VL ``scheme.extent`` bounds for a Vega scheme under
    ``arm_mode="asymmetric"``, keeping ``domain`` exactly ``[lo, hi]``.

    A scheme's continuous ramp runs 0..1 with a diverging palette's neutral
    color conventionally at 0.5. ``extent`` selects a sub-range of that ramp
    to map linearly across ``domain`` — this picks the sub-range that makes
    ``hinge`` (not the domain midpoint) land exactly at ramp position 0.5,
    with each arm's far edge reaching only as deep into the ramp as
    asymmetric's shared (wider-arm) denominator allows, matching
    ``diverging_arm_denominators``'s "no arm reaches full saturation past
    the wider arm's per-unit rate" semantics — but, unlike baking that as
    extra ``domain`` breakpoints past ``lo``/``hi``, ``domain`` here stays
    exactly the real bounds, so VL's ``clamp`` pins an out-of-domain value
    to the real edge's own (correctly asymmetric-compressed) color, not an
    extended virtual one.

    ``arm_mode="symmetric"`` needs no such trick — its per-arm denominators
    equal each arm's own real width, which is exactly what a plain
    ``domainMid`` already expresses natively (extent effectively `[0, 1]`
    under symmetric's denominators, verified: the two are equivalent).
    """
    neg_denom, pos_denom = diverging_arm_denominators(hinge, lo, hi, "asymmetric")
    a = 0.5 - (hinge - lo) / (2 * neg_denom)
    b = 0.5 + (hi - hinge) / (2 * pos_denom)
    return a, b


def _diverging_breakpoints_truncated(
    palette: list[Any],  # type-state: explicit_any — VL range: str or float stops
    hinge: float,
    lo: float,
    hi: float,
    neg_denom: float,
    pos_denom: float,
) -> tuple[list[float], list[Any]]:  # type-state: explicit_any — VL range: str stops
    """``(domain, range)`` breakpoints for a discrete diverging stop list,
    built from the ORIGINAL authored stops at their natural breakpoints (no
    RGB-lerp resampling of them) and truncated to the real ``[lo, hi]``
    bound.

    An even-length palette has no single shared center stop to pivot on, so
    it is normalized to odd first: an HCL 50/50 blend of the two original
    center stops is synthesized and inserted as the new middle color, then
    ``_diverging_domain``'s ordinary odd-length placement (a single
    breakpoint at EXACTLY ``hinge``) runs unchanged — see that function's
    docstring for why placing two independently-spaced center breakpoints
    directly (the reverted approach) put the visual pivot off the real
    hinge whenever the two arms had unequal widths.

    ``neg_denom``/``pos_denom`` decide where each (now-odd-length) stop
    lands (see ``_diverging_domain``): symmetric's own-arm-width
    denominators keep every breakpoint inside ``[lo, hi]`` by construction,
    so this is a no-op for the ordinary symmetric case; asymmetric's shared
    (wider-arm) denominator can push the shorter arm's breakpoints past the
    real bound, which this truncates. Called unconditionally for both arm
    modes — a no-op truncation is cheap, and it also correctly handles a
    degenerate (zero-width) arm under EITHER mode (see below), which
    symmetric's plain breakpoint placement alone cannot.

    The two outermost breakpoints are snapped to exactly ``lo``/``hi`` first
    when they're already extremely close RELATIVE TO THE DOMAIN'S OWN SPAN
    (``hi - lo``, not the raw magnitude of the breakpoint values — a fixed
    tolerance scaled by magnitude alone would wrongly swallow a genuine,
    substantial overshoot that just happens to sit at a large absolute
    value) — float rounding in ``hinge - (hinge - lo)`` can leave a
    wide-magnitude domain a ULP or two short of the real bound — AND
    snapping wouldn't invert their order against the next breakpoint in
    (a STRICT inequality: the center breakpoint of a fully degenerate arm
    can legitimately land exactly ON the bound, and snapping the outer one
    onto that same value would create a same-value duplicate rather than
    fixing anything). The second condition is what keeps this from ever
    firing on a genuinely degenerate arm, whose neighboring breakpoint is
    deliberately on the wrong side of the bound (or, with only 3 stops,
    exactly on it) — see below.

    Any breakpoint landing outside ``[lo, hi]`` is dropped outright. If that
    drops at least one breakpoint on an arm, exactly ONE synthetic boundary
    breakpoint is added at the real bound, whose color is the perceptual
    (CIE LCh / "hcl", matching Vega-Lite's own diverging-gradient
    interpolation — see ``interpolate_hcl``) blend between the last
    surviving in-bounds breakpoint and the first truncated one, at the exact
    fraction where the real domain value crosses the bound. No synthetic
    stop is added when a surviving breakpoint already sits exactly on the
    bound — nothing to blend, the natural stop already terminates there.

    That "already sits exactly on the bound" case is also how a fully
    degenerate (zero-width) arm resolves: every breakpoint on that arm gets
    truncated except the shared center one, which sits exactly at
    ``hinge`` — itself the bound once the arm is zero-width — so the whole
    arm collapses to a single neutral-colored stop, never the old bug where
    a floored-denominator breakpoint landed a hair short of the bound,
    paired with the extreme (not neutral) color.

    A translucent original stop that survives untouched keeps its alpha
    unmodified, passed straight through to VL/CSS, which renders it
    natively; when it's one of the two stops straddling a truncation
    boundary, the synthetic stop's alpha is interpolated linearly alongside
    the perceptual L/C/H blend (``interpolate_hcl``'s own contract — alpha
    has no analog in Lab/LCh space).
    """
    if len(palette) < 2:
        raise ValueError(
            f"diverging gradient (hinge set) requires at least 2 palette "
            f"stops, got {len(palette)}"
        )
    if len(palette) % 2 == 0:
        mid = len(palette) // 2
        center = interpolate_hcl(palette[mid - 1], palette[mid], 0.5)
        palette = [*palette[:mid], center, *palette[mid:]]

    n = len(palette)
    domain = _diverging_domain(n, hinge, neg_denom, pos_denom)
    snap_tolerance = 1e-9 * (hi - lo)
    if abs(domain[0] - lo) < snap_tolerance and lo < domain[1]:
        domain[0] = lo
    if abs(domain[-1] - hi) < snap_tolerance and hi > domain[-2]:
        domain[-1] = hi
    points = list(zip(domain, palette, strict=True))

    kept = [(x, c) for x, c in points if lo <= x <= hi]
    dropped_lo = [(x, c) for x, c in points if x < lo]
    dropped_hi = [(x, c) for x, c in points if x > hi]

    # hinge is always within [lo, hi] (extended, if needed, by the one
    # caller — gradient_scale_to_vl), and the center breakpoint(s) always
    # equal hinge exactly, so `kept` is never empty here.
    if dropped_lo and kept[0][0] != lo:
        x0, c0 = dropped_lo[-1]  # nearest truncated point to the boundary
        x1, c1 = kept[0]  # nearest surviving point to the boundary
        t = (lo - x0) / (x1 - x0)
        kept.insert(0, (lo, interpolate_hcl(c0, c1, t)))
    if dropped_hi and kept[-1][0] != hi:
        x0, c0 = kept[-1]
        x1, c1 = dropped_hi[0]
        t = (hi - x0) / (x1 - x0)
        kept.append((hi, interpolate_hcl(c0, c1, t)))

    return [x for x, _ in kept], [c for _, c in kept]


def gradient_scale_to_vl(
    scale: ResolvedScaleTarget,
    data: list[dict[str, Any]] | None = None,
    field: str | None = None,
) -> VLDict:
    """Build a VL continuous-scale dict from a resolved gradient ScaleTargetConfig.

    ``palette`` is either a Vega scheme name (forwarded as VL ``scheme``) or a
    dbt charts named palette / explicit color list, resolved to hex ``range``
    stops via ``resolve_palette_stops`` — the same resolution table/KPI
    conditional formatting already use for this field. Forwarding a dbt charts
    name straight through as a VL scheme string (the pre-fix behavior) is a
    silent no-op: Vega logs an unrecognized-scheme warning and paints with its
    default coloring instead of erroring, so the chart looks fine while
    ignoring the palette the author asked for.

    A ``list[float]`` (the opacity/stroke_width channel default) is not a
    color palette and passes straight through as ``range`` unchanged —
    ``resolve_palette_stops`` is color-stop-only.

    Shared by ``channel_to_encoding``'s gradient mode and the geoshape/heatmap
    chart-style gradient paths (``geo.py``, ``heatmap.py``) so the three
    render sites decide scheme-vs-stops the same way instead of each
    reimplementing the branch.

    ``data``/``field`` are optional and only matter when neither
    ``scale.min`` nor ``scale.max`` is author-set: passing them lets a
    nice-widened domain (see ``_nice_domain_ticks``) be baked as VL
    ``domain`` here, so the rendered gradient's colors actually extend to the
    same round bounds ``apply_gradient_legend_endpoint_labels`` labels — a
    mismatched label/color pairing (label says 100, swatch stops at 97) would
    be a real bug, not cosmetic. Omitting ``data`` is itself the opt-out
    signal for a caller that must never derive a domain from data (geoshape's
    pre-lookup-join call — see ``apply_gradient_legend_endpoint_labels``'s
    docstring for why) — ``_nice_domain_ticks`` requires ``data`` truthy
    regardless. ``channel_to_encoding``'s generic gradient branch omits them
    too, keeping its pre-existing domainMin/domainMax-only behavior
    unchanged — except when ``scale.hinge`` is set, in which case it passes
    them through so the diverging pivot can be resolved against the real
    data extent (see that call site).

    ``scale.hinge`` set makes this a diverging scale: the neutral point
    pivots at the hinge value instead of the domain midpoint. This needs
    resolvable bounds (authored min/max, or data/field) — when bounds can't
    be resolved, a discrete stop list's hinge is silently a no-op and the
    sequential path below runs instead, same opt-out shape as
    ``_nice_domain_ticks``. With bounds resolved, ``[lo, hi]`` is first
    EXTENDED (never clamped) to include ``hinge`` — a diverging palette
    shared across charts means "hinge is neutral" on every one of them, even
    a chart whose own data never reaches it; extending whichever bound
    excludes it keeps that promise, rather than pinning the hinge onto the
    nearer bound and silently repainting the pivot somewhere the author
    never asked for. Then:

    - Both a scheme and a discrete stop list honor ``arm_mode``: a scheme
      keeps ``domain`` exactly ``[lo, hi]`` and either re-windows its ramp
      via ``scheme.extent`` (``arm_mode="asymmetric"``, the shared
      wider-arm denominator) or uses VL's native ``domainMid``
      (``arm_mode="symmetric"``, each arm's own real width). A discrete
      stop list gets a synthesized ``domain``/``range`` breakpoint pair via
      ``_diverging_breakpoints_truncated`` — the original authored stops
      placed at their natural breakpoints (``_diverging_domain``, fed the
      same per-mode denominators) and truncated back to ``[lo, hi]`` with
      one perceptually-blended (HCL) synthetic boundary stop wherever a
      breakpoint would otherwise land outside it. Called for both arm
      modes uniformly (symmetric truncates nothing in the ordinary case,
      by construction) — this is also what correctly resolves a fully
      degenerate (zero-width) arm under either mode, not a separate
      special case.
    - ``interpolate: "hcl"`` is pinned explicitly on every diverging branch
      (scheme and discrete stop list alike), so it's not left implicit even
      though it's a harmless no-op for a scheme: confirmed empirically that
      ``interpolate`` has zero effect on a Vega-scheme-based scale (a scheme
      is its own pre-baked continuous ramp, sampled by position — not
      something ``interpolate`` re-blends) — it only actually changes
      anything on the discrete stop-list (``range``) branch, where it
      already equals VL's own default (see
      ``dbt_charts.core.colors.interpolate_hcl``'s docstring), but a future
      Vega-Lite default change should not silently retune that branch's
      gradient.

    A bare Vega scheme still has one narrower fallback when bounds can't be
    resolved at all (e.g. geoshape's pre-lookup-join opt-out — see this
    function's own docstring above — with no authored min/max either): an
    explicit numeric hinge alone reaches VL's native ``domainMid`` (plus
    ``domainMin``/``domainMax`` for whichever bound *is* authored, each
    likewise extended rather than clamped when hinge falls outside it),
    which needs no data because VL/Vega computes the rest of the domain from
    the actual rendered mark data at render time (not this function's
    ``data`` argument) and simply inserts the pivot into it. That fallback
    is inherently symmetric-only — ``domainMid`` has no ``arm_mode`` concept,
    and there is no known lo/hi to build asymmetric breakpoints from.
    ``hinge="auto"`` can never use it, since deciding zero-crossing vs.
    midpoint itself requires knowing the real domain.
    """
    if isinstance(scale, ResolvedNamedPaletteScaleTargetConfig):
        vl_scale: VLDict = {"range": list(scale.resolved_stops)}
    else:
        palette = scale.palette
        if isinstance(palette, str):
            vl_scale = {"scheme": palette}
        else:
            vl_scale = {"range": list(palette)}

    if scale.hinge is not None:
        bounds = _diverging_bounds(scale, data, field)
        if bounds is not None:
            lo, hi = bounds
            hinge_val = _resolve_hinge_value(scale, data, field)
            if hinge_val is not None:
                # resolve_hinge never checks an explicit (non-"auto") hinge
                # against the domain — an author can genuinely set hinge: 0
                # on data that never crosses zero. EXTEND whichever bound
                # excludes it (never clamp the hinge onto the nearer bound —
                # see this function's own docstring) so a shared diverging
                # palette's pivot means the same thing on every chart that
                # uses it, even one whose own data sits entirely on one side.
                lo, hi = min(lo, hinge_val), max(hi, hinge_val)
                asymmetric = scale.arm_mode == "asymmetric"
                if "scheme" in vl_scale:
                    vl_scale["domain"] = [lo, hi]
                    if asymmetric:
                        a, b = _diverging_scheme_extent(hinge_val, lo, hi)
                        vl_scale["scheme"] = {
                            "name": vl_scale["scheme"],
                            "extent": [a, b],
                        }
                    else:
                        vl_scale["domainMid"] = hinge_val
                else:
                    neg_denom, pos_denom = diverging_arm_denominators(
                        hinge_val, lo, hi, scale.arm_mode
                    )
                    vl_scale["domain"], vl_scale["range"] = (
                        _diverging_breakpoints_truncated(
                            vl_scale["range"], hinge_val, lo, hi, neg_denom, pos_denom
                        )
                    )
                vl_scale["clamp"] = True
                vl_scale["interpolate"] = "hcl"
                return vl_scale
        elif "scheme" in vl_scale and isinstance(scale.hinge, (int, float)):
            hinge_val = float(scale.hinge)
            # Extend whichever bound IS authored when hinge falls outside it
            # (never clamp hinge onto it — same reasoning as the
            # resolved-bounds branch above). Neither bound authored (this
            # fallback's core case) has nothing to extend against; that
            # residual gap is documented on this function's own docstring.
            if scale.min is not None:
                vl_scale["domainMin"] = min(scale.min, hinge_val)
            if scale.max is not None:
                vl_scale["domainMax"] = max(scale.max, hinge_val)
            vl_scale["domainMid"] = hinge_val
            vl_scale["clamp"] = True
            vl_scale["interpolate"] = "hcl"
            return vl_scale

    if scale.min is not None and scale.max is not None:
        vl_scale["domain"] = [scale.min, scale.max]
    else:
        nice_ticks = _nice_domain_ticks(scale, data, field)
        if nice_ticks is not None:
            vl_scale["domain"] = [nice_ticks[0], nice_ticks[-1]]
        elif scale.min is not None:
            vl_scale["domainMin"] = scale.min
        elif scale.max is not None:
            vl_scale["domainMax"] = scale.max
    return vl_scale


def apply_gradient_legend_endpoint_labels(
    enc: dict[str, Any],
    scale: ScaleTargetConfig,
    data: list[dict[str, Any]],
    field: str,
) -> None:
    """Label a gradient legend's ticks in place on an already-
    ``apply_color_legend``'d encoding dict.

    The domain an author's ``min``/``max`` override describes IS the scale's
    effective domain (``gradient_scale_to_vl`` bakes it as VL ``domain``/
    ``domainMin``/``domainMax``), so an authored bound always wins over
    anything data-derived for the edge(s) it covers: both set is labeled
    with just those two values outright; a single-sided bound is honored as
    that exact edge, with the free edge falling back to the real data
    extent — never silently dropped in favor of a nice-widened domain that
    ignores it, and never silently overridden by the raw data extent on the
    *bound* edge either (both would produce the same symptom: the legend's
    labeled ticks disagree with what ``gradient_scale_to_vl`` actually baked
    into the scale). Otherwise:

    - ``scale.nice`` True (the default): the domain widens to a "nice"
      round ladder via ``nice_tick_values`` (mirroring Vega-Lite's own
      ``scale.nice`` concept) and EVERY nice tick is labeled — min, max, and
      the round in-betweens — not just the two endpoints. This mirrors
      ``gradient_scale_to_vl``'s own widened ``domain`` for the same scale,
      so the labeled ticks always match what's actually rendered. Nice
      widening only fires when NEITHER bound is authored — see
      ``_nice_domain_ticks``.
    - ``scale.nice`` False (author opt-out): falls back to the real data
      extent for ``field``, matching what VL derives on its own with no
      ``domainMin``/``domainMax`` set — only the two endpoints are labeled,
      exact, never nice-rounded.

    Server-side only — ``data`` must be the exact rows VL will render (no
    later join step can drop any of them). Geoshape's choropleth data is
    pre-lookup-join, so it calls ``apply_geo_choropleth_legend_endpoint_labels``
    instead, which reads the resolved scale domain client-side rather than
    trusting a Python-side extent over data VL hasn't joined yet.

    Deliberately scoped to this gradient-only seam (heatmap.py / geo.py call
    sites) rather than ``legend_to_vl``/``apply_color_legend`` — those are
    shared with categorical legends, which have no scale-type discriminator
    and must not gain min/max labels from this.

    No-op when the legend was suppressed (``enc["legend"]`` is ``None`` or
    absent), when the legend already carries an authored ``values`` ladder —
    checked via ``is not None`` rather than truthiness, so an authored empty
    ladder (``values: []``, an odd but real author choice) is also respected
    and not silently overwritten (``apply_color_legend``'s write from
    ``ResolvedLegendStyle.values`` wins outright either way) — or when the
    data has no numeric values for ``field`` on whichever edge isn't
    author-bound.

    ``scale.hinge`` set threads hinge-awareness through every branch above:
    the resolved pivot (``_resolve_hinge_value`` — the same resolution
    ``gradient_scale_to_vl`` uses) EXTENDS whichever labeled endpoint
    excludes it, so the legend's endpoints always match what
    ``gradient_scale_to_vl`` actually rendered (a legend showing [500, 1000]
    while the scale secretly renders [0, 1000] would be a real bug, not
    cosmetic). When the resolved hinge sits strictly between the (possibly
    now-extended) endpoints, it is also inserted into ``values`` as its own
    labeled tick — sorted, deduplicated — so the legend shows where the
    palette pivots, not just its two extremes. Does not add a hinge TICK to
    ``apply_geo_choropleth_legend_endpoint_labels`` (see that function).
    """
    legend = enc.get("legend")
    if not isinstance(legend, dict):
        return
    if legend.get("values") is not None:
        return
    hinge_val = _resolve_hinge_value(scale, data, field)
    if scale.min is not None and scale.max is not None:
        # No float() cast here -- an authored int bound (min: 0, max: 100)
        # must stay an int in the emitted spec when there's no hinge to
        # extend for; _extend_for_hinge passes it straight through unless
        # hinge_val is set.
        lo, hi = _extend_for_hinge(scale.min, scale.max, hinge_val)
        legend["values"] = _with_hinge_tick([lo, hi], hinge_val)
        return
    nice_ticks = _nice_domain_ticks(scale, data, field)
    if nice_ticks is not None:
        lo, hi = _extend_for_hinge(nice_ticks[0], nice_ticks[-1], hinge_val)
        ticks = list(nice_ticks)
        if lo < ticks[0]:
            ticks.insert(0, lo)
        if hi > ticks[-1]:
            ticks.append(hi)
        legend["values"] = _with_hinge_tick(ticks, hinge_val)
        return
    extent = _numeric_extent(data, field)
    extent_lo = scale.min if scale.min is not None else (extent[0] if extent else None)
    extent_hi = scale.max if scale.max is not None else (extent[1] if extent else None)
    if extent_lo is None or extent_hi is None:
        return
    lo, hi = _extend_for_hinge(extent_lo, extent_hi, hinge_val)
    legend["values"] = _with_hinge_tick([lo, hi], hinge_val)


def _is_scheme_palette(scale: ScaleTargetConfig) -> bool:
    """True iff ``scale``'s palette resolves to a Vega scheme string in
    ``gradient_scale_to_vl`` (``{"scheme": ...}``, never ``{"range": ...}``)
    -- the exact condition that function's own domainMid fallback branch
    gates on. Shared here so a legend function's hinge extension can never
    fire on a case the scale build itself doesn't support."""
    return not isinstance(scale, ResolvedNamedPaletteScaleTargetConfig) and isinstance(
        scale.palette, str
    )


def _extend_for_hinge(
    lo: float, hi: float, hinge_val: float | None
) -> tuple[float, float]:
    """Extend ``[lo, hi]`` to include ``hinge_val``, or return it unchanged
    when there's no hinge to extend for. Shared by every
    ``apply_gradient_legend_endpoint_labels`` branch. ``lo``/``hi`` pass
    through verbatim (including their own ``int`` vs ``float`` type) when
    ``hinge_val`` is ``None`` -- callers must not pre-cast an authored int
    bound to float, or a no-hinge scale's spec JSON gains an unrelated
    float/int drift."""
    if hinge_val is None:
        return lo, hi
    return min(lo, hinge_val), max(hi, hinge_val)


def _with_hinge_tick(ticks: list[float], hinge_val: float | None) -> list[float]:
    """Insert ``hinge_val`` into an ascending, already-extended tick ladder
    when it sits strictly between the two endpoints and isn't already one
    of the ticks — the diverging pivot earns its own labeled tick, not just
    the two extremes."""
    if (
        hinge_val is None
        or hinge_val <= ticks[0]
        or hinge_val >= ticks[-1]
        or hinge_val in ticks
    ):
        return ticks
    return sorted([*ticks, hinge_val])


def apply_geo_choropleth_legend_endpoint_labels(
    enc: dict[str, Any], scale: ScaleTargetConfig
) -> None:
    """Label a geoshape choropleth's gradient legend with its scale's true
    endpoints, computed CLIENT-SIDE as a Vega signal reading the color
    scale's own resolved domain (``domain('color')``) — sidestepping the
    problem ``apply_gradient_legend_endpoint_labels`` can't solve for
    geoshape: its ``data`` is pre-lookup-join, so a row whose lookup key
    never matches a shape (e.g. a zero-padded FIPS code like ``"06"``
    against the ``us-states`` preset's unpadded ``"6"``) would still count
    toward a Python-side min/max, labeling an endpoint that corresponds to
    nothing shaded on the map. Vega resolves ``domain(...)`` from the ACTUAL
    post-join mark data at render time, so an unmatched row is excluded the
    same way VL's own rendered color scale already excludes it — no
    join-awareness needed here.

    An authored ``scale.min``/``scale.max`` bound still wins outright for
    the edge(s) it covers (that domain isn't derived from data, so the
    join-drop concern doesn't apply) — a single-sided bound mixes a literal
    author value with the signal for the free edge.

    ``scale.nice`` True (the default) is a no-op here: nice-WIDENING isn't
    wired to geoshape yet (widening a domain read back from a signal is a
    separate, larger change). Only ``nice: false``'s exact-endpoint mode is
    implemented.

    No-op when the legend was suppressed, when it already carries an
    authored ``values`` ladder (``is not None``, so an authored empty ladder
    is respected too — see ``apply_gradient_legend_endpoint_labels``), or
    when neither bound is authored and ``scale.nice`` is True.

    The upper edge reads ``peek(domain('color'))`` rather than
    ``domain('color')[1]``: a diverging hinge can bake a 3-element resolved
    domain (``[min, pivot, max]`` — see ``gradient_scale_to_vl``'s
    ``domainMid`` fallback), and index ``[1]`` would then read the pivot
    instead of the true max. ``peek`` (Vega's "last element" expression
    function) returns the true max in both the 2- and 3-element case, so it
    is correct whether or not a hinge is set.

    ``scale.hinge`` set EXTENDS whichever labeled endpoint it falls outside
    of — the same hinge resolution ``apply_gradient_legend_endpoint_labels``
    uses — so the labeled endpoints always match what ``gradient_scale_to_vl``
    actually baked. The both-authored branch extends outright (no data
    dependency, unlike the join-drop concern above); the mixed
    literal+signal branch extends only the literal half — the signal half
    already reads Vega's own resolved domain, which reflects any extension
    automatically. Unlike ``apply_gradient_legend_endpoint_labels``, this
    does NOT insert a separate hinge TICK: doing that against a Vega
    expression-computed legend (rather than a Python-computed list) is a
    materially harder, separate change — left as an explicit follow-up.
    """
    legend = enc.get("legend")
    if not isinstance(legend, dict):
        return
    if legend.get("values") is not None:
        return
    if scale.min is not None and scale.max is not None:
        hinge_val = _resolve_hinge_value(scale, None, None)
        lo, hi = _extend_for_hinge(scale.min, scale.max, hinge_val)
        legend["values"] = [lo, hi]
        return
    if scale.nice:
        return
    # Bounds aren't both resolvable here, so "auto" can't decide
    # zero-crossing vs. midpoint (same residual gap gradient_scale_to_vl's
    # own domainMid fallback documents) — only an EXPLICIT numeric hinge on
    # a SCHEME palette can still extend a literal bound directly, matching
    # exactly the gate gradient_scale_to_vl's own domainMid fallback uses
    # ("scheme" in vl_scale). A discrete stop list with unresolved bounds
    # can't pivot at all there (a pre-existing, documented limitation, not
    # something to newly fix here) — extending the legend for it anyway
    # would label an endpoint the scale itself never renders.
    hinge_val = (
        float(scale.hinge)
        if _is_scheme_palette(scale) and isinstance(scale.hinge, (int, float))
        else None
    )
    min_literal = (
        min(scale.min, hinge_val)
        if scale.min is not None and hinge_val is not None
        else scale.min
    )
    max_literal = (
        max(scale.max, hinge_val)
        if scale.max is not None and hinge_val is not None
        else scale.max
    )
    lo_expr = repr(min_literal) if min_literal is not None else "domain('color')[0]"
    hi_expr = repr(max_literal) if max_literal is not None else "peek(domain('color'))"
    legend["values"] = {"signal": f"[{lo_expr}, {hi_expr}]"}


def gap_fill_ordinal_time(
    ax: ResolvedAxisStyle,
    x_field: str | None,
    color_field: str | None,
    data: list[dict[str, Any]],
    resolves_cartesian_x: bool,
    *,
    is_temporal: bool | None,
    detected_time_unit: DetectedTimeUnit,
) -> tuple[list[dict[str, Any]] | None, bool]:
    """Bucket + gap-fill ordinal bucketed-time data, or just normalize it.

    Shared by the line/area/bar emitters. When the x axis carries an ordinal
    bucketed calendar grain (authored ``time_unit`` or auto-detected), collapse
    the rows to one per (bucket, series) and fill missing buckets per ``ax.fill``
    — mirrors V1 ``profile._resolve_ordinal_time_unit`` + ``_gap_fill``. When
    that grain instead resolves to a continuous temporal scale (line/area's
    implicit default) under ``fill: "null"``, no buckets are synthesized, but
    the rows are still sorted and x-canonicalized via
    ``canonicalize_and_sort_ordinal_x`` — this always returns a transformed
    list on either path, never a "just gap-filled" one.

    Returns ``(rows, x_authored_temporal)``. ``rows`` is None only when
    neither path applies (no data mutation needed). ``x_authored_temporal``
    is the ``resolve_authored_x_type(ax) == "temporal"`` verdict this
    function gates on — the ONE evaluation of that predicate for the whole
    chart render. Callers thread it straight into
    ``render_cartesian_overlay``'s ``base_x_authored_temporal`` instead of
    re-deriving it; ``x_field`` accepts None so a caller can call this
    unconditionally (no "does this chart even have an x field" branch of
    its own) and still get the real verdict back either way.

    ``resolves_cartesian_x`` must be False for a caller whose x-field instead
    renders as a plain categorical axis outside ``resolve_cartesian_x_type``'s
    resolution (e.g. horizontal bar, whose y-axis is a nominal category
    field) — such a caller always needs the full ordinal scaffold, since its
    axis can never resolve to a continuous temporal scale no matter what the
    density gate says. This function itself never calls
    ``resolve_cartesian_x_type`` — the ordinal-vs-temporal verdict for a
    caller with ``resolves_cartesian_x`` True arrives pre-computed via
    ``is_temporal``; ``mark_type``/``is_band_step_curve`` (the inputs
    ``resolve_cartesian_x_type`` needs to agree with the x-encoding) live
    only on the caller that computes it, not here.

    ``is_temporal`` is this function's one caller's (``gap_fill_ordinal_time_per_panel``)
    own ordinal-vs-temporal verdict, computed once against the whole
    (pre-panel-split) dataset and threaded through every panel, instead of
    each panel re-deriving it from its own (smaller) ``data`` — so a small
    panel's own bucket count can never diverge from the chart-wide
    x-encoding decision (a panel is a subset of the whole, so it can only
    have fewer distinct buckets, never push a whole-set-temporal verdict to
    ordinal). It is ``None`` only when the caller's own gate
    (``resolves_cartesian_x and x_field and data``) doesn't hold — which, by
    that same gate repeated on the branch below, means this function never
    reads ``is_temporal`` while it's ``None``: the branch below requires
    ``resolves_cartesian_x`` True, and this function's own early return two
    paragraphs up already exited for empty ``data``/``x_field`` on THIS
    panel, which (a panel being a subset of the whole) can only happen when
    the caller's pooled data/x_field were empty too.

    ``detected_time_unit`` likewise overrides the auto-detect branch below
    instead of re-running ``detect_time_unit`` against this call's own
    ``data`` — threaded through exactly like ``is_temporal``, for the same
    reason: re-deriving per panel lets sibling panels bucket at different
    grains, and runs ``detect_time_unit``'s ≥10% unparseable-value check
    against a much smaller per-panel denominator, so a chart that resolves
    fine pooled can newly raise for one sparse panel alone.

    Raises:
        ChartDataError: (ERR-GAP-FILL-BUCKET-COLLISION) via
            ``complete_ordinal_time_series`` when two rows collapse to the
            same (bucket, series) key.
    """
    x_authored_temporal = resolve_authored_x_type(ax) == "temporal"

    if not data or not x_field:
        return None, x_authored_temporal

    if x_authored_temporal:
        return None, True

    # Author explicitly disabled bucketing.
    authored_tu = ax.time_unit
    if authored_tu == "none":
        return None, False

    time_unit_authored = bool(authored_tu)
    # Resolve the effective time_unit: authored if explicit, else the
    # pooled-detected grain threaded in by the caller.
    time_unit: DetectedTimeUnit = (
        authored_tu if time_unit_authored else detected_time_unit
    )

    if time_unit is None or time_unit not in BUCKETED_CALENDAR_UNITS:
        return None, False

    # Only fire for ordinal (default) and auto; temporal handled above.
    if ax.type not in (None, "auto", "ordinal"):
        return None, False

    if (
        resolves_cartesian_x
        and ax.fill == "null"
        and ax.type in (None, "auto")
        and not time_unit_authored
    ):
        # is_temporal is never None here: this branch requires
        # resolves_cartesian_x True, and the caller only omits is_temporal
        # (leaves it None) when its own gate — the same
        # resolves_cartesian_x/x_field/data condition, evaluated pooled —
        # doesn't hold. See the is_temporal docstring paragraph above.
        assert is_temporal is not None
        if is_temporal:
            # No missing-bucket synthesis needed on a continuous temporal
            # scale, but the other two complete_ordinal_time_series side
            # effects (chronological sort, date-only ISO canonicalization)
            # still apply — see canonicalize_and_sort_ordinal_x.
            return canonicalize_and_sort_ordinal_x(data, x_field), False

    dim_fields = [color_field] if color_field else []
    assert ax.fill is not None, (
        "x-axis fill must be resolved before ordinal time-series completion"
    )
    return (
        complete_ordinal_time_series(
            data, x_field, time_unit, dim_fields, ax.fill, ax.fiscal_year_start_month
        ),
        False,
    )


def gap_fill_ordinal_time_per_panel(
    ax: ResolvedAxisStyle,
    x_field: str | None,
    color_field: str | None,
    dataset: ChartDataset,
    mark_type: str,
    is_band_step_curve: bool,
    resolves_cartesian_x: bool,
) -> tuple[list[dict[str, Any]] | None, bool]:
    """``gap_fill_ordinal_time``, run once per small-multiples panel.

    ``dataset`` must already be panel-shaped for this chart (the resolve-baked
    split, or a caller's ``restripe()`` of it after a render-time value
    mutation) — this never re-derives panel membership from row values.
    Nulls ``color_field`` first when it is itself a partition field
    (``map_panels``' rule 2 — the channel is constant within a panel, nothing
    left to cross-join over), then runs ``gap_fill_ordinal_time`` per panel
    and concatenates. ``dim_fields = [color_field]`` inside it stays
    unchanged and becomes correct: within a panel the grain genuinely is
    ``(x, color)``. A non-faceted chart is the N=1 case — one panel holding
    every row, so its single ``gap_fill_ordinal_time`` call is already the
    only source of order — the final pooled re-sort below is gated on
    ``bucketed_any`` (whether any panel actually bucketed, NOT on panel
    count) instead of running unconditionally: it is not
    idempotent on ``complete_ordinal_time_series``'s identity-return path
    (unfired: rows returned unmodified, in the query's own order), so an
    unconditional call would silently replace a preaggregated N=1 chart's
    query-ordered wire data with alphabetical order.

    Same ``(rows | None, x_authored_temporal)`` contract as
    ``gap_fill_ordinal_time``: returns ``None`` when it fired for no panel,
    so a caller's ``if filled is not None`` no-op path is unchanged.

    The ordinal-vs-temporal verdict AND the auto-detected time_unit are both
    decided once here, against the whole (pre-panel-split)
    ``dataset.all_rows()`` — see ``gap_fill_ordinal_time``'s ``is_temporal``/
    ``detected_time_unit`` docstrings for why: a per-panel re-derivation
    could see a smaller panel's own bucket count fall under
    ``max_ordinal_buckets`` while the chart-wide x-encoding (and every
    higher-cardinality sibling panel) resolves temporal, synthesizing a
    gap-fill null row under a continuous scale that draws no mark for a
    missing bucket and needs no synthetic row for it — and could bucket at
    a coarser or finer grain than a denser sibling, or newly raise on a
    ≥10% unparseable-value ratio a smaller panel alone doesn't clear.

    Each panel still enumerates ONLY ITS OWN [min, max] bucket range (never
    the pooled whole-dataset range) — pooling it would synthesize a
    scaffold row for every bucket between two panels' dates even when each
    panel is individually dense over a narrow window (e.g. one panel's data
    sits entirely in 2000, a sibling's entirely in 2020: pooling would
    scaffold the 20-year gap between them for BOTH panels, ~240 bogus
    buckets, which would itself cross ``max_ordinal_buckets`` and flip the
    chart to a continuous temporal scale the density gate above was built
    to avoid; see ``TestGapFillTimeUnitCentralizedAcrossPanels`` and the
    local-time-label-expr warning detector's density-gate tests). Disjoint
    per-panel ranges therefore concatenate (``map_panels`` -> ``all_rows()``,
    panel-declaration order) in a NON-chronological sequence when the
    panels don't happen to already be date-ordered — fixed below by a final
    chronological re-sort of the pooled flat result, which costs nothing
    per panel's own bucket count.

    Raises:
        ChartDataError: (ERR-GAP-FILL-BUCKET-COLLISION) via
            ``gap_fill_ordinal_time`` when two rows in one panel collapse
            to the same (bucket, series) key.
    """
    data = dataset.all_rows()
    panel_fields = tuple(axis.field for axis in dataset.axes)
    is_temporal = None
    if resolves_cartesian_x and x_field and data:
        vl_type, _, _ = resolve_cartesian_x_type(
            data, x_field, ax, mark_type, is_band_step_curve, panel_fields
        )
        is_temporal = vl_type == "temporal"
    # A pure function of `ax` (the resolved axis style), not of any panel's
    # rows — computed once here so a chart resolved WITH data (baked
    # `panel_axes` non-empty) but rendered against zero rows (`dataset` has
    # 0 panels, `fill_one_panel` never runs) still gets the real verdict
    # instead of regressing to the `False` this variable would otherwise
    # stay initialized to. A chart resolved with NO data bakes
    # `panel_axes == ()`, the trivial N=1 case — one (empty) panel, so
    # `fill_one_panel` does run there. Mirrors gap_fill_ordinal_time's own
    # first line.
    x_authored_temporal = resolve_authored_x_type(ax) == "temporal"
    detected_time_unit: DetectedTimeUnit = None
    # Same predicates gap_fill_ordinal_time gates its own (pre-hoist)
    # detect_time_unit call on: an authored time_unit (including the
    # explicit "none" opt-out) or a temporal x-axis both skip auto-detect
    # entirely, so this must never run — and never raise — when either
    # applies. See that function's `detected_time_unit` docstring.
    if x_field and data and not ax.time_unit and not x_authored_temporal:
        x_values = [row.get(x_field) for row in data if x_field in row]
        x_type = (
            infer_vega_type_from_data(data, x_field)
            if x_field in data[0]
            else "nominal"
        )
        detected_time_unit = (
            detect_time_unit(x_values) if x_type == "temporal" else None
        )
    fired = False
    bucketed_any = False

    def fill_one_panel(
        effective_color: str | None, panel_rows: PanelRows
    ) -> list[dict[str, Any]]:
        nonlocal fired, bucketed_any
        filled, _x_authored_temporal = gap_fill_ordinal_time(
            ax,
            x_field,
            effective_color,
            panel_rows,
            resolves_cartesian_x,
            is_temporal=is_temporal,
            detected_time_unit=detected_time_unit,
        )
        if filled is None:
            return panel_rows
        fired = True
        # complete_ordinal_time_series returns its `data` argument unchanged
        # (the same list object) on every identity path — no rows, no parseable
        # x values, no dates. Identity means nothing was bucketed, so the rows
        # are still in the query's own order and the pooled re-sort below must
        # not touch them.
        if filled is not panel_rows:
            bucketed_any = True
        return filled

    result = map_panels(dataset, color_field, fill_one_panel)
    if not fired:
        return None, x_authored_temporal
    filled_rows = result.all_rows()
    if x_field and bucketed_any:
        # complete_ordinal_time_series ran per panel above (never pooled —
        # see the density-gate paragraph in this function's docstring), so
        # ChartDataset.all_rows()'s panel-order concatenation is not
        # guaranteed chronological when panels' own ranges are disjoint.
        # canonicalize_and_sort_ordinal_x re-sorts the FLAT pooled result by
        # bucket key, independent of any panel's own enumerated range, so
        # the ordinal x scale's encounter-order domain reads chronologically
        # regardless of how the panels' dates interleave. NOT gated on
        # ``is_temporal``: that verdict is decided on the RAW rows, and only
        # ``fill: null`` lets it short-circuit gap-fill — under any other
        # authored fill the buckets get enumerated anyway and the emitter,
        # re-resolving against those filled rows, lands back on ordinal, where
        # encounter order IS the domain. Gating on the pre-fill verdict left
        # that axis reading 2020→2021 then 2000→2001. Running the sort on a
        # genuinely continuous scale is near-free (marks position by literal
        # date value), which is the cheaper side to be wrong on. Not entirely
        # free: one order-dependent consumer survives there — a faceted line
        # chart with ``style.dashes`` and a non-partition ``color`` reaches
        # ``_distinct_in_order`` (line.py), so the sort can shift that scale's
        # domain order and its palette slots. Cosmetic, and arguably the more
        # correct order.
        # Skipped when no panel actually bucketed (``bucketed_any``): this
        # sort is not idempotent on
        # ``complete_ordinal_time_series``'s identity-return path, where the
        # rows are unmodified and still in the query's own order, so running it
        # there would silently replace query-ordered wire data with
        # alphabetical order — for a faceted chart as much as an N=1 one.
        # Panel count is the wrong gate: whether bucketing ran is the property
        # that makes the re-sort safe, and a faceted chart on an unbucketable
        # x column takes the same identity path a single-panel one does.
        filled_rows = canonicalize_and_sort_ordinal_x(filled_rows, x_field)
    return filled_rows, x_authored_temporal


def channel_to_encoding(
    ch: ResolvedStyleChannel,
    data: list[dict[str, Any]],
    *,
    axis_style: ResolvedAxisStyle | None = None,
    title: str | None = None,
    format_str: str | None = None,
    scale: dict[str, Any] | None = None,
    legend_hidden: bool = False,
) -> dict[str, Any]:  # type-state: explicit_any — VL encoding fragment
    """Map a resolved style channel to a Vega-Lite encoding fragment.

    Never receives a conditional-mode channel: the bar/line/area/scatter/pie/
    heatmap/geo emitters that call this are the only families that ever reach
    it, and only kpi/table can still produce ``mode="conditional"`` — neither
    calls this function (kpi has its own row evaluator, table bypasses the
    channel projector entirely).

    Palette is NOT embedded here; emitters set config.range.category directly.

    Args:
        ch: The resolved style channel.
        data: Row data for the chart — used to infer the VL type for series-mode
            channels (quantitative vs nominal) matching oracle infer_vega_type_from_data
            behavior.
        axis_style: Optional resolved axis style; when provided, emits an ``axis``
            key via ``axis_to_vl``. Family PRs pass ``board_style.charts.axis_*``
            here — this is the shared seam for per-axis presentation porting.
        title: Optional axis/encoding title override.
        format_str: Optional VL format string (e.g. ``",.0f"``).
        scale: Optional VL scale dict override.
    """
    if ch.mode == "literal":
        return {"value": ch.literal_value}

    if ch.mode == "series":
        # Plain field binding only — palette goes in spec.config.range.category,
        # not in encoding.color.scale.range.
        # Type is inferred from data: quantitative for numeric columns, nominal
        # otherwise.
        enc: dict[str, Any] = {
            "field": ch.data_field,
            "type": infer_vega_type_from_data(data, ch.data_field),
        }
        if axis_style is not None:
            ax = axis_to_vl(axis_style)
            if ax:
                enc["axis"] = ax
        if title is not None:
            enc["title"] = title
        if format_str is not None:
            enc["format"] = format_str
        if scale is not None:
            enc["scale"] = scale
        if legend_hidden:
            enc["legend"] = None
        return enc

    if ch.mode == "gradient":
        assert ch.scale is not None
        # data/field are withheld unless hinge is set: passing them
        # unconditionally would also switch on _nice_domain_ticks widening
        # for every unauthored-bound gradient channel here, a behavior
        # change outside this branch's existing domainMin/domainMax-only
        # contract (see gradient_scale_to_vl's docstring). hinge needs the
        # real extent to pivot when no min/max is authored.
        hinge_data = data if ch.scale.hinge is not None else None
        hinge_field = ch.data_field if ch.scale.hinge is not None else None
        enc = {
            "field": ch.data_field,
            "type": "quantitative",
            "scale": gradient_scale_to_vl(ch.scale, hinge_data, hinge_field),
        }
        if axis_style is not None:
            ax = axis_to_vl(axis_style)
            if ax:
                enc["axis"] = ax
        if title is not None:
            enc["title"] = title
        if format_str is not None:
            enc["format"] = format_str
        if scale is not None:
            raise ValueError(
                f"channel '{ch.channel}': cannot apply scale override to a gradient "
                "channel — gradient scale is set by the channel definition"
            )
        return enc

    raise ValueError(
        f"channel_to_encoding does not handle mode {ch.mode!r} — 'conditional' is a "
        "real mode, but only table/kpi still produce it, and neither reaches this "
        "function (kpi has its own row evaluator, table bypasses the channel projector)"
    )


def field_encoding(field: str, type_: str) -> dict[str, str]:
    return {"field": field, "type": type_}


def categorical_color_encoding(
    color_ch: ResolvedStyleChannel | None, enc_type: str | None
) -> bool:
    """True when a color encoding is a categorical series worth resolving
    ``legend.values`` against -- a series-mode channel, a nominal or
    ordinal VL type, and a bound field.

    ``enc_type`` is the caller's own VL type for this encoding (typically
    ``enc.get("type")``; the render-warnings detector, which has no
    ``enc``, passes its own independently-inferred type instead). Every
    family checked this same triple before calling
    ``apply_legend_entry_order``, spelled differently per call site --
    this is the one place that spells it.
    """
    return (
        color_ch is not None
        and color_ch.mode == "series"
        and enc_type in ("nominal", "ordinal")
        and bool(color_ch.data_field)
    )


def apply_color_legend(
    enc: dict[str, Any],  # type-state: explicit_any — VL fragment
    legend: ResolvedLegendStyle,
    *,
    force_hidden: bool = False,
    drop_values: bool = False,
) -> None:
    """Inject resolved legend config into a color encoding dict, in-place.

    Sets ``enc["legend"] = None`` when the legend is suppressed, or injects
    the full ``legend_to_vl`` config otherwise.  No-op when ``legend_to_vl``
    returns nothing (not reachable for fully-resolved legends).

    ``force_hidden`` suppresses the legend regardless of ``legend.visible`` — the
    caller passes it when a chart-level concern (e.g. endpoint labels replacing
    the legend) overrides the resolved visibility.

    ``drop_values`` strips an authored ``values`` key from the injected
    config after the fact, since a caller can't suppress it by copying
    the (construction-final) ``legend`` model itself.
    """
    if force_hidden or not legend.visible:
        enc["legend"] = None
    else:
        legend_val = legend_to_vl(legend)
        if legend_val:
            if drop_values:
                legend_val.pop("values", None)
            enc["legend"] = legend_val


_LEGEND_TOKEN_FOLD = re.compile(r"[-_\s]+")


def _fold_legend_token(token: str) -> str:
    """Case/separator-fold a legend token for alias matching.

    Collapses ``-``, ``_`` and runs of whitespace to a single space, then
    casefolds, so ``net_revenue``, ``Net-Revenue`` and ``net revenue`` all
    fold to the same key. Only ever compares spellings a caller already
    enumerated (the domain entry itself, plus its ``aliases``) -- never used
    to invent or guess one.
    """
    return _LEGEND_TOKEN_FOLD.sub(" ", token).strip().casefold()


def resolve_legend_entries(
    authored: list[str],
    domain: list[str],
    aliases: dict[str, frozenset[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve an authored ``legend.values`` list against the real domain.

    ``domain`` is the display-order list (a permutation of the legend's
    real entries). ``aliases`` maps a domain entry to the extra spellings
    that also resolve to it (e.g. a wide chart's raw measure name for its
    humanized label), built forward from each entry's own provenance.

    Two tiers, exact then fold. An exact spelling of a domain entry or
    its alias always wins, even over a fold match on a different entry.
    Fold matching (case/separator-insensitive) only runs when there is
    no exact spelling, and only that tier can be ambiguous.

    Returns ``(resolved, unmatched)``: ``resolved`` is the matched
    entries in authored order; ``unmatched`` is every authored token
    that matched nothing or matched more than one entry ambiguously --
    never guessed, never raised. The caller drops an unmatched entry;
    the render-warnings detector reports it.
    """
    # Pass 1 claims every domain entry's own name first, locked into
    # own_names, so pass 2's alias fill can never overwrite it with an
    # alias that collides with a DIFFERENT entry's own name.
    exact_to_entry: dict[str, str | None] = {}
    for entry in domain:
        exact_to_entry.setdefault(entry, entry)
    own_names = frozenset(exact_to_entry)
    for entry in domain:
        entry_aliases = (
            aliases.get(
                entry, ()
            )  # type-state: silent_fallback — no aliases is a legal empty set, not masked bad input
            if aliases
            else ()
        )
        for spelling in entry_aliases:
            if spelling in own_names:
                continue
            if spelling not in exact_to_entry:
                exact_to_entry[spelling] = entry
            elif exact_to_entry[spelling] != entry:
                exact_to_entry[spelling] = (
                    None  # two aliases collide -- fall through to fold tier
                )

    folded_to_entries: dict[str, list[str]] = {}
    for entry in domain:
        entry_aliases = (
            aliases.get(
                entry, ()
            )  # type-state: silent_fallback — no aliases is a legal empty set, not masked bad input
            if aliases
            else ()
        )
        # Same own_names exclusion, carried into the fold tier: a
        # case/separator variant of an entry's own name must resolve as
        # unambiguously as the literal spelling does.
        for spelling in {entry, *entry_aliases}:
            if spelling in own_names and spelling != entry:
                continue
            folded_to_entries.setdefault(_fold_legend_token(spelling), []).append(entry)

    resolved: list[str] = []
    unmatched: list[str] = []
    for token in authored:
        exact_hit = exact_to_entry.get(token)
        if exact_hit is not None:
            resolved.append(exact_hit)
            continue
        candidates = list(
            dict.fromkeys(
                folded_to_entries.get(  # type-state: silent_fallback — zero candidates is legal, feeds the unmatched branch below, never hides an error
                    _fold_legend_token(token), []
                )
            )
        )
        if len(candidates) != 1:
            unmatched.append(token)
            continue
        resolved.append(candidates[0])
    return resolved, unmatched


def layer_label_and_aliases(
    authored_label: str | None, y_field: str
) -> tuple[str, frozenset[str]]:
    """The legend label for a colorless overlay layer (or a colorless
    base), and its alias set: the label itself, the raw y-column, and the
    column's default title -- an authored ``legend.values`` entry may name
    any of the three.

    Pure config-to-config: takes no rows, so it cannot diverge in
    behavior between callers. Shared by the overlay emitter
    (``_overlay.py``) and the render-warnings detector
    (``legend_values_unresolved.py``).
    """
    label = authored_label or default_axis_title(y_field)
    return label, frozenset({label, y_field, default_axis_title(y_field)})


def apply_legend_entry_order(
    enc: VLDict,
    order: list[str],
    *,
    authored: list[str] | None,
    aliases: dict[str, frozenset[str]] | None = None,
) -> None:
    """Pin the color legend's rendered entry order.

    Vega's own SVG legend re-derives its order independently of an
    explicit ``scale.domain`` override for a stacked mark, falling back
    to alphabetical -- ``legend.values`` is the one override it honors,
    so every call site that reorders the paint scale for display also
    pins this to the SAME order.

    ``authored`` must be ``ResolvedLegendStyle.values``, not
    ``enc["legend"]["values"]``, which may carry unrelated state from an
    earlier call. When not ``None`` it is resolved against ``order`` and
    wins outright, reordering and filtering the legend to exactly the
    entries it names. An entry that fails to resolve is dropped, never a
    hard failure; if nothing matched, the full ``order`` is used instead
    of an empty legend.

    No-op when the legend is suppressed -- a caller that also needs to
    reorder a paint scale regardless of legend visibility must resolve
    independently (see ``bar.py``'s grouped branches).
    """
    legend = enc.get("legend")
    if not isinstance(legend, dict):
        return
    if authored is None:
        # dict.fromkeys, not set(): `order` can legitimately repeat one
        # entry (an overlay base and a layer whose labels collide) --
        # Vega's own inference dedupes that automatically, so an explicit
        # pin must too, or the collision renders as two identical swatches.
        legend["values"] = list(dict.fromkeys(order))
        return
    resolved, _unmatched = resolve_legend_entries(authored, order, aliases)
    resolved = list(dict.fromkeys(resolved))
    if resolved or not authored:
        # Non-empty, or the author explicitly wrote `values: []` --
        # respected, never silently replaced with the default order.
        legend["values"] = resolved
    else:
        # Nothing matched at all -- fall back to the full default order
        # instead of an empty legend.
        legend["values"] = list(dict.fromkeys(order))


__all__ = [
    "apply_color_legend",
    "apply_geo_choropleth_legend_endpoint_labels",
    "apply_gradient_legend_endpoint_labels",
    "apply_legend_entry_order",
    "categorical_color_encoding",
    "channel_to_encoding",
    "field_encoding",
    "gradient_scale_to_vl",
    "infer_vega_type_from_data",
    "resolve_legend_entries",
]
