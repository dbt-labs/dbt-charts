"""End-to-end: the warnings the engine measures reach `RenderResult.warnings`
from a real chart, so deleting an emitting branch fails a test."""

from __future__ import annotations

import pytest

from .._svg_render import render_board_svg_result


def _warning_codes(chart: str, rows: list[str]) -> list[str]:
    board = "title: T\nqueries:\n  q:\n    type: values\n    rows:\n"
    board += "".join(f"      - {row}\n" for row in rows)
    board += f"charts:\n  c:\n    query: q\n{chart}\nrows:\n  - c\n"
    result = render_board_svg_result(board)
    assert not result.chart_errors
    return [w.code for w in result.warnings]


def test_grouped_bars_on_a_continuous_time_axis_warn_that_the_series_coincide() -> None:
    rows = [
        f"{{d: '2020-{1 + i // 3:02}-{1 + (i * 7) % 27:02}', s: s{s}, v: {i + s}}}"
        for i in range(36)
        for s in range(2)
    ]
    chart = (
        "    type: bar\n    x: d\n    y: v\n    color: s\n    style:\n      stack: none"
    )
    assert "WARN-BAR-GROUPED-SERIES-COINCIDE" in _warning_codes(chart, rows)


def test_grouped_bars_on_a_numeric_x_dodge_and_stay_quiet() -> None:
    rows = [f"{{x: {x}, s: s{s}, v: {x + s}}}" for x in range(12) for s in range(3)]
    chart = (
        "    type: bar\n    x: x\n    y: v\n    color: s\n    style:\n      stack: none"
    )
    assert "WARN-BAR-GROUPED-SERIES-COINCIDE" not in _warning_codes(chart, rows)


def test_a_cut_chart_title_is_reported_once_per_chart() -> None:
    rows = [f"{{x: {x}, v: {x}}}" for x in range(6)]
    chart = (
        "    type: scatter\n    x: x\n    y: v\n    width: 200\n"
        "    title: A very long chart title that cannot fit a 200px card at all"
    )
    codes = _warning_codes(chart, rows)
    assert codes.count("WARN-CHART-TITLE-TRUNCATED") == 1
    assert "WARN-CHART-TITLE-TRUNCATED" in codes


def test_bars_wider_than_their_numeric_x_step_warn_about_the_band_width() -> None:
    rows = [f"{{x: {x}.{d}, v: {x}}}" for x in range(3) for d in range(5)]
    chart = (
        "    type: bar\n    x: x\n    y: v\n"
        "    style:\n      marks:\n        bar:\n          size: 40"
    )
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" in _warning_codes(chart, rows)


@pytest.mark.parametrize("family", ["line", "area"])
def test_crowded_value_labels_warn_on_line_and_area(family: str) -> None:
    rows = [f"{{c: cat{i:02d}, v: 7.46761}}" for i in range(40)]
    chart = (
        f"    type: {family}\n    x: c\n    y: v\n"
        "    style:\n      marks:\n        line:\n          labels:\n            visible: true"
    )
    assert "WARN-VALUE-LABELS-CROWD-WIDTH" in _warning_codes(chart, rows)


@pytest.mark.parametrize(
    "layout",
    ["  - cols: [c, d, d, d]\n  - c\n", "  - c\n  - cols: [c, d, d, d]\n"],
    ids=["narrow-then-wide", "wide-then-narrow"],
)
def test_a_chart_placed_twice_is_judged_at_its_narrowest_placement(
    layout: str,
) -> None:
    rows = "".join(
        f"      - {{x: {x}.{d}, v: {x}}}\n" for x in range(3) for d in range(5)
    )
    board = (
        "title: T\nqueries:\n  q:\n    type: values\n    rows:\n"
        + rows
        + "charts:\n  c:\n    query: q\n    type: bar\n    x: x\n    y: v\n"
        "    style:\n      marks:\n        bar:\n          size: 40\n"
        "  d:\n    query: q\n    type: bar\n    x: x\n    y: v\n"
        "rows:\n" + layout
    )
    codes = [w.code for w in render_board_svg_result(board).warnings]
    assert "WARN-BAR-BAND-WIDTH-TOO-NARROW" in codes
