"""A daily axis is labeled by its calendar span, not by which days have rows.

Sparse daily series (quiet days produce no row) used to lose the month-opener
rung whenever no row landed on the 1st, falling to one rotated label and one
gridline per day. The same date range must lay out the same either way.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_STYLE, _CTX = resolve_style_and_context(get_theme_style())
_CHART_ADAPTER: TypeAdapter[Chart] = TypeAdapter(Chart)
_HALF_WIDTH_CARD = 560.0


def _rows(skip_every: int | None) -> list[dict[str, Any]]:
    first = dt.date(2026, 7, 27)
    days = [first + dt.timedelta(days=i) for i in range(66)]
    if skip_every:
        days = [d for i, d in enumerate(days) if i % skip_every and d.day != 1]
    return [{"d": d.isoformat(), "v": i + 1} for i, d in enumerate(days)]


def _x_axis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chart = _CHART_ADAPTER.validate_python(
        {"id": "sparse_daily", "type": "area", "x": "d", "y": "v"}
    )
    resolved = resolve(chart, rows, chart_style_context=_CTX)
    spec = render_resolved_chart(
        resolved, rows, _STYLE, width=_HALF_WIDTH_CARD, height=300.0
    ).payload
    return spec["encoding"]["x"]["axis"]


@pytest.mark.parametrize("skip_every", [None, 3])
def test_daily_area_axis_steps_to_weekly_day_numbers(skip_every: int | None) -> None:
    """66 days on a half-width card: Monday day numbers over month context,
    flat, with ticks only at the weeks — full or sparse alike."""
    axis = _x_axis(_rows(skip_every))
    assert axis["labelAngle"] == 0
    weeks = axis["values"]
    assert 8 <= len(weeks) <= 11
    assert {dt.date.fromisoformat(w[:10]).weekday() for w in weeks} == {0}
    assert "tickCount" not in axis


def test_ordinal_sparse_daily_axis_keeps_per_row_bands() -> None:
    """An ordinal axis has one band per row: a month opener with no row has
    nothing to label, so the span must not resolve a month cadence there."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    axis = resolved_axis_style(
        resolve_chart_style_context(get_theme_style()),
        "axis_x",
        "temporal",
        chart_type="",
        label_authored=False,
    )
    layout = resolve_axis_x_overlap(
        axis,
        "d",
        _rows(3),
        label_usable_ratio=0.9,
        bucket_aligned_temporal=False,
        edge_labels_flushed=False,
        continuous_temporal=False,
        chart_width=_HALF_WIDTH_CARD,
    )
    assert layout.visibility_time_unit != "yearmonth"
