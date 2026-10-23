"""Tests for the LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS render-warning
detector.

Detection rule: chart.style.axis_x.labels.expr contains a bare local-time
accessor (timeFormat(/year(/quarter(/month(/date(, not their utc-prefixed
siblings) AND resolve_cartesian_x_type() returns a calendar grain in
BUCKETED_CALENDAR_UNITS for this chart's x field AND datum.value is a Date
(vl_type == "temporal") or the expr explicitly converts ISO strings to Dates
with toDate().

The end-to-end tests (``test_local_time_label_expr_on_bucketed_axis_warning_e2e.py``)
drive this through the real compile() -> render() seam; these unit tests
drive the detector directly against a hand-built WarningContext so each
branch guard (orientation, family, missing chart_results) is covered cheaply
without a full render.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.authored import MultiplesConfig
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    HeatmapChart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.style.authored import (
    AreaChartStylePatch,
    AxisXStylePatch,
    BarChartStylePatch,
    HeatmapChartStylePatch,
    LineChartStylePatch,
    ScatterChartStylePatch,
)
from dbt_charts.core.diagnostics import (
    WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS,
)
from dbt_charts.core.render.warnings import (
    WarningContext,
    local_time_label_expr_on_bucketed_axis as detector,
)

from ...core._board_utils import make_test_resolved_board, make_test_resolved_chart

# Monthly rows well under the default max_ordinal_buckets density gate, so a
# bar chart resolves to VL's ordinal scale (no encoding.x.timeUnit emitted) --
# the shape a spec-reading detector was silent on.
_ROWS = [
    {"month": "2024-04-15 00:00:00", "value": 10},
    {"month": "2024-05-15 00:00:00", "value": 20},
    {"month": "2024-06-15 00:00:00", "value": 30},
]

# The same shape one grain coarser, for the yearquarter cases: mid-quarter
# timestamps, one per quarter.
_QUARTERLY_ROWS = [
    {"month": "2024-02-15 00:00:00", "value": 10},
    {"month": "2024-05-15 00:00:00", "value": 20},
    {"month": "2024-08-15 00:00:00", "value": 30},
]


def _make_ctx(
    chart: Chart, rows: list[dict[str, Any]] | None = _ROWS
) -> WarningContext:
    resolved = make_test_resolved_chart(chart, rows or [])
    board = make_test_resolved_board(charts={resolved.id: resolved})
    return WarningContext(
        board_spec=board,
        chart_results={resolved.id: rows} if rows is not None else {},
    )


def test_silent_for_ordinal_bar_without_todate() -> None:
    """An ordinal bar chart with timeFormat(datum.value, ...) -- no toDate() --
    renders 0NaN under BOTH TZs (datum.value is an ISO string, not a Date).
    This is a broken expression, not the UTC/local off-by-one; the detector
    must stay silent so the user isn't told to fix a TZ problem that isn't
    there."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_horizontal_bar_with_todate() -> None:
    """A horizontal bar with toDate() is the hazard: complete_ordinal_time_series
    rewrites every x value to a date-only ISO string (bucket.isoformat()), so
    toDate() in the label expr always creates a UTC-midnight Date from a date-only
    form -- regardless of whether the raw rows held date objects or datetime strings."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "orientation": "horizontal",
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                ),
            }
        ),
    )
    ctx = _make_ctx(
        chart
    )  # _ROWS uses datetime-space strings, emitter rewrites to date-only
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_for_horizontal_bar_without_todate() -> None:
    """A horizontal bar without toDate() is NOT the hazard: datum.value is an
    ISO string and timeFormat can't parse it (renders 0NaN in both TZs).
    Silence is correct -- but the reason is no-toDate, not skipping hbar."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "orientation": "horizontal",
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                ),
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_year_accessor() -> None:
    """year(datum.value) reads the year in local time; on a UTC-bucketed axis
    the result is one year early for negative-offset hosts (2024 → 2023).
    The old regex only matched timeFormat/month/date -- year was a gap."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "'FY' + year(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_for_string_literal_containing_month_accessor() -> None:
    """The regex must not match month( inside a single-quoted string literal.
    datum.index + ' month(s) in' contains the text 'month(' but the author
    never calls the month() time function."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "datum.index + ' month(s) in'"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_silent_for_scatter() -> None:
    """Scatter is intentionally excluded from this detector's scope. The hazard
    is confirmed present (scatter.py:104-125 resolves a temporal x via
    resolve_cartesian_x_type), but its emitter path was not mirrored here.
    A follow-up task should implement scatter (and heatmap) coverage."""
    chart = ScatterChart(
        id="c1",
        type="scatter",
        query_name="q",
        x="month",
        y="value",
        style=ScatterChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_month_accessor() -> None:
    """month(datum.value) carries the same off-by-one as timeFormat() -- both
    read local time; only utcmonth() is TZ-safe."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "'M' + month(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_over_budget_fine_grain_bar() -> None:
    """The scaffold-budget gate drops the detected grain when it flips a bar
    to a continuous temporal scale, but the TZ hazard is a property of the
    DOMAIN (date-only ISO values a bare accessor coerces in local time), not
    of whether the axis bands. Dropping the grain must not blind the
    detector."""
    import datetime as dt

    sparse = [
        {
            "day": (dt.date(2023, 1, 1) + dt.timedelta(days=45 * i)).isoformat(),
            "value": i,
        }
        for i in range(10)
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="day",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"labels": {"expr": "'M' + month(datum.value)"}}
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=sparse)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_date_accessor() -> None:
    """date(datum.value) is the day-of-month sibling of month() -- same
    local-time hazard."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "'D' + date(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_when_expr_already_utc_safe() -> None:
    """utcmonth(/utcdate(/utcFormat( must never match -- they share no word
    boundary with the bare local accessor."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "utcFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_ordinal_bar_with_datetime_strings_and_todate() -> None:
    """complete_ordinal_time_series rewrites every x row to a date-only ISO
    bucket string (bucket.isoformat()). So even when the raw rows have datetime-
    space strings like '2024-04-15 00:00:00', the emitted spec has '2024-04-01'
    (date-only ISO). toDate('2024-04-01') creates a UTC-midnight Date per the ES
    spec -- timeFormat then reads it in local time -- the real hazard. The
    detector must test the EMITTED bucket value, not the raw row string."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    # _ROWS has datetime-space strings; emitter rewrites → date-only ISO → hazard.
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_ordinal_bar_with_python_date_objects_and_todate() -> None:
    """Python date objects are the common case for a warehouse DATE column. The
    emitter rewrites them to date-only ISO strings via _ordinal_bucket_key, so
    toDate() in the label expr creates a UTC-midnight Date. The raw-row check
    isinstance(v, str) is False for date objects -- the old gate was silent on
    the most common real-world data shape. Must fire."""
    import datetime as dt

    date_object_rows = [
        {"month": dt.date(2024, 4, 1), "value": 10},
        {"month": dt.date(2024, 5, 1), "value": 20},
        {"month": dt.date(2024, 6, 1), "value": 30},
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=date_object_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_no_fire_when_chart_not_in_chart_results() -> None:
    """Charts absent from chart_results (failed execution) must be skipped."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                )
            }
        ),
    )
    resolved = make_test_resolved_chart(chart, _ROWS)
    board = make_test_resolved_board(charts={resolved.id: resolved})
    ctx = WarningContext(
        board_spec=board,
        chart_results={},
    )
    assert detector.detect(ctx) == []


def test_warning_message_names_the_matched_accessor() -> None:
    """The message must name the specific accessor (year/month/date/quarter/
    timeFormat) the detector matched, not a hardcoded 'timeFormat'. Asserts
    w.message for the year() accessor, which is distinct from timeFormat."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "'FY' + year(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    diags = detector.detect(ctx)
    assert len(diags) == 1
    w = diags[0]
    assert "year()" in w.message
    assert "yearmonth" in w.message
    assert "c1" in w.message


def test_fires_for_quarter_accessor() -> None:
    """quarter(datum.value) reads the 0-indexed quarter in local time; on a
    UTC-midnight yearquarter axis a Q1 tick at UTC midnight shows as Q4 of the
    previous year under a negative UTC offset. The regex includes 'quarter' in
    the alternation -- this test pins that it's actually exercised."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearquarter",
                        "labels": {"expr": "'Q' + (quarter(datum.value) + 1)"},
                    }
                )
            }
        ),
    )
    # Quarter-grained rows: _ROWS is monthly, and three months inside one
    # quarter is a bucket collision, not one point per bucket.
    ctx = _make_ctx(chart, rows=_QUARTERLY_ROWS)
    diags = detector.detect(ctx)
    codes = {w.code for w in diags}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes
    w = next(
        d for d in diags if d.code == WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code
    )
    assert "quarter()" in w.message


def test_silent_for_utc_prefixed_accessors_on_temporal_axis() -> None:
    """utcmonth/utcyear/utcdate/utcquarter all contain the bare accessor name
    as a substring (e.g. utcmonth contains 'month'). The \\b word-boundary anchor
    on _LOCAL_TIME_ACCESSOR_RE must exclude them. Without \\b, utcmonth( would
    match as 'month(' -- this test detects if \\b is removed."""
    safe_exprs = [
        "utcmonth(datum.value)",
        "utcyear(datum.value)",
        "utcdate(datum.value)",
        "utcquarter(datum.value)",
    ]
    for expr in safe_exprs:
        chart = LineChart(
            id="c1",
            type="line",
            query_name="q",
            x="month",
            y="value",
            style=LineChartStylePatch.model_validate(
                {
                    "axis_x": AxisXStylePatch.model_validate(
                        {
                            "time_unit": "yearmonth",
                            "labels": {"expr": expr},
                        }
                    )
                }
            ),
        )
        ctx = _make_ctx(chart)
        assert detector.detect(ctx) == [], f"{expr!r} should be silent (utc-safe)"


def test_silent_for_double_quoted_string_literal_containing_month_accessor() -> None:
    """The accessor-match regex must not fire on accessor-shaped text inside a
    double-quoted string literal. datum.index + \" month(s) in\" contains the
    text 'month(' inside double quotes -- not a function call. A single-quote-
    only strip regex would miss this and fire a false positive."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": 'datum.index + " month(s) in"'},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_day_accessor_on_temporal_axis() -> None:
    """day(datum.value) reads the day-of-week in local time. On a UTC-midnight
    axis the result differs by one across a midnight boundary depending on the
    host TZ. The accessor is listed alongside year/month/date/quarter in
    _LOCAL_TIME_ACCESSOR_RE -- this test pins that it fires."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "day(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_for_horizontal_bar_above_density_threshold_without_todate() -> None:
    """Horizontal bar always emits x as a nominal encoding regardless of bucket count
    (no timeUnit transform). datum.value is always a raw ISO string. Above
    max_ordinal_buckets (60), resolve_cartesian_x_type returns vl_type='temporal'
    because mark_type='bar' at that density -- but horizontal bar never emits that
    encoding. Passing that vl_type to the temporal branch fires a false positive on
    bare timeFormat(datum.value, ...) without toDate. The fix forces vl_type='ordinal'
    for orientation='horizontal'."""
    from datetime import date

    start = date(2019, 1, 1)

    def _nth_month(n: int) -> str:
        y = start.year + (start.month - 1 + n) // 12
        m = (start.month - 1 + n) % 12 + 1
        return date(y, m, 1).isoformat()

    # 65 contiguous months -- crosses max_ordinal_buckets=60
    many_rows = [{"month": _nth_month(i), "value": i} for i in range(65)]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "orientation": "horizontal",
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                ),
            }
        ),
    )
    ctx = _make_ctx(chart, rows=many_rows)
    assert detector.detect(ctx) == []


def test_silent_for_non_date_x_values_with_todate() -> None:
    """When x-field values are non-date-parseable strings (e.g. 'alpha'),
    _ordinal_bucket_key returns 'alpha' unchanged, which does not match
    _ISO_UTC_SAFE_RE. The any() guard in the ordinal branch is the sole
    gatekeeper -- deleting it would fire a false positive on every bar chart
    with toDate() in the label expr regardless of data shape. This test pins
    the guard is load-bearing."""
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    non_date_rows = [
        {"month": "alpha", "value": 10},
        {"month": "beta", "value": 20},
        {"month": "gamma", "value": 30},
    ]
    ctx = _make_ctx(chart, rows=non_date_rows)
    assert detector.detect(ctx) == []


def test_fires_for_line_with_integer_year_x_auto_detected() -> None:
    """Integer-year x columns (e.g. EXTRACT(YEAR ...)) are a common data shape.
    The emitter calls normalize_labeled_temporal before type resolution, which
    rewrites 2020 → '2020-01-01'. The detector must mirror that preprocessing
    step: without it, infer_vega_type_from_data sees quantitative integers and
    detect_time_unit is never called, so detected_tu stays None and the chart is
    silently skipped -- the original spec's FY-label hazard left open for this
    data shape."""
    year_rows = [
        {"year": 2020, "value": 10},
        {"year": 2021, "value": 20},
        {"year": 2022, "value": 30},
    ]
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="year",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "labels": {"expr": "'FY' + year(datum.value)"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=year_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_bar_with_integer_year_x_and_authored_time_unit_with_todate() -> None:
    """Integer-year x column, authored time_unit: year, toDate() in the expr.
    Without normalize_labeled_temporal preprocessing, _ordinal_bucket_key(2020)
    falls back to str(2020) == '2020', which does not match _ISO_UTC_SAFE_RE
    (requires YYYY-MM or YYYY-MM-DD). After normalization, the value is
    '2020-01-01' which matches -- the real hazard is detected."""
    year_rows = [
        {"year": 2020, "value": 10},
        {"year": 2021, "value": 20},
        {"year": 2022, "value": 30},
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="year",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "year",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=year_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_sparse_bar_after_gap_fill_crosses_density_threshold() -> None:
    """Sparse monthly data with large gaps: 38 distinct rows spanning 75 months.
    Before gap-fill the detector sees 38 distinct buckets (< max_ordinal_buckets=60)
    and resolves to ordinal, then stays SILENT because there is no toDate() in the
    bare timeFormat expr. After gap-fill the emitter synthesizes 75 rows (one per
    missing bucket), which crosses the density gate and resolves to temporal -- the
    same temporal encoding that produces the UTC/local off-by-one. The detector must
    call gap_fill_ordinal_time to mirror the emitter's preprocessing; without it
    this case is a false negative."""
    from datetime import date

    start = date(2019, 1, 1)

    def _nth_month(n: int) -> str:
        y = start.year + (start.month - 1 + n) // 12
        m = (start.month - 1 + n) % 12 + 1
        return date(y, m, 1).isoformat()

    # 38 rows at even month indices over a 75-month span (2019-01 through 2025-03).
    # gap_fill produces 75 rows, which crosses max_ordinal_buckets=60.
    sparse_rows = [{"month": _nth_month(i * 2), "value": i} for i in range(38)]

    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=sparse_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_wide_bar_with_python_date_objects_and_todate() -> None:
    """normalize_scalar_for_json(date(2024,4,1)) returns '2024-04-01', which
    matches _ISO_UTC_SAFE_RE -- so a multi-metric bar over a warehouse DATE
    column (Python date objects) with toDate() in the label expr IS the
    UTC/local hazard and must fire. Mirrors the narrow-bar sibling
    test_fires_for_ordinal_bar_with_python_date_objects_and_todate but with
    y as a list (wide bar), which routes through _emit_wide_bar (before
    gap_fill_ordinal_time) and uses normalize_scalar_for_json on x values."""
    import datetime as dt

    date_object_rows = [
        {"month": dt.date(2024, 4, 1), "val_a": 10, "val_b": 20},
        {"month": dt.date(2024, 5, 1), "val_a": 30, "val_b": 40},
        {"month": dt.date(2024, 6, 1), "val_a": 50, "val_b": 60},
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["val_a", "val_b"],
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=date_object_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_wide_bar_with_datetime_strings_and_todate() -> None:
    """Multi-measure (y: [a, b]) bar charts now go through the same
    gap_fill_ordinal_time -> complete_ordinal_time_series pipeline as a
    regular bar (gap-fill runs before the wide/long-form dispatch in every
    emitter, called with chart.color=None, which degenerates its dim-cross-
    join to a plain per-bucket fill -- there's no separate wide-only
    preprocessing branch left). The x-values are therefore rewritten to
    date-only ISO buckets ('2024-04-15 00:00:00' -> '2024-04-01'), same as a
    regular bar, so toDate() promotes them to a UTC-midnight Date and the
    real hazard is detected -- same as test_fires_for_bar_with_integer_year_x
    (long-form)."""
    wide_bar_rows = [
        {"month": "2024-04-15 00:00:00", "val_a": 10, "val_b": 20},
        {"month": "2024-05-15 00:00:00", "val_a": 30, "val_b": 40},
        {"month": "2024-06-15 00:00:00", "val_a": 50, "val_b": 60},
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["val_a", "val_b"],
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=wide_bar_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_for_step_curve_line_with_bucketed_year_accessor() -> None:
    """A line chart with curve='step' flips a bucketed grain from temporal to
    ordinal in resolve_cartesian_x_type (is_band_step=True → vl_type='ordinal').
    On an ordinal axis datum.value is a string, not a Date. timeFormat on a raw
    ISO string renders 0NaN in both TZs -- wrong, but TZ-invariant. Without
    toDate() there is no UTC-midnight hazard and the detector must be silent.
    This test pins that is_band_step is correctly forwarded from
    chart.style.line_mark.curve; if dropped, the chart resolves to temporal
    and the detector fires a false positive. Uses timeFormat (not year() or
    month()) because those accessors coerce the string internally via new
    Date() and ARE TZ-dependent on date-only ISO domains even without toDate."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                # marks.line.curve is the authored path; resolves to
                # chart.style.line_mark.curve, which the detector reads.
                "marks": {"line": {"curve": "step"}},
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                ),
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_date_part_accessor_on_ordinal_axis_without_todate() -> None:
    """Vega's date-part accessors (year/month/quarter/date/day) do their own
    new Date(...) coercion internally -- they don't need an explicit toDate()
    wrapper to become TZ-dependent. 'M' + month(datum.value) on an ordinal
    axis with date-only ISO strings: month() coerces '2024-04-01' to a Date
    internally, then reads it in local time. Reviewer measured directly:
    ('M3','M4') UTC vs ('M2','M3') PDT -- off by one, no toDate present.
    The detector's toDate() requirement was wrong for non-timeFormat accessors.
    Uses a step-curve line (is_band_step=True → ordinal) so the ordinal branch
    is exercised at a bucket count below the density threshold."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "marks": {"line": {"curve": "step"}},
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "'M' + month(datum.value)"},
                    }
                ),
            }
        ),
    )
    # _ROWS has datetime-space strings; _ordinal_bucket_key rewrites to date-only
    # ISO ('2024-04-15 00:00:00' → '2024-04-15'), which month() coerces via new
    # Date() internally -- no toDate() needed for this accessor.
    ctx = _make_ctx(chart)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_wide_vertical_bar_above_density_threshold() -> None:
    """Vertical wide bar (y: [a, b]) routes through _emit_vertical, the same
    function narrow single-metric bars use, which resolves x through
    the engine's x-axis builder and CAN emit a temporal encoding when the bucket
    count crosses max_ordinal_buckets=60. The detector must NOT force vl_type=
    'ordinal' for wide bars; it must let the density gate decide -- same as a
    narrow bar. Regression: the previous fix force-ordinaled all isinstance(y, list)
    bars unconditionally, making a 65-bucket vertical wide bar with bare
    timeFormat(datum.value, ...) silently miss the real UTC/local hazard."""
    from datetime import date

    start = date(2019, 1, 1)

    def _nth_month(n: int) -> str:
        y = start.year + (start.month - 1 + n) // 12
        m = (start.month - 1 + n) % 12 + 1
        return date(y, m, 1).isoformat()

    # 65 contiguous months -- crosses max_ordinal_buckets=60 without gap-fill.
    wide_many_rows = [
        {"month": _nth_month(i), "val_a": i, "val_b": i * 2} for i in range(65)
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y=["val_a", "val_b"],
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=wide_many_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_fires_for_multi_metric_line_with_step_curve_and_bucketed_accessor() -> None:
    """The multi-metric line emitter (_emit_multi_metric_line at line.py:296)
    calls resolve_cartesian_x() WITHOUT passing curve -- so is_band_step is
    effectively False for multi-metric resolution, regardless of the authored
    curve value. A yearmonth grain on a line chart always resolves to temporal
    when is_band_step=False. The detector was setting is_band_step=True from the
    authored curve for all line charts, making multi-metric step+yearmonth resolve
    to ordinal, silencing year(datum.value) which IS a real hazard. Fix: force
    is_band_step=False when isinstance(chart.y, list) for line/area, mirroring
    what the multi-metric emitter path actually does."""
    chart = LineChart(
        id="c1",
        type="line",
        query_name="q",
        x="month",
        y=["val_a", "val_b"],
        style=LineChartStylePatch.model_validate(
            {
                "marks": {"line": {"curve": "step"}},
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "year(datum.value)"},
                    }
                ),
            }
        ),
    )
    multi_rows = [
        {"month": "2024-04-15 00:00:00", "val_a": 10, "val_b": 20},
        {"month": "2024-05-15 00:00:00", "val_a": 30, "val_b": 40},
        {"month": "2024-06-15 00:00:00", "val_a": 50, "val_b": 60},
    ]
    ctx = _make_ctx(chart, rows=multi_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_silent_for_histogram() -> None:
    """BarEmitter.emit() returns _emit_histogram before any normalize/gap-fill/
    bucketing logic -- histogram emits {"type": "quantitative", "bin": True}, no
    calendar bucketing at all. The detector's isinstance(chart, ResolvedBarChart)
    branch must exclude histogram charts via chart.chart_type == 'histogram':
    otherwise a histogram with an integer-year x and a toDate/accessor expr gets
    warned about 'year bucketing producing UTC-midnight tick values' -- a
    bucketing that chart never does -- with a utcFormat fix suggestion that
    doesn't apply to a quantitative-binned axis. Must stay silent."""
    year_rows = [
        {"year": 2020, "value": 10},
        {"year": 2021, "value": 20},
        {"year": 2022, "value": 30},
    ]
    chart = BarChart(
        id="c1",
        type="histogram",
        query_name="q",
        x="year",
        y=None,
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "year",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=year_rows)
    assert detector.detect(ctx) == []


def test_silent_for_heatmap_not_yet_covered() -> None:
    """Heatmap carries the identical UTC/local off-by-one hazard as ordinal bar:
    toDate("2024-04-01") creates a UTC-midnight Date per the ES spec, and
    timeFormat reads it in host local time. The hazard is real and confirmed.

    This detector does NOT cover heatmap. Its emitter path (normalize_data_types
    vs normalize_labeled_temporal + gap_fill_ordinal_time) diverges structurally
    from bar/line/area. A follow-up task should implement heatmap and scatter
    coverage -- the ContextVar sink pattern (like text_truncations/
    series_label_truncations in render/chart/text_truncation.py) is worth
    evaluating first rather than another hand-mirrored branch.

    The detector must be silent here -- not because the hazard is absent, but
    because this scope boundary is deliberate."""
    heatmap_rows = [
        {"month": "2024-04-01", "cat": "A", "value": 10},
        {"month": "2024-05-01", "cat": "A", "value": 20},
        {"month": "2024-06-01", "cat": "A", "value": 30},
    ]
    chart = HeatmapChart(
        id="c1",
        type="heatmap",
        query_name="q",
        x="month",
        y="cat",
        color="value",
        style=HeatmapChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(toDate(datum.value), '%b %Y')"},
                    }
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=heatmap_rows)
    assert detector.detect(ctx) == []


def test_silent_for_step_curve_area_with_bucketed_year_accessor() -> None:
    """Single-metric area chart with curve='step' on a yearmonth grain: the
    emitter passes style.area_mark.curve into resolve_cartesian_x, so
    is_band_step=True → vl_type='ordinal'. timeFormat on a raw ISO string
    renders 0NaN in both TZs -- TZ-invariant, not the hazard. Must be silent.
    Mirrors the line sibling test_silent_for_step_curve_line_with_bucketed_year_accessor.
    Uses timeFormat (not year()/month()) because date-part accessors coerce the
    string internally via new Date() and ARE TZ-dependent on date-only ISO
    domains even without toDate."""
    chart = AreaChart(
        id="c1",
        type="area",
        query_name="q",
        x="month",
        y="value",
        style=AreaChartStylePatch.model_validate(
            {
                "marks": {"area": {"curve": "step"}},
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "timeFormat(datum.value, '%b %Y')"},
                    }
                ),
            }
        ),
    )
    ctx = _make_ctx(chart)
    assert detector.detect(ctx) == []


def test_fires_for_multi_metric_area_with_step_curve_and_bucketed_accessor() -> None:
    """The multi-metric area emitter (_emit_multi_metric_area at area.py:249)
    calls resolve_cartesian_x() WITHOUT passing curve -- so is_band_step is
    effectively False for multi-metric resolution, regardless of the authored
    curve value. A yearmonth grain on an area chart always resolves to temporal
    when is_band_step=False. The detector forces is_band_step=False when
    isinstance(chart.y, list) for area, mirroring the multi-metric emitter path.
    year(datum.value) on a temporal axis IS the UTC/local hazard -- must fire.
    Mirrors the line sibling test_fires_for_multi_metric_line_with_step_curve_and_bucketed_accessor."""
    chart = AreaChart(
        id="c1",
        type="area",
        query_name="q",
        x="month",
        y=["val_a", "val_b"],
        style=AreaChartStylePatch.model_validate(
            {
                "marks": {"area": {"curve": "step"}},
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "year(datum.value)"},
                    }
                ),
            }
        ),
    )
    multi_rows = [
        {"month": "2024-04-15 00:00:00", "val_a": 10, "val_b": 20},
        {"month": "2024-05-15 00:00:00", "val_a": 30, "val_b": 40},
        {"month": "2024-06-15 00:00:00", "val_a": 50, "val_b": 60},
    ]
    ctx = _make_ctx(chart, rows=multi_rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code in codes


def test_valueerror_from_one_chart_does_not_suppress_other_chart_warning() -> None:
    """When detect_time_unit raises ValueError for chart A (e.g. an impossible
    date string like '2024-02-30' causing ≥10% unparseable), the except ValueError:
    continue guard must skip chart A and continue to chart B -- not let the
    exception propagate and drop chart B's warning from the result. Deleting the
    guard would cause detect() to raise instead of returning chart B's diagnostic."""
    bad_rows = [
        {"x": "2024-01-01", "value": 10},
        # Impossible date -- fromisoformat raises ValueError, _parse_date returns
        # None, detect_time_unit sees 50% unparseable and raises ValueError.
        {"x": "2024-02-30", "value": 20},
    ]
    chart_a = LineChart(
        id="c_bad",
        type="line",
        query_name="q1",
        x="x",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "labels": {"expr": "year(datum.value)"},
                    }
                )
            }
        ),
    )
    # Chart B is an ordinary firing chart: yearmonth line with year() accessor.
    chart_b = LineChart(
        id="c_good",
        type="line",
        query_name="q2",
        x="month",
        y="value",
        style=LineChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {
                        "time_unit": "yearmonth",
                        "labels": {"expr": "year(datum.value)"},
                    }
                )
            }
        ),
    )
    resolved_a = make_test_resolved_chart(chart_a, bad_rows)
    resolved_b = make_test_resolved_chart(chart_b, _ROWS)
    board = make_test_resolved_board(
        charts={resolved_a.id: resolved_a, resolved_b.id: resolved_b}
    )
    ctx = WarningContext(
        board_spec=board,
        chart_results={resolved_a.id: bad_rows, resolved_b.id: _ROWS},
    )
    diags = detector.detect(ctx)
    assert len(diags) == 1
    assert diags[0].chart == "c_good"


def test_silent_for_faceted_chart_whose_panels_stay_ordinal_when_pooled_looks_temporal() -> (
    None
):
    """The detector must gap-fill per panel, mirroring the emitters
    (gap_fill_ordinal_time_per_panel), not the flat gap_fill_ordinal_time on
    every panel's rows pooled together with no panel boundary.

    Two disjoint, densely-populated 6-month windows 20 years apart (region A:
    2000, region B: 2020). Each panel is already dense in its own narrow
    window, so the real per-panel emitter path synthesizes nothing and stays
    well under max_ordinal_buckets=60 -> ordinal -> no encoding.x.timeUnit ->
    timeFormat() without toDate() is a broken-but-harmless expression, not
    the UTC/local hazard. A flat, panel-blind gap-fill has no panel boundary
    to respect and fills the ENTIRE pooled span (2000-01 through 2020-06,
    246 months) with synthetic rows, crossing the density gate to temporal
    and firing a false positive.
    """
    rows = [
        {"month": f"2000-{month:02d}-01", "region": "A", "value": 5}
        for month in range(1, 7)
    ] + [
        {"month": f"2020-{month:02d}-01", "region": "B", "value": 5}
        for month in range(1, 7)
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        multiples=MultiplesConfig(rows="region"),
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"labels": {"expr": "timeFormat(datum.value, '%b %Y')"}}
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code not in codes


def test_silent_for_faceted_dense_daily_panels_on_a_fine_grain() -> None:
    """The faceted sibling of the test above, on a FINE grain — the only
    grains the scaffold-budget gate runs for.

    The existing faceted cases use day-1 dates, which detect as ``yearmonth``
    and never reach the gate, so they leave the detector's own ``panel_fields``
    argument unread. Here two panels of 60 contiguous dailies twenty years
    apart each score a zero-bucket deficit, so the chart stays ordinal and a
    bare ``timeFormat`` is broken-but-TZ-invariant, not the hazard. Measured
    on pooled rows instead, the span scores ~7,200, resolves temporal, and the
    grain-recovery re-key fires a false positive on a chart with no TZ hazard.
    """
    import datetime as dt

    rows = [
        {
            "day": (start + dt.timedelta(days=i)).isoformat(),
            "region": region,
            "value": 5,
        }
        for region, start in (("A", dt.date(2000, 1, 1)), ("B", dt.date(2020, 1, 1)))
        for i in range(60)
    ]
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="day",
        y="value",
        multiples=MultiplesConfig(rows="region"),
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"labels": {"expr": "timeFormat(datum.value, '%b %Y')"}}
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code not in codes


def test_silent_for_faceted_chart_with_interleaved_query_rows_and_labeled_x() -> None:
    """restripe()'s panel re-split must key off each row's recorded original
    index, not its position in the caller's row list — the detector's own
    ``rows`` (``ctx.chart_results``) is query-ordered, so an interleaved
    two-panel query is exactly the shape a POSITIONAL restripe corrupts:
    slicing `normalized[0:n]`/`normalized[n:]` by panel size, not by which
    panel a row actually belongs to, would fold region A's dense 2000
    window and region B's dense 2020 window into two mixed panels each
    spanning 20 years. That corrupted span crosses the ordinal density
    gate, resolves temporal, and fires a false positive; the correct,
    index-based re-split keeps each panel in its own dense 6-month window,
    stays ordinal, and — same as the sibling density test above — the bare
    timeFormat() (no toDate()) is broken-but-TZ-invariant on an ordinal
    axis, so the detector must stay silent.

    Labeled month strings ("Jan 2000", not ISO dates) so
    normalize_labeled_temporal actually rewrites the x values — the exact
    shape restripe() has to re-split correctly.
    """
    rows = []
    for month in ("Jan", "Feb", "Mar", "Apr", "May", "Jun"):
        rows.append({"month": f"{month} 2000", "region": "A", "value": 5})
        rows.append({"month": f"{month} 2020", "region": "B", "value": 5})
    chart = BarChart(
        id="c1",
        type="bar",
        query_name="q",
        x="month",
        y="value",
        multiples=MultiplesConfig(rows="region"),
        style=BarChartStylePatch.model_validate(
            {
                "axis_x": AxisXStylePatch.model_validate(
                    {"labels": {"expr": "timeFormat(datum.value, '%b %Y')"}}
                )
            }
        ),
    )
    ctx = _make_ctx(chart, rows=rows)
    codes = {w.code for w in detector.detect(ctx)}
    assert WARN_LOCAL_TIME_LABEL_EXPR_ON_BUCKETED_AXIS.code not in codes
