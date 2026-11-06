"""An annotation anchor outside the data widens the axis domain: ticks follow it
and the annotation draws, on every family and axis kind that can widen. A range
on a line or area x never widens it: the band is clipped at the plot edge."""

from __future__ import annotations

import datetime
import re

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.session import BoardRenderSession

from ...._results import rows_result
from ..._svg_render import path_vertices

_MONTHS = [
    {"month": f"{2024 + i // 12}-{i % 12 + 1:02d}-01", "revenue": 10.0 + i}
    for i in range(24)
]
_STEPS = [{"step": i, "val": 10.0 + 3 * i} for i in range(20)]
_NOTE = "marker"


def _labels(
    family: str,
    rows: list[dict[str, object]],
    x: str,
    anchor: dict[str, object],
    kind: str = "rule",
    rendered_at: datetime.datetime = datetime.datetime(
        2024, 6, 14, tzinfo=datetime.UTC
    ),
) -> tuple[list[str], str]:
    spec: dict[str, object] = {
        "type": family,
        "query": "q",
        "x": x,
        "y": "revenue" if x == "month" else "val",
        "annotations": [{"type": kind, "text": _NOTE, **anchor}],
    }
    style, ctx = resolve_style_and_context(get_theme_style(get_default_theme_name()))
    resolved = resolve(
        normalize_chart("c1", spec, {"q": ValuesQuery(rows=rows)}, sources={}),
        rows,
        chart_style_context=ctx,
        rendered_at=rendered_at,
    )
    svg = BoardRenderSession(style).render_svg_family(
        resolved, rows_result(rows), width=600.0, height=300.0
    )
    assert svg is not None
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg), svg


@pytest.mark.parametrize("family", ["line", "area", "bar", "scatter"])
def test_a_date_past_the_data_gets_ticks_across_the_widened_domain(
    family: str,
) -> None:
    labels, _ = _labels(family, _MONTHS, "month", {"x": "2030-01-01"})
    assert _NOTE in labels
    # 2030-01-01 ends the domain: its slice is empty (or, on bars, one bucket too
    # thin for its label), so the last labeled year is 2029.
    for year in ("2024", "2026", "2028", "2029"):
        assert year in labels, f"{year} missing: {labels}"
    assert "2030" not in labels


@pytest.mark.parametrize("family", ["bar", "scatter"])
def test_a_band_before_the_data_gets_ticks_across_the_widened_domain(
    family: str,
) -> None:
    labels, _ = _labels(
        family,
        _MONTHS,
        "month",
        {"x": {"from": "2020-01-01", "to": "2021-01-01"}},
        kind="band",
    )
    for year in ("2020", "2022", "2024"):
        assert year in labels, f"{year} missing: {labels}"


@pytest.mark.parametrize("family", ["line", "area"])
def test_a_band_before_the_data_is_clipped_not_widened_on_a_line(
    family: str,
) -> None:
    labels, _ = _labels(
        family,
        _MONTHS,
        "month",
        {"x": {"from": "2020-01-01", "to": "2021-01-01"}},
        kind="band",
    )
    assert "2020" not in labels, labels
    assert "2024" in labels, labels


def test_a_one_day_band_on_the_last_day_of_a_daily_line_shows_its_left_half() -> None:
    days = [{"month": f"2024-06-{d:02d}", "revenue": 10.0 + d} for d in range(1, 11)]
    _, svg = _labels(
        "line",
        days,
        "month",
        {"x": {"from": "2024-06-10", "to": "2024-06-11"}},
        kind="band",
    )
    line = re.search(r'<path d="(M[^"]*)"[^>]*stroke-width="4"', svg)
    assert line is not None
    xs = [x for x, _ in path_vertices(line.group(1))]
    step = xs[1] - xs[0]
    band = re.search(
        r'<rect x="([\d.]+)" y="0" width="([\d.]+)" height="[\d.]+" fill="[^"]*" opacity',
        svg,
    )
    assert band is not None
    x, width = float(band.group(1)), float(band.group(2))
    assert x == pytest.approx(xs[-1] - step / 2, abs=0.05)
    assert width == pytest.approx(step / 2, abs=0.05)


@pytest.mark.parametrize("family", ["line", "area", "bar"])
def test_a_clock_anchor_past_the_data_widens_the_domain(family: str) -> None:
    labels, _ = _labels(
        family,
        _MONTHS,
        "month",
        {"x": "now()"},
        rendered_at=datetime.datetime(2030, 1, 5, tzinfo=datetime.UTC),
    )
    assert _NOTE in labels
    # The domain reaches 2030-01-05; that four-day slice cannot hold "2030", and
    # only the widening yields a 2029 label (the data ends in 2025).
    assert "2029" in labels
    assert "2030" not in labels


def test_a_numeric_line_x_past_the_data_widens_the_domain() -> None:
    labels, _ = _labels("line", _STEPS, "step", {"x": 24})
    assert _NOTE in labels
    assert "20" in labels


def _bar_centers(svg: str) -> dict[str, float]:
    bars = re.findall(
        r'<path d="([^"]*)"[^>]*data-dct-role="bar" aria-label="\W*(\w+ \d{4});',
        svg,
    )
    centers = {}
    for d, month in bars:
        xs = [x for x, _ in path_vertices(d)]
        centers[month] = (min(xs) + max(xs)) / 2
    return centers


def _assert_even_slots(centers: dict[str, float]) -> None:
    xs = sorted(centers.values())
    steps = [b - a for a, b in zip(xs, xs[1:], strict=False)]
    assert max(steps) - min(steps) < 0.1, f"uneven bar slots: {steps}"


@pytest.mark.parametrize("day", ["2024-06-01", "2025-12-01", "2024-01-01"])
def test_a_date_inside_the_bars_anchors_on_its_own_slot(day: str) -> None:
    _, svg = _labels("bar", _MONTHS, "month", {"x": day})
    centers = _bar_centers(svg)
    assert len(centers) == 24
    _assert_even_slots(centers)
    rule = re.search(r'annotations-marks.*?<line x1="([\d.]+)"', svg)
    assert rule is not None
    month = datetime.date.fromisoformat(day).strftime("%b %Y")
    assert float(rule.group(1)) == pytest.approx(centers[month], abs=0.01)


@pytest.mark.parametrize(
    "anchor",
    [
        {"type": "note", "x": "2025-12-01", "y": 30, "connector": "arrow"},
        {"type": "trend", "fit": "linear()"},
    ],
)
def test_an_in_range_anchor_inserts_no_bar_slot(anchor: dict[str, object]) -> None:
    kind = str(anchor.pop("type"))
    _, svg = _labels("bar", _MONTHS, "month", anchor, kind=kind)
    centers = _bar_centers(svg)
    assert len(centers) == 24
    _assert_even_slots(centers)


def test_a_y_rule_keeps_the_date_bar_label_cadence() -> None:
    labels, _ = _labels("bar", _MONTHS, "month", {"y": 25})
    quarters = [t for t in labels if re.fullmatch(r"Q[1-4]", t)]
    assert quarters == ["Q1", "Q2", "Q3", "Q4"] * 2


def test_a_widened_numeric_line_ticks_at_round_steps() -> None:
    labels, _ = _labels("line", _STEPS, "step", {"x": 40})
    ticks = [t for t in labels if t.isdigit() and int(t) <= 40]
    x_ticks = [t for t in ticks if int(t) % 5 == 0]
    assert "21" not in labels and "39" not in labels
    assert {"0", "20", "40"} <= set(x_ticks)
