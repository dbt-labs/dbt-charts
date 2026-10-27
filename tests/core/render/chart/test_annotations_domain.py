"""An annotation anchor outside the data widens the axis domain: ticks follow it
and the annotation draws, on every family and axis kind that can widen."""

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
        resolved, rows, width=600.0, height=300.0
    )
    assert svg is not None
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg), svg


@pytest.mark.parametrize("family", ["line", "area", "bar", "scatter"])
def test_a_date_past_the_data_gets_ticks_across_the_widened_domain(
    family: str,
) -> None:
    labels, _ = _labels(family, _MONTHS, "month", {"x": "2030-01-01"})
    assert _NOTE in labels
    for year in ("2024", "2026", "2028", "2030"):
        assert year in labels, f"{year} missing: {labels}"


@pytest.mark.parametrize("family", ["line", "area", "bar", "scatter"])
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
    assert "2030" in labels


def test_a_numeric_line_x_past_the_data_widens_the_domain() -> None:
    labels, _ = _labels("line", _STEPS, "step", {"x": 24})
    assert _NOTE in labels
    assert "20" in labels


def _bar_centers(svg: str) -> dict[str, float]:
    bars = re.findall(
        r'<path d="M ([\d.]+),[^"]* ([\d.]+),[\d.]+ V [\d.]+ Z"[^>]*data-dct-role="bar" '
        r'aria-label="\W*(\w+ \d{4});',
        svg,
    )
    return {month: (float(lo) + float(hi)) / 2 for lo, hi, month in bars}


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
    months = [t for t in labels if t in ("Jan", "Feb", "Mar", "Apr", "Jul", "Oct")]
    assert months == ["Jan", "Apr", "Jul", "Oct"] * 2


def test_a_widened_numeric_line_ticks_at_round_steps() -> None:
    labels, _ = _labels("line", _STEPS, "step", {"x": 40})
    ticks = [t for t in labels if t.isdigit() and int(t) <= 40]
    x_ticks = [t for t in ticks if int(t) % 5 == 0]
    assert "21" not in labels and "39" not in labels
    assert {"0", "20", "40"} <= set(x_ticks)
