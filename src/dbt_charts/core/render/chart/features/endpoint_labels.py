"""Endpoint label feature: sets endpoint_label_layout and pre-computes label data."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import _BaseResolvedChartFields
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
from dbt_charts.core.compile.models.style.resolved import ResolvedSeriesLabelStyle
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    category_scale_for,
    color_at,
    ink_at,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    humanize_wide_series_name,
    raw_wide_series_names,
    unfold_wide_rows,
    wide_measure_labels_for,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_ENDPOINT_LABELS_CENTER_STACK,
    ERR_ENDPOINT_LABELS_NEGATIVE_STACK,
    ERR_ENDPOINT_LABELS_UNORDERABLE_SORT,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.emitters._cartesian import (
    companion_color_for_fill,
    emitted_categorical_color_scale,
    last_nonnull_value_per_series,
    last_nonnull_xy_per_series,
)
from dbt_charts.core.render.chart.emitters._endpoint_rail import (
    endpoint_rail_layout,
    measure_label_pane_width,
)
from dbt_charts.core.text.case import default_axis_title
from dbt_charts.core.utils import (
    VlSortOp,
    cumulative_stack_midpoints,
    numeric_column_values,
    sorted_series_by_stack_order,
    x_domain_order,
)


def _pack_against_bound(
    items: list[tuple[str, float]],
    push: Callable[[float, float], float],
    overflows: Callable[[float], bool],
) -> tuple[list[tuple[str, float]], list[str]]:
    """Greedy-pack pre-sorted, extremity-first ``items`` against a bound.

    The bound and direction are supplied entirely by the closures: ``push
    (anchor, prev_y)`` returns this item's forced position given the
    previously-placed item's position (its own anchor, unless that is too
    close — see the two call sites for the concrete direction and gap).
    ``overflows(y)`` reports whether a computed position has crossed the
    bound.

    Need-based, not positional: when placing an item would overflow, this
    evicts placed items off the top of the stack — but only ones that were
    themselves pushed away from their own anchor (a label with genuine room
    needs no push and is never evicted) — retrying the new item after each
    eviction, so an item is dropped only once no evictable neighbor remains
    and it still does not fit. Cascade order alone (which item happens to be
    processed last) never decides who gets dropped; an isolated anchor with
    real room keeps its place regardless of what a distant, unrelated
    cluster ahead of it ran out of room for.
    """
    stack: list[tuple[str, float, float]] = []  # (name, forced_y, own_anchor)
    dropped: list[str] = []
    for series, anchor in items:
        y = anchor if not stack else push(anchor, stack[-1][1])
        while overflows(y) and stack and stack[-1][1] != stack[-1][2]:
            evicted, _, _ = stack.pop()
            dropped.append(evicted)
            y = anchor if not stack else push(anchor, stack[-1][1])
        if overflows(y):
            dropped.append(series)
            continue
        stack.append((series, y, anchor))
    return [(s, y) for s, y, _own in stack], dropped


def _apply_label_cascade(
    anchors: dict[str, float],
    min_data_gap: float,
    y_domain_min: float,
    y_domain_max: float,
) -> tuple[list[tuple[str, float]], list[str]]:
    """Bidirectional greedy collision-avoidance over an anchor map.

    Returns ``(positions, dropped)``. ``positions`` is ordered top-to-bottom
    (descending y). Every raw anchor is clamped into ``[y_domain_min,
    y_domain_max]`` first — an authored ``style.axis_y.scale.domain`` can sit
    narrower than the raw data extent, so an anchor outside it is not a
    collision, it is off-domain, and Vega-Lite clips the mark at the same
    bound rather than dropping it. Only genuine crowding (see
    ``_pack_against_bound``) reaches the drop branch.

    A ``(n - 1) * min_data_gap <= y_domain_max - y_domain_min`` check by the
    caller only proves the block fits in the *best* case (anchored at the
    domain edge). Real anchors sit wherever the data puts them, so the walk
    below can still run out of room locally even when that check passes.
    """
    if not anchors:
        return [], []

    clamped = {
        series: min(y_domain_max, max(y_domain_min, y)) for series, y in anchors.items()
    }

    domain_mid = (y_domain_min + y_domain_max) / 2.0
    anchor_mean = sum(clamped.values()) / len(clamped)

    if anchor_mean >= domain_mid:
        items = sorted(clamped.items(), key=lambda kv: kv[1], reverse=True)
        positions, dropped = _pack_against_bound(
            items,
            push=lambda anchor, prev_y: min(anchor, prev_y - min_data_gap),
            overflows=lambda y: y < y_domain_min,
        )
        return positions, dropped

    items = sorted(clamped.items(), key=lambda kv: kv[1])
    positions, dropped = _pack_against_bound(
        items,
        push=lambda anchor, prev_y: max(anchor, prev_y + min_data_gap),
        overflows=lambda y: y > y_domain_max,
    )
    return list(reversed(positions)), dropped


from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelSource,
    record_series_label_truncations,
)
from dbt_charts.core.render.chart.spec import ChartSpec, EndpointLabelData, RenderBox
from dbt_charts.core.render.chart.x_domain import rendered_x_domain, vl_sort_op
from dbt_charts.core.render.utils import normalize_scalar_for_json

# Alias for the position column in the pre-computed inline data.
_Y_ALIAS = "__y"
_X_ALIAS = "__x"
# Alias for the label-text column on the layered-single-series rail (no real
# data column names a "series" there — each entry is a layer, not a row value).
_LABEL_ALIAS = "__label"


def _y_domain(
    data: list[dict[str, Any]],
    y_field: str,
    axis_y: ResolvedAxisStyle,
) -> tuple[float, float]:
    """Return (y_min, y_max) for the cascade domain.

    Prefers an explicit authored axis_y.scale.domain (2-element numeric list)
    so cascade decisions use the same bounds as VL's rendered y scale. Falls
    back to raw data range when no explicit domain is authored.
    """
    if axis_y.scale is not None:
        _ay_cont_el = axis_y.scale.continuous
        domain = _ay_cont_el.domain if _ay_cont_el is not None else None
        if domain is not None:
            try:
                lo, hi = float(domain[0]), float(domain[1])
                if hi >= lo:
                    return lo, hi
            except (TypeError, ValueError):
                pass
    ys = [
        float(row[y_field])
        for row in data
        if y_field in row and row[y_field] is not None
    ]
    if ys:
        return float(min(ys)), float(max(ys))
    return 0.0, 1.0


def _stacked_y_domain(
    data: list[dict[str, Any]],
    x_field: str,
    y_field: str,
    stack: str | None,
) -> tuple[float, float]:
    """Return the rendered (y_min, y_max) domain for a stacked bar or area chart.

    normalize → [0,1]; grouped → data range clamped at 0; stacked-zero →
    [0, max column total]. The column-sum math is identical for bar and
    area — both cumulate the same y_field per x.

    ``center`` (streamgraph) renders on this SAME [0, max column total] domain,
    not a domain symmetric around zero: Vega-Lite's own center-offset formula
    (see ``_stacked_midpoints``) floats each column up by
    ``(max_total - this_column_total) / 2``, so the column matching
    max_total sits flush at [0, max_total] and every shorter column floats
    upward but never exceeds that same ceiling — confirmed against Vega's
    compiled scenegraph output (vl-convert), not assumed.
    """
    if stack == "normalize":
        return 0.0, 1.0
    ys = [
        float(row[y_field])
        for row in data
        if y_field in row and row[y_field] is not None
    ]
    if not ys:
        return 0.0, 1.0
    if stack == "none":
        return min(0.0, min(ys)), max(0.0, max(ys))
    # "zero"/"center": column-sum math is identical for both.
    col_sums: dict[object, float] = {}
    for row in data:
        x = row.get(x_field)
        y = row.get(y_field)
        if x is not None and y is not None:
            if x not in col_sums:
                col_sums[x] = 0.0
            col_sums[x] += float(y)
    return 0.0, (max(col_sums.values()) if col_sums else max(ys))


@dataclass(frozen=True)
class RecascadeResult:
    """Final label positions plus the outcome that produced them.

    Four distinct outcomes, deliberately not one boolean: they have different
    causes and different remedies, and collapsing them made the rail report a
    height problem for a case height cannot cause or cure.

    - ``fit`` — the intended gap was honored, every label placed.
    - ``gap_did_not_fit`` — the gap is known but ``(n-1) * gap`` exceeds the
      domain span in the best case. More height (or fewer series) resolves it.
      Every label is kept, spaced evenly below the intended gap.
    - ``no_slope`` — no pixels-per-data-unit could be measured, because every
      label ties on one value or the scale collapsed them onto one pixel. The
      plot's height is irrelevant here; the data is. Every label is kept.
    - ``rail_overflow`` — the global check above passed, but the real anchors
      are clustered such that the greedy cascade (``_apply_label_cascade``)
      still ran out of room. ``dropped`` names the series that could not be
      placed at the intended gap; they are omitted from ``positions`` rather
      than piled onto the domain edge.
    """

    positions: list[tuple[str, float]]
    outcome: Literal["fit", "gap_did_not_fit", "no_slope", "rail_overflow"]
    dropped: list[str] = field(default_factory=list)


def _measure_label_pane_slope(
    label_mark_leaves: list[VLDict], emitted: dict[str, float]
) -> float | None:
    """Pixels-per-data-unit slope measured off the label pane's own rendered marks.

    We place the endpoint labels ourselves, so each mark's data-space value is
    already known (``anchors``); vl-convert's probe scenegraph carries each
    mark's rendered pixel ``y`` plus its own ``text`` — the exact series/label
    name, since the pane's ``text`` encoding is ``{"field": series_field}``.
    Pairing a mark's known value with its observed pixel position gives
    px-per-data-unit directly, with no need to read (and no risk of
    misreading) the rendered y-scale's resolved domain, which
    ``vegalite_to_scenegraph`` does not expose at all.

    Matches leaves to anchors by rendered text, not list position — leaf
    order in the scenegraph is not a contract, and this is exactly as
    collision-safe: ``anchors`` is already keyed by the same series_field
    value, so two distinct anchors can never render the same text. (Vega's
    auto-generated ``description`` — ``"field: value"`` — would work too on
    the ordinary series-color path, but is silently omitted for a
    double-underscore-prefixed field name, which is exactly what the
    layered-single-series path's ``series_field`` is; ``text`` has no such
    gap.) Picks the pair with the largest known-value spread for numerical
    stability.

    Returns ``None`` when no slope is defined: fewer than two leaves matched a
    known anchor, every matched leaf ties on the same data value (real data
    produces this — a stacked series whose trailing values are all null lands
    every label on the same total), or two distinct values render at the same
    pixel (a collapsed scale, e.g. an authored ``scale.domain: [5, 5]``).

    None is not an error, and it is not the end of the story: the caller falls
    back to even distribution, which needs no slope. Raising here instead would
    blank the whole chart over a label-placement detail.
    """
    matched: list[tuple[float, float]] = []
    for leaf in label_mark_leaves:
        text = leaf.get("text")
        if not isinstance(text, str):
            continue
        value = emitted.get(text)
        if value is not None:
            matched.append((value, float(leaf["y"])))
    if len(matched) < 2:
        return None
    lo = min(matched, key=lambda pair: pair[0])
    hi = max(matched, key=lambda pair: pair[0])
    if hi[0] == lo[0] or hi[1] == lo[1]:
        return None
    return (hi[1] - lo[1]) / (hi[0] - lo[0])


def _distribute_evenly(
    anchors: dict[str, float],
    y_domain_min: float,
    y_domain_max: float,
    outcome: Literal["gap_did_not_fit", "no_slope"],
) -> RecascadeResult:
    """Spread labels evenly across the domain, preserving their relative order.

    The answer whenever the intended gap cannot be honored — because it does
    not fit, or because no slope exists to express it in data units. It needs
    no slope: positions are assigned from the domain directly. Always reports
    a non-``fit`` outcome so the caller records the degradation; an unreported
    one would be a silent fallback.

    A zero-width domain leaves every position identical — there is nowhere to
    spread into — which is honest rather than fabricated separation.
    """
    n = len(anchors)
    step = (y_domain_max - y_domain_min) / (n - 1) if n > 1 else 0.0
    # Ascending by value, then ascending y. Python's sort is stable, so a tie
    # group keeps insertion order — which on the stacked path is baseline-first
    # (`_stacked_midpoints` accumulates from 0 upward). Assigning y upward from
    # y_domain_min therefore puts the baseline series at the bottom, matching
    # `_apply_label_cascade`. Sorting descending and mapping down from
    # y_domain_max looks equivalent but inverts every tie group, and the rail
    # replaces the color legend — its vertical order IS the series order, so an
    # inverted tie group is a wrong picture drawn from correct data.
    ordered = sorted(anchors.items(), key=lambda kv: kv[1])
    placed = [(name, y_domain_min + i * step) for i, (name, _) in enumerate(ordered)]
    placed.reverse()  # emit top-to-bottom, as `_apply_label_cascade` does
    return RecascadeResult(positions=placed, outcome=outcome)


def recascade_endpoint_labels(
    anchors: dict[str, float],
    pixel_gap: float,
    y_domain_min: float,
    y_domain_max: float,
    label_mark_leaves: list[VLDict],
    height_correction_ratio: float,
    emitted: dict[str, float],
) -> RecascadeResult:
    """Re-cascade endpoint labels against the plot geometry Vega-Lite actually resolved.

    The one entry point that turns an intended *pixel* gap into final
    data-unit label positions. Everything the pixel<->data conversion and the
    greedy nudge need lives here — the converter that calls this supplies
    only measurements (the raw anchor values, the probe's label marks, and
    the pane's height-correction ratio) and never does cascade arithmetic
    itself.

    N=1 (``anchors`` has at most one entry) is a no-op: there is no adjacent
    pair to separate, so the single anchor's position is already correct —
    skip measuring a slope (there would be no second point to measure it
    against) rather than inventing a fallback for a case with no work in it.

    Otherwise, measures px-per-data-unit off the rendered label marks
    (``_measure_label_pane_slope``) — but that measurement comes from a probe
    of the pane at its *pre-correction* declared height, while the real render
    uses the pane at its *post-correction* height (the overshoot correction
    shrinks both hconcat panes so their outer heights keep matching, per
    ``converters/chart._correct_concat_overshoot``). The label pane carries no
    axis or title (``translate.py``'s ``_wrap_hconcat_label_pane``: ``axis:
    None``, no title block), so unlike the main pane it has no chrome eating
    into its declared height — its plot rectangle *is* its declared height,
    both before and after correction. That makes the slope scale by the exact
    same ratio the pane's own height was corrected by: ``height_correction_ratio``
    (``corrected_height / pre_correction_height``, decided by the caller — 1.0
    when nothing was corrected, e.g. a chart rendered with ``height=None``),
    with no second probe needed to confirm it.

    The intended pixel gap is converted into a data-unit gap via that
    corrected slope, then run through the same bidirectional greedy cascade
    (``_apply_label_cascade``) used everywhere else, which holds each label to
    the raw data extent on the side it nudges toward. Every real endpoint
    already sits inside ``[y_domain_min, y_domain_max]`` — the extent is its
    own min and max — so no label is pushed past a bound, and none can widen
    the shared y-scale the slope was measured against (the non-circularity
    invariant this whole approach rests on).

    Two conditions make the intended gap unsatisfiable, and both take the same
    exit (``_distribute_evenly``), each tagged with its own outcome so the
    warning names the real cause:

    - ``(n - 1) * data_gap`` exceeds the domain span — the gap is known but
      does not fit.
    - No slope could be measured at all, so the gap cannot be expressed in
      data units in the first place.

    Even distribution needs no slope, so it serves both. Reporting it is what
    keeps this a defined degradation rather than a silent fallback, and the
    result stays inside ``[y_domain_min, y_domain_max]`` either way.

    A third, narrower failure survives past that global check: real anchors
    clustered away from the domain's own edge can still exhaust the room the
    greedy cascade has to work with, even though ``(n - 1) * data_gap`` fits
    in the best case. ``_apply_label_cascade`` reports this by returning the
    series it could not place; those are dropped from ``positions`` and the
    outcome is ``rail_overflow``, rather than clamping every excess label
    onto the same pixel.
    """
    if len(anchors) <= 1:
        return RecascadeResult(positions=list(anchors.items()), outcome="fit")
    if height_correction_ratio <= 0:
        raise ChartDataError(
            "could not re-cascade endpoint labels: height_correction_ratio "
            f"must be positive, got {height_correction_ratio}"
        )
    domain_span = y_domain_max - y_domain_min
    n = len(anchors)
    # Measure against the values the pane actually emitted, which may have been
    # spread apart to break a tie; place against the true anchors. Pairing
    # pixels with tied anchors would measure nothing.
    raw_slope = _measure_label_pane_slope(label_mark_leaves, emitted)
    if raw_slope is None:
        # No slope exists to convert the pixel target into data units — every
        # label ties on one value, or the scale collapsed them onto one pixel.
        # Even distribution is still available and needs no slope, so take it
        # and report it: leaving the ties in place would stack every label on
        # one coordinate and say nothing about having done so.
        return _distribute_evenly(anchors, y_domain_min, y_domain_max, "no_slope")
    slope = raw_slope * height_correction_ratio
    data_gap = pixel_gap / abs(slope)
    if domain_span > 0 and (n - 1) * data_gap > domain_span:
        return _distribute_evenly(
            anchors, y_domain_min, y_domain_max, "gap_did_not_fit"
        )
    positions, dropped = _apply_label_cascade(
        anchors,
        min_data_gap=data_gap,
        y_domain_min=y_domain_min,
        y_domain_max=y_domain_max,
    )
    if dropped:
        return RecascadeResult(
            positions=positions, outcome="rail_overflow", dropped=dropped
        )
    return RecascadeResult(positions=positions, outcome="fit")


def _refuse_unorderable_sort(
    chart_id: str, data: list[dict[str, Any]], sort: Any
) -> None:
    """Refuse a `sort:` whose column carries no numbers to aggregate.

    ``x_domain_order`` reproduces Vega-Lite's domain order by folding the sort
    field per category, and a rail that anchors on the wrong category is worse
    than a legend. Resolve steers the default away from this shape; an explicit
    opt-in lands here and gets told why. The twin gate in
    ``compile/resolve/chart/bar.py`` says why this is conservative.
    """
    if sort is None or numeric_column_values(data, sort.by):
        return
    raise ChartDataError.from_code(
        ERR_ENDPOINT_LABELS_UNORDERABLE_SORT, chart_id=chart_id, sort_by=sort.by
    )


# _stacked_midpoints's row values are query-result-shaped, genuinely dynamic.
_RankedRow = dict[str, Any]  # type-state: explicit_any — see comment above


def _stacked_midpoints(
    data: list[dict[str, Any]],
    x_field: str,
    y_field: str,
    series_field: str,
    series_names: list[str],
    stack_mode: str,
    max_column_total: float,
    sort_by: str,
    descending: bool,
    op: VlSortOp,
    stack_order: str | None = None,
) -> list[tuple[str, float]]:
    """Compute cumulative segment midpoints for vertical stacked bars or areas.

    Anchors each series at its own most-recent non-null x — not one shared
    trailing column — so a series with a trailing null, or one that stops
    early, is labeled at the midpoint of its own last real segment rather
    than dragged to the baseline of a column it has no value in (reuses
    ``last_nonnull_xy_per_series``, the same per-series walk-back the
    line/area rail already uses via ``last_nonnull_value_per_series``).
    Returns raw, un-cascaded midpoints — the greedy-nudge pass runs later,
    once the real plot geometry is known (see ``recascade_endpoint_labels``).
    Series/x/y-field generic — used by both the bar and area families.

    The stack order is one global order shared by every column (Vega-Lite
    renders one consistent series order across the whole x domain), but the
    *cumulative offset* within that order is column-local: a series stacks on
    top of whichever OTHER series precede it in that same column, whether or
    not those series' own labels anchor there. So each anchor column is
    snapshotted independently (0.0-seeded per series, filled from real rows)
    and a series' midpoint is computed against its own anchor column's
    snapshot, never a single shared one.

    Every name in *series_names* gets an anchor, including a series with no
    non-null value anywhere: it falls back to the domain's last x, zero-height
    there, so its anchor is the seam between its neighbors — the place its
    band would begin. The rail replaces the color legend, so a dropped
    anchor would leave that series painting segments in other columns under
    no name anywhere on the chart. This is the one case the 0.0 seeding still
    covers; a series with real rows just not at the last column no longer
    falls into it.

    Sort order (baseline = cumulative zero):
    - None / "value": largest global sum at baseline (VL's joinaggregate default).
    - "alphabetical": alphabetically first series at baseline.
    - "data": globally first-encountered series at baseline.
    For ``stack_mode == "normalize"``, a series' midpoint is divided by its
    OWN anchor column's total, so it lands on the 0..1 share VL actually
    renders for that column — not the trailing column's share, which a
    differently-timed series never occupies. For ``stack_mode == "center"``
    (streamgraph), each series' midpoint is offset by ``(max_column_total -
    this_series'_own_column_total) / 2`` — Vega-Lite's own center-offset
    formula (verified against its compiled scenegraph output, not the
    d3-style per-column ``-total/2`` silhouette one might assume) — applied
    per anchor column so it lands on the shared [0, max_column_total] domain
    the area mark actually renders on. Callers must pass the same
    ``max_column_total`` that ``_stacked_y_domain`` computes for this data
    when ``stack_mode == "center"``.
    """
    if not series_names:
        return []

    domain = x_domain_order(data, x_field, sort_by, descending, op=op)
    last_rank = len(domain) - 1 if domain else 0

    # "Own last non-null x" means last in the *rendered* domain order, not
    # last by raw x comparison — an authored `sort:` can render a
    # lexicographically earlier x last
    # (test_vertical_stacked_labels_follow_an_authored_sort). Every column
    # below is keyed by this rank rather than the raw x value: a rank is a
    # plain int, so it also sidesteps typing every column dict against x's
    # genuinely dynamic type (str/date/int, whatever the query returned).
    x_rank: dict[Hashable, int] = {x: i for i, x in enumerate(domain)}
    ranked_rows = [
        {x_field: x_rank[x], y_field: row[y_field], series_field: row[series_field]}
        for row in data
        if (x := row.get(x_field)) in x_rank
    ]
    # Reuses last_nonnull_value_per_series's walk-back (via the xy variant)
    # rather than a second one — a rank is still comparable with `>=`,
    # exactly what that walk-back needs.
    anchor_rank = last_nonnull_xy_per_series(
        ranked_rows, x_field, y_field, series_field
    )

    # Group the same ranked_rows by rank for the column snapshots below,
    # rather than re-scanning raw `data` a second time — ranked_rows already
    # carries every field _column needs (series_field, y_field).
    rows_by_rank: dict[int, list[_RankedRow]] = {}
    for row in ranked_rows:
        rows_by_rank.setdefault(row[x_field], []).append(row)

    series_order = sorted_series_by_stack_order(
        series_names, data, series_field, stack_order, y_field=y_field
    )

    # Prefix sums of each anchor column's stack, in series_order — cached per
    # column so series sharing an anchor (the common, non-degenerate case)
    # reuse one computation, and so the summation order matches the old
    # single-column code exactly when every series does share one.
    column_cache: dict[int, tuple[dict[str, float], list[float]]] = {}

    def _column(rank: int) -> tuple[dict[str, float], list[float]]:
        if rank not in column_cache:
            values = dict.fromkeys(series_names, 0.0)
            # A rank with no rows (e.g. the fallback seam column when no
            # domain exists at all) is a legitimate empty stack, not a
            # missing-key bug — every series is already 0.0-seeded above.
            for row in rows_by_rank.get(
                rank, []
            ):  # type-state: silent_fallback — see comment above
                s, y = row.get(series_field), row.get(y_field)
                if s is not None and y is not None:
                    values[str(s)] = float(y)
            prefix = [0.0]
            for s in series_order:
                prefix.append(prefix[-1] + values[s])
            column_cache[rank] = (values, prefix)
        return column_cache[rank]

    result: list[tuple[str, float]] = []
    for i, s in enumerate(series_order):
        anchor_x_rank = anchor_rank[s][0] if s in anchor_rank else last_rank
        values, prefix = _column(anchor_x_rank)
        v = values[s]
        cum_lower = prefix[i]
        col_total = prefix[-1]
        mid = cum_lower + v / 2.0
        if stack_mode == "normalize" and col_total > 0:
            mid = mid / col_total
        elif stack_mode == "center":
            mid += (max_column_total - col_total) / 2.0
        result.append((s, mid))
    return result


def _anchor_rows(
    spec: ChartSpec, rows: ChartRenderData, domain_rows: ChartRenderData, x_field: str
) -> ChartRenderData:
    """``rows`` with x rewritten to its position on the axis, where that is safe.

    The walk-back that picks a series' endpoint compares raw x values with
    ``>=``. On a categorical x that compares the category strings, so a slope
    chart over "Before"/"After" anchors every label on the *first* column —
    the one the labels do not name. Ranking the rows first lets the same
    walk-back read the axis's own order instead. The ranking shape is the
    stacked rail's (``_stacked_midpoints``), but not its domain source:
    ``rendered_x_domain`` also accounts for layer-contributed categories and
    reads the encoding's own field ``sort`` — which every cartesian emitter
    sets from an authored ``sort:``, and which no row order reflects.

    ``domain_rows`` is the row set Vega-Lite orders the axis from, which is
    not always the row set being ranked: a wide ``y: [a, b]`` chart is folded
    to long form here first, and that fold drops a null measure cell, so a
    sort field summed over the folded rows understates any category holding
    one. VL folds too, but uniformly — a row per measure, nulls included — so
    only the unfolded sums keep its order. The folded x values are a subset of
    the unfolded ones and the fold copies the x cell verbatim, so every row
    still finds a rank.

    Returns ``rows`` untouched — keep comparing raw values — the moment
    ``spec.data`` is set. ``BoardRenderSession.emit_chart`` stamps that field
    only after this feature has run, so its being unset is what guarantees
    Vega-Lite receives exactly the rows evaluated here; an emitter that
    populated it has rewritten what it drew, x cells included (a labeled
    "Q1 2024" bucket is drawn as an ISO date), and a rank taken against those
    would name a column this row set never held. Continuous x is left alone
    too: its raw values already compare in axis order, and so is an empty
    domain — ``rendered_x_domain`` returns one for a non-``str`` encoding
    field, and for empty ``domain_rows``, where leaving the rows be is right.
    """
    if spec.data is not None:
        return rows
    x_enc = spec.encoding.get("x")
    if not isinstance(x_enc, dict) or x_enc.get("type") not in ("nominal", "ordinal"):
        return rows
    domain = rendered_x_domain(x_enc, domain_rows, [], None)
    if not domain:
        return rows
    ranks = {value: index for index, value in enumerate(domain)}
    # A rank is an int, so the walk-back's own `>=` now reads axis order.
    # Values are normalized on the way in because rendered_x_domain keys its
    # domain that way (a datetime.date cell becomes an ISO string). Rows with
    # no x carry no endpoint and are dropped, as the walk-back already does.
    return [
        {**row, x_field: ranks[normalize_scalar_for_json(row[x_field])]}
        for row in rows
        if row.get(x_field) is not None
    ]


def _wide_endpoint_positions(
    data: list[VLDict],
    x_field: str,
    measures: list[str],
) -> dict[str, float]:
    """Return the most recent non-null endpoint for every wide measure."""
    endpoints: dict[str, tuple[VLDict, float]] = {}
    for row in data:
        x = row.get(x_field)
        if x is None:
            continue
        for measure in measures:
            value = row.get(measure)
            label = measure
            if value is not None and (
                label not in endpoints or x > endpoints[label][0]
            ):
                endpoints[label] = (x, float(value))
    return {label: value for label, (_, value) in endpoints.items()}


def _layer_color_scale(spec: ChartSpec, chart_id: str) -> dict[str, str]:
    """Return {label: fill} from the shared VL color scale the overlay built.

    emitters/_overlay.py builds one shared color scale's ``{domain, range}``
    that paints every layer's marks AND its legend swatch off the same pair
    (``render_cartesian_overlay``'s ``shared_scale``), stamped onto every
    participating layer's own ``encoding.color.scale`` — the same object, so
    reading it back off any one of them is authoritative and can't drift from
    what actually painted the marks. Searches every sub-layer rather than
    assuming ``spec.layers[0]`` is the base: an earlier feature (e.g.
    ``BaselineFeature``) may have inserted a rule layer ahead of it.
    """
    for layer_spec in spec.layers:
        fill_by_series = emitted_categorical_color_scale(
            layer_spec.encoding.get("color")
        )
        if fill_by_series is not None:
            return fill_by_series
    raise ChartDataError(
        "layered chart has no shared color scale for its endpoint-label rail",
        chart_id=chart_id,
    )


def _layered_y_domain(
    spec: ChartSpec,
    entries: list[tuple[str, ChartRenderData, str, str]],
    axis_y: ResolvedAxisStyle,
) -> tuple[float, float]:
    """Return the shared y domain across the base rows and every layer's own rows.

    The layered rail renders on one shared VL scale (translate.py pins
    ``resolve.scale.y = "shared"``), which unions the base column with every
    overlay's y column — each entry may carry its own rows (a layer's own
    ``query:``) and its own y field name.

    Below the authored short-circuit, prefers the domain the emitter already
    baked onto a sub-layer's own ``encoding.y.scale`` — mirrors
    ``_layer_color_scale``'s read-back pattern: a bar base zero-anchors and
    stamps ``domainMin``/``domainMax`` (headroom-applied) at emit time
    (``emitters/bar.py``), and that is the domain VL actually renders;
    re-deriving raw min/max from rows would silently drift from it (a data
    floor sitting well above zero produces a materially narrower span than
    the zero-anchored one VL draws). Searches every sub-layer, not just
    ``spec.layers[0]``, for the same reason ``_layer_color_scale`` does.
    Falls back to raw row bounds only when no layer's y scale carries baked
    bounds (e.g. an un-zero-anchored line/area, which VL auto-fits).
    """
    if axis_y.scale is not None and axis_y.scale.continuous is not None:
        domain = axis_y.scale.continuous.domain
        if domain is not None:
            try:
                lo, hi = float(domain[0]), float(domain[1])
                if hi >= lo:
                    return lo, hi
            except (TypeError, ValueError):
                pass
    for layer_spec in spec.layers:
        y_enc = layer_spec.encoding.get("y")
        if not isinstance(y_enc, dict):
            continue
        scale = y_enc.get("scale")
        if not isinstance(scale, dict):
            continue
        baked_domain = scale.get("domain")
        if isinstance(baked_domain, list) and len(baked_domain) == 2:
            try:
                return float(baked_domain[0]), float(baked_domain[1])
            except (TypeError, ValueError):
                pass
        domain_min, domain_max = scale.get("domainMin"), scale.get("domainMax")
        if domain_min is not None and domain_max is not None:
            try:
                return float(domain_min), float(domain_max)
            except (TypeError, ValueError):
                pass
    values = [
        float(row[y_f])
        for _, rows, _, y_f in entries
        for row in rows
        if row.get(y_f) is not None
    ]
    return (min(values), max(values)) if values else (0.0, 1.0)


def _has_negative_measure(data: list[dict[str, Any]], measure_field: str) -> bool:
    """True if any row carries a negative value in the stacked measure field.

    Negative segments push a stacked column across both signs, so the
    cumulative-midpoint anchors would float off the rendered segments.
    """
    return any(
        float(row[measure_field]) < 0
        for row in data
        if row.get(measure_field) is not None
    )


def _series_label_font_props(sl: ResolvedSeriesLabelStyle) -> dict[str, str | float]:
    """VL text-mark font props for a series label — shared by the multi-series
    right-pane rail and the layered single-series rail (the two places this
    chart-family-agnostic, compile-baked style reaches a mark)."""
    return {
        "fontSize": sl.font_size,
        "font": sl.font_family,
        "fontWeight": sl.font_weight,
        "fontStyle": sl.font_style,
    }


def _humanize_positions(
    positions: list[tuple[str, float]],
    is_wide: bool,
    wide_dimension: str | None,
    wide_measure_labels: dict[str, str],
) -> list[tuple[str, float]]:
    """Map each ``(raw series name, value)`` pair's name through
    ``humanize_wide_series_name`` -- a no-op when this isn't a wide chart,
    since ``positions`` already carries the right (non-wide) identity
    then."""
    if not is_wide:
        return positions
    return [
        (humanize_wide_series_name(name, wide_dimension, wide_measure_labels), value)
        for name, value in positions
    ]


@dataclass
class EndpointLabelFeature:
    """Sets ``endpoint_label_layout`` and pre-computes label positions.

    - line/area/vertical bar with series color → ``"right_pane"`` (hconcat).
    - horizontal stacked bar with series color → ``"top_rail"`` (vconcat).

    Positions are un-nudged; full greedy-nudge pass and dark companion stops
    are deferred.
    """

    def applies_to(self, chart: ResolvedChart) -> bool:
        # Single source of truth shared with the pre-crowding rail-width
        # estimate (emitters/_endpoint_rail.py) — see that module's
        # endpoint_rail_layout docstring for the full gate.
        return endpoint_rail_layout(chart) is not None

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        assert isinstance(chart, _BaseResolvedChartFields)
        # Endpoint labels and multiples are mutually exclusive
        # (ERR_MULTIPLES_ENDPOINT_LABELS), but this feature is registered
        # BEFORE MirrorAxisFeature/FacetFeature (features/__init__.py) — the
        # only two raisers of that error — so a faceted chart can still
        # reach this line. .all_rows() pools every panel's rows together in
        # that case, which is fine: a later feature aborts the whole render
        # before the composed spec (built here) is ever used.
        data = chart_rows(chart, datasets).all_rows()

        color_ch = chart.resolved_channels.get("color")
        has_series_color = color_ch is not None and color_ch.mode == "series"
        if not has_series_color:
            # applies_to() only reaches here when chart.layers is non-empty.
            assert isinstance(
                chart, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
            )
            return self._apply_layered_single_series(spec, chart, box, data, datasets)

        assert color_ch is not None
        if not color_ch.data_field:
            raise ValueError("series color channel must have a data_field")
        series_field = color_ch.data_field

        # For wide charts (y: [a, b, ...]), pre-fold wide rows into long form so
        # the ordinary domain/position helpers operate on (x, WIDE_LABEL_FIELD,
        # WIDE_VALUE_FIELD) triples, same as any authored-color series chart.
        # The rows VL orders the axis from — the fold below is ours, not its,
        # and the two disagree on a null measure cell (see _anchor_rows).
        domain_rows = data
        is_wide = isinstance(
            chart, (ResolvedBarChart, ResolvedAreaChart, ResolvedLineChart)
        ) and bool(chart.wide_measures)
        wide_measure_labels: dict[str, str] = {}
        wide_dimension: str | None = None
        if is_wide:
            assert isinstance(
                chart, (ResolvedBarChart, ResolvedAreaChart, ResolvedLineChart)
            )
            assert chart.wide_measures
            # order_series_names (below) must compute stack position from
            # the RAW identity -- humanizing is not order-preserving (a
            # `_usd` suffix injects "(" before a sort sees the letters), so
            # sorting already-humanized text can land a series on the wrong
            # band. Humanized text is mapped back in only once, on the
            # finished `positions`, below.
            wide_dimension = chart.color
            data = unfold_wide_rows(data, chart.wide_measures, wide_dimension)
            order_series_names = raw_wide_series_names(
                chart.wide_measures, wide_dimension, domain_rows
            )
            wide_measure_labels = wide_measure_labels_for(chart.wide_measures)
            # Color domain comes from wide_measures, not observed data: a
            # measure absent from every row would otherwise be missing and
            # desync the palette slot from the chart's own scale domain.
            all_series = [
                humanize_wide_series_name(name, wide_dimension, wide_measure_labels)
                for name in order_series_names
            ]
        else:
            # Build color domain from observed data.
            all_series = sorted(
                {
                    str(row[series_field])
                    for row in data
                    if row.get(series_field) is not None
                }
            )
            order_series_names = all_series
        palette = list(chart.palette)
        color_domain = all_series
        # Board-slot lookup for THIS series field, when it is board-bound — a
        # value's color and companion ink must key off the same slot the
        # value's own fill uses everywhere else on the board, never off its
        # position in this chart's locally-sorted series list (that position
        # can differ chart to chart, which is what mislabeled the rail).
        color_scale = category_scale_for(chart.category_colors, series_field)
        fill_by_series = emitted_categorical_color_scale(
            spec.encoding.get("color"),
            *(layer.encoding.get("color") for layer in spec.layers),
        )
        if color_scale is not None and palette:
            color_range = [color_at(color_scale, s, palette) for s in color_domain]
        elif (
            isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
            and chart.stack not in (None, "none")
            and fill_by_series is not None
        ):
            color_range = [fill_by_series[series] for series in color_domain]
        else:
            color_range = (
                [palette[index % len(palette)] for index in range(len(color_domain))]
                if palette
                else []
            )
        # Series-label typography + dark-companion ink, baked at compile time
        # (the v2 render layer cannot reach compile.palette). applies_to() has
        # already restricted this to the three cartesian families.
        assert isinstance(
            chart, (ResolvedLineChart, ResolvedAreaChart, ResolvedBarChart)
        )
        sl = chart.style.series_label
        # A bound value's ink is its SLOT's companion, never its fill's — an
        # authored literal fill sits outside the palette, so the fill-to-index
        # lookup below cannot find its companion and would hand the label the
        # fill itself.
        dark_companion_range = (
            [ink_at(color_scale, s, sl.dark_companion_palette) for s in color_domain]
            if color_scale is not None and palette
            else [
                companion_color_for_fill(fill, palette, sl.dark_companion_palette)
                for fill in color_range
            ]
        )
        label_pane_width, truncated_labels = measure_label_pane_width(
            all_series, sl.font_family, sl.font_size, box.width
        )
        label_mark_font_props: dict[str, str | float] = _series_label_font_props(sl)

        if isinstance(chart, ResolvedBarChart) and chart.orientation == "horizontal":
            x_field = chart.x
            if not isinstance(x_field, str):
                return spec
            y_field = chart.y
            if not isinstance(y_field, str):
                return spec
            _refuse_unorderable_sort(chart.id, data, chart.sort)
            if chart.stack == "center":
                raise ChartDataError.from_code(
                    ERR_ENDPOINT_LABELS_CENTER_STACK, chart_id=chart.id
                )
            if _has_negative_measure(data, y_field):
                raise ChartDataError.from_code(
                    ERR_ENDPOINT_LABELS_NEGATIVE_STACK, chart_id=chart.id
                )
            positions = cumulative_stack_midpoints(
                data,
                x_field,
                y_field,
                series_field,
                order_series_names,
                chart.sort.by if chart.sort else "",
                bool(chart.sort and chart.sort.order == "desc"),
                stack_mode=chart.stack or "zero",
                stack_order=chart.style.stack_order,
            )
            positions = _humanize_positions(
                positions, is_wide, wide_dimension, wide_measure_labels
            )
            # For normalize stacks the chart pane's x encoding must explicitly
            # pin [0, 1] so the shared vconcat x-scale propagates to the rail
            # pane correctly.  Without this, VL only auto-derives the domain
            # from stack=normalize on the main chart pane, and the rail row
            # (which has no mark) inherits an unconstrained scale.
            if chart.stack == "normalize":
                x_enc = spec.encoding.get("x")
                if isinstance(x_enc, dict):
                    x_scale = x_enc.setdefault("scale", {})
                    x_scale["domain"] = [0, 1]
            spec.endpoint_label_layout = "top_rail"
            spec.endpoint_label_data = EndpointLabelData(
                series_field=series_field,
                value_alias=_X_ALIAS,
                positions=positions,
                color_domain=color_domain,
                color_range=color_range,
                dark_companion_range=dark_companion_range,
                label_pane_width=label_pane_width,
                label_mark_font_props=label_mark_font_props,
                label_offset=chart.style.endpoint_labels.label_offset,
                height=chart.style.endpoint_labels.height,
            )
        else:
            x_field = chart.x
            if not isinstance(x_field, str):
                return spec
            y_field = chart.y
            if not isinstance(y_field, str):
                return spec

            label_gap_px = chart.style.series_label.gap_px

            # Stacked bars and areas: cumulative segment midpoints so labels
            # anchor over their rendered bands rather than at raw values.
            # Line has no stack concept (ResolvedLineChart declares no `stack`
            # field) so it never enters this branch.
            is_stacked = isinstance(
                chart, (ResolvedBarChart, ResolvedAreaChart)
            ) and chart.stack not in (None, "none")
            if is_stacked:
                assert isinstance(chart, (ResolvedBarChart, ResolvedAreaChart))
                # Bar only: a dimension axis pins its domain explicitly
                # (pin_sorted_x_domain, emitters/_cartesian.py) and bar does
                # not, so bar's rail is the one that has to predict the
                # rendered order rather than read it back off a pin.
                _refuse_unorderable_sort(
                    chart.id,
                    data,
                    chart.sort if isinstance(chart, ResolvedBarChart) else None,
                )
                if _has_negative_measure(data, y_field):
                    raise ChartDataError.from_code(
                        ERR_ENDPOINT_LABELS_NEGATIVE_STACK, chart_id=chart.id
                    )
                stack_mode = chart.stack or "zero"
                stack_order = (
                    chart.style.stack_order
                    if isinstance(chart, ResolvedBarChart)
                    else None
                )
                # Read off the emitted encoding, not re-derived from the
                # chart class: the rail has to rank columns the same way the
                # axis does. Vega-Lite applies a field sort to a DISCRETE
                # scale only — on a continuous temporal x it carries the key
                # and ignores it — so ranking by an authored sort there would
                # anchor every series on a column the axis does not draw last.
                # Every authored sort states its aggregate on the encoding —
                # a dimension axis's flat min, or bar's own verdict — and
                # vl_sort_op reads it back off the emitted key.
                _x_enc = spec.encoding.get("x")
                _x_enc = _x_enc if isinstance(_x_enc, dict) else {}
                _sort_ranks = _x_enc.get("type") in ("nominal", "ordinal")
                _sort = chart.sort if _sort_ranks else None
                _sort_op = vl_sort_op(_x_enc.get("sort"))
                y_domain_min, y_domain_max = _stacked_y_domain(
                    data, x_field, y_field, stack_mode
                )
                # Raw, un-cascaded midpoints — the greedy nudge is deferred
                # until the real plot geometry is known (see
                # EndpointLabelData's class docstring and
                # recascade_endpoint_labels).
                positions = _stacked_midpoints(
                    data,
                    x_field,
                    y_field,
                    series_field,
                    order_series_names,
                    stack_mode,
                    y_domain_max,
                    _sort.by if _sort else "",
                    bool(_sort and _sort.order == "desc"),
                    _sort_op,
                    stack_order=stack_order,
                )
                positions = _humanize_positions(
                    positions, is_wide, wide_dimension, wide_measure_labels
                )
                # Pin the main pane's y domain so VL's shared hconcat y-scale
                # matches the label positions' basis: [0, 1] for normalize,
                # [0, max_total] for center (streamgraph, same as zero) —
                # otherwise the shared scale falls back to VL's raw-value
                # auto-domain, which doesn't match either.
                if chart.stack in ("normalize", "center"):
                    y_enc = spec.encoding.get("y")
                    if isinstance(y_enc, dict):
                        y_scale = y_enc.setdefault("scale", {})
                        y_scale["domain"] = (
                            [0.0, 1.0]
                            if chart.stack == "normalize"
                            else [y_domain_min, y_domain_max]
                        )
            else:
                y_domain_min, y_domain_max = _y_domain(
                    data,
                    y_field,
                    chart.style.axis_y,
                )
                # Raw, un-cascaded last-per-series values — see the is_stacked
                # branch above and EndpointLabelData's class docstring.
                positions = list(
                    last_nonnull_value_per_series(
                        _anchor_rows(spec, data, domain_rows, x_field),
                        x_field,
                        y_field,
                        series_field,
                    ).items()
                )
                positions = _humanize_positions(
                    positions, is_wide, wide_dimension, wide_measure_labels
                )

            spec.endpoint_label_layout = "right_pane"
            # Only this layout honors label_pane_width, so only here does the
            # cap actually cut anything (the top_rail branch above ignores it).
            # A wide chart's series names come from y: [...] — unless it
            # also authors color:, whose values then lead every composite
            # label and are the part worth shortening.
            authored_field: SeriesLabelSource = (
                "y" if chart.wide_measures and chart.color is None else "color"
            )
            record_series_label_truncations(chart.id, authored_field, truncated_labels)
            spec.endpoint_label_data = EndpointLabelData(
                series_field=series_field,
                value_alias=_Y_ALIAS,
                positions=positions,
                color_domain=color_domain,
                color_range=color_range,
                dark_companion_range=dark_companion_range,
                label_pane_width=label_pane_width,
                label_mark_font_props=label_mark_font_props,
                label_offset=chart.style.endpoint_labels.label_offset,
                height=chart.style.endpoint_labels.height,
                label_gap_px=label_gap_px,
                y_domain_min=y_domain_min,
                y_domain_max=y_domain_max,
            )

        return spec

    def _apply_layered_single_series(
        self,
        spec: ChartSpec,
        chart: ResolvedLineChart | ResolvedAreaChart | ResolvedBarChart,
        box: RenderBox,
        data: ChartRenderData,
        datasets: dict[str | None, ChartRenderData],
    ) -> ChartSpec:
        """Label the base series and every overlay layer's own endpoint.

        Reachable only when applies_to() has confirmed chart.layers is
        non-empty, there is no base color-series channel to drive the
        multi-series rail above, and x/y are both plain scalar columns (see
        ``layered_endpoint_rail_fires``) — each layer stands in for a
        "series" here, named the same way emitters/_overlay.py names it for
        the legend (authored ``label:``, else the humanized column name) and
        colored the same way it paints: read straight off the shared color
        scale the overlay already built (``_layer_color_scale``), not
        re-derived.

        A layer pinning its own ``axis_y.position`` is refused at resolve
        time (``_reject_dual_axis_layered_endpoint_labels`` in
        compile/resolve/chart/_axes.py, called from every gated call site) — the
        rail anchors on one shared y-scale, a dual-axis layer renders on a
        different one, and the trigger is purely authored, known before
        render ever sees this chart.
        """
        x_field = chart.x
        y_field = chart.y
        assert isinstance(x_field, str) and isinstance(y_field, str)

        # Must match the base color-scale domain name the emitters build
        # from titles.y_plain (bar.py/line.py/area.py) exactly — the
        # `fill_by_label` lookup below reads that already-built scale, not a
        # re-derivation, so any divergence is a KeyError, not a cosmetic
        # mismatch.
        base_label = chart.y_label or default_axis_title(y_field)
        entries: list[tuple[str, ChartRenderData, str, str]] = [
            (base_label, data, x_field, y_field)
        ]
        for layer in chart.layers:
            layer_y = layer.y
            # layer.color is always None here — layered_endpoint_rail_fires
            # (applies_to()'s gate) already refused entry otherwise.
            if layer_y is None:
                continue
            layer_x = layer.x if layer.x is not None else x_field
            own_data = (
                datasets.get(layer.query_name) if layer.query_name is not None else None
            )
            rows = own_data if own_data is not None else data
            label = layer.label or default_axis_title(layer_y)
            entries.append((label, rows, layer_x, layer_y))

        # The categorical-x endpoint (_anchor_rows) is deliberately not applied
        # here: the overlay emitter publishes rewritten rows on spec.data for
        # every layered chart, and each entry may read a different dataset, so
        # there is no single row set the rendered domain can be keyed to
        # without reconciling them first, so this keeps the raw comparison
        # rather than a half-applied rank.
        anchors: dict[str, float] = {}
        for label, rows, x_f, y_f in entries:
            value = _wide_endpoint_positions(rows, x_f, [y_f]).get(y_f)
            if value is not None:
                anchors[label] = value
        if not anchors:
            return spec

        fill_by_label = _layer_color_scale(spec, chart.id)
        y_domain_min, y_domain_max = _layered_y_domain(
            spec, entries, chart.style.axis_y
        )
        # Raw, un-cascaded anchors — see EndpointLabelData's class docstring
        # and recascade_endpoint_labels.
        positions = list(anchors.items())

        color_domain = list(anchors)
        color_range = [fill_by_label[label] for label in color_domain]
        sl = chart.style.series_label
        palette = list(chart.palette)
        dark_companion_range = [
            companion_color_for_fill(fill, palette, sl.dark_companion_palette)
            for fill in color_range
        ]
        label_pane_width, truncated_labels = measure_label_pane_width(
            color_domain, sl.font_family, sl.font_size, box.width
        )
        record_series_label_truncations(chart.id, "y", truncated_labels)
        spec.endpoint_label_layout = "right_pane"
        spec.endpoint_label_data = EndpointLabelData(
            series_field=_LABEL_ALIAS,
            value_alias=_Y_ALIAS,
            positions=positions,
            color_domain=color_domain,
            color_range=color_range,
            dark_companion_range=dark_companion_range,
            label_pane_width=label_pane_width,
            label_mark_font_props=_series_label_font_props(sl),
            label_offset=chart.style.endpoint_labels.label_offset,
            height=chart.style.endpoint_labels.height,
            label_gap_px=sl.gap_px,
            y_domain_min=y_domain_min,
            y_domain_max=y_domain_max,
        )
        return spec
