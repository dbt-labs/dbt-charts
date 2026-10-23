"""The x-axis overlap resolver must measure the strings Vega actually paints.

Cadence and tilt are picked before rendering by measuring label widths against
real glyph advances. When ``style.time_format`` (``axis_x.labels.format``) is
authored, Vega applies it to every tick and the smart cadence ``labelExpr`` is
never emitted (the engine's x-axis builder gates it on ``"format" not in
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
        charts, "axis_x", "temporal", chart_type="", labeled=False
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


def _ordinal_axis(time_format: str | None) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", "ordinal", chart_type="", labeled=False
    )
    return dataclasses.replace(
        axis_x,
        labels=dataclasses.replace(
            axis_x.labels,
            angle=None,
            format=None if time_format is None else ResolvedFormat(spec=time_format),
        ),
    )


def _pinned_axis(time_format: str | None, channel_type: str = "temporal") -> Any:
    """An axis with an author-pinned tilt — the one path that skips the
    ladders entirely and measures via ``_pinned_angle_block_height``."""
    axis = (
        _axis(time_format) if channel_type == "temporal" else _ordinal_axis(time_format)
    )
    return dataclasses.replace(
        axis, labels=dataclasses.replace(axis.labels, angle=-45.0)
    )
