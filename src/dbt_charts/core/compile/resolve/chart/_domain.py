"""Y-domain baking and tick resolution shared across cartesian chart families."""

from __future__ import annotations

import contextlib
import math
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.authored._layer import BarChartBarLayer
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.style.theme import (
    AxisYStyle,
    BaseScaleStyle,
    ScaleContinuousStyle,
    _CartesianChartStyle,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    CartesianChart,
    ChartDataset,
    ChartRows,
    LayerDatasets,
    fold_panels,
    restamp,
)
from dbt_charts.core.compile.resolve.chart.enrich import (
    classify_column_type,
    first_non_null_samples,
)
from dbt_charts.core.compile.resolve.chart.tick_values import (
    apply_headroom,
    numeric_domain_bounds,
    stacked_totals_max,
    zero_anchor_domain_floor,
    zero_anchor_floor,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LOG_SCALE_REQUIRES_POSITIVE_DATA,
    ERR_TICKS_COUNT_REQUIRES_NON_LOG_SCALE,
)
from dbt_charts.core.numeric import nice_tick_values
from dbt_charts.core.utils import numeric_column_values

__all__ = [
    "_CartesianTickResolution",
    "_authored_axis_y_ticks_count",
    "_axis_headroom",
    "_bake_normalize_domain",
    "_bake_y_zero",
    "_bake_zero_flag",
    "_first_non_numeric_y",
    "_numeric_y_values",
    "_reject_non_positive_log_scale_data",
    "_resolve_cartesian_ticks",
    "_resolve_stacked_bar_ticks",
    "_shared_y_values",
    "_y_gridline_caps_bottom",
    "_zero_anchor_floats",
    "resolve_y_zero",
]


def resolve_y_zero(
    ay: AxisYStyle,
    min_val: float | None,
    max_val: float | None,
    chart_type: str,
) -> bool | None:
    """Canonical measure-axis zero decision for optional-zero chart families.

    An author-pinned ``axis_y.scale.continuous.zero`` bool wins — read off the
    cascaded axis ``ay``, so a pin authored at any level (board, chart,
    family) is honored identically; otherwise the smart-zero heuristic
    (``compile.enrich._pick_scale``) runs on the (min_val, max_val) extent.
    Returns None when neither the pin nor the heuristic has an opinion (no
    extent, or the data spans or touches zero — on either side, since the
    heuristic mirrors its branches for all-negative data).

    The single source of this decision — consumed by ``_bake_y_zero`` for
    filtered rows and by the family resolvers for a cross-filter stable extent.
    One canonical mapper keeps both paths aligned when anchor rules change.
    """
    axis_y_scale = ay.scale
    cont = axis_y_scale.continuous if axis_y_scale is not None else None
    if cont is not None and isinstance(cont.zero, bool):
        return cont.zero

    if min_val is None or max_val is None:
        return None
    from dbt_charts.core.compile.resolve.chart.enrich import ColumnProfile, _pick_scale

    profile = ColumnProfile(min_val=min_val, max_val=max_val)
    picked = _pick_scale(profile, chart_type=chart_type)
    if not picked or "zero" not in picked:
        return None
    return bool(picked["zero"])


def _bake_zero_flag(ay: AxisYStyle, zero: bool) -> AxisYStyle:
    """Bake an explicit zero-anchor bool onto ``ay.scale.continuous.zero``.

    Callers must have already excluded a log-typed axis themselves — see
    ``_bake_y_zero``'s docstring for why baking ``zero`` onto one is invalid.
    """
    _existing_cont = ay.scale.continuous if ay.scale is not None else None
    new_cont = (
        _existing_cont.model_copy(update={"zero": zero})
        if _existing_cont is not None
        else ScaleContinuousStyle(zero=zero)
    )
    baked_scale = (
        ay.scale.model_copy(update={"continuous": new_cont})
        if ay.scale is not None
        else BaseScaleStyle(continuous=new_cont)
    )
    return ay.model_copy(update={"scale": baked_scale})


def _bake_y_zero(
    ay: AxisYStyle,
    y_values: list[float],
    chart_type: str,
) -> AxisYStyle:
    """Bake the measure-axis zero decision onto a working axis scale.

    Thin wrapper over ``resolve_y_zero`` (the canonical decision) that bakes a
    concrete outcome onto the cascaded axis; None (no opinion) leaves ``ay``
    unchanged. The caller constructs the resolved axis after every scale and
    tick decision is complete.

    Skipped entirely for a log-typed axis: ``ScaleContinuousStyle``'s own
    ``type: log`` + ``zero: true`` validator only fires on construction, not
    on the ``model_copy`` ``_bake_zero_flag`` uses — baking ``zero=True`` here
    would silently produce an internally-inconsistent, unvalidated
    ``ScaleContinuousStyle(type="log", zero=True)`` a log domain cannot
    represent. ``ay.scale.continuous.zero`` already carries whatever the
    author explicitly pinned (or None); this only skips the heuristic's own
    default.
    """
    _existing_cont = ay.scale.continuous if ay.scale is not None else None
    if _existing_cont is not None and _existing_cont.type == "log":
        return ay
    zero = resolve_y_zero(
        ay,
        min(y_values) if y_values else None,
        max(y_values) if y_values else None,
        chart_type,
    )
    if zero is None:
        return ay
    return _bake_zero_flag(ay, zero)


def _reject_non_positive_log_scale_data(
    chart_id: str,
    ay: AxisYStyle,
    y_fields: list[str],
    data: list[dict[str, Any]],
) -> None:
    """Raise when a log measure axis's data contains a value ``<= 0``.

    A log domain is undefined at and below zero. Without this check, a zero
    or negative data point silently degenerates the render instead of
    erroring: Vega-Lite's own automatic domain inference degenerates on
    non-positive data for line/scatter (no baked domain), and the area
    domain-bake (below) would compute a ``<= 0`` lower bound directly from
    the data — exactly the failure mode the authored-``zero``/no-domain
    validators guard against, but not yet for the data's own content.
    """
    _cont = ay.scale.continuous if ay.scale is not None else None
    if _cont is None or _cont.type != "log":
        return
    for field in y_fields:
        if any(v <= 0 for v in numeric_column_values(data, field)):
            raise CompilationError.from_code(
                ERR_LOG_SCALE_REQUIRES_POSITIVE_DATA,
                chart_id=chart_id,
                y_field=field,
            )


def _authored_axis_y_ticks_count(
    chart_style: _CartesianChartStyle | None,
) -> int | None:
    """The chart's OWN authored ``axis_y.ticks.count`` — pre-cascade, not the
    theme-defaulted resolved value.

    The theme always resolves ``ay.ticks.count`` to a concrete default (e.g.
    6 nice ticks) for every quantitative axis, so the resolved
    ``ResolvedAxisStyle.ticks.count`` is essentially never ``None`` in
    practice. Only a count the *chart itself* deliberately set is a real
    authoring conflict with ``type: log`` — the theme's blanket tick-count
    default is not a per-chart decision about this axis and must not trip it.
    """
    if chart_style is None or chart_style.axis_y is None:
        return None
    # AxisStylePatch.ticks: its TYPE_CHECKING stub inherits AxisYStyle's
    # non-Optional `ticks: AxisTicksStyle = Field(default_factory=...)`, but
    # build_patch_model_ext genuinely makes this field Optional at runtime —
    # getattr is the correct escape hatch for this Patch-vs-compiled-type
    # stub mismatch (mypy would otherwise flag a real `is None` check as
    # unreachable), not a defensive read of a guaranteed field.
    ticks = getattr(chart_style.axis_y, "ticks", None)
    return ticks.count if ticks is not None else None


def _axis_headroom(ay: AxisYStyle) -> float | None:
    """Read the authored (or theme-default) headroom off a cascaded axis scale."""
    return ay.scale.headroom if ay.scale is not None else None


def _numeric_y_values(data: ChartRows, y_fields: tuple[str, ...]) -> list[float]:
    """Extract finite numeric values of every column in ``y_fields`` from data rows.

    Accepts int, float, Decimal, and string representations of numbers (CSV-ingested
    data commonly arrives as strings). Skips bools, None, and non-numeric strings.
    """
    result: list[float] = []
    for row in data:
        for y_field in y_fields:
            if y_field not in row:
                continue
            v = row[y_field]
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float, Decimal)):
                result.append(float(v))
            elif isinstance(v, str):
                with contextlib.suppress(ValueError, ArithmeticError):
                    result.append(float(v))
    return result


def _zero_anchor_floats(
    normalized_y: str | list[str] | None,
    data: ChartRows,
) -> list[float]:
    """Numeric floats for the zero-anchor heuristic across all y fields.

    Multi-metric (``y: [a, b]``, area/line/scatter's wide-y): floats span
    every measure's own values so the anchor decision reads the real extent
    instead of always-anchored on empty data.
    """
    if isinstance(normalized_y, str):
        return _numeric_y_values(data, (normalized_y,))
    if isinstance(normalized_y, list):
        return _numeric_y_values(data, tuple(normalized_y))
    return []


def _shared_y_values(
    data: ChartRows,
    y_field: str,
    chart: CartesianChart,
    datasets: LayerDatasets,
) -> list[float]:
    """The numeric values drawn against the chart's primary y scale.

    A shared scale has to span every series drawn on it, or the overlay is drawn
    outside the plot: pin the domain to the base series alone and a larger goal
    line floats above the axis top — and once the base max is small enough
    relative to it, autosize-fit collapses the plot height to zero and the chart
    renders as title-plus-axis only.

    A layer pinned to ``axis_y.position: right`` has an independent scale, so it
    must not widen this one. Layers sharing the base query read from ``data``;
    own-query layers read from ``datasets`` when the caller can supply it.
    """
    base_fields: tuple[str, ...] = (y_field,)
    if isinstance(chart, BarChart) and chart.y_start is not None:
        base_fields = (y_field, chart.y_start)
    values = _numeric_y_values(data, base_fields)
    seen = {(chart.query_name, field) for field in base_fields}
    for layer in chart.layers:
        if layer.y is None or (
            layer.axis_y is not None and layer.axis_y.position == "right"
        ):
            continue
        query_name = layer.query
        fields = tuple(
            field
            for field in (
                layer.y,
                layer.y_start if isinstance(layer, BarChartBarLayer) else None,
            )
            if field is not None and (query_name, field) not in seen
        )
        if not fields:
            continue
        seen.update((query_name, field) for field in fields)
        if query_name is None or query_name == chart.query_name:
            rows = data
        else:
            if datasets is ...:
                continue
            if query_name not in datasets:
                raise ChartDataError(
                    f"Missing rows for shared-scale layer query {query_name!r}"
                )
            rows = datasets[query_name]
        values.extend(_numeric_y_values(rows, fields))
    return values


class _CartesianTickResolution(NamedTuple):
    """Nice tick ladder + exact (non-nice-rounded) measure-axis domain bounds.

    ``domain_max`` / ``domain_min`` are None when VL should auto-fit that edge:
    authored ``scale.domain`` is in effect, no data available, or headroom is 0.
    On a zero-anchored axis only ``domain_max`` is set and the bottom stays
    at 0 — except with all-negative data, where 0 is the ceiling instead and
    ``domain_min`` carries the headroom-expanded data floor.
    On zoomed axes both are set via symmetric span-relative headroom.
    """

    ticks: tuple[float, ...]
    domain_max: float | None
    domain_min: float | None = None


def _authored_tick_ladder(ay: AxisYStyle) -> tuple[float, ...] | None:
    """The authored ``axis_y.scale.values`` ladder, as floats — or ``None``
    when unauthored (the caller falls through to ``nice_tick_values``).

    ``axis_to_vl`` emits this list verbatim as VL's ``values``, and
    ``bake_tick_ladder`` — which every emit path now routes through — only
    bakes a ladder when the axis carries no ``values`` already, so an
    authored list always wins over whatever ``nice_tick_values`` would
    otherwise pick. Baking THIS list —
    not a separately-computed ladder — as the axis's ``tick_values`` is what
    keeps ``quantitative_tick_labels``'s gutter measurement matched to what
    Vega actually renders: the gutter must be sized from the exact same tick
    bodies Vega paints, so the formula-based labelExpr and the Python-side
    measurement can never diverge.
    """
    if ay.scale is None or ay.scale.values is None:
        return None
    return tuple(float(v) for v in ay.scale.values)


def _resolve_cartesian_ticks(
    chart_id: str,
    ay: AxisYStyle,
    y_floats: list[float],
    zero_anchor: bool,
    authored_ticks_count: int | None,
    scale: Literal["shared", "independent"],
) -> _CartesianTickResolution:
    """Compute the nice tick ladder + domain bounds for a cartesian y-axis (non-stacked).

    Used by bar (non-stacked), line, area, and scatter.  For stacked bars use
    ``_resolve_stacked_bar_ticks`` which substitutes the stacked domain max.

    ``y_floats`` contains every series drawn against this scale. The bounds span
    all of them, because a series drawn outside the domain is outside the plot.

    A log measure axis always delegates tick placement to Vega-Lite (log
    decades), regardless of the theme's tick-count default — unless the
    chart itself explicitly authored ``ticks.count``, which is a genuine
    conflict (a target count is meaningless on a log axis) and raises.

    Two headroom formulas depending on zero-anchor:
    - Zero-anchored: multiplicative on the far edge, with 0 as the near one.
      All-positive — domain_max = data_max * (1+h), bottom stays at 0 and
      domain_min stays None. All-negative mirrors it — domain_min =
      data_min * (1+h), top stays at 0 (pinned by `zero: True`) and
      domain_max stays None.
    - Zoomed (not zero-anchored): symmetric span-relative —
      domain_max = data_max + h * span; domain_min = data_min - h * span.

    Returns empty ticks + None bounds when ``ticks.count`` is unset or no data
    — unless ``axis_y.scale.values`` is authored, which bypasses both checks
    (see ``_authored_tick_ladder``): an explicit ladder needs no ``ticks.count``
    to be known exactly, and headroom/domain bounds stay None (VL auto-fits
    the scale range; an explicit tick list says nothing about it).

    ``scale`` mirrors ``_resolve_stacked_bar_ticks``'s own parameter: an
    authored ``axis_y.scale.continuous.domain`` wins regardless of it (explicit
    author intent, not the auto-derived ladder ``multiples: {scale:
    independent}`` suppresses); absent that, ``independent`` bakes no ladder
    and no domain bound at all — each small-multiples panel gets Vega-Lite's
    own per-panel scale instead of one shared union-of-panels ladder.
    ``y_floats`` itself stays whole-dataset even under ``independent`` (a
    column-wise union read is identical whether the chart is faceted or
    not — only an aggregate needs one panel's rows); what changes is only
    whether the result gets baked.
    """
    _cont = ay.scale.continuous if ay.scale is not None else None
    if _cont is not None and _cont.type == "log":
        if authored_ticks_count is not None:
            raise CompilationError.from_code(
                ERR_TICKS_COUNT_REQUIRES_NON_LOG_SCALE,
                chart_id=chart_id,
            )
        return _CartesianTickResolution((), None)
    # A zero-anchored axis whose data never reaches zero pins 0 as its
    # CEILING, so it has to carry its own floor out of every exit below.
    # Without one, `zero_anchor_floor` falls back to the literal 0.0 — a legal
    # floor only while the data is non-negative — and render pins 0 as the
    # BOTTOM of an all-negative domain, collapsing every mark onto one pixel
    # row. Mirrors the positive branch's headroom-expanded top.
    # `independent` is excluded at the computation, not at one exit: the
    # authored-ladder and `ticks.count is None` exits both return before the
    # `scale` check below, and a floor escaping through either becomes one
    # `domainMin` on the shared encoding while `resolve.scale` says every panel
    # owns its own.
    negative_floor: float | None = None
    if scale != "independent" and zero_anchor and y_floats and max(y_floats) < 0:
        _raw_min = min(y_floats)
        _h = _axis_headroom(ay)
        negative_floor = _raw_min * (1.0 + _h) if _h else _raw_min
    authored_ladder = _authored_tick_ladder(ay)
    if authored_ladder is not None:
        return _CartesianTickResolution(authored_ladder, None, negative_floor)
    if ay.ticks.count is None or not y_floats:
        return _CartesianTickResolution((), None, negative_floor)
    authored = numeric_domain_bounds(_cont.domain if _cont is not None else None)
    domain_max: float | None = None
    domain_min: float | None = None
    if authored is not None:
        tick_min, tick_max = authored
    elif scale == "independent":
        # No chart-wide floor: `independent` gives each panel its own scale,
        # and one pinned domainMin would apply to all of them. `negative_floor`
        # is already None here — see its own guard above.
        #
        # Area additionally suppresses its own zero-anchor bake for this
        # combination (`area.py`); bar and line do not (`bar_zero` never
        # consults `multiples_scale`, and every family honors an authored
        # `zero: true` regardless). All of them can therefore reach render
        # with `zero_anchor` true and no ladder, which is safe because no
        # site pins a floor without a rung: they share that decision through
        # `zero_anchor_pinned_floor`. `y_zero_scale` and bar's vertical branch
        # additionally set `nice: False` in the floor's place; bar's
        # horizontal branch deliberately does not, and says why.
        return _CartesianTickResolution((), None)
    elif zero_anchor:
        # Zero-anchored: the ladder runs between 0 and the headroom-expanded
        # far edge. Which side 0 sits on follows the data's sign, and the band
        # between the data and 0 is plot area either way, so its rungs have to
        # be labeled — an all-negative axis renders up to 0 (`zero: True`) and
        # a ladder built from the data extent alone left that band blank.
        # The far edge is the headroom side and needs no rung of its own.
        raw_min = min(y_floats)
        raw_max = max(y_floats)
        h = _axis_headroom(ay)
        tick_min = min(0.0, raw_min)
        tick_max = max(0.0, apply_headroom(raw_max, h))
        if raw_max > 0:
            # Bake domainMax only when headroom expanded past the data max.
            # headroom=0 leaves it None — VL auto-fits.
            if tick_max > raw_max:
                domain_max = tick_max
        else:
            # All-negative: the anchored edge is the ladder's TOP (0, pinned by
            # `zero: True`), so the floor can no longer come from its bottom
            # rung the way it does for positive data, where that rung IS 0.
            # Rounding a ladder out to cover [data, 0] drops its bottom rung
            # well below the data (-150 for data reaching -103), and
            # zero_anchor_floor would pin the domain there, wasting a third of
            # the plot. Bake the mirror of the positive top instead: the data
            # floor times (1 + headroom), exact, which apply_domain_headroom_bounds
            # then lays over the ladder-derived domainMin on both sides.
            domain_min = negative_floor
    else:
        # Zoomed (not zero-anchored): symmetric span-relative headroom.
        data_min = min(y_floats)
        raw_max = max(y_floats)
        h = _axis_headroom(ay)
        span = raw_max - data_min
        if h and span > 0:
            domain_max = raw_max + h * span
            domain_min = data_min - h * span
            tick_min = domain_min
            tick_max = domain_max
        else:
            tick_min, tick_max = data_min, raw_max
    ticks = tuple(nice_tick_values(tick_min, tick_max, ay.ticks.count))
    return _CartesianTickResolution(ticks, domain_max, domain_min)


def _y_domain_floor(
    ay: AxisYStyle,
    ticks: _CartesianTickResolution,
    zero_anchor: bool,
    endpoint_rail_may_discard_domain: bool,
) -> float | None:
    """The exact data value the y-axis's plot-bottom edge renders, when knowable.

    Reads only facts already baked by ``_resolve_cartesian_ticks``/the axis
    cascade -- never a prediction of where Vega-Lite's own auto-fit will
    land, with one narrow exception noted in case 4 below. In priority
    order:

    1. An authored fixed domain: its own low bound, taken *literally* --
       ``authored[0]``, not ``min(authored)``. This deliberately does not
       match render's ``authored_measure_domain``/``effective_measure_domain``,
       which normalize to ``(lo, hi)`` for a different question ("what range
       does this axis cover", used for a parity check) -- not "which value
       renders at the bottom pixel". A reversed ``domain: [100, 0]`` renders
       100 at the bottom edge; collapsing the two questions into one call
       would answer this one wrong.
    2. A baked ``domain_min`` (a zoomed axis with headroom > 0) -- exact,
       never nice-rounded.
    3. On a zero-anchored axis: ``zero_anchor_floor`` of the ladder filtered
       through ``zero_anchor_domain_floor`` -- byte for byte the same
       formula ``y_zero_scale`` bakes onto the emitted VL scale's
       ``domainMin``, so this branch always resolves. An authored
       ``scale.values`` ladder is filtered out (it says nothing about the
       domain) and falls back to the literal ``0.0``, exactly like render
       does -- it is NOT sourced from ``min(ticks.ticks)`` unfiltered, which
       is the bug this replaces (an authored ``[25, 50, 75, 100]``
       ladder used to compare 25 against itself and always answer "caps").
    4. Otherwise (zoomed, no authored/baked bound, not zero-anchored -- a
       ``headroom: 0`` axis with real data): the ladder's own lowest rung,
       ``ticks.ticks[0]``. This one case IS a prediction rather than a
       read-back: it assumes Vega-Lite's own "nice" auto-fit (unbaked here)
       lands on the same floor our ``nice_tick_values`` already computed
       from the identical data extent -- verified by rendering this exact
       shape (scatter, headroom 0) and reading the emitted grid/tick pixel
       positions: the lowest gridline and the plot's bottom edge coincide.
       An *authored* ladder never reaches this branch un-zero-anchored
       either (same ``zero_anchor_domain_floor`` filter): its rungs have no
       relation to the data extent VL will auto-fit, so that combination
       falls through to the ``None`` below instead of predicting from it.

    ``zero_anchor`` is the SAME bool the caller already passed into
    ``_resolve_cartesian_ticks`` -- not re-derived from
    ``ay.scale.continuous.zero``, which stays unbaked (None) for a
    single-metric line/area/bar: those anchor at render time via
    ``BaselineFeature``'s ``datum: 0`` rule, not a resolve-time bake, so the
    scale field alone under-detects the zero-anchored case.

    Returns None only when nothing pins the floor at all: no tick ladder
    was baked (a log measure axis, a theme that never sets
    ``axis_quantitative.ticks.count`` such as stark/plain, ``scale:
    independent`` small multiples, or no data), or a zoomed axis with an
    authored (never data-related) ladder and no authored/baked bound.
    ``_y_gridline_caps_bottom`` treats this as the undecidable case.

    ``endpoint_rail_may_discard_domain`` skips case 2 (the baked
    ``domain_min``) even when it is set. A multi-series (color or wide)
    area/line with ``endpoint_labels.visible`` and no real stack renders its
    label pane as a second, unscaled view sharing the y-scale
    (``resolve.scale.y: shared``) -- Vega-Lite's shared-scale merge then
    silently drops the main pane's ``domainMin``/``domainMax`` pair and
    falls back to its own auto-fit of the raw data instead, which in
    practice lands on the same rounded extremes our own ladder already
    computed (case 4) -- confirmed by rendering this exact composition
    through ``vl_convert`` and reading the emitted gridline pixel positions:
    they span the ladder's own (10000, 50000) extremes exactly, not the
    headroom-adjusted (14920, 45080) bake. The caller (``area.py``) is the
    only one that can know whether this composition applies -- it already
    resolves ``endpoint_labels.visible`` and the stack mode before calling
    here.
    """
    if not ticks.ticks:
        return None
    cont = ay.scale.continuous if ay.scale is not None else None
    authored_domain = numeric_domain_bounds(cont.domain if cont is not None else None)
    if authored_domain is not None:
        return authored_domain[0]
    if ticks.domain_min is not None and not endpoint_rail_may_discard_domain:
        return ticks.domain_min
    scale_values = ay.scale.values if ay.scale is not None else None
    floor_ticks = zero_anchor_domain_floor(scale_values, list(ticks.ticks))
    if zero_anchor:
        return zero_anchor_floor(floor_ticks)
    return floor_ticks[0] if floor_ticks else None


def _y_gridline_caps_bottom(
    ay: AxisYStyle,
    ticks: _CartesianTickResolution,
    zero_anchor: bool,
    endpoint_rail_may_discard_domain: bool,
) -> bool:
    """Whether the y-axis's lowest rendered gridline lands on the plot's
    bottom edge -- the case where an x-axis tick stub bridges a real gap
    (see the ``axis_x.ticks.visible: "auto"`` rule in the base theme).

    False when the y-axis draws no gridlines at all (nothing to cap the
    x-gridlines with). **True** -- not False -- when the domain floor can't
    be determined at compile time at all (see ``_y_domain_floor``'s ``None``
    case: a log measure axis, a theme with no ``axis_quantitative.ticks.count``,
    ``scale: independent``, or an authored zoomed ladder unrelated to the
    data). With no gridline position knowable, hiding the stub risks
    deleting a mark the reader needs to place the axis at all; showing it
    unnecessarily only draws a mark they can ignore -- the conservative
    default is to show it. An earlier revision answered False here by
    treating "no ladder baked" as if it meant "gridlines already reach the
    labels" -- confirmed wrong by rendering a log-scale axis and a
    ``stark``/``plain``-themed chart, where Vega-Lite draws gridlines with
    no ladder baked on our side at all.

    Otherwise: an exact/near-exact match between the ladder's lowest rung
    and the floor (a span-relative tolerance, not an absolute one -- a
    ppm-scale measure column's whole domain span can be smaller than a fixed
    epsilon, which would call every such axis "close" regardless of the
    real gap).
    """
    if ay.grid.visible is not True:
        return False
    floor = _y_domain_floor(ay, ticks, zero_anchor, endpoint_rail_may_discard_domain)
    if floor is None:
        return True
    return math.isclose(ticks.ticks[0], floor, rel_tol=1e-9, abs_tol=1e-9)


def _resolve_stacked_bar_ticks(
    ay: AxisYStyle,
    dataset: ChartDataset,
    y_fields: list[str],
    cat_field: str,
    scale: Literal["shared", "independent"],
) -> tuple[float, ...]:
    """Compute nice tick values for a stacked bar using the stacked domain max.

    Stacked bars always zero-anchor unless an authored domain overrides both
    bounds.  The per-category positive stacked total (with headroom applied)
    replaces the raw data max so the tick ladder spans the full stacked
    height.  The emitter reads the separately-baked ``stacked_domain_max``
    (also headroom-applied — see ``_resolve_bar``) for VL ``domainMax``, not
    ``max(ay.tick_values)`` — a nice-rounded ladder rung is not the exact
    headroom bound.

    Never reached with a log measure axis — every caller must reject log+stack
    upstream. Bar rejects it in ``_resolve_bar_chart`` before stack resolution
    runs; area rejects it via ``ERR_AREA_STACKED_LOG_SCALE_NOT_SUPPORTED``.

    An authored ``axis_y.scale.values`` bypasses the stacked-total ladder
    entirely and returns that list verbatim — see ``_authored_tick_ladder``.
    An authored ``axis_y.scale.continuous.domain`` likewise wins regardless of
    ``scale`` — it is explicit author intent, not the auto-derived ladder
    ``multiples: {scale: independent}`` suppresses. Absent either override,
    ``independent`` bakes no ladder at all: each panel gets its own,
    Vega-Lite-computed scale.
    """
    authored_ladder = _authored_tick_ladder(ay)
    if authored_ladder is not None:
        return authored_ladder
    if ay.ticks.count is None:
        return ()
    data = dataset.all_rows()
    if not data:
        return ()
    y_floats = [
        value for y_field in y_fields for value in numeric_column_values(data, y_field)
    ]
    if not y_floats:
        return ()
    _cont = ay.scale.continuous if ay.scale is not None else None
    authored = numeric_domain_bounds(_cont.domain if _cont is not None else None)
    if authored is not None:
        tick_min, tick_max = authored
    elif scale == "independent":
        return ()
    else:
        # cat_field can itself be the multiples field — partition() strips a
        # panel's own partition column from its rows, so restamp it back
        # before grouping, or every row's cat_field reads as absent and the
        # stacked total silently folds to None (nothing to sum).
        sm = fold_panels(
            restamp(dataset, cat_field),
            scale,
            lambda rows: stacked_totals_max(rows, cat_field, y_fields),
        )
        raw_max = sm if sm is not None else max(y_floats)
        tick_max = apply_headroom(raw_max, _axis_headroom(ay))
        tick_min = min(0.0, min(y_floats))  # stacked bars always zero-anchor
    return tuple(nice_tick_values(tick_min, tick_max, ay.ticks.count))


def _first_non_numeric_y(
    y: str | list[str] | None,
    data: list[dict[str, Any]],
) -> str | None:
    """Return the first y field that has samples but is non-numeric, else None.

    Shared by bar and line — both hardcode y to "quantitative" in the axis
    cascade, so a categorical y silently bakes a NaN axis. This helper
    surfaces the offending field so the caller can raise the right DF code.
    Uses classify_column_type (not the stricter _classify_to_channel_type)
    so numeric-looking strings from untyped sources (CSV, Decimal-as-str)
    still count as numeric — only genuinely categorical values trip the guard.
    """
    fields: list[str] = (
        [y] if isinstance(y, str) else list(y) if isinstance(y, list) else []
    )
    for field in fields:
        samples = first_non_null_samples(field, data)
        if samples and classify_column_type(field, samples) != "numeric":
            return field
    return None


def _bake_normalize_domain(ay_merged: AxisYStyle) -> AxisYStyle:
    """Bake [0, 1] domain onto ay_merged for normalize-stack axes.

    measure_axis_to_vl estimates label widths from the resolved domain; without
    this bake it reads raw query values (e.g. 0..1000) and computes an
    over-wide label pad. Only bakes when the author has not already set a domain.
    """
    ay_sc = ay_merged.scale
    ay_cont = ay_sc.continuous if ay_sc is not None else None
    if ay_cont is not None and ay_cont.domain is not None:
        return ay_merged
    norm_cont = (
        ay_cont.model_copy(update={"domain": (0.0, 1.0)})
        if ay_cont is not None
        else ScaleContinuousStyle(domain=(0.0, 1.0))
    )
    norm_scale = (
        ay_sc.model_copy(update={"continuous": norm_cont})
        if ay_sc is not None
        else BaseScaleStyle(continuous=norm_cont)
    )
    return ay_merged.model_copy(update={"scale": norm_scale})
