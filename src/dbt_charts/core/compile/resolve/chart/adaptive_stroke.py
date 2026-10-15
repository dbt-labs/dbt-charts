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
from typing import TYPE_CHECKING, Any, TypeVar

from dbt_charts.core.compile.resolve.chart._chart_rows import PanelRows, reduce_panels
from dbt_charts.core.constants.estimates import FACET_CHROME_PX, FACET_MIRROR_AXIS_PX
from dbt_charts.core.constants.marks import (
    POINT_DIAMETER_RATIO,
    POINT_MIN_PX_PER_POINT,
    STROKE_MAX_WIDTH,
    STROKE_MIN_WIDTH,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.authored._base import MultiplesConfig
    from dbt_charts.core.compile.models.chart.resolved._channel import (
        ResolvedStyleChannel,
    )
    from dbt_charts.core.compile.models.chart.resolved._partition import PartitionAxis
    from dbt_charts.core.compile.models.style.theme.marks import PointMarkStyle
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
    the ``STROKE_MIN_WIDTH``/``STROKE_MAX_WIDTH`` clamp bounds. They
    are calibration constants (fit against the density lab) — retune by
    re-running that calibration, not casually.

    Args:
        px_per_point: Chart pixel width divided by the number of distinct
            x-values in the densest rendered series.  Must be positive.
        min_w: Clamp floor in pixels (``STROKE_MIN_WIDTH``).
        max_w: Clamp ceiling in pixels (``STROKE_MAX_WIDTH``).

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
        floored to ``FACET_MIN_PANEL_PX``. The card boundary always wins — a panel
        count that would otherwise need more than the card's width shrinks
        below the legibility floor instead of pushing painted content past
        the card's edge (``render/warnings/facet_panel_width_below_minimum.py``
        warns the author when that happens).
    """
    chrome = FACET_CHROME_PX + (FACET_MIRROR_AXIS_PX if has_mirror else 0.0)
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

    The one place that reads the stroke clamp constants and calls
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
    return adaptive_stroke(px_per_point, STROKE_MIN_WIDTH, STROKE_MAX_WIDTH)


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


def bake_point_companions(
    point_mark: _PointMarkT,
    stroke: float,
    px_per_point: float,
) -> _PointMarkT:
    """Derive a line's point-marker ring and size from its resolved stroke.

    ``stroke`` is the effective line stroke and ``px_per_point`` the density
    signal from ``resolve_px_per_point`` (``0.0`` when none was measured: a
    line overlay on a non-line base, or a line base with no x channel, no
    rows, or no width). A companion any cascade tier already set on
    ``point_mark`` is left alone. Both gates read the cascaded mark, which is
    honest only while the parent style carries no theme literal for either
    companion: ``charts.line`` carries neither, but ``charts.area``/
    ``scatter``/``point_map`` set ``point.stroke_width``, so a call site
    parented on one of those must not reuse this unchanged.

    - Ring ``stroke_width`` follows the stroke, with or without a density
      signal: a line overlay on a bar, scatter or area base has none, and its
      ring must still read with the weight of the line it rings rather than
      falling through to Vega-Lite's unthemeable 2px.
    - ``size`` is ``(POINT_DIAMETER_RATIO * stroke) ** 2`` (VL ``size`` is
      area, but the rendered diameter is ``sqrt(size)``, not the textbook
      ``pi * r**2`` reading) once ``px_per_point`` clears
      ``POINT_MIN_PX_PER_POINT``, and ``0.0`` (points off) below it. Without a
      density signal the cascaded size stands: ``0.0`` means "measured, and too
      dense to show dots", which is not "never measured".
    """
    if point_mark.stroke_width is None:
        point_mark = point_mark.model_copy(update={"stroke_width": stroke})
    if point_mark.size is None and px_per_point > 0:
        auto_size = (
            (POINT_DIAMETER_RATIO * stroke) ** 2
            if px_per_point >= POINT_MIN_PX_PER_POINT
            else 0.0
        )
        point_mark = point_mark.model_copy(update={"size": auto_size})
    return point_mark
