"""A sub-day temporal x's tooltip header names the time of day while the data
span is short (48 hours or less), and keeps the date-only header beyond it."""

from __future__ import annotations

import datetime as dt
import re

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.render.chart.emitters._tooltip import ROLE_HEADER
from dbt_charts.core.render.chart.time_unit_detect import (
    subday_header_expr,
    subday_header_for,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec


@pytest.fixture(autouse=True)
def reset():
    reset_config()
    yield
    reset_config()


def _headers(rows: list[dict[str, object]], clock: int | None = None) -> list[str]:
    style = {"axis_x": {"labels": {"clock": clock}}} if clock else None
    chart = LineChart.model_validate(
        {"id": "t", "type": "line", "x": "ts", "y": "v", "style": style}
    )
    svg = vlc.vegalite_to_svg(generate_vega_lite_spec(chart, rows, width=600))
    labels = re.findall(r'aria-label="([^"]+)"', svg)
    return [
        lb.lstrip(ROLE_HEADER).split(";")[0].strip()
        for lb in labels
        if lb.startswith(ROLE_HEADER)
    ]


def _rows(
    start: dt.datetime, step: dt.timedelta, count: int
) -> list[dict[str, object]]:
    return [{"ts": (start + step * i).isoformat(), "v": i} for i in range(count)]


_MORNING = dt.datetime(2026, 3, 2, 9, 5)


def test_one_day_header_is_the_time_alone() -> None:
    headers = _headers(_rows(_MORNING, dt.timedelta(minutes=4), 31))
    assert headers[0] == "9:05am"
    assert headers[-1] == "11:05am"


def test_24_hour_clock_header() -> None:
    headers = _headers(_rows(_MORNING, dt.timedelta(minutes=4), 31), clock=24)
    assert headers[0] == "09:05"


def test_header_across_midnight_names_the_date_and_the_time() -> None:
    start = dt.datetime(2026, 3, 2, 18, 0)
    headers = _headers(_rows(start, dt.timedelta(hours=1), 13))
    assert headers[0] == "2 Mar 2026, 6:00pm"
    assert headers[-1] == "3 Mar 2026, 6:00am"


def test_header_keeps_the_time_at_exactly_48_hours() -> None:
    headers = _headers(_rows(dt.datetime(2026, 3, 2, 0, 0), dt.timedelta(hours=1), 49))
    assert headers[1] == "2 Mar 2026, 1:00am"


def test_header_drops_the_time_beyond_48_hours() -> None:
    headers = _headers(_rows(dt.datetime(2026, 3, 2, 0, 0), dt.timedelta(hours=1), 50))
    assert set(headers) <= {"2 Mar 2026", "3 Mar 2026", "4 Mar 2026"}


def _layered_headers(layer_type: str, *, own_query: bool) -> set[str]:
    """Header strings of a sub-day bar chart with one overlay layer, emitted and
    run through the default feature pipeline like a real render."""
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter
    from dbt_charts.core.render.chart.feature import FeaturePipeline
    from dbt_charts.core.render.chart.features import DEFAULT_FEATURES
    from dbt_charts.core.render.chart.translate import translate_to_vl

    from ...test_layers_on_cartesian import (
        _DEFAULT_BOX,
        _bar_normalized,
        _default_board_style,
        _layer_ta,
        regroup,
    )

    base = [
        {"month": r["ts"], "revenue": r["v"], "target": 0}
        for r in _rows(dt.datetime(2026, 3, 2, 18, 0), dt.timedelta(hours=1), 13)
    ]
    goals = [
        {"month": r["ts"], "target": r["v"]}
        for r in _rows(dt.datetime(2026, 3, 2, 18, 0), dt.timedelta(hours=1), 5)
    ]
    layer = _layer_ta.validate_python(
        {"type": layer_type, "y": "target", **({"query": "goals"} if own_query else {})}
    )
    resolved = resolve(
        _bar_normalized(y="revenue", layers=[layer]), base, _default_board_style()
    )
    datasets = {"q": base, **({"goals": goals} if own_query else {})}
    spec = BarEmitter().emit(
        resolved, _DEFAULT_BOX, regroup((), base), datasets=datasets
    )
    spec = FeaturePipeline(list(DEFAULT_FEATURES)).apply(
        spec, resolved, _DEFAULT_BOX, datasets
    )
    svg = vlc.vegalite_to_svg(translate_to_vl(spec))
    return {
        lb.lstrip(ROLE_HEADER).split(";")[0].strip()
        for lb in re.findall(r'aria-label="([^"]+)"', svg)
        if lb.startswith(ROLE_HEADER)
    }


def test_chart_without_own_query_layer_keeps_the_time_header() -> None:
    headers = _layered_headers("line", own_query=False)
    assert "2 Mar 2026, 6:00pm" in headers


def test_own_query_layer_keeps_every_header_date_only() -> None:
    """The JS folds marks into one bubble by header-string equality, and a layer
    with its own rows can span a different range than the base. So once any
    layer has a query of its own, no header on the chart names the time."""
    headers = _layered_headers("line", own_query=True)
    assert headers
    assert all(":" not in h for h in headers), headers


def test_scatter_layer_with_its_own_query_renders() -> None:
    assert _layered_headers("scatter", own_query=True)


def test_header_names_seconds_when_the_data_has_them() -> None:
    rows = _rows(_MORNING, dt.timedelta(seconds=30), 31)
    assert _headers(rows)[1] == "9:05:30am"
    assert _headers(rows, clock=24)[1] == "09:05:30"


def test_header_of_offset_data_reads_the_utc_clock() -> None:
    """Timestamps carrying an offset are read in UTC, as the axis labels are,
    not in the viewer's zone. The renderer's own zone is UTC, so only the
    emitted accessor can tell the two apart."""
    start = dt.datetime(2026, 3, 2, 9, 5, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    values = [r["ts"] for r in _rows(start, dt.timedelta(minutes=4), 31)]
    header = subday_header_for(values, None)
    assert header is not None
    assert header.utc
    expr = subday_header_expr("datum.x", header)
    assert "utcFormat" in expr
    assert "timeFormat" not in expr
    naive = subday_header_for(
        [r["ts"] for r in _rows(_MORNING, dt.timedelta(minutes=4), 31)], None
    )
    assert naive is not None
    assert "utcFormat" not in subday_header_expr("datum.x", naive)
