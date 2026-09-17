"""Detector: WARN_BAR_BAND_WIDTH_TOO_NARROW — see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule, categorical category axis (nominal/ordinal, a genuine band
scale): chart is a ResolvedBarChart AND the emitted spec's categorical
channel type is in {"nominal", "ordinal"} AND the estimated per-bar pixel
width < _MIN_BAND_WIDTH_PX, where per-bar width is the panel's bounding
pixel extent divided by distinct category values, further divided by the
series count when the emitted spec subdivides the band with an offset
channel (see _grouped_series_count below). Orientation changes which VL
channel names carry this, and, for horizontal, where the bounding extent
comes from:

* Vertical: category rides VL `x`, subdivides via `xOffset`, and is bounded
  by the spec's own `width` key (always populated: `renderer.py`'s
  `_collect_render_warnings` always passes a concrete `width` into
  `render_resolved_chart`).
* Horizontal: category rides VL `y` (`chart.x` is still the category
  *field* on both orientations; orientation flips the VL channel, not the
  authored field), subdivides via `yOffset`. The bounding extent is NOT the
  spec's `height` key: `_collect_render_warnings` never passes a `height`
  into `render_resolved_chart` (only `width`), so `unit.get("height")` is
  always absent in production and reading it would make this branch dead
  code. `_horizontal_render_extent` below reads `ctx.layout_chart_heights`
  instead (`ResolvedLayoutItem.height`), divides it by the row-facet
  cardinality, and grows the result to the emitter's own single-bar floor
  (`min_height_for_horizontal_bar_categories`, the same floor
  `vega_lite.py`'s `effective_height` applies). That growth is usually
  redundant -- `layout_chart_heights` is normally already past the floor by
  the time the layout pass records it, because the layout pass's own
  measure step runs the same floor internally -- EXCEPT when a
  layout-wrapper `height:` (a `rows:`/`cols:` item's own authored height,
  not the chart's own) clamps the slot: `sizing.py`'s
  `_clamp_to_authored_ceiling` keeps that authored ceiling even when the
  content resolves taller, so `layout_chart_heights` can genuinely read
  BELOW the floor there. Re-applying the floor here is what keeps this
  detector's verdict matched to what the chart actually paints in that
  clamped case, not what a too-small slot claims.

A horizontal bar's categorical axis is always emitted "nominal"
(`_emit_horizontal` never produces a continuous horizontal-category shape),
so the continuous branch below is reachable only for a vertical bar's
x-axis.

Floor rationale: the theme's default bar border stroke is 1px
(``marks.bar.border.width``) and the fill occupies ``band_width * 0.8``
(``marks.bar.band_width``) of each band. At the floor a bar still shows a
sliver of fill wider than the two borders bracketing it; below it, the
border strokes consume the whole band and the fill reads as a hairline or
vanishes. 4px keeps a visible margin above the ~1px point where the bug
report shows fills already gone.

Detection rule, continuous x (quantitative — no band, see
``continuous_bar_size_prop``): the bar's effective width (authored
``bar.size``, or the computed ``gap``/``min_size``/``max_size`` ladder)
exceeds the minimum pixel gap between adjacent distinct x values — bars will
visually overlap. This is a DIFFERENT failure mode than the categorical
case's readability floor (collision, not unreadable fill — the comparison
direction is inverted: ``bar_width`` crosses ``min_band_width`` from above
here, from below in the categorical case), so it gets its own comparison in
``_detect_continuous_overlap`` — the SAME diagnostic code as the categorical
case, per this repo's inform-don't-intervene policy (a bound ``min_size``
clamp, or an author's own oversized ``bar.size``, is never silently squeezed
to fit, only reported), but its OWN message wording
(``_CONTINUOUS_MESSAGE_TEMPLATE`` below): there is no band scale here at all
(``distinct`` counting "bands" is a categorical-only concept), and the two
branches' numbers don't share a printable shape — ``codes_render.py``'s
shared ``message_template`` prints one rounded number into both the bar
width and the threshold slots, which reads self-contradictory once
``min_band_width`` carries a live measured ``step_px`` instead of the
categorical branch's fixed floor constant.
"""

from __future__ import annotations

from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    raw_wide_series_names,
)
from dbt_charts.core.compile.resolve.chart.adaptive_stroke import (
    panel_axis_cardinality,
)
from dbt_charts.core.diagnostics import WARN_BAR_BAND_WIDTH_TOO_NARROW, Diagnostic
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    min_height_for_horizontal_bar_categories,
    widest_panel_distinct_count,
)
from dbt_charts.core.render.chart.vl_field_maps import effective_bar_size
from dbt_charts.core.render.warnings.base import (
    WarningContext,
    encoding_channel_type,
    facet_channel_is_independent,
)
from dbt_charts.core.utils import coerce_numeric_cell

# Below this per-band pixel width, the bar's fill is unreadable (see rationale above).
_MIN_BAND_WIDTH_PX = 4.0

_CATEGORICAL_TYPES = frozenset({"nominal", "ordinal"})

# Own wording for the continuous-x branch — see the module docstring for why
# this doesn't reuse WARN_BAR_BAND_WIDTH_TOO_NARROW.message_template (the
# categorical branch's "N bands" framing doesn't apply to a scale with no
# band, and printing bar_width/min_band_width through the same rounded shape
# reads self-contradictory once min_band_width carries a live step_px).
_CONTINUOUS_MESSAGE_TEMPLATE = (
    "Chart {chart_id!r} draws {distinct} bars ~{bar_width:.2f}px wide across "
    "{render_width:.0f}px, but the closest two x values are only "
    "~{step_px:.2f}px apart — bars will overlap their neighbors."
)


def _grouped_series_count(
    unit: VLDict, chart: ResolvedBarChart, rows: list[VLDict], offset_channel: str
) -> int:
    """Bars-per-band from the emitter's own offset decision, or 1 if none.

    The bar emitter (``render/chart/emitters/bar.py``) is the single source
    of truth for whether a band is subdivided — it already accounts for
    stack mode, ``overlap: full``, wide-form grouping (``y`` as a measure
    list), and suppressing the offset channel when color is 1:1 with x (that
    would draw one solo sub-band per category, not a genuine group). Reading
    its emitted offset channel (``xOffset`` for a vertical bar, ``yOffset``
    for horizontal — ``offset_channel`` names whichever the caller's
    orientation emits) here means this detector reports exactly what
    renders; re-deriving the same "is this grouped" predicate from resolved
    fields drifted from that decision in both directions (false positives on
    colors the emitter doesn't group by, false negatives on wide-form
    measures it does).

    Small multiples do NOT get a lower count here: the offset/color scale is
    never one of the channels ``facet_bound_position_channels``
    (``emitters/_cartesian.py``) can narrow — only a position channel (``x``/
    ``y``) resolves independently, when a panel's own rows carry a proper
    subset of that channel's domain, and color stays shared across panels
    by design (cross-panel color identity) regardless. So a panel facing
    one series value still divides its band across the *global* series
    domain, occupying a single sub-slot — the real render measures
    sub-pixel bars here, not full-width ones. Counting the offset field's
    cardinality in the chart's unpartitioned rows is the correct domain, not
    an overcount.

    The outer category-axis band count is a different story — see
    ``detect()``'s own check of the emitted ``resolve.scale`` for the same
    channel.
    """
    encoding = unit.get("encoding")
    offset = encoding.get(offset_channel) if isinstance(encoding, dict) else None
    if not isinstance(offset, dict):
        return 1
    # A value-mode offset (`{"value": ...}`, no "type" key — e.g. an
    # endpoint-label anchor or an axis-flush offset) isn't a field-based
    # discrete grouping — guard the same way the x-axis categorical check
    # above does. Every field-based offset the emitters produce is already
    # nominal/ordinal (bar.py clamps the offset channel's own type
    # regardless of the color channel's).
    if offset.get("type") not in _CATEGORICAL_TYPES:
        return 1
    offset_field = offset.get("field")
    if not isinstance(offset_field, str):
        return 1
    # Wide-form charts fold measures into a synthetic label field via VL's fold
    # transform (client-side, never in query rows): cardinality = measures ×
    # the authored dimension's values.
    if chart.wide_measures:
        measures = len(raw_wide_series_names(chart.wide_measures, chart.color, rows))
    else:
        measures = len({row[offset_field] for row in rows if offset_field in row})
    # A zero count means no series reached this detector at all — an empty
    # result set, or an offset field absent from every row. Treat the band as
    # undivided rather than dividing by zero: this is a diagnostic, and it must
    # never be the thing that fails a render.
    if measures == 0:
        return 1
    return measures


def _horizontal_render_extent(
    ctx: WarningContext, chart_id: str, chart: ResolvedBarChart, distinct: int
) -> float | None:
    """The per-panel pixel height a horizontal bar's bands actually divide.

    None when `chart_id`'s laid-out height is zero or absent from
    `ctx.layout_chart_heights` (a chart the layout pass sized to nothing).

    Divides `ctx.layout_chart_heights` (`ResolvedLayoutItem.height`) by the
    row-facet cardinality, then grows the result to
    `min_height_for_horizontal_bar_categories` -- see the module docstring
    for why that growth matters: a layout-wrapper `height:` can clamp
    `layout_chart_heights` below the floor even though the chart's real
    painted height is not clamped, and this `max()` is what recovers the
    real painted height in exactly that case.
    """
    layout_height = ctx.layout_chart_heights.get(chart_id)
    if not isinstance(layout_height, int | float) or layout_height <= 0:
        return None
    row_field = chart.multiples.rows if chart.multiples is not None else None
    row_cardinality = panel_axis_cardinality(chart.panel_axes, row_field)
    min_height = min_height_for_horizontal_bar_categories(
        distinct, chart.style.axis_x, effective_bar_size(chart.style.mark)
    )
    return max(layout_height / row_cardinality, min_height)


def _detect_continuous_overlap(
    chart_id: str,
    chart: ResolvedBarChart,
    render_width: float,
    rows: list[VLDict],
    x_field: str,
) -> Diagnostic | None:
    """Continuous (quantitative) x: warn when the bar's effective width
    would overlap its neighbor — see the module docstring's continuous-x
    rule for why this is a different comparison, and message, than the
    categorical readability floor above.

    ``render_width`` is the chart's whole plot width, but the emitter
    installs ``padding = effective_bar_size / 2`` on each side of a
    quantitative x scale (``bar.py``'s ``_emit_vertical``, so the min/max
    bars stay fully on-plot), for one ``effective_bar_size`` of chrome in
    total — a plain ``render_width / domain_span`` treats
    that reserved chrome as usable domain span and systematically
    overestimates the real per-value pixel step, which can miss an overlap
    the live scale would actually render (the estimate never models VL's own
    ``nice`` domain rounding either — this narrows, not closes, that gap).
    """
    xs = sorted(
        {
            coerced
            for row in rows
            if x_field in row
            and (coerced := coerce_numeric_cell(row[x_field])) is not None
        }
    )
    if len(xs) < 2:
        return None
    domain_span = xs[-1] - xs[0]
    if domain_span <= 0:
        return None

    bar = chart.style.mark
    reserved = effective_bar_size(bar)
    assert reserved is not None, (
        "marks.bar.size and marks.bar.max_size both unset — "
        "theme cascade must populate at least one"
    )
    # padding is effective_bar_size / 2 on EACH side, so the total reserved
    # chrome is one effective_bar_size, not two. Subtracting twice understates
    # every step this warning prints and fires it on charts that do not overlap.
    usable_width = render_width - reserved
    if usable_width <= 0:
        return None
    px_per_unit = usable_width / domain_span
    step_px = min(b - a for a, b in zip(xs, xs[1:], strict=False)) * px_per_unit

    if bar.size is not None:
        effective_width = bar.size
    else:
        assert bar.gap is not None, (
            "marks.bar.gap unset — theme cascade must populate it when marks.bar.size is unset"
        )
        assert bar.min_size is not None, (
            "marks.bar.min_size unset — theme cascade must populate it "
            "when marks.bar.size is unset"
        )
        assert bar.max_size is not None, (
            "marks.bar.max_size unset — theme cascade must populate it "
            "when marks.bar.size is unset"
        )
        effective_width = min(max(step_px - bar.gap, bar.min_size), bar.max_size)
    if effective_width <= step_px:
        return None

    return Diagnostic.from_code(
        WARN_BAR_BAND_WIDTH_TOO_NARROW,
        chart=chart_id,
        path=f"charts.{chart_id}.x",
        field=x_field,
        message=_CONTINUOUS_MESSAGE_TEMPLATE.format(
            chart_id=chart_id,
            distinct=len(xs),
            render_width=render_width,
            bar_width=effective_width,
            step_px=step_px,
        ),
        fix=WARN_BAR_BAND_WIDTH_TOO_NARROW.fix_template,
    )


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per bar chart whose bars are too narrow for their
    category spacing (vertical and horizontal orientation alike; see the
    module docstring for how orientation maps to VL channel names and, for
    horizontal, where the bounding extent comes from)."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedBarChart):
            continue
        if chart.x is None:
            continue
        if chart_id not in ctx.vega_specs or chart_id not in ctx.chart_results:
            continue

        is_horizontal = chart.orientation == "horizontal"
        # A vertical bar's category rides VL x and subdivides via xOffset; a
        # horizontal bar flips both to y/yOffset. See the module docstring.
        cat_channel = "y" if is_horizontal else "x"
        offset_channel = "yOffset" if is_horizontal else "xOffset"

        # Small multiples wrap the unit spec (encoding, per-panel width) under
        # "spec" — the facet root only carries facet/config/data. Unwrap it so
        # both checks below read the panel the bands actually render into.
        spec = ctx.vega_specs[chart_id]
        unit = spec["spec"] if "facet" in spec else spec

        cat_type = encoding_channel_type(unit, cat_channel)
        x_field: str = chart.x
        rows = ctx.chart_results[chart_id]

        render_width: float | None = None
        if not is_horizontal:
            candidate = unit.get("width")
            if not isinstance(candidate, int | float) or candidate <= 0:
                continue
            render_width = candidate

            if cat_type == "quantitative":
                # A histogram's x is quantitative but BINNED: _emit_histogram
                # sizes its mark as a band fraction of the bin span and
                # installs no scale.padding, so neither premise of the
                # continuous ladder holds. Running it over the raw, unbinned
                # rows reports the row spacing as a bar gap and the row count
                # as a bar count — every number in the message fictional.
                # Same carve-out, same reason, as the bucketed-axis
                # detector's own histogram guard.
                if chart.chart_type == "histogram":
                    continue
                diagnostic = _detect_continuous_overlap(
                    chart_id, chart, render_width, rows, x_field
                )
                if diagnostic is not None:
                    warnings.append(diagnostic)
                continue
        if cat_type not in _CATEGORICAL_TYPES:
            continue

        whole_dataset_distinct = len({row[x_field] for row in rows if x_field in row})
        # facet_bound_position_channels (emitters/_cartesian.py) can resolve
        # the category channel independently for a panel holding any proper
        # subset of its domain, not only the single-value case a name-matched
        # facet field used to guarantee by construction — read the WIDEST
        # panel's own count, not the whole-dataset union `rows` would give
        # (and not a flat 1, which only ever held for that one degenerate
        # shape).
        if facet_channel_is_independent(spec, cat_channel):
            distinct = (
                widest_panel_distinct_count(x_field, chart.panel_axes, rows)
                or whole_dataset_distinct
            )
        else:
            distinct = whole_dataset_distinct
        if distinct == 0:
            continue

        if is_horizontal:
            render_extent = _horizontal_render_extent(ctx, chart_id, chart, distinct)
            if render_extent is None:
                continue
        else:
            assert render_width is not None, (
                "render_width resolved above for every vertical, non-continuous branch"
            )
            render_extent = render_width

        band_width = render_extent / distinct
        series = _grouped_series_count(unit, chart, rows, offset_channel)
        bar_width = band_width / series
        if bar_width >= _MIN_BAND_WIDTH_PX:
            continue

        warnings.append(
            Diagnostic.from_code(
                WARN_BAR_BAND_WIDTH_TOO_NARROW,
                chart=chart_id,
                path=f"charts.{chart_id}.x",
                field=x_field,
                message=WARN_BAR_BAND_WIDTH_TOO_NARROW.message_template.format(
                    chart_id=chart_id,
                    distinct=distinct,
                    series=series,
                    render_width=render_extent,
                    bar_width=bar_width,
                    min_band_width=_MIN_BAND_WIDTH_PX,
                ),
                fix=WARN_BAR_BAND_WIDTH_TOO_NARROW.fix_template,
            )
        )

    return warnings
