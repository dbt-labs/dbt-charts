"""Density-adaptive line/area stroke width formula — compile-side.

Pure functions of chart geometry and data density.  Lives in compile/resolve/
so both the resolve layer (which bakes the final stroke) and the render layer
(which sizes facet panels) share exactly one copy of the math.  Render imports
the facet helper; compile uses the full suite.

Calibrated against examples/playground-experimental/charts/labs/line-stroke-density-lab.yml:
doubling px/pt adds a constant to stroke (log, not linear) so the width stays
legible at the moiré-dense end and proportional at the sparse end.
See the closed task brief density-adaptive-line-and-area-stroke.md for the
full calibration table and design rationale.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, TypeVar, overload

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.resolve.chart._chart_rows import PanelRows, reduce_panels

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
    from dbt_charts.core.compile.models.chart.resolved._channel import (
        ResolvedStyleChannel,
    )
    from dbt_charts.core.compile.models.chart.resolved._partition import PartitionAxis
    from dbt_charts.core.compile.models.style.theme.area import AreaLineStyle
    from dbt_charts.core.compile.models.style.theme.marks import (
        LineMarkStyle,
        PointMarkStyle,
    )
    from dbt_charts.core.compile.resolve.chart._chart_rows import ChartDataset

_PointMarkT = TypeVar("_PointMarkT", bound="PointMarkStyle")


def adaptive_stroke(
    px_per_point: float,
    min_w: float,
    max_w: float,
) -> float:
    """Compute the density-adaptive stroke width in pixels.

    ``stroke = snap_half(clamp(1 + ln(px_per_point), min_w, max_w))`` where
    ``snap_half(x) = round(2*x) / 2`` — half-integer steps keep the dense end
    from rounding straight to whole pixels (which would defeat the formula's
    bottom region).

    ``min_w``/``max_w`` are required (no in-code defaults): the caller passes
    the ``chart_rendering.stroke`` clamp bounds so they stay overridable and
    introspectable like the sibling ``chart_rendering.facet`` geometry. They
    are calibration constants (fit against the density lab) — retune by
    re-running that calibration, not casually.

    Args:
        px_per_point: Chart pixel width divided by the number of distinct
            x-values in the densest rendered series.  Must be positive.
        min_w: Clamp floor in pixels (``chart_rendering.stroke.min_width``).
        max_w: Clamp ceiling in pixels (``chart_rendering.stroke.max_width``).

    Returns:
        Stroke width in pixels, snapped to the nearest 0.5.

    Raises:
        ValueError: ``px_per_point`` is not positive — a caller bug (the
            caller must only invoke this once chart width and point count are
            both known and non-zero; anything else is the fallback path).
    """
    if px_per_point <= 0:
        raise ValueError(f"px_per_point must be positive, got {px_per_point!r}")
    raw = 1.0 + math.log(px_per_point)
    clamped = max(min_w, min(max_w, raw))
    return round(clamped * 2) / 2


def max_points_per_series(
    data: PanelRows,
    x_field: str,
    series_field: str,
) -> int:
    """Distinct x-values in the densest series, within one panel's rows.

    A melted multi-series query has one row per (x, series) pair — raw row
    count overcounts density by a factor of series count.  Grouping by the
    color/series field and taking the max group size gives the true per-series
    point count; any series in moiré regime kills legibility for the whole
    chart, so the densest one wins.

    Density is computed one panel at a time — ``data`` is a single panel's
    rows (the N=1 case for a non-faceted chart is one panel holding every
    row). A faceted chart folds this across panels via ``reduce_panels``, so
    this function itself has no facet awareness left to carry.

    Args:
        data: One panel's rows.
        x_field: The x-axis data field name.
        series_field: The series/color data field, or ``""`` for single-series
            charts (all rows land in one group).

    Returns:
        Number of distinct x-values in the densest group.  0 when data is empty.
    """
    if not data:
        return 0

    groups: dict[Any, set[Any]] = {}
    for row in data:
        x_val = row.get(x_field)
        if x_val is None:
            continue
        series_key = row.get(series_field) if series_field else None
        groups.setdefault(series_key, set()).add(x_val)

    return max((len(xs) for xs in groups.values()), default=0)


def facet_panel_width(
    width: float,
    panel_cols: int,
    has_mirror: bool,
    extra_axis_px: float,
) -> float:
    """Per-panel pixel width for a small-multiples chart.

    Mirrors the arithmetic in render/chart/vega_lite._apply_facet_layout so
    that the adaptive stroke baked at resolve time uses the same width the
    renderer actually paints into.  Render calls this helper directly so the
    formula has a single source of truth.

    Args:
        width: Full card pixel width (what resolve() receives).
        panel_cols: Number of column panels derived from facet cardinality.
        has_mirror: True when the measure axis is mirrored to both edges
            (adds an extra gutter for the far-edge axis).
        extra_axis_px: Width ONE extra axis instance costs, VL being forced
            to draw a whole new one inside EVERY column panel beyond what
            the base ``chrome_px`` budget already covers — the columns/
            grid-facet case where narrowing a position channel's scale
            independent makes VL paint that axis per panel instead of
            sharing one down the left edge (see
            ``render/chart/emitters/_cartesian.py``'s
            ``facet_bound_position_channels`` docstring and
            ``facet_extra_axis_width_px``, which measures this from the
            channel's own widest label rather than a flat constant). Costs
            every column panel, not the whole card once — chrome_px/
            mirror_axis_px are shared edge gutters subtracted from the
            total before dividing by ``panel_cols``, but this extra axis
            repaints inside each panel, so its total cost scales with
            ``panel_cols`` too. ``0.0`` when no channel forces that.

    Returns:
        Per-panel pixel width: ``(width - chrome) / panel_cols``, never
        floored to ``min_panel_px``. The card boundary always wins — a panel
        count that would otherwise need more than the card's width shrinks
        below the legibility floor instead of pushing painted content past
        the card's edge (``render/warnings/facet_panel_width_below_minimum.py``
        warns the author when that happens).
    """
    facet_cfg = get_chart_rendering().facet
    chrome = facet_cfg.chrome_px + (facet_cfg.mirror_axis_px if has_mirror else 0.0)
    usable = max(width - chrome, 0.0)
    return max(usable / panel_cols - extra_axis_px, 0.0)


def panel_axis_cardinality(axes: tuple[PartitionAxis, ...], field: str | None) -> int:
    """Distinct values on one baked small-multiples axis (>= 1).

    ``field`` is ``multiples.rows`` or ``multiples.columns``; ``None`` means
    that facet dimension is absent, a single track. Reads the cardinality
    resolve already baked onto ``panel_axes`` — the replacement for the old
    ``panel_count``, which re-derived the same number by re-scanning raw rows
    at both resolve and render, risking drift between the two counts.
    """
    if field is None:
        return 1
    for axis in axes:
        if axis.field == field:
            # partition() never bakes an axis with empty values: a non-empty
            # rows list means every row supplied a value for this field (or
            # partition() raised), so at least one canonical value lands in
            # axis_canon_order[field]; an empty rows list short-circuits to
            # the trivial axes == () case before any axis is built at all —
            # this loop then never matches, falling through to `return 1`
            # below instead.
            return len(axis.values)
    return 1


def resolve_px_per_point(
    channels: dict[str, ResolvedStyleChannel],
    dataset: ChartDataset,
    x_field: str,
    width: float,
    multiples: MultiplesConfig | None,
    has_mirror: bool,
) -> float:
    """Chart pixel width per distinct x-value in the densest rendered series.

    The shared density signal: ``adaptive_stroke``'s input, and the point-
    companion spacing trigger (``bake_point_companions``) both key off this
    same measurement, so it is computed once here rather than twice.

    Consolidates the density block that was duplicated in ``_resolve_line`` and
    ``_resolve_area``:  series_field extraction → per-panel width via
    ``facet_panel_width`` when faceted → densest-panel ``max_points_per_series``.

    Args:
        channels: Resolved channel bindings for the chart (from
            ``_channels_for``).
        dataset: The chart's panel-split rows (one panel, keyed ``()``, for a
            non-faceted chart — the N=1 case).
        x_field: The x-axis field name (caller guards ``x is not None``).
        width: Full card pixel width.
        multiples: Small-multiples config, or ``None`` for non-faceted charts.
        has_mirror: Whether the measure axis is mirrored (from ``ay.mirror``).

    Returns:
        Pixels of width per distinct x-value (> 0), or 0.0 when not
        applicable (no data, zero-width panel, or zero distinct x-values).
    """
    color_ch = channels.get("color")
    series_field = (
        color_ch.data_field
        if color_ch is not None and color_ch.mode == "series"
        else ""
    )
    effective_width = width
    if multiples is not None:
        panel_cols = panel_axis_cardinality(dataset.axes, multiples.columns)
        # extra_axis_px is always 0 here: the columns/grid-unbudgeted case
        # only fires for a channel `facet_bound_position_channels` narrows,
        # which requires a nominal/ordinal VL "y" — line/area's own y (the
        # only families whose stroke this function computes) is always the
        # chart's quantitative measure, so it can never be the narrowed
        # channel this budgets for. Determining the real narrowing decision
        # also needs the emitted VL encoding type, which does not exist yet
        # at resolve time (compile/ cannot import render/'s FacetFeature
        # either way).
        effective_width = facet_panel_width(
            width, panel_cols, has_mirror, extra_axis_px=0.0
        )
    if not effective_width > 0:
        return 0.0
    n_pts = reduce_panels(
        dataset,
        lambda rows: max_points_per_series(rows, x_field, series_field),
    )
    if not n_pts:
        return 0.0
    return effective_width / n_pts


def stroke_from_px_per_point(px_per_point: float) -> float:
    """Turn a density signal into an adaptive stroke width, or 0.0 when inapplicable.

    The one place that reads ``chart_rendering.stroke``'s clamp and calls
    ``adaptive_stroke`` — every caller that already has ``px_per_point`` (from
    ``resolve_px_per_point``) goes through this leaf instead of re-deriving
    the clamp read, so a future change to how a stroke derives from density
    lands in one place for every chart family.

    Args:
        px_per_point: Pixels of width per distinct x-value, from
            ``resolve_px_per_point``. ``<= 0`` means not applicable.

    Returns:
        Adaptive stroke in pixels (> 0), or 0.0 when not applicable.
    """
    if px_per_point <= 0:
        return 0.0
    clamp = get_chart_rendering().stroke
    return adaptive_stroke(px_per_point, clamp.min_width, clamp.max_width)


def density_adaptive_stroke(
    channels: dict[str, ResolvedStyleChannel],
    dataset: ChartDataset,
    x_field: str,
    width: float,
    multiples: MultiplesConfig | None,
    has_mirror: bool,
) -> float:
    """Compute the density-adaptive stroke for one chart family (line or area).

    Args:
        channels: Resolved channel bindings for the chart (from
            ``_channels_for``).
        dataset: The chart's panel-split rows (one panel, keyed ``()``, for a
            non-faceted chart — the N=1 case).
        x_field: The x-axis field name (caller guards ``x is not None``).
        width: Full card pixel width.
        multiples: Small-multiples config, or ``None`` for non-faceted charts.
        has_mirror: Whether the measure axis is mirrored (from ``ay.mirror``).

    Returns:
        Adaptive stroke in pixels (> 0), or 0.0 when not applicable (no data,
        zero-width panel, or zero distinct x-values).
    """
    px_per_point = resolve_px_per_point(
        channels, dataset, x_field, width, multiples, has_mirror
    )
    return stroke_from_px_per_point(px_per_point)


@overload
def bake_line_stroke(mark: LineMarkStyle, adaptive: float) -> LineMarkStyle: ...


@overload
def bake_line_stroke(mark: AreaLineStyle, adaptive: float) -> AreaLineStyle: ...


def bake_line_stroke(
    mark: LineMarkStyle | AreaLineStyle,
    adaptive: float,
) -> LineMarkStyle | AreaLineStyle:
    """Bake an adaptive stroke width into a line-like mark if eligible.

    Returns the mark unchanged when any of the following are true:
    - ``adaptive <= 0`` — no adaptive value computed (no data, no x field, etc.)
    - ``mark.stroke is None`` — no stroke slot to update
    - ``mark.stroke.width is not None`` — some tier of the cascade already
      pinned a width (including the ``0.0`` zero-sentinel, "no stroke");
      a pin always wins outright and must never be resurrected or
      overwritten by the adaptive pass. This is the single place that
      decision is made — callers pass the already-merged mark, so a width
      inherited from any tier (theme, board, chart-local, or a layer's own
      patch) is treated identically; a caller must not re-derive its own
      "is this pinned" check before calling in.

    Otherwise returns a model_copy with ``stroke.width`` replaced by
    ``adaptive``.

    Args:
        mark: The pre-resolved line (or area line) mark (a frozen model).
        adaptive: The adaptive stroke value from ``density_adaptive_stroke``.

    Returns:
        A ``model_copy`` with the baked width, or the original mark unchanged
        when any of the conditions above hold.
    """
    if adaptive <= 0 or mark.stroke is None or mark.stroke.width is not None:
        return mark
    return mark.model_copy(
        update={"stroke": mark.stroke.model_copy(update={"width": adaptive})}
    )


def bake_point_companions(
    point_mark: _PointMarkT,
    stroke: float,
    px_per_point: float,
    size_authored: bool,
    ring_authored: bool,
) -> _PointMarkT:
    """Derive a line's point-marker geometry from its baked stroke and density.

    Lives next to ``bake_line_stroke`` because the two are one decision: once
    the stroke is set, the point ring and disk track it so the dot-to-line
    proportion holds across densities. Callers pass ``stroke`` = the effective
    (post-bake) line stroke and ``px_per_point`` = the same density signal
    ``resolve_px_per_point`` computes (the shared input both
    ``density_adaptive_stroke`` and a family resolver's own
    ``stroke_from_px_per_point`` call turn into a stroke width), which is
    ``0.0`` when there was no density to measure — a line overlay on a
    non-line base, or a line base with no x channel, no rows, or no width.
    Call whenever there is a real stroke to track; the density signal gates
    the ``size`` half below, not the call.

    - Ring ``stroke_width`` follows the line stroke unless some tier authored
      ``marks.point.stroke_width``. Unconditional: a line overlay on a bar,
      scatter or area base has no density signal at all, and its ring must
      still read with the weight of the line it rings rather than falling
      through to Vega-Lite's unthemeable 2px.
    - ``size`` is density-driven, not a themed literal: it is the square of
      ``chart_rendering.point.diameter_ratio * stroke`` — VL ``size`` is area,
      but the diameter-to-size relationship here is ``diameter = sqrt(size)``,
      measured directly off the rendered mark path radius (the textbook
      ``π·r²`` reading is wrong for this mark and gives the wrong dot) — while
      ``px_per_point`` clears ``chart_rendering.point.min_px_per_point``, and
      ``0.0`` (points off) below it. Skipped entirely when there is no density
      signal (``px_per_point <= 0``): ``0.0`` is the resolved model's word for
      "measured, and too dense to show dots", which is not what "never
      measured" means. Leave the cascaded value standing rather than assert a
      density that was never taken.

    Both gates read the *cascaded* mark, so an authoring tier is an authoring
    tier — board, family, chart or layer alike. That read is only honest while
    the parent style carries no theme literal for either companion: today the
    only parent handed here is ``charts.line``, which carries neither.
    ``charts.area``/``scatter``/``point_map`` do set ``point.stroke_width``,
    so a future call site parented on one of those must not reuse this gate
    unchanged.

    Returns the mark unchanged when both companions are authored.
    """
    if not ring_authored:
        point_mark = point_mark.model_copy(update={"stroke_width": stroke})
    if not size_authored and px_per_point > 0:
        point_cfg = get_chart_rendering().point
        auto_size = (
            (point_cfg.diameter_ratio * stroke) ** 2
            if px_per_point >= point_cfg.min_px_per_point
            else 0.0
        )
        point_mark = point_mark.model_copy(update={"size": auto_size})
    return point_mark
