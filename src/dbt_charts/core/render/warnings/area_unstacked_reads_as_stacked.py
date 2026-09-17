"""Detector: WARN_AREA_UNSTACKED_READS_AS_STACKED -- see its `doc` in
core/diagnostics/codes_render.py for what this fires on.

Detection rule:
  chart is a ResolvedAreaChart AND
  its effective stack mode is unstacked (`stack` is None or "none") AND
  it has no overlay layers (an overlay can paint the total the base's outer
  edge only looks like) AND
  it paints >= 2 series AND
  no panel of its rows ever shows a CROSSED pair, and at least one panel
  admitted a real (all compared values on one side of zero, coercible,
  shared-x, no duplicate x per series) comparison.

Order-free: whether two bands trade the "on top" position is a fact about
their VALUES at the x's they share, not about the order a renderer paints
those x's in. A pair CROSSES when the largest gap by which `a` sits over `b`
and the largest gap by which `b` sits over `a` both clear a discernibility
threshold -- both directional maxima being real means each band is visibly on
top somewhere, so the painted bands must trade places at some point on the
rendered image, wherever the renderer actually puts that swap. This needs
only x *identity* to pair `a(x)` against `b(x)`; it never asks *where* a
crossing lands, so it never needs to sort, type, or otherwise reconstruct the
order the chart paints its x's in. Every function below that walks per-x
diffs or sums relies on the same property: a running max or sum is
commutative, so no ordering of dict or panel iteration changes a result.

The threshold is a fraction of the y-domain span (`0.03`, not a pixel count):
area carries no baked plot height to convert a data gap into a rendered
pixel gap, and deriving one duplicates the bar family's legend-charge
machinery outside its own module. `0.03 * span` is ~12px at the ~350px
default plot height this repo's chart cards render at. On an unusually short
or tall chart the effective pixel threshold moves by roughly ±60%, which
shifts boundary verdicts but does not create a new class of false positive.

Reads only resolved config and the query's rows -- no vega spec, no live
pixel geometry -- so it is a DATA detector and reports a chart that never
laid out (an inactive tab, a collapsed `details:` section) alongside one
that did.

The unstacked test is `stack in (None, "none")`, matching the emitter's own
check (emitters/area.py). Resolve always assigns from the theme's required
Literal, whose default is "none", so the None arm is unreachable from an
authored board today -- keep both so the two cannot drift apart.

Nesting only reads as nesting when every compared band sits on the same
side of the zero baseline: two bands on opposite sides (or a single band
whose own values straddle it) paint in genuinely disjoint regions, not one
inside the other, however their `sign(a - b)` behaves. A series that shares
no same-side comparison with anything else in its panel is excluded from
both the reported band count and the remedy's hidden fraction, the same way
an uncomparable pair is excluded from the crossing verdict.

A faceted chart is judged per panel (`_panels`, via `regroup`/`restamp`):
collapsing every panel's rows into one dict before comparing would let
whichever panel's row lands last for a shared (series, x) key silently win,
manufacturing crossings and remedies no single panel actually paints.
"""

from __future__ import annotations

from collections.abc import Iterable

from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.style.resolved import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    PanelRows,
    regroup,
    restamp,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
    unfold_wide_rows,
)
from dbt_charts.core.diagnostics import (
    WARN_AREA_UNSTACKED_READS_AS_STACKED,
    Diagnostic,
)
from dbt_charts.core.render.chart.emitters._cartesian import (
    effective_measure_domain,
)
from dbt_charts.core.render.warnings.base import (
    ChartSeries,
    WarningContext,
    chart_series,
)
from dbt_charts.core.utils import CellValue, Rows, coerce_numeric_cell

_UNSTACKED = (None, "none")

_SeriesValues = dict[CellValue, dict[CellValue, CellValue]]

# Discernibility threshold, a fraction of the y-domain span rather than a
# pixel count -- see the module docstring for why area has no baked plot
# height to convert against, and where 0.03 (~12px at a ~350px default plot
# height) comes from.
_MIN_DISCERNIBLE_FRACTION = 0.03

# Hidden-fraction threshold above which the series plausibly compose a
# total -- see `_hidden_fraction`'s docstring for how the fraction itself is
# computed.
_COMPOSES_TOTAL_MIN_HIDDEN_FRACTION = 0.4


def detect(ctx: WarningContext) -> list[Diagnostic]:
    """Return one Diagnostic per unstacked area chart whose series never
    CROSS."""
    warnings: list[Diagnostic] = []

    for chart_id, chart in ctx.board_spec.charts.items():
        if not isinstance(chart, ResolvedAreaChart):
            continue
        if chart.stack not in _UNSTACKED:
            continue
        # An overlay layer can paint the very total the base series' outer edge
        # only looks like -- judging the base alone would fire on a chart
        # that already resolves the ambiguity. Abstain rather than guess.
        if chart.layers:
            continue
        if chart_id not in ctx.chart_results:
            continue
        rows = ctx.chart_results[chart_id]

        # A chart_series count under 2 needs no separate check: fewer than
        # two names ever reaching `_any_pair_crossed`'s panel loop means zero
        # pairs to compare, which is UNDECIDABLE (None) for every panel, so
        # `_judged_band_count` returns 0 on its own and the `if not
        # band_count` guard below already stays silent.
        series = chart_series(chart)
        if series is None:
            continue
        band_count = _judged_band_count(chart, rows, series)
        if not band_count:
            continue

        # `_judged_band_count` reports the widest panel the detector actually
        # judged, which is what a reader sees overlap on one canvas: a NULL
        # color key is a real painted band, a wide measure null on every row
        # paints none, a faceted chart never shows two panels' bands together,
        # and a panel the detector could not judge contributed no verdict.
        warnings.append(
            Diagnostic.from_code(
                WARN_AREA_UNSTACKED_READS_AS_STACKED,
                chart=chart_id,
                path=f"charts.{chart_id}.{series.authored_key}",
                field=series.authored_field,
                message=WARN_AREA_UNSTACKED_READS_AS_STACKED.message_template.format(
                    chart_id=chart_id,
                    count=band_count,
                    field=series.authored_field,
                ),
                fix=_remedy(chart, rows, series),
            )
        )

    return warnings


def _panels(chart: ResolvedAreaChart, rows: Rows) -> ChartDataset:
    """The chart's own baked small-multiples split (see module docstring for
    why the crossing check and the remedy both read per panel). ``restamp``
    puts x/color back on each panel's rows when either is itself the facet
    field -- both are stripped by the split otherwise, and ``restamp``
    no-ops when its field isn't a baked axis.
    """
    dataset: ChartDataset = regroup(chart.panel_axes, rows)
    if isinstance(chart.x, str):
        dataset = restamp(dataset, chart.x)
    if chart.color is not None:
        dataset = restamp(dataset, chart.color)
    return dataset


def _remedy(chart: ResolvedAreaChart, rows: Rows, series: ChartSeries) -> str:
    """The per-occurrence fix line: leads with `style.stack: "zero"` when
    the series plausibly compose a total, `type: line` when one dominates,
    mentioning the other briefly. The registry's own ``fix_template``
    (codes_render.py) stays generic -- it has no occurrence's data to
    compute this from. The author's own knowledge of what the series mean
    always outranks this heuristic.

    Walks each panel separately (see module docstring for why merging panels
    is wrong) and, per panel, keeps only the series names ``_any_pair_crossed``
    judged -- every x of a judged series feeds the hidden fraction, not only
    its same-side x's. A panel with a duplicate (series, x) or an unusable
    span contributes nothing, the same abstention ``_judged_band_count``
    applies.
    """
    panels: list[_SeriesValues] = []
    for panel in _panels(chart, rows).panels:
        values, duplicate = _series_values(chart, panel.rows, series)
        if duplicate:
            continue
        span = _series_value_span(chart.style.axis_y, values)
        if span <= 0:
            continue
        _, judged = _any_pair_crossed(values, span)
        panels.append({name: values[name] for name in judged})
    hidden = _hidden_fraction(panels)
    if hidden >= _COMPOSES_TOTAL_MIN_HIDDEN_FRACTION:
        return (
            'Set `style.stack: "zero"` so the bands read as parts of a whole '
            "(use `type: line` instead if the series are independent trends)."
        )
    return (
        "Use `type: line`: one series here dominates the rest, so stacking "
        "would likely misread them as parts of a whole they do not compose "
        '(set `style.stack: "zero"` instead if they truly do).'
    )


def _hidden_fraction(panels: Iterable[_SeriesValues]) -> float:
    """How much of the real total is hidden behind the outer edge, summed
    across every given panel and x rather than read off a single worst one:
    `1 - (sum of the largest magnitude at each x) / (sum of all magnitudes
    at that x)`. Word choice only, never part of the crossing verdict: a
    high value means the series plausibly sum to something real (lead with
    `style.stack: "zero"`); a low one means one series swamps the rest
    everywhere (lead with `type: line`).

    Magnitude, not signed value: a below-baseline panel's comparisons are
    exactly as real as an above-baseline one's, and summing signed values
    would let a negative total suppress or invert the fraction.

    Summing rather than maxing each x's own fraction keeps one thin x
    (fewer, noisier values) from outvoting every other x -- weighting by
    each x's own magnitude before combining is what
    ``test_fix_is_not_swayed_by_one_noisy_low_magnitude_x`` relies on.

    0.0 (the low-hidden default) when no x has any coercible value to sum,
    or no panel was given at all -- an edge case this word choice alone
    reaches for, never a crossing-undecidable state.
    """
    total_of_totals = 0.0
    total_of_tops = 0.0
    for series_values in panels:
        per_x: dict[CellValue, list[float]] = {}
        for values in series_values.values():
            for x, raw in values.items():
                coerced = coerce_numeric_cell(raw)
                if coerced is None:
                    continue
                per_x.setdefault(x, []).append(coerced)
        for vals in per_x.values():
            magnitudes = [abs(v) for v in vals]
            total_of_totals += sum(magnitudes)
            total_of_tops += max(magnitudes)
    return 1.0 - total_of_tops / total_of_totals if total_of_totals else 0.0


def _judged_band_count(
    chart: ResolvedAreaChart, rows: Rows, series: ChartSeries
) -> int:
    """The widest panel's JUDGED band count when every panel that admitted a
    real comparison found no CROSSED pair, and at least one panel admitted
    one -- 0 otherwise (nothing decided anywhere, or some panel crossed). A
    panel with no rows, no x, a duplicate (series, x), or an unusable
    y-domain span (no data, an authored domain the values fall outside, a
    log axis) decides nothing, per panel, and never counts as "never cross".

    The count is the panel's judged names (from ``_any_pair_crossed``) --
    the ones that shared a real, same-side comparison with something else --
    not every series in the panel: a series with no such comparison (a
    disjoint x, the opposite side of the baseline) paints in a region the
    reader never mistakes for part of this nesting.

    The y-domain span is read PER PANEL. Under ``multiples.scale:
    "independent"`` that is exact; under the default ``"shared"`` a panel's
    own span understates the real (chart-wide) axis, which overstates its
    gaps as a fraction of it and errs toward calling a crossing real --
    a false negative on a shared-scale facet, never a false alarm.
    """
    widest = 0
    for panel in _panels(chart, rows).panels:
        panel_values, ambiguous = _series_values(chart, panel.rows, series)
        if ambiguous:
            continue
        span = _series_value_span(chart.style.axis_y, panel_values)
        if span <= 0:
            continue
        verdict, judged = _any_pair_crossed(panel_values, span)
        if verdict is True:
            return 0
        if verdict is False:
            widest = max(widest, len(judged))
    return widest


def _series_value_span(ay: ResolvedAxisStyle, series_values: _SeriesValues) -> float:
    """The panel's y-axis span in data units, the denominator for the
    discernible-gap fraction.

    0.0 means unusable, and the caller abstains rather than guessing.

    Reads the domain off the cascaded axis via ``effective_measure_domain``
    rather than deriving one, which `compile/resolve/chart/AGENTS.md` asks of
    every family for the zero-anchor. Deriving it is wrong in each direction
    the author can reach: an authored ``scale.continuous.domain`` wins
    outright, ``scale.continuous.zero: false`` drops the baseline anchor, and
    a negative value drops it too. Each leaves the rendered span far narrower
    than the data's own reach, so a derived span scores a plainly separated
    crossing as a huge fraction of a too-small span and fires on a correct
    chart.

    A log axis abstains: position is ``log(v)``, so no linear
    ``|diff| / span`` is right at any magnitude. That shape is reachable --
    ``ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED`` tells the author of a stacked
    log area to use ``stack: none``, landing them here -- and judging it would
    answer ``stack: "zero"``, sending them back into that error."""
    cont = ay.scale.continuous if ay.scale is not None else None
    if cont is not None and cont.type == "log":
        return 0.0
    values = [
        coerced
        for series in series_values.values()
        for raw in series.values()
        if (coerced := coerce_numeric_cell(raw)) is not None
    ]
    if not values:
        return 0.0
    bounds = effective_measure_domain(ay, (min(values), max(values)))
    if bounds is None:
        # Unreachable from this call site: `effective_measure_domain` only
        # returns None when its `data_extent` argument is None, and the `not
        # values` guard above already ensures the tuple passed here never
        # is. `raise` is only for pyright's narrowing (the return type below
        # is non-Optional) -- it is not a safety net: `registry.run_all`
        # catches `Exception` and drops this detector's whole result list, so
        # this path IS the board-wide silence it would cause, not a guard
        # against one.
        raise AssertionError(
            "effective_measure_domain returned None for a non-empty data_extent"
        )
    lo, hi = bounds
    return hi - lo


def _series_values(
    chart: ResolvedAreaChart, rows: PanelRows | Rows, series: ChartSeries
) -> tuple[_SeriesValues, bool]:
    """Map each series name to {x_value: measure_value}, non-null x and
    non-null measure values only. The second return value is True when some
    series repeats an x -- a duplicate (series, x) pair -- across two or more
    contributing rows.

    A wide (`y: [a, b]`) chart's rows carry one measure per column; VL's own
    fold turns them into series runs client-side, so this reads through
    ``unfold_wide_rows``, the Python mirror of that same fold, to group them
    identically. A `color:`-encoded chart's rows are already one series value
    per row.

    A NULL x -- a left-join miss, an unbucketed row -- is an ordinary query
    result, not a real position on the axis. It names no comparable point, so
    the row is dropped.

    A NULL color key is a real, painted band on the `color:` path -- VL's
    nominal color scale has no `invalid: "filter"` the way a continuous
    scale does, so a `None` group draws like any other, not the phantom
    ``distinct_series_values`` excludes from the *legend* (a different
    question: what the palette needs a slot for, not what the chart paints).
    Only a null *measure* value is skipped, and only for that one x -- the
    band it belongs to still gets its other points. The wide path never
    produces a `None` series name to begin with (``unfold_wide_rows`` drops
    a row whose dimension is null before it ever reaches a label), so there
    is nothing to special-case there.

    The mapping is last-write-wins on a duplicate (series, x): with no x
    ordering to break the tie, there is no principled way to choose which of
    two rows for the same series and x the chart "really" paints, and a
    discrete x (newly judged by the order-free rule) makes a repeated
    category more likely than a continuous one ever was. The caller abstains
    on the whole panel rather than silently picking one value.
    """
    x_field = chart.x
    if not isinstance(x_field, str):
        return {}, False

    grouped: _SeriesValues = {}
    duplicate = False

    if chart.wide_measures:
        for row in unfold_wide_rows(rows, chart.wide_measures, chart.color):
            x = row[x_field]
            if x is None:
                continue
            bucket = grouped.setdefault(row[WIDE_LABEL_FIELD], {})
            if x in bucket:
                duplicate = True
            bucket[x] = row[WIDE_VALUE_FIELD]
        return grouped, duplicate

    y_field = chart.y
    if not isinstance(y_field, str):
        return {}, False
    color_field = series.authored_field
    for row in rows:
        value = row.get(y_field)
        x = row.get(x_field)
        if value is None or x is None:
            continue
        bucket = grouped.setdefault(row.get(color_field), {})
        if x in bucket:
            duplicate = True
        bucket[x] = value
    return grouped, duplicate


def _any_pair_crossed(
    series_values: _SeriesValues, span: float
) -> tuple[bool | None, frozenset[CellValue]]:
    """The crossing verdict, plus (only on a False verdict) the names that
    shared at least one real (same-side, coercible) comparison with another
    series -- a name that never does (a disjoint x, the opposite side of the
    baseline from everything else in the panel) is excluded, the same way
    its pairs already abstain from the verdict itself.

    True when some pair CROSSES -- both directional maxima of their
    shared-x diffs clear the discernible fraction; the name set is undefined
    for this verdict; no caller reads it, since a chart with any crossing
    fires no diagnostic. False when at least one pair was actually compared
    and none crossed. None when no pair shared even one such x -- nothing to
    compare, and the returned name set is empty."""
    names = list(series_values)
    compared_any = False
    judged: set[CellValue] = set()
    for i, a_name in enumerate(names):
        a = series_values[a_name]
        for b_name in names[i + 1 :]:
            b = series_values[b_name]
            crossed = _pair_crossed(a, b, span)
            if crossed is None:
                continue
            compared_any = True
            judged.add(a_name)
            judged.add(b_name)
            if crossed:
                return True, frozenset()
    return (False, frozenset(judged)) if compared_any else (None, frozenset())


def _pair_crossed(
    a: dict[CellValue, CellValue], b: dict[CellValue, CellValue], span: float
) -> bool | None:
    """None when this pair shares no coercible-numeric x, or when the two
    bands do not paint on one common side of the zero baseline -- neither is a
    real comparison, distinct from ``False`` ("compared, does not cross").

    Occlusion needs both bands on one side. Two constant series at +5 and -5
    hold ``a - b == +10`` at every x, but they fill [0, 5] and [-5, 0]:
    disjoint regions, nothing nested, and the outer edge hides nothing. A
    series that alternates sign across the shared x's paints across the
    baseline on its own and is never nested in anything either. Scoped to the
    shared, coercible points, the same rule this function already applies to
    ragged data.

    Order-free: pairs every shared x's `(a - b)` in whatever order dict
    iteration hands them out and tracks only the largest positive diff and
    the largest negative diff across all of them -- a running max is
    commutative, so no ordering of the shared x's changes either result.
    CROSSED when BOTH clear ``_MIN_DISCERNIBLE_FRACTION * span``: `a` sits
    visibly over `b` somewhere, and `b` sits visibly over `a` somewhere
    else, so the two bands must trade the "on top" position at some point on
    the rendered image -- wherever the renderer actually puts that swap,
    which this function never needs to know.

    Both directions must clear the threshold, not just one: a pair that
    separates by a hair one direction and by a lot the other is exactly the
    shape a rule checking only the larger direction would call a real
    crossing and wrongly silence. Requiring both is what still catches it.
    """
    max_pos = 0.0
    max_neg = 0.0
    compared: list[float] = []
    found = False
    for x, a_raw in a.items():
        if x not in b:
            continue
        a_value = coerce_numeric_cell(a_raw)
        b_value = coerce_numeric_cell(b[x])
        if a_value is None or b_value is None:
            continue
        found = True
        compared += [a_value, b_value]
        diff = a_value - b_value
        if diff > max_pos:
            max_pos = diff
        if -diff > max_neg:
            max_neg = -diff
    if not found:
        return None
    if not (all(v >= 0 for v in compared) or all(v <= 0 for v in compared)):
        return None

    threshold = _MIN_DISCERNIBLE_FRACTION * span
    return max_pos >= threshold and max_neg >= threshold
