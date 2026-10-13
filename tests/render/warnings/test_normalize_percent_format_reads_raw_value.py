"""Tests for the NORMALIZE-PERCENT-FORMAT-READS-RAW-VALUE detector.

The detection rule lives in the detector module's own docstring; what this file
pins is that each half of it is load-bearing -- the stack mode, the percent
format, the sums-to-1 predicate, and both authoring doors -- in the firing AND
the silent direction, since a detector that returns nothing passes every silent
test on its own.

Suppression is not tested here -- ``diagnostics/suppression.py::partition``
applies it centrally after every detector, covered by ``test_suppression.py``.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.normalized import AreaChart, BarChart, Chart
from dbt_charts.core.compile.models.style.authored import AreaChartStylePatch
from dbt_charts.core.diagnostics import WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE
from dbt_charts.core.render.warnings import (
    WarningContext,
    normalize_percent_format_reads_raw_value as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

_PERCENT_STYLE: dict[str, Any] = {"axis_y": {"labels": {"format": "percent_whole"}}}


def _ctx(chart: Chart, rows: list[dict[str, Any]]) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board, chart_results={resolved.id: rows}, vega_specs={}
    )


def _bar(**kwargs: Any) -> BarChart:
    base: dict[str, Any] = {
        "id": "c1",
        "type": "bar",
        "query_name": "q",
        "x": "x",
        "color": "series",
        "stack": "normalize",
    }
    return BarChart(**{**base, **kwargs})


def _counts() -> list[dict[str, Any]]:
    """Raw counts: each x group sums to 80, not 1."""
    return [
        {"x": "Aug 25", "series": "bugs", "val": 20},
        {"x": "Aug 25", "series": "hackers", "val": 60},
        {"x": "Sep 25", "series": "bugs", "val": 40},
        {"x": "Sep 25", "series": "hackers", "val": 40},
    ]


def _shares() -> list[dict[str, Any]]:
    """A real 0..1 share: each x group already sums to 1."""
    return [
        {"x": "Aug 25", "series": "bugs", "val": 0.25},
        {"x": "Aug 25", "series": "hackers", "val": 0.75},
        {"x": "Sep 25", "series": "bugs", "val": 0.5},
        {"x": "Sep 25", "series": "hackers", "val": 0.5},
    ]


# --- fires -----------------------------------------------------------------


def test_fires_on_raw_counts_under_a_percent_axis_format() -> None:
    chart = _bar(y="val", style=_PERCENT_STYLE)

    (warning,) = detector.detect(_ctx(chart, _counts()))

    assert warning.code == WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.code
    assert warning.chart == "c1"
    assert warning.field == "val"
    assert warning.path == "charts.c1.style.axis_y.labels.format"


def test_message_names_the_group_total_that_is_not_one() -> None:
    """The reader needs the number that disproves "this is already a share"."""
    chart = _bar(y="val", style=_PERCENT_STYLE)

    (warning,) = detector.detect(_ctx(chart, _counts()))

    assert "80" in warning.message
    assert "'val'" in warning.message


def test_fires_through_the_authored_axis_format_door() -> None:
    """`style.axis_y.labels.format` is the second door onto the same format."""
    chart = _bar(y="val", style={"axis_y": {"labels": {"format": ".0%"}}})

    (warning,) = detector.detect(_ctx(chart, _counts()))

    assert warning.code == WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.code


def test_fires_on_a_normalized_area_chart() -> None:
    """Area resolves the same measure format through the same helper."""
    chart = AreaChart(
        id="c1",
        type="area",
        query_name="q",
        x="x",
        y="val",
        color="series",
        stack="normalize",
        style=AreaChartStylePatch.model_validate(_PERCENT_STYLE),
    )

    (warning,) = detector.detect(_ctx(chart, _counts()))

    assert warning.code == WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.code


def test_wide_measures_are_summed_across_the_measure_list() -> None:
    """`y: [a, b]` stacks the measures themselves -- the group is one x row."""
    rows = [
        {"x": "Aug 25", "bugs": 20, "hackers": 60},
        {"x": "Sep 25", "bugs": 40, "hackers": 40},
    ]
    chart = _bar(y=["bugs", "hackers"], color=None, style=_PERCENT_STYLE)

    (warning,) = detector.detect(_ctx(chart, rows))

    assert warning.code == WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.code
    # Resolve has replaced chart.y with the synthetic fold field by now, so the
    # message has to name the measures the author actually wrote.
    assert warning.field == "bugs, hackers"


def test_wide_measures_survive_a_facet_on_their_own_color_column() -> None:
    """The panel split strips every partition column from a panel's rows, and
    the wide fold drops a row whose dimension is null -- so a facet on the
    color column silences the whole chart unless color is restamped too."""
    rows = [
        {"x": "Aug 25", "tier": tier, "bugs": 20, "hackers": 60}
        for tier in ("free", "paid")
    ]
    chart = _bar(
        y=["bugs", "hackers"],
        color="tier",
        style=_PERCENT_STYLE,
        multiples={"columns": "tier"},
    )

    (warning,) = detector.detect(_ctx(chart, rows))

    assert warning.code == WARN_NORMALIZE_PERCENT_FORMAT_READS_RAW_VALUE.code


def test_fires_on_a_panel_whose_group_is_not_a_share() -> None:
    """The firing direction of the panel split: a regression that empties the
    per-panel walk would pass the silent facet test below and only this one."""
    rows = [
        {"x": "Aug 25", "series": series, "tier": tier, "val": 20}
        for tier in ("free", "paid")
        for series in ("bugs", "hackers")
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE, multiples={"columns": "tier"})

    (warning,) = detector.detect(_ctx(chart, rows))

    assert "40" in warning.message


# --- stays silent ----------------------------------------------------------


def test_silent_when_every_group_already_sums_to_one() -> None:
    """The shipped `examples/ai_spend` and `examples/rockets` pattern: the y IS
    the share, so the authored percent format is honest on the raw value."""
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, _shares())) == []


def test_silent_when_the_measure_format_is_not_a_percent() -> None:
    """A currency format prints the true raw number; only its unit differs
    from the axis, which is a legitimate thing to author."""
    chart = _bar(y="val", style={"axis_y": {"labels": {"format": "currency_whole"}}})

    assert detector.detect(_ctx(chart, _counts())) == []


def test_silent_without_an_authored_measure_format() -> None:
    chart = _bar(y="val")

    assert detector.detect(_ctx(chart, _counts())) == []


def test_silent_on_an_absolute_stack() -> None:
    """`stack: zero` paints the raw values and labels its axis with the same
    authored format, so the chart is self-consistent -- the author's call."""
    chart = _bar(y="val", stack="zero", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, _counts())) == []


def test_silent_when_each_panel_group_sums_to_one() -> None:
    """Panels are judged separately: merging them would sum one x to 2.0 and
    fire on a faceted chart whose every panel is a real share."""
    rows = [
        {"x": "Aug 25", "series": series, "tier": tier, "val": val}
        for tier in ("free", "paid")
        for series, val in (("bugs", 0.25), ("hackers", 0.75))
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE, multiples={"columns": "tier"})

    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_within_the_rounding_tolerance() -> None:
    """A share rounded for display lands either side of 1; a raw count never
    lands near it."""
    rows = [
        {"x": "Aug 25", "series": "bugs", "val": 0.25},
        {"x": "Aug 25", "series": "hackers", "val": 0.79},
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, rows)) == []


def test_fires_once_past_the_rounding_tolerance() -> None:
    rows = [
        {"x": "Aug 25", "series": "bugs", "val": 0.25},
        {"x": "Aug 25", "series": "hackers", "val": 0.9},
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert len(detector.detect(_ctx(chart, rows))) == 1


def test_silent_when_a_group_totals_zero() -> None:
    """A share is undefined at a zero total, and zeros coerce to 0.0 rather
    than abstaining the way nulls do -- so an honest share board with one
    empty x bucket must not be reported for it."""
    rows = [
        {"x": "Aug 25", "series": "bugs", "val": 0.25},
        {"x": "Aug 25", "series": "hackers", "val": 0.75},
        {"x": "Sep 25", "series": "bugs", "val": 0},
        {"x": "Sep 25", "series": "hackers", "val": 0},
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_when_a_group_cancels_to_a_tiny_remainder() -> None:
    """Signed values that cancel land on 5.55e-17, not on exactly 0.0, so the
    zero abstention is tested against a float epsilon -- an exact `!= 0` would
    report "values sum to 5.55112e-17, not 1"."""
    rows = [
        {"x": "Aug 25", "series": "bugs", "val": 0.1},
        {"x": "Aug 25", "series": "hackers", "val": 0.2},
        {"x": "Aug 25", "series": "crashes", "val": -0.3},
    ]
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, rows)) == []


def test_silent_when_no_group_has_a_numeric_value() -> None:
    """Nothing to judge -- abstain rather than call an all-null chart a lie."""
    rows = [{"x": "Aug 25", "series": s, "val": None} for s in ("bugs", "hackers")]
    chart = _bar(y="val", style=_PERCENT_STYLE)

    assert detector.detect(_ctx(chart, rows)) == []
