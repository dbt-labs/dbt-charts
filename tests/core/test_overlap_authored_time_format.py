"""The x-axis overlap resolver must measure the strings Vega actually paints.

Cadence and tilt are picked before rendering by measuring label widths against
real glyph advances. When ``style.time_format`` (``axis_x.labels.format``) is
authored, Vega applies it to every tick and the smart cadence ``labelExpr`` is
never emitted (``build_cartesian_x_encoding`` gates it on ``"format" not in
result``) — so the per-grain vocabulary the resolver used to measure ("Jan",
"Q1", "2024") is not what gets drawn, and a quarterly cadence chosen for "Jan"
paints "Oct 2023" on top of itself.

These pin the measurement, not a particular rescue strategy: the geometric
assertion below is true of any layout that actually fits.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Any

import pytest

from dbt_charts.core.compile.models.primitives import ResolvedFormat


def _monthly_rows_with_gaps(count: int) -> list[str]:
    """Monthly first-of-month dates with three months missing.

    The gaps are load-bearing: a complete series lands the cadence ladder on
    yearly labels, which fit either way. Dropping months shrinks the opener
    count enough to flip the ladder to quarterly — where the mismeasurement
    shows.
    """
    dropped = {(2022, 5), (2023, 8), (2024, 3)}
    values: list[str] = []
    year, month = 2022, 1
    while len(values) < count:
        if (year, month) not in dropped:
            values.append(f"{year:04d}-{month:02d}-01")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return values


def _axis(
    time_format: str | None = None,
    *,
    skip: bool = True,
    tilt: bool = True,
) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", "temporal", chart_type="", label_authored=False
    )
    labels = dataclasses.replace(
        axis_x.labels,
        overlap=dataclasses.replace(axis_x.labels.overlap, skip=skip, tilt=tilt),
        angle=None,
        format=None if time_format is None else ResolvedFormat(spec=time_format),
    )
    return dataclasses.replace(axis_x, labels=labels)


def _painted_label_clearance(
    layout: Any, axis: Any, values: list[str], chart_width: float
) -> tuple[float, float]:
    """(widest painted label, spacing between neighboring painted labels).

    Measures the text Vega really draws under an authored format — the format
    applied to each visible tick — against the distance between those ticks.
    """
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.chart.time_unit_detect import is_label_opener
    from dbt_charts.core.text.format_d3 import portable_strftime

    dates = [datetime.date.fromisoformat(value) for value in values]
    encoding_tu = "yearmonth"
    visibility = layout.visibility_time_unit or encoding_tu
    visible = [
        (i, d)
        for i, d in enumerate(dates)
        if is_label_opener(d, encoding_tu, visibility, axis.fiscal_year_start_month)
    ]
    font = axis.labels.font
    measurer = get_font_measurer(font.family)
    widest = max(
        measurer.measure(portable_strftime(d, axis.labels.format.spec), font.size)
        for _, d in visible
    )
    band = chart_width * 0.9 / len(dates)
    spacing = min(
        (right - left) * band
        for (left, _), (right, _) in zip(visible, visible[1:], strict=False)
    )
    return widest, spacing


def test_authored_time_format_changes_the_resolved_layout() -> None:
    """The root cause, stated directly: without the authored format threaded
    into the measurement these two collapse to the same answer, because the
    measurement never sees the format at all."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    values = _monthly_rows_with_gaps(37)
    data: list[dict[str, Any]] = [{"month": v, "y": 1} for v in values]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 420.0,
    }

    bare = resolve_axis_x_overlap(
        _axis(), "month", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _axis("%b %Y"), "month", data, **kwargs, continuous_temporal=False
    )

    assert bare != formatted


def test_wide_authored_format_does_not_leave_labels_overprinting() -> None:
    """A month-and-year label is roughly two and a half times the width of the
    bare month vocabulary, so measuring the latter picks a cadence the real
    labels overrun. The assertion below measures both for real rather than
    trusting those proportions."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    values = _monthly_rows_with_gaps(37)
    data: list[dict[str, Any]] = [{"month": v, "y": 1} for v in values]
    axis = _axis("%b %Y")
    layout = resolve_axis_x_overlap(
        axis,
        "month",
        data,
        label_usable_ratio=0.9,
        edge_labels_flushed=False,
        chart_width=420.0,
        continuous_temporal=False,
    )
    # A tilted or Vega-thinned layout is a legitimate rescue; only an
    # untilted, unthinned cadence has to clear on width alone.
    if layout.angle or layout.label_overlap != "allow":
        return
    widest, spacing = _painted_label_clearance(layout, axis, values, 420.0)
    assert widest <= spacing, (
        f"{layout.visibility_time_unit} cadence paints {widest:.1f}px labels "
        f"every {spacing:.1f}px"
    )


@pytest.mark.parametrize(
    ("time_format", "expected_text"),
    [
        ("%b %Y", "Jan 2022"),
        ("%Y", "2022"),
        ("%B", "January"),
        ("%q", "1"),  # quarter number, not the letter "q"
        ("%L", "000"),  # milliseconds, not the letter "L"
        ("%Q", "1640995200000"),  # epoch ms, not the letter "Q"
    ],
)
def test_the_label_vocabulary_speaks_the_authored_format_at_every_grain(
    time_format: str, expected_text: str
) -> None:
    """``yearquarter`` would say "Q1"; under an authored format every grain
    says what the format says instead."""
    from dbt_charts.core.render.chart.time_unit_detect import cadence_label_text

    assert (
        cadence_label_text(
            datetime.date(2022, 1, 1), "yearquarter", authored_format=time_format
        )
        == expected_text
    )


def test_tilt_is_picked_against_the_authored_format_width() -> None:
    """The tilt ladder is the fix's second measurement site, and the cadence
    ladder can't stand in for it: this domain is too short for the coarsen
    ladder to reach ``year`` at all, so tilt is the only strategy left, and
    the angle it picks must come from the painted label's width."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"month": f"2022-{month:02d}-01", "y": 1} for month in range(2, 13)
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 200.0,
    }
    bare = resolve_axis_x_overlap(
        _axis(), "month", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _axis("%B %Y"), "month", data, **kwargs, continuous_temporal=False
    )

    assert bare.angle == 0.0
    assert formatted.angle is not None and formatted.angle < 0.0
    # The tilted block reserves the widest painted label's own width; a bare
    # "Apr" would reserve a fraction of that.
    assert formatted.label_block_height > bare.label_block_height * 3


def test_daily_data_with_an_authored_format_drops_the_two_row_day_shape() -> None:
    """The sub-month branches model ``_day_label``'s stacked day-over-month
    rows. An authored ``format`` suppresses the labelExpr that paints them,
    so those branches must not decide this axis."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    start = datetime.date(2024, 1, 1)
    data: list[dict[str, Any]] = [
        {"day": (start + datetime.timedelta(days=i)).isoformat(), "y": 1}
        for i in range(90)
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 400.0,
    }
    bare = resolve_axis_x_overlap(
        _axis(), "day", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _axis("%d/%m"), "day", data, **kwargs, continuous_temporal=False
    )

    assert bare.format_time_unit == "yearweek"
    assert formatted.format_time_unit != "yearweek"


def test_collision_warning_fires_when_the_authored_format_cannot_fit() -> None:
    """With both rescue strategies off, a format too wide for the monthly
    cadence has nowhere to go — and must say so. The bare vocabulary at the
    same width fits, so this is the authored format's collision, not the
    axis's."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    values = _monthly_rows_with_gaps(37)
    data: list[dict[str, Any]] = [{"month": v, "y": 1} for v in values]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 900.0,
    }

    bare = resolve_axis_x_overlap(
        _axis(skip=False, tilt=False),
        "month",
        data,
        **kwargs,
        continuous_temporal=False,
    )
    assert bare.collision_label_count is None

    formatted = resolve_axis_x_overlap(
        _axis("%B %Y", skip=False, tilt=False),
        "month",
        data,
        **kwargs,
        continuous_temporal=False,
    )
    assert formatted.collision_label_count == len(values)


def _ordinal_axis(time_format: str | None) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", "ordinal", chart_type="", label_authored=False
    )
    return dataclasses.replace(
        axis_x,
        labels=dataclasses.replace(
            axis_x.labels,
            angle=None,
            format=None if time_format is None else ResolvedFormat(spec=time_format),
        ),
    )


def test_ordinal_bucket_strings_measure_the_authored_format_too() -> None:
    """The same root cause reached by the other door. ``2022-01`` infers as
    ordinal, not temporal, so it lands on the discrete branch — but
    ``build_cartesian_x_encoding`` still routes an authored time format on a
    date-shaped ordinal axis into ``utcFormat(toDate(datum.value), fmt)``.
    Confirmed against a real render: that axis paints "Jan 2022", while the
    resolver measured the raw "2022-01"."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    values = [f"{2022 + i // 12:04d}-{i % 12 + 1:02d}" for i in range(37)]
    data: list[dict[str, Any]] = [{"month": v, "y": 1} for v in values]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 420.0,
    }

    bare = resolve_axis_x_overlap(
        _ordinal_axis(None), "month", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _ordinal_axis("%b %Y"), "month", data, **kwargs, continuous_temporal=False
    )
    # Both tilt to vertical, so the fit verdict is the same; the block height
    # is what differs — it reserves the widest label's own width at -90, and
    # "Jan 2022" is wider than "2022-01".
    assert formatted.label_block_height > bare.label_block_height


def test_non_date_ordinal_values_keep_their_own_text() -> None:
    """A time format on genuinely nominal values paints "Invalid Date" in
    Vega — a defect in the emission, not a width to invent a number for. The
    measurement leaves those bands as their own text."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"region": name, "y": 1} for name in ("North", "South", "East", "West")
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 420.0,
    }
    bare = resolve_axis_x_overlap(
        _ordinal_axis(None), "region", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _ordinal_axis("%b %Y"), "region", data, **kwargs, continuous_temporal=False
    )
    assert bare == formatted


def _pinned_axis(time_format: str | None, channel_type: str = "temporal") -> Any:
    """An axis with an author-pinned tilt — the one path that skips the
    ladders entirely and measures via ``_pinned_angle_block_height``."""
    axis = (
        _axis(time_format) if channel_type == "temporal" else _ordinal_axis(time_format)
    )
    return dataclasses.replace(
        axis, labels=dataclasses.replace(axis.labels, angle=-45.0)
    )


def test_pinned_tilt_reserves_the_authored_format_not_the_grain_vocabulary() -> None:
    """``labels.angle`` short-circuits both ladders, so nothing else measures
    this axis. Its block height sizes the gap a bottom support-table strip
    leaves under the labels — under-reserve it and the strip clips them."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"month": f"2022-{month:02d}-01", "y": 1} for month in range(1, 13)
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 300.0,
    }
    bare = resolve_axis_x_overlap(
        _pinned_axis(None), "month", data, **kwargs, continuous_temporal=False
    )
    formatted = resolve_axis_x_overlap(
        _pinned_axis("%B %Y"), "month", data, **kwargs, continuous_temporal=False
    )

    assert bare.angle == formatted.angle == -45.0
    assert formatted.label_block_height > bare.label_block_height * 2


def test_pinned_tilt_closes_the_ordinal_door_too() -> None:
    """Same axis, same format, differing only in that the values are bucket
    strings — which infer ordinal and so never reach the temporal branch."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"month": f"2022-{month:02d}", "y": 1} for month in range(1, 13)
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 300.0,
    }
    bare = resolve_axis_x_overlap(
        _pinned_axis(None, "ordinal"),
        "month",
        data,
        **kwargs,
        continuous_temporal=False,
    )
    formatted = resolve_axis_x_overlap(
        _pinned_axis("%B %Y", "ordinal"),
        "month",
        data,
        **kwargs,
        continuous_temporal=False,
    )

    # "January 2022" is wider than the "2022-01" datum it is painted over.
    assert formatted.label_block_height > bare.label_block_height


def test_a_calendar_invalid_iso_shaped_band_is_not_reformatted() -> None:
    """``2023-02-30`` is ISO-shaped and is not a date; Vega's ``toDate``
    rejects it, so the measurement must leave it alone rather than crash
    trying to render it."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"x": "2023-02-30", "y": 1},
        {"x": "North", "y": 2},
    ]
    kwargs: dict[str, Any] = {
        "label_usable_ratio": 0.9,
        "edge_labels_flushed": False,
        "chart_width": 400.0,
    }
    # Not merely "does not raise": neither band is reformatted, so the
    # authored format changes nothing about this axis.
    assert resolve_axis_x_overlap(
        _ordinal_axis("%b %Y"), "x", data, **kwargs, continuous_temporal=False
    ) == resolve_axis_x_overlap(
        _ordinal_axis(None), "x", data, **kwargs, continuous_temporal=False
    )
