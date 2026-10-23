"""Tests for the AREA_UNSTACKED_READS_AS_STACKED render-warning detector.

Detection rule: fires on an area chart that resolves to an unstacked mode
(``stack`` None or "none"), paints two or more series (from either a
``color:`` column or a wide ``y: [a, b, c]`` measure list), AND no pair of
those series ever CROSSES -- order-free: the pair's largest gap with `a`
above `b`, and its largest gap with `b` above `a`, must BOTH clear a
discernibility threshold (a fraction of the y-domain span) for the pair to
count as crossed. Whether a pair crosses needs only x *identity* to line up
`a(x)` against `b(x)`; it is never a question about the ORDER a chart paints
its x's in, so this detector never sorts x values, never resolves a Vega-Lite
x type, and never reads a baked plot height.

dbt charts already signals stacking through fill weight -- an unstacked
area's fill is translucent, a stacked one's is solid -- so the real hazard is
a chart that defeats that cue anyway: series that never cross paint one band
nested inside the next at every x, indistinguishable from a real stack. A
pair that crosses declares itself an overlap, so it never fires regardless of
series count.

Suppression is not tested here. It is applied centrally by
``diagnostics/suppression.py::partition`` after every detector has run, and is
covered by ``test_suppression.py`` -- a per-detector copy would only re-test
``partition``.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
)
from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.diagnostics import WARN_AREA_UNSTACKED_READS_AS_STACKED
from dbt_charts.core.render.warnings import (
    WarningContext,
    area_unstacked_reads_as_stacked as detector,
)
from dbt_charts.core.render.warnings.base import chart_series

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart


def _nested_rows(n_series: int, n_x: int = 4) -> list[dict[str, Any]]:
    """Series that never cross: each series' band sits at a fixed offset far
    enough from every other that adding x's small range never reorders them."""
    return [
        {"x": x, "series": f"s{s}", "val": s * 1000 + x}
        for x in range(n_x)
        for s in range(n_series)
    ]


def _crossing_rows(n_series: int) -> list[dict[str, Any]]:
    """A cyclic rotation over n_series x's: every series takes every rank in
    turn, so every pair swaps places (crosses) at some point."""
    return [
        {"x": x, "series": f"s{s}", "val": (x + s) % n_series}
        for x in range(n_series)
        for s in range(n_series)
    ]


def _ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows},
    )


def _area(**kwargs: Any) -> AreaChart:
    base: dict[str, Any] = {"id": "c1", "type": "area", "query_name": "q", "x": "x"}
    return AreaChart(**{**base, **kwargs})


# --- fires -----------------------------------------------------------------


def test_message_names_the_effect_and_what_the_outer_edge_really_is() -> None:
    """The message states what the reader sees, not the predicate that fired.

    It must not claim the series "never cross": the detector also fires on a
    pair that does cross, too faintly for the chart to show it.
    """
    warnings = detector.detect(_ctx(_area(y="val", color="series"), _nested_rows(2)))
    message = warnings[0].message
    assert "hard to tell" in message
    assert "outer edge" in message
    assert "individual series value" in message
    assert "not the total" in message
    assert "never cross" not in message


def test_fires_on_two_nested_never_crossing_series() -> None:
    """Two nested series that never cross read exactly like a stack,
    regardless of series count."""
    chart = _area(y="val", color="series")
    warnings = detector.detect(_ctx(chart, _nested_rows(2)))
    assert len(warnings) == 1
    assert warnings[0].code == WARN_AREA_UNSTACKED_READS_AS_STACKED.code
    assert warnings[0].chart == "c1"
    assert warnings[0].field == "series"
    assert warnings[0].path == "charts.c1.color"
    assert "2 series" in warnings[0].message


def test_fires_on_two_nested_never_crossing_series_below_the_baseline() -> None:
    """The below-baseline mirror of the comparable-magnitude nested pair:
    two bands close enough in magnitude that neither dominates read as
    composing a total whichever side of zero they sit on -- the remedy
    compares magnitude, not signed value, so a negative pair gets the same
    `style.stack: "zero"` answer its positive mirror would.

    The gap converts against the domain the axis really renders, read off the
    cascaded style, so an all-negative panel is measured on its own fitted
    range rather than a range derived from the data's distance to zero."""
    rows = [{"x": x, "series": "a", "val": -(100 + x)} for x in range(4)] + [
        {"x": x, "series": "b", "val": -(90 + x)} for x in range(4)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1
    assert "outer edge" in warnings[0].message
    fix = warnings[0].fix or ""
    assert fix.startswith('Set `style.stack: "zero"`')


def test_fires_on_six_nested_never_crossing_series() -> None:
    chart = _area(y="val", color="series")
    warnings = detector.detect(_ctx(chart, _nested_rows(6)))
    assert len(warnings) == 1
    assert "6 series" in warnings[0].message


def test_fires_on_wide_y_without_a_color_and_names_y() -> None:
    """A wide chart's series are its measures; resolve injects a synthetic color
    channel for them, so the authored key to name is `y`, not the synthetic field."""
    rows = [{"x": i, "a": i + 1000, "b": i} for i in range(4)]
    warnings = detector.detect(_ctx(_area(y=["a", "b"]), rows))
    assert len(warnings) == 1
    assert warnings[0].field == "y"
    assert warnings[0].path == "charts.c1.y"


def test_fires_on_wide_y_crossed_with_a_color_dimension() -> None:
    """Series are measures x dimension values, a product no raw row carries."""
    rows = [
        {"x": i, "region": r, "a": i + (1000 if r == "north" else 0), "b": i}
        for i in range(4)
        for r in ("north", "south")
    ]
    warnings = detector.detect(_ctx(_area(y=["a", "b"], color="region"), rows))
    assert len(warnings) == 1
    assert warnings[0].field == "region"
    assert "4 series" in warnings[0].message


def test_fix_leads_with_line_when_one_series_dominates() -> None:
    """`_nested_rows` offsets series by 1000 apart -- one series dominates the
    other at every x, so the hidden fraction is near zero and the fix leads
    with `type: line`, mentioning stacking only as the secondary option."""
    warnings = detector.detect(_ctx(_area(y="val", color="series"), _nested_rows(2)))
    fix = warnings[0].fix or ""
    assert fix.startswith("Use `type: line`")
    assert "style.stack" in fix


def test_fix_leads_with_stack_when_series_are_comparable() -> None:
    """Two series close enough in magnitude that neither dominates -- the
    hidden fraction clears the composes-a-total threshold, so the fix leads
    with `style.stack: "zero"`, mentioning `type: line` only as the
    secondary option."""
    rows = [{"x": x, "series": "a", "val": 100 + x} for x in range(4)] + [
        {"x": x, "series": "b", "val": 90 + x} for x in range(4)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    fix = warnings[0].fix or ""
    assert fix.startswith('Set `style.stack: "zero"`')
    assert "type: line" in fix


def test_fix_is_not_swayed_by_one_noisy_low_magnitude_x() -> None:
    """One series dominates at 19 of 20 x's (hidden fraction ~0.2, well under
    the 0.4 composes-a-total threshold), plus a single x where both series
    happen to tie at a tiny magnitude (1 and 1, hidden fraction 0.5 at that
    x alone). A per-x `max` would let that one low-volume x decide the whole
    chart's remedy -- exactly the failure a finer time bucketing (more x's,
    each with a smaller chance of being representative) makes likely.
    Weighting by each x's own magnitude before combining keeps one thin data
    point from outvoting nineteen substantial ones."""
    rows = (
        [{"x": x, "series": "a", "val": 20} for x in range(19)]
        + [{"x": x, "series": "b", "val": 5} for x in range(19)]
        + [
            {"x": 19, "series": "a", "val": 1},
            {"x": 19, "series": "b", "val": 1},
        ]
    )
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    fix = warnings[0].fix or ""
    assert fix.startswith("Use `type: line`")


def test_hidden_fraction_is_order_free() -> None:
    """Feeding the SAME (series, x, value) content, built by inserting
    entries in two different orders, must produce the same hidden fraction:
    summing per-x magnitudes needs no order to be well-defined."""
    a1: dict[int, float] = {}
    b1: dict[int, float] = {}
    for x, (av, bv) in {0: (100.0, 90.0), 1: (10.0, 1000.0)}.items():
        a1[x] = av
        b1[x] = bv
    a2: dict[int, float] = {}
    b2: dict[int, float] = {}
    for x, (av, bv) in {1: (10.0, 1000.0), 0: (100.0, 90.0)}.items():
        a2[x] = av
        b2[x] = bv
    first = detector._hidden_fraction([{"a": a1, "b": b1}])
    second = detector._hidden_fraction([{"a": a2, "b": b2}])
    assert first == second


def test_fires_on_a_tie_that_never_reverses() -> None:
    """A pair that ties at one x but never exceeds it still counts as
    nested: order-free comparison only cares about the two directional
    maxima, and a tie contributes 0 to both."""
    s0_values = [10, 11, 12, 13]
    s1_values = [0, 1, 2, 13]  # ties s0 at x=3, never exceeds it
    rows = [{"x": x, "series": "s0", "val": s0_values[x]} for x in range(4)] + [
        {"x": x, "series": "s1", "val": s1_values[x]} for x in range(4)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_fires_on_ragged_series_comparing_only_shared_x() -> None:
    """s1 runs high at x=0,1 where s0 has no value, and sits beneath s0 at
    x=2,3 where both do. A union-and-zero-fill comparison would read s1's
    high values against a filled 0 for s0 at x=0,1, see s0 below s1 there and
    above it at x=2,3, and call that a crossing it manufactured. Comparing
    only the shared x's (2, 3) finds no such thing: s0 stays above s1 at
    both, so the two never cross."""
    rows = [
        {"x": 0, "series": "s1", "val": 10},
        {"x": 1, "series": "s1", "val": 10},
        {"x": 2, "series": "s0", "val": 3},
        {"x": 3, "series": "s0", "val": 4},
        {"x": 2, "series": "s1", "val": 1},
        {"x": 3, "series": "s1", "val": 1},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


# --- discernibility: direct unit tests on the order-free predicate --------
#
# `_pair_crossed` is exercised directly, with an explicit `span`, rather than
# through a resolved chart's axis: the exact discernible-gap floor a real
# axis renders depends on headroom padding this module does not own, and
# hardcoding an assumed span would make these tests describe axis behavior
# they do not actually control. `span=1000.0` makes `_THRESHOLD` (30.0) a
# round number to reason about; the board-level tests below cover the real
# wiring from a resolved chart's axis into this same function.

_SPAN = 1000.0
_THRESHOLD = detector._MIN_DISCERNIBLE_FRACTION * _SPAN  # 30.0
# A single shared x used below to anchor a chart's fitted domain to reach
# from 0 to at least `_SPAN` -- unlike `_pair_rows`' own tied anchor pair,
# these two below tests only need SOME point establishing that reach, not a
# threshold-precise one, since they assert a qualitative crossing/nesting
# shape rather than a boundary.
_SPAN_ANCHOR_X = -1


def test_pair_crossed_requires_both_directions_not_either() -> None:
    """`a` sits over `b` by far more than the threshold at x=0, but `b`
    never sits over `a` by enough anywhere -- only ONE directional maximum
    clears the threshold. Proves the `and`, not `or`: an `or` here would
    call this pair crossed."""
    a = {0: 500.0, 1: 480.0}
    b = {0: 100.0, 1: 470.0}
    assert detector._pair_crossed(a, b, _SPAN) is False


def test_pair_crossed_true_when_both_directions_clear_it() -> None:
    """`a` sits over `b` at x=0 and `b` sits over `a` at x=1, both by more
    than the threshold -- BOTH directional maxima clear it."""
    a = {0: 500.0, 1: 100.0}
    b = {0: 100.0, 1: 500.0}
    assert detector._pair_crossed(a, b, _SPAN) is True


def test_pair_crossed_false_when_reverse_direction_is_just_under_threshold() -> None:
    """Same shape as the true case above, but `b`'s gap over `a` at x=1 sits
    1 unit under `_THRESHOLD` -- must stay False, proving the boundary is
    really compared against `_THRESHOLD` and not some looser number."""
    a = {0: 500.0, 1: 100.0 - (_THRESHOLD - 1.0)}
    b = {0: 100.0, 1: 100.0}
    assert detector._pair_crossed(a, b, _SPAN) is False


def test_pair_crossed_true_when_reverse_direction_exactly_meets_threshold() -> None:
    """`b`'s gap over `a` at x=1 sits exactly at `_THRESHOLD` -- the
    comparison is `>=`, so this must clear it."""
    a = {0: 500.0, 1: 100.0 - _THRESHOLD}
    b = {0: 100.0, 1: 100.0}
    assert detector._pair_crossed(a, b, _SPAN) is True


def test_pair_crossed_false_on_many_swings_all_under_the_threshold() -> None:
    """A pair that swings back and forth, never by more than the threshold in
    either direction, must stay False regardless of how many times rank
    alternates -- a running max is what the order-free rule is built on, and
    this proves it accumulates correctly across more than one swing."""
    a = {0: 510.0, 1: 500.0, 2: 508.0, 3: 500.0, 4: 516.0}
    b = {0: 500.0, 1: 510.0, 2: 500.0, 3: 515.0, 4: 500.0}
    assert detector._pair_crossed(a, b, _SPAN) is False


def test_pair_crossed_result_does_not_depend_on_dict_iteration_order() -> None:
    """The running max is commutative, so feeding the SAME points in a
    different insertion order must not change the verdict."""
    ordered_a = {0: 500.0, 1: 100.0, 2: 480.0}
    ordered_b = {0: 100.0, 1: 500.0, 2: 470.0}
    reordered_a = {2: 480.0, 0: 500.0, 1: 100.0}
    reordered_b = {1: 500.0, 2: 470.0, 0: 100.0}
    assert detector._pair_crossed(
        ordered_a, ordered_b, _SPAN
    ) == detector._pair_crossed(reordered_a, reordered_b, _SPAN)


# --- discernibility: wired through a resolved chart's real axis -----------


def _measured_threshold() -> float:
    """The real discernible-gap floor for a bare two-series area chart's
    axis -- read off the actual resolved axis (headroom padding included)
    rather than assumed, so the board-level tests below don't have to
    hardcode a number this module does not own."""
    rows = [
        {"x": -2, "series": "s0", "val": 0.0},
        {"x": -2, "series": "s1", "val": 0.0},
        {"x": -1, "series": "s0", "val": _SPAN},
        {"x": -1, "series": "s1", "val": _SPAN},
    ]
    resolved = make_test_resolved_chart(_area(y="val", color="series"), rows)
    assert isinstance(resolved, ResolvedAreaChart)
    series = chart_series(resolved)
    assert series is not None
    values, _ = detector._series_values(resolved, rows, series)
    span = detector._series_value_span(resolved.style.axis_y, values)
    return detector._MIN_DISCERNIBLE_FRACTION * span


def _pair_rows(*points: tuple[int, float, float]) -> list[dict[str, Any]]:
    """Two-series rows from explicit (x, s0_value, s1_value) points, plus a
    tied 0/`_SPAN` anchor pair so the fitted y-domain reaches at least
    `[0, _SPAN]` without itself contributing a diff -- a tie's `a - b` is
    always 0, so it can never inflate either directional maximum."""
    rows = [
        {"x": -2, "series": "s0", "val": 0.0},
        {"x": -2, "series": "s1", "val": 0.0},
        {"x": -1, "series": "s0", "val": _SPAN},
        {"x": -1, "series": "s1", "val": _SPAN},
    ]
    for x, s0, s1 in points:
        rows.append({"x": x, "series": "s0", "val": s0})
        rows.append({"x": x, "series": "s1", "val": s1})
    return rows


def test_fires_when_only_one_direction_clears_the_threshold() -> None:
    """s0 sits over s1 by a lot at x=0, but s1 never sits over s0 by enough
    anywhere -- the pair is not CROSSED, so the chart fires. Board-level
    counterpart of `test_pair_crossed_requires_both_directions_not_either`,
    proving `detect` really wires the resolved axis's span into the same
    predicate."""
    rows = _pair_rows((0, 500.0, 100.0), (1, 480.0, 470.0))
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_silent_when_both_directions_clear_the_threshold() -> None:
    """s0 sits over s1 at x=0, and s1 sits over s0 at x=1, both by more than
    the measured threshold -- BOTH directional maxima clear it, so the pair
    is CROSSED and the chart stays silent."""
    threshold = _measured_threshold()
    rows = _pair_rows((0, 500.0, 100.0), (1, 100.0, 100.0 + threshold + 50.0))
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


def test_silent_on_series_with_disjoint_x() -> None:
    """Two series that share no x at all admit no real comparison --
    ``_pair_crossed`` returns ``None`` for every pair, so the panel
    contributes nothing to ``compared_any``. This must stay silent (no
    verdict, never "no crossing observed"): every other test in this module
    that reaches the panel loop with a usable span also happens to find a
    real crossing or a real non-crossing pair, so only this shape actually
    exercises the ``compared_any`` gate itself -- reverting it to
    unconditional ``True`` (or the crossing function to always return
    ``False``) leaves every other test in this file green."""
    rows = [
        {"x": 0, "series": "s0", "val": 100},
        {"x": 1, "series": "s0", "val": 200},
        {"x": 2, "series": "s1", "val": 50},
        {"x": 3, "series": "s1", "val": 60},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


def test_a_panel_with_an_unusable_span_never_silences_a_real_panel_elsewhere() -> None:
    """A dedicated mutation-detecting shape for the `span <= 0` guard: with a
    threshold of `0.03 * 0 == 0`, every pair trivially "crosses" on a
    zero-span panel (any diff, even 0, is `>= 0`), so a panel made only of
    all-zero values would stay silent whether or not the guard runs --
    silence for the wrong reason if it were the only panel on the chart.

    Under `multiples.scale: "independent"` each panel's own data reaches
    `_series_value_span`, so a "zero" panel (span 0.0) sits beside a "real"
    panel that genuinely nests two series (span > 0, no real crossing). The
    guard must let the zero panel contribute NOTHING; without it, the zero
    panel's trivially-"crossed" verdict short-circuits `_judged_band_count`'s
    per-panel loop and silences the whole chart, hiding the real panel's
    genuine nesting."""
    zero_panel = [
        {"x": x, "tier": "zero", "series": f"s{s}", "val": 0}
        for x in range(2)
        for s in range(2)
    ]
    real_panel = [
        {"x": x, "tier": "real", "series": f"s{s}", "val": s * 1000 + x}
        for x in range(4)
        for s in range(2)
    ]
    chart = _area(
        y="val",
        color="series",
        multiples={"columns": "tier", "scale": "independent"},
    )
    warnings = detector.detect(_ctx(chart, zero_panel + real_panel))
    assert len(warnings) == 1


def test_silent_on_a_null_keyed_series_that_crosses() -> None:
    """A `color:` column carrying NULLs (an unmatched left join, an unset
    attribute) still paints a real band -- Vega-Lite's nominal color scale
    has no `invalid: "filter"` the way a continuous scale does, so a null
    group is drawn, not dropped. `s0` sits far below both `s1` and the null
    group at every x (never crosses anything); `s1` and the null group
    genuinely cross. A null band that isn't compared at all would miss this
    crossing and fire on a chart that plainly does not read as a stack."""
    rows = [
        {"x": _SPAN_ANCHOR_X, "series": "s0", "val": 0},
        {"x": _SPAN_ANCHOR_X, "series": "s1", "val": _SPAN},
        {"x": _SPAN_ANCHOR_X, "series": None, "val": _SPAN},
        {"x": 0, "series": "s0", "val": 10},
        {"x": 0, "series": "s1", "val": 500},
        {"x": 0, "series": None, "val": 100},
        {"x": 1, "series": "s0", "val": 10},
        {"x": 1, "series": "s1", "val": 100},
        {"x": 1, "series": None, "val": 500},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


def test_fires_on_a_series_whose_measure_is_null_every_row_and_names_the_painted_count() -> (
    None
):
    """`s2`'s color value is present on every row, but its measure is NULL
    throughout -- it never contributes a paintable point, so nothing draws
    a third band. The message must count what actually paints (2), not the
    color column's raw cardinality (3)."""
    rows = [{"x": x, "series": "s0", "val": x} for x in range(4)]
    rows += [{"x": x, "series": "s1", "val": 1000 + x} for x in range(4)]
    rows += [{"x": x, "series": "s2", "val": None} for x in range(4)]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message


def test_fires_on_wide_y_with_a_measure_null_on_every_row_and_names_the_painted_count() -> (
    None
):
    """`y: [a, b, c]` where `c` is NULL on every row: `unfold_wide_rows`
    drops every row for it before `_series_values` ever sees a `c` label,
    so it never reaches `_judged_band_count`'s judged names -- nothing
    paints a `c` band. The message must count the painted bands (2), not
    the authored measure list's size (3)."""
    rows = [{"x": i, "a": i + 1000, "b": i, "c": None} for i in range(4)]
    warnings = detector.detect(_ctx(_area(y=["a", "b", "c"]), rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message


def test_silent_on_opposite_sign_constant_series() -> None:
    """Two constant series on opposite sides of zero (+5, -5) trivially
    satisfy `a - b == +10` at every x -- but they paint in disjoint regions
    ([0, 5] and [-5, 0]): nothing is nested, nothing is hidden. Occlusion
    only applies to bands sharing one side of the baseline."""
    rows = [{"x": x, "series": "s0", "val": 5} for x in range(4)] + [
        {"x": x, "series": "s1", "val": -5} for x in range(4)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


def test_band_count_excludes_a_series_with_no_same_side_comparison() -> None:
    """`s2` sits on the opposite side of the zero baseline from `s0`/`s1`, so
    every pair involving it abstains (same rule as the opposite-sign test
    above) while `s0`/`s1` themselves nest normally. The reported count must
    name only the 2 series a reader actually sees nested, not all 3 -- `s2`
    paints in a disjoint region nothing here mistakes for part of the
    stack. The trailing `.fix` assertion is the only guard on `_remedy`'s
    judged-name filter: it pins that `_remedy` recomputes its own judged set
    instead of reusing `_judged_band_count`'s, which would let `s2` back in
    and flip the message to the wide-fraction `style.stack: "zero"` answer."""
    rows = (
        [{"x": x, "series": "s0", "val": 1000.0 + x} for x in range(4)]
        + [{"x": x, "series": "s1", "val": float(x)} for x in range(4)]
        + [{"x": x, "series": "s2", "val": -(1000.0 + x)} for x in range(4)]
    )
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message
    assert (warnings[0].fix or "").startswith("Use `type: line`")


def test_silent_when_a_single_band_straddles_the_baseline() -> None:
    """`s0` alternates sign across x, so the panel holds a negative value and
    is never judged. Without that, every one of these small swings would read
    as a never-crossing pair and the chart would fire on the ordinary
    crossing rule."""
    rows = [
        {"x": _SPAN_ANCHOR_X, "series": "s0", "val": 0},
        {"x": _SPAN_ANCHOR_X, "series": "s1", "val": _SPAN},
        {"x": 0, "series": "s0", "val": -3},
        {"x": 0, "series": "s1", "val": 1},
        {"x": 1, "series": "s0", "val": 3},
        {"x": 1, "series": "s1", "val": 1},
        {"x": 2, "series": "s0", "val": -3},
        {"x": 2, "series": "s1", "val": 1},
        {"x": 3, "series": "s0", "val": 3},
        {"x": 3, "series": "s1", "val": 1},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


# --- duplicate (series, x) guard -------------------------------------------


def test_silent_on_a_duplicate_series_x_pair_even_though_the_last_value_would_nest() -> (
    None
):
    """Two rows for `s0` at the same x -- a join fan-out, a repeated
    category on a discrete axis. The values dict is last-write-wins, so
    without the duplicate guard this reads as an ordinary nested pair (the
    LAST `s0` row for x=0 is 10, well under `s1`'s 500) and fires. The guard
    must abstain instead: the chart's own geometry is ambiguous, so nothing
    is reported, even though "read the last row" alone would have produced a
    firing verdict."""
    rows = _pair_rows((0, 500.0, 10.0), (1, 10.0, 500.0))
    rows.append({"x": 0, "series": "s0", "val": 10.0})  # duplicate (s0, x=0)
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []


def test_silent_on_a_duplicate_series_x_pair_on_a_wide_chart() -> None:
    """The wide (`y: [a, b]`) branch builds its own values dict through
    `unfold_wide_rows` -- it needs the same duplicate guard as the long
    branch, exercised independently since it is a separate code path."""
    rows = [{"x": i, "a": i + 1000, "b": i} for i in range(4)]
    rows.append({"x": 0, "a": 5, "b": 0})  # duplicate (a, x=0)
    warnings = detector.detect(_ctx(_area(y=["a", "b"]), rows))
    assert warnings == []


def test_remedy_ignores_a_panel_with_a_duplicate_series_x_pair() -> None:
    """The remedy must abstain on a panel `_series_values` flags ambiguous,
    the same way the crossing verdict does -- not fold its last-write-wins
    values into the hidden fraction. `clean` alone decides the fix (`s0`
    dominates `s1`, `type: line`); `dup`'s single duplicated x resolves to
    either a value close to its other series (comparable magnitude, would
    push the fix to `style.stack`) or one that dwarfs it (dominant, would
    keep it at `type: line`) depending purely on which of its two rows for
    (s0, x=100) lands last. Swapping only those two rows must not change
    the reported fix."""
    clean_rows = [
        {"x": x, "tier": "clean", "series": "s0", "val": 1000.0 + x} for x in range(4)
    ] + [{"x": x, "tier": "clean", "series": "s1", "val": float(x)} for x in range(4)]
    dup_s1 = {"x": 100, "tier": "dup", "series": "s1", "val": 80000.0}
    dup_s0_comparable = {"x": 100, "tier": "dup", "series": "s0", "val": 80000.0}
    dup_s0_dominant = {"x": 100, "tier": "dup", "series": "s0", "val": 8000000.0}

    variant_a = clean_rows + [dup_s0_dominant, dup_s0_comparable, dup_s1]
    variant_b = clean_rows + [dup_s0_comparable, dup_s0_dominant, dup_s1]

    chart = _area(y="val", color="series", multiples={"columns": "tier"})
    warnings_a = detector.detect(_ctx(chart, variant_a))
    warnings_b = detector.detect(_ctx(chart, variant_b))
    assert len(warnings_a) == 1
    assert len(warnings_b) == 1
    assert warnings_a[0].fix == warnings_b[0].fix
    assert (warnings_a[0].fix or "").startswith("Use `type: line`")


# --- silent ------------------------------------------------------------


def test_silent_on_four_constantly_crossing_series() -> None:
    """Four series that swap ranks constantly declare their own overlap and
    must stay silent, regardless of series count."""
    chart = _area(y="val", color="series")
    assert detector.detect(_ctx(chart, _crossing_rows(4))) == []


def test_silent_on_wide_y_crossing() -> None:
    rows = [{"x": i, "a": i, "b": 3 - i} for i in range(4)]
    assert detector.detect(_ctx(_area(y=["a", "b"]), rows)) == []


def test_silent_on_a_gradient_color_channel() -> None:
    """`color: grp` plus `style.color.gradient` resolves the color channel to
    `mode="gradient"` -- a single-series area chart with a real color field,
    the one shape that actually reaches `chart_series`'s `mode != "series"`
    gate. Without that gate, `grp`'s two distinct values are counted as two
    nested, never-crossing series (same shape as `_nested_rows`) and this
    fires "2 series from 'grp' overlap" -- the exact false positive the gate
    exists to prevent."""
    rows = [{"x": x, "val": 1000 + x, "grp": "a"} for x in range(4)]
    rows += [{"x": x, "val": x, "grp": "b"} for x in range(4)]
    chart = _area(
        y="val",
        color="grp",
        style=AreaChartStylePatch.model_validate(
            {"color": {"gradient": {"palette": ["#fee", "#900"]}}}
        ),
    )
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_on_a_single_series_without_color() -> None:
    rows = [{"x": i, "val": i} for i in range(4)]
    assert detector.detect(_ctx(_area(y="val"), rows)) == []


def test_silent_on_a_single_distinct_color_value() -> None:
    """A `color:` column present but taking only ONE distinct value: there is
    no separate "fewer than 2 series" check in `detect` for this -- a single
    name reaching `_any_pair_crossed`'s panel loop produces zero pairs, which
    is UNDECIDABLE (not NOT_CROSSED) for every panel, so `_judged_band_count`
    returns 0 on its own. This is the regression test for that emergent
    behavior, independent of `test_silent_on_series_with_disjoint_x`'s
    two-names-zero-shared-x shape."""
    rows = [{"x": i, "series": "only", "val": i} for i in range(4)]
    assert detector.detect(_ctx(_area(y="val", color="series"), rows)) == []


def test_silent_when_stacked() -> None:
    rows = _nested_rows(4)
    for mode in ("zero", "normalize", "center"):
        chart = _area(y="val", color="series", stack=mode)
        assert detector.detect(_ctx(chart, rows)) == [], mode


def test_silent_on_other_families_with_the_same_shape() -> None:
    """Line series never occlude, and an unstacked bar groups side by side --
    bar's own coincide defect has its own detector."""
    rows = _nested_rows(4)
    line = LineChart(
        id="c1", type="line", query_name="q", x="x", y="val", color="series"
    )
    bar = BarChart(id="c1", type="bar", query_name="q", x="x", y="val", color="series")
    assert detector.detect(_ctx(line, rows)) == []
    assert detector.detect(_ctx(bar, rows)) == []


def test_silent_on_a_chart_with_overlay_layers() -> None:
    """An overlay layer can paint the very total the base series' outer edge
    only looks like -- the base alone would otherwise fire (two nested
    series, same shape as `_nested_rows`) even though the overlay already
    resolves what the outer edge means. The detector abstains rather than
    judge the base in isolation."""
    chart = _area(y="val", color="series", layers=[{"type": "line", "y": "total"}])
    rows = _nested_rows(2)
    for row in rows:
        row["total"] = row["val"]
    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_when_faceted_by_the_series_column() -> None:
    """Small multiples split by the color column give each series its own panel.
    Two panels each painting one nested area have nothing left to mistake for a
    stack; warning there would tell the author to fix a chart that is already
    right."""
    rows = [{"x": x, "region": g, "val": x} for x in range(4) for g in ("n", "s")]
    chart = _area(y="val", color="region", multiples={"columns": "region"})
    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_when_faceted_by_a_different_column() -> None:
    """Faceting on some OTHER column leaves both series nested inside each
    panel -- the misread is per-panel and real."""
    rows = [
        {"x": x, "region": g, "tier": t, "val": x * 1000 + (0 if g == "n" else 1)}
        for x in range(4)
        for g in ("n", "s")
        for t in ("free", "paid")
    ]
    chart = _area(y="val", color="region", multiples={"columns": "tier"})
    assert len(detector.detect(_ctx(chart, rows))) == 1


def test_fires_when_faceted_by_its_own_x_column() -> None:
    """Faceting on the chart's OWN x column is the one case where `regroup`
    strips the x field off every panel's rows (it's the facet field), so
    `_panels` must restamp it back on -- without that, `_series_values` sees
    `x` as missing on every row, drops every row as unpositioned, and the
    chart goes silent instead of firing on its two nested series."""
    rows = [{"x": x, "series": "s0", "val": 1000.0 + x} for x in range(4)] + [
        {"x": x, "series": "s1", "val": float(x)} for x in range(4)
    ]
    chart = _area(y="val", color="series", multiples={"columns": "x"})
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message


def test_faceted_count_reports_the_widest_panel_not_the_flat_total() -> None:
    """A facet split by `tier`, colored by `region`, with two DIFFERENT
    regions per tier (four distinct regions overall) must report 2 -- the
    most bands any single panel actually paints -- not 4, the flat row
    set's total across panels that never share a canvas."""
    rows = [
        {"x": x, "tier": "free", "region": r, "val": x * 1000 + i}
        for x in range(4)
        for i, r in enumerate(("n1", "n2"))
    ]
    rows += [
        {"x": x, "tier": "paid", "region": r, "val": x * 1000 + i}
        for x in range(4)
        for i, r in enumerate(("n3", "n4"))
    ]
    chart = _area(y="val", color="region", multiples={"columns": "tier"})
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message


def test_faceted_count_reports_the_maximum_panel_width_regardless_of_bake_order() -> (
    None
):
    """Panel `a` (baked first, since its rows come first) judges 3 nested
    series; panel `b` (baked last) judges only 2. The reported count must be
    the true maximum (3) across every panel, not whichever panel the bake
    happens to visit last."""
    rows = (
        [{"x": x, "tier": "a", "series": "s0", "val": float(x)} for x in range(4)]
        + [{"x": x, "tier": "a", "series": "s1", "val": 1000.0 + x} for x in range(4)]
        + [{"x": x, "tier": "a", "series": "s2", "val": 2000.0 + x} for x in range(4)]
        + [{"x": x, "tier": "b", "series": "s0", "val": float(x)} for x in range(4)]
        + [{"x": x, "tier": "b", "series": "s1", "val": 1000.0 + x} for x in range(4)]
    )
    chart = _area(y="val", color="series", multiples={"columns": "tier"})
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "3 series" in warnings[0].message


def test_fires_on_a_wide_chart_faceted_by_its_color_dimension() -> None:
    """A wide chart's series are measures x the color column's values, so faceting
    by that column leaves one fill per MEASURE inside every panel. The panel axis
    accounts for only part of the count, and the misread is real per panel."""
    rows = [
        {"x": x, "region": g, "a": x + 1000, "b": x}
        for x in range(4)
        for g in ("n", "s")
    ]
    chart = _area(y=["a", "b"], color="region", multiples={"columns": "region"})
    assert len(detector.detect(_ctx(chart, rows))) == 1


def test_silent_when_the_chart_has_no_results() -> None:
    """A chart whose query did not run must not fire on an empty count."""
    chart = _area(y="val", color="series")
    resolved = make_test_resolved_chart(chart, _nested_rows(2))
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={},
    )
    assert detector.detect(ctx) == []


def test_silent_on_zero_rows_for_a_wide_chart() -> None:
    """`chart_series` reads a wide chart's authored `y:` list, not its rows,
    so it returns non-None even with zero data. Zero rows must never be
    read as "no crossing observed" -- there was nothing to compare at
    all, and `_judged_band_count` finds no judged pairs on its own."""
    assert detector.detect(_ctx(_area(y=["a", "b"]), [])) == []


def test_silent_when_chart_has_no_x() -> None:
    """`x` is str | None on the shared Cartesian base; a chart whose two
    series plainly cross must not be reported as never crossing just
    because there is no x field to compare them through."""
    chart = AreaChart(id="c1", type="area", query_name="q", y="val", color="series")
    assert detector.detect(_ctx(chart, _crossing_rows(4))) == []


def test_decimal_beside_float_coerces_without_raising() -> None:
    """Mixed warehouse numeric types (Postgres Decimal beside a plain float)
    must not raise -- coerce_numeric_cell puts them on common ground before
    subtracting."""
    rows = [{"x": x, "series": "s0", "val": Decimal(1000 + x)} for x in range(4)] + [
        {"x": x, "series": "s1", "val": float(x)} for x in range(4)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_non_coercible_cells_are_not_comparable_evidence() -> None:
    """A stray cell that fails to coerce (a non-numeric string) must not be
    treated as equal, zero, or evidence of nesting -- and must not raise. A
    genuinely null cell is filtered before it ever reaches the crossing
    comparison. The poisoned x (8) is skipped, but eight other x's are real,
    consistently-nested comparisons on one side of the baseline, so the
    chart still fires -- a poisoned cell must not flip a real verdict either
    way."""
    rows = [{"x": x, "series": "s0", "val": 5} for x in range(8)]
    rows += [{"x": x, "series": "s1", "val": 11} for x in range(8)]
    rows += [
        {"x": 8, "series": "s0", "val": "not-a-number"},
        {"x": 8, "series": "s1", "val": 11},
        {"x": 9, "series": "s0", "val": None},
        {"x": 9, "series": "s1", "val": 11},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_temporal_x_with_a_null_row_does_not_raise() -> None:
    """A NULL x is an ordinary query result on a temporal-x chart (a
    left-join miss, an unbucketed row), not a value to compare -- the row
    must be dropped rather than paired under a `None` key.

    The null bucket carries DIFFERENT values in the two series on purpose,
    so a phantom point that leaked through would manufacture a crossing at
    that key and silence the chart. Dropping it is what leaves the real,
    nested comparison behind, so the chart fires."""
    dates = [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1), date(2024, 4, 1)]
    rows = [{"x": d, "series": "s0", "val": 300 + i} for i, d in enumerate(dates)]
    rows += [{"x": d, "series": "s1", "val": 100 + i} for i, d in enumerate(dates)]
    rows += [
        {"x": None, "series": "s0", "val": 0},
        {"x": None, "series": "s1", "val": 500},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_wide_y_with_a_null_x_row_does_not_raise() -> None:
    """The wide branch (`y: [a, b]`) reads `row[x_field]` directly -- it
    needs the same NULL-x drop as the long branch."""
    xs = [date(2024, 1 + i, 1) for i in range(4)]
    rows = [{"x": x, "a": i + 1000, "b": i} for i, x in enumerate(xs)]
    rows += [{"x": None, "a": 0, "b": 9999}]
    warnings = detector.detect(_ctx(_area(y=["a", "b"]), rows))
    assert len(warnings) == 1


def test_unorderable_mixed_type_x_is_still_judged() -> None:
    """`sorted()` raises `TypeError` comparing a `str` to a `date` in Python 3
    -- proof that this detector genuinely never sorts x values, since it
    judges this chart instead of raising or silently abstaining. Pairing is
    by dict-key identity, which needs only equality/hashing, never
    ordering."""
    rows = [
        {"x": "jan", "series": "s0", "val": 1000},
        {"x": "jan", "series": "s1", "val": 0},
        {"x": date(2024, 2, 1), "series": "s0", "val": 1001},
        {"x": date(2024, 2, 1), "series": "s1", "val": 1},
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_panel_that_crosses_silences_whole_chart() -> None:
    """Faceted by another column: one panel's series never cross, the
    other's do. Any crossing in any panel silences the whole chart, the
    same conservative direction as the single-plot rule."""
    nested_panel = [
        {
            "x": x,
            "region": g,
            "tier": "steady",
            "val": x * 1000 + (0 if g == "n" else 1),
        }
        for x in range(4)
        for g in ("n", "s")
    ]
    crossing_panel = [
        {"x": 0, "region": "n", "tier": "swap", "val": 0},
        {"x": 0, "region": "s", "tier": "swap", "val": 1000},
        {"x": 1, "region": "n", "tier": "swap", "val": 1000},
        {"x": 1, "region": "s", "tier": "swap", "val": 0},
    ]
    chart = _area(y="val", color="region", multiples={"columns": "tier"})
    assert detector.detect(_ctx(chart, nested_panel + crossing_panel)) == []


def _region_tier_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two facet-tier panels, each internally nested (no real crossing) but
    sharing the same `(region, x)` keys across panels -- a naive flat
    collapse would let one panel's row silently overwrite the other's."""
    free_rows = [
        {"x": x, "region": "n", "tier": "free", "val": 10 + x} for x in range(4)
    ]
    free_rows += [
        {"x": x, "region": "s", "tier": "free", "val": 20 + x} for x in range(4)
    ]
    paid_rows = [
        {"x": x, "region": "n", "tier": "paid", "val": 50 + x} for x in range(4)
    ]
    paid_rows += [
        {"x": x, "region": "s", "tier": "paid", "val": 5 + x} for x in range(4)
    ]
    return free_rows, paid_rows


def test_faceted_by_another_column_same_verdict_under_shuffled_row_order() -> None:
    """A facet split by a column other than the series field must answer
    the crossing question per panel. A `{series: {x: value}}` collapse
    across panels lets whichever row lands last for a given (series, x)
    win -- with two panels each nested but in opposite directions,
    shuffling the row order can manufacture a cross-panel sign reversal
    that flips the verdict. The real per-panel answer must not depend on
    row order at all."""
    free_rows, paid_rows = _region_tier_rows()
    natural_order = free_rows + paid_rows
    shuffled_order = list(natural_order)
    random.Random(7).shuffle(shuffled_order)

    chart = _area(y="val", color="region", multiples={"columns": "tier"})
    natural = detector.detect(_ctx(chart, natural_order))
    shuffled = detector.detect(_ctx(chart, shuffled_order))
    assert len(natural) == 1
    assert len(shuffled) == 1


def test_remedy_sentence_stable_under_shuffled_row_order_on_a_faceted_chart() -> None:
    """`_remedy` must not read the flat, unsplit rows either -- the same
    cross-panel collapse the verdict path fixes with `regroup`. Both
    panels are internally nested (n vs s never reverses sign within either
    tier, so the verdict itself always fires regardless of row order), but
    `region: "s"` at the shared x=2 differs sharply between tiers -- close
    enough to `region: "n"` under `free` to clear the hidden-fraction
    "compose a total" threshold, dominated by it under `paid` to miss it.
    A flat, unsplit collapse lets whichever tier's row for that key landed
    last in the (possibly shuffled) row list decide which side of the
    threshold wins, flipping the fix sentence between `style.stack` and
    `type: line` purely as a function of row order -- reproduced at this
    fixed seed. The regrouped-per-panel fix must not depend on it."""
    free_rows = [
        {"x": 0, "region": "n", "tier": "free", "val": 10},
        {"x": 1, "region": "n", "tier": "free", "val": 11},
        {"x": 2, "region": "n", "tier": "free", "val": 100},
        {"x": 3, "region": "n", "tier": "free", "val": 13},
        {"x": 0, "region": "s", "tier": "free", "val": 20},
        {"x": 1, "region": "s", "tier": "free", "val": 21},
        {"x": 2, "region": "s", "tier": "free", "val": 120},
        {"x": 3, "region": "s", "tier": "free", "val": 23},
    ]
    paid_rows = [
        {"x": 0, "region": "n", "tier": "paid", "val": 50},
        {"x": 1, "region": "n", "tier": "paid", "val": 51},
        {"x": 2, "region": "n", "tier": "paid", "val": 100},
        {"x": 3, "region": "n", "tier": "paid", "val": 53},
        {"x": 0, "region": "s", "tier": "paid", "val": 5},
        {"x": 1, "region": "s", "tier": "paid", "val": 6},
        {"x": 2, "region": "s", "tier": "paid", "val": 10},
        {"x": 3, "region": "s", "tier": "paid", "val": 8},
    ]
    natural_order = free_rows + paid_rows
    shuffled_order = list(natural_order)
    random.Random(0).shuffle(shuffled_order)

    chart = _area(y="val", color="region", multiples={"columns": "tier"})
    natural = detector.detect(_ctx(chart, natural_order))
    shuffled = detector.detect(_ctx(chart, shuffled_order))
    assert len(natural) == 1
    assert len(shuffled) == 1
    assert natural[0].fix == shuffled[0].fix


def test_independent_scale_panel_crossing_is_measured_against_its_own_span() -> None:
    """Under `multiples.scale: "independent"` each panel gets its own y
    domain -- reading the span off the whole chart instead makes a genuine
    crossing in a small panel look like a tiny fraction of a much larger
    span because it is measured against a huge OTHER panel's values. `big`
    never crosses (`s0`/`s1` nested, ~10000 magnitude); `small` genuinely
    crosses (~60 magnitude, so the same 20-unit swing reads as a real
    crossing against its own span, but as noise against `big`'s ~10000).
    Distinct x ranges per panel (100/101 vs 0/1) so a chart-wide, unsplit
    read of the rows doesn't itself corrupt the comparison by letting one
    panel's row silently overwrite the other's at a shared x -- that is the
    cross-panel row collision, not this test's own concern; keeping the x's
    disjoint isolates the span-scope question this test is actually about."""
    big_panel = [
        {"x": x, "grp": "big", "series": "s0", "val": 9000} for x in (100, 101)
    ]
    big_panel += [
        {"x": x, "grp": "big", "series": "s1", "val": 10000} for x in (100, 101)
    ]
    small_panel = [
        {"x": 0, "grp": "small", "series": "s0", "val": 40},
        {"x": 0, "grp": "small", "series": "s1", "val": 60},
        {"x": 1, "grp": "small", "series": "s0", "val": 60},
        {"x": 1, "grp": "small", "series": "s1", "val": 40},
    ]
    chart = _area(
        y="val",
        color="series",
        multiples={"columns": "grp", "scale": "independent"},
    )
    warnings = detector.detect(_ctx(chart, big_panel + small_panel))
    assert warnings == []


# --- the axis owns the domain -----------------------------------------------


def _crossing_near_1000() -> list[dict[str, Any]]:
    """Two series around 1000 that trade places once, separating by 5 either
    side. On a zero-anchored axis that swap is a small fraction of the span
    and reads as invisible; on any axis fitted to the data it is most of the
    plot."""
    return [
        {"x": 0, "series": "s0", "val": 1000.0},
        {"x": 1, "series": "s0", "val": 1000.0},
        {"x": 2, "series": "s0", "val": 1010.0},
        {"x": 3, "series": "s0", "val": 1010.0},
        {"x": 0, "series": "s1", "val": 1005.0},
        {"x": 1, "series": "s1", "val": 1005.0},
        {"x": 2, "series": "s1", "val": 1005.0},
        {"x": 3, "series": "s1", "val": 1005.0},
    ]


def test_silent_when_the_author_turns_off_the_zero_anchor() -> None:
    """`scale.continuous.zero: false` fits the axis to the data, so the swap
    that would be a small fraction of `[0, 1010]` fills the plot and is
    plainly real. Deriving the span from the values instead of reading the
    axis scored it as a tiny fraction and told a correct chart it reads as
    stacked."""
    chart = _area(
        y="val",
        color="series",
        style={"axis_y": {"scale": {"continuous": {"zero": False}}}},
    )
    assert detector.detect(_ctx(chart, _crossing_near_1000())) == []


def test_silent_when_the_author_pins_the_domain() -> None:
    """An authored `scale.continuous.domain` wins outright over every baked
    bound, so the rendered span is the one the author asked for."""
    chart = _area(
        y="val",
        color="series",
        style={"axis_y": {"scale": {"continuous": {"domain": [995, 1015]}}}},
    )
    assert detector.detect(_ctx(chart, _crossing_near_1000())) == []


def test_silent_on_a_log_axis() -> None:
    """Position is `log(v)` on a log scale, so no linear gap-to-span ratio is
    right at any magnitude and the detector abstains. The shape is reachable:
    ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED sends a stacked log area here
    with `stack: none`, and answering `stack: "zero"` would send it back."""
    rows = [
        {"x": x, "series": f"s{s}", "val": float(s * 1000 + x + 1)}
        for x in range(4)
        for s in range(2)
    ]
    chart = _area(
        y="val",
        color="series",
        style={"axis_y": {"scale": {"continuous": {"type": "log"}}}},
    )
    assert detector.detect(_ctx(chart, rows)) == []


def test_count_names_only_a_panel_the_detector_actually_judged() -> None:
    """A panel the detector could not judge never contributed to the verdict,
    so its band count must not be the number the message reports.

    The judged panel paints two nested bands. The other holds three series on
    disjoint x's, so no pair shares a point and nothing there is decidable.
    Counting every panel would announce three bands for a verdict two
    produced."""
    rows: list[dict[str, Any]] = []
    for x in range(4):
        rows.append({"x": x, "tier": "judged", "series": "s0", "val": 100.0 + x})
        rows.append({"x": x, "tier": "judged", "series": "s1", "val": 500.0 + x})
    for s in range(3):
        rows.append({"x": 100 + s, "tier": "skip", "series": f"n{s}", "val": 50.0})

    chart = _area(y="val", color="series", multiples={"columns": "tier"})
    warnings = detector.detect(_ctx(chart, rows))
    assert len(warnings) == 1
    assert "2 series" in warnings[0].message


# --- every x type is judged, not only continuous ---------------------------


def test_fires_on_a_discrete_x_with_nested_series() -> None:
    """The order-free rule needs no x ordering, so a discrete (nominal) x is
    judged exactly like a continuous one. Two series nested at every
    category read as a stack whichever axis type paints them."""
    xs = ["north", "south", "east", "west"]
    rows = [
        {"x": x, "series": f"s{s}", "val": float(s * 1000 + i)}
        for i, x in enumerate(xs)
        for s in range(2)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert len(warnings) == 1


def test_silent_on_a_discrete_x_with_crossing_series() -> None:
    """The discrete-x widening must still recognize a real crossing on a
    categorical axis, not just fire unconditionally once the continuous-only
    gate is gone."""
    xs = ["north", "south", "east", "west"]
    rows = [
        {"x": x, "series": "s0", "val": v}
        for x, v in zip(xs, [500.0, 100.0, 500.0, 100.0], strict=True)
    ]
    rows += [
        {"x": x, "series": "s1", "val": v}
        for x, v in zip(xs, [100.0, 500.0, 100.0, 500.0], strict=True)
    ]
    warnings = detector.detect(_ctx(_area(y="val", color="series"), rows))
    assert warnings == []
