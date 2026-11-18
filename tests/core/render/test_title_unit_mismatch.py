"""WARN-TITLE-UNIT-MISMATCH: a title or subtitle that names a unit other than
the compacted value axis's. A title that names no unit never warns."""

from __future__ import annotations

import pytest

from .._svg_render import render_board_svg_result

CODE = "WARN-TITLE-UNIT-MISMATCH"
DOLLAR_K = "        labels:\n          format: currency_compact"


def _board(chart: str, values: list[float]) -> str:
    rows = "".join(f"      - [Q{i}, {v}]\n" for i, v in enumerate(values))
    return (
        "queries:\n  q:\n    type: values\n    columns: [q, v]\n    values:\n"
        f"{rows}charts:\n  c:\n    query: q\n    type: line\n    x: q\n    y: v\n"
        f"{chart}\nrows:\n  - c\n"
    )


def _unit_warnings(chart: str, values: list[float] | None = None) -> list[str]:
    result = render_board_svg_result(_board(chart, values or [212000, 388000, 412000]))
    assert not result.chart_errors
    return [w.message for w in result.warnings if w.code == CODE]


@pytest.mark.parametrize(
    "authored",
    [
        "    title: Pipeline by quarter, $M",
        "    title: Pipeline by quarter\n    subtitle: In $ millions",
        "    title: Pipeline by quarter, €K",
    ],
    ids=["wrong-compact", "wrong-spelled", "wrong-symbol"],
)
def test_a_wrong_unit_warns(authored: str) -> None:
    chart = authored + "\n    style:\n      axis_y:\n" + DOLLAR_K
    messages = _unit_warnings(chart)
    assert len(messages) == 1
    assert "'$K'" in messages[0]


@pytest.mark.parametrize(
    "authored",
    [
        "    title: Pipeline by quarter, $K",
        "    title: Pipeline by quarter\n    subtitle: In $K",
        "    title: Pipeline by quarter, $ thousands",
        "    title: Pipeline by quarter\n    subtitle: Thousands of dollars",
        "    title: Pipeline by quarter (thousands)",
    ],
    ids=["title", "subtitle", "spelled", "spelled-subtitle", "tier-only"],
)
def test_the_right_unit_in_title_or_subtitle_stays_quiet(authored: str) -> None:
    chart = authored + "\n    style:\n      axis_y:\n" + DOLLAR_K
    assert _unit_warnings(chart) == []


def test_a_stale_title_warns_even_beside_the_right_subtitle() -> None:
    chart = (
        "    title: Pipeline, $M\n    subtitle: In $K\n    style:\n      axis_y:\n"
        + DOLLAR_K
    )
    assert len(_unit_warnings(chart)) == 1


def test_a_title_with_no_unit_never_warns() -> None:
    assert _unit_warnings("    title: Pipeline by quarter") == []


def test_a_plain_number_axis_never_warns() -> None:
    assert _unit_warnings("    title: Deals by quarter", [12, 38, 41]) == []


def test_an_uncompacted_axis_with_a_title_unit_never_warns() -> None:
    chart = "    title: Deals, $M\n    style:\n      axis_y:\n        labels:\n          format: ',.0f'"
    assert _unit_warnings(chart) == []


def test_a_chart_with_no_title_has_nothing_to_check() -> None:
    assert _unit_warnings("    style:\n      axis_y:\n" + DOLLAR_K) == []


def test_a_symbol_only_axis_never_warns() -> None:
    dollars = "    style:\n      axis_y:\n        labels:\n          format: '$,.0f'"
    percent = "    style:\n      axis_y:\n        labels:\n          format: '.0%'"
    assert _unit_warnings("    title: Pipeline by quarter\n" + dollars) == []
    assert (
        _unit_warnings("    title: Win rate by quarter\n" + percent, [0.2, 0.4]) == []
    )


def test_a_scatter_warns_once_per_unit_not_per_axis() -> None:
    board = (
        "queries:\n  q:\n    type: values\n    columns: [a, b]\n    values:\n"
        "      - [212000, 300000]\n      - [388000, 150000]\n      - [412000, 90000]\n"
        "charts:\n  c:\n    query: q\n    type: scatter\n    x: a\n    y: b\n"
        "    title: Deals by size, $M\nrows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    assert [w.code for w in result.warnings].count(CODE) == 1


def _scatter(title: str, x_fmt: str, y_fmt: str) -> list[str]:
    board = (
        "queries:\n  q:\n    type: values\n    columns: [a, b]\n    values:\n"
        "      - [0.2, 300000]\n      - [0.35, 150000]\n      - [0.4, 90000]\n"
        "charts:\n  c:\n    query: q\n    type: scatter\n    x: a\n    y: b\n"
        f"    title: {title}\n    style:\n      axis_x:\n        labels:\n"
        f"          format: '{x_fmt}'\n      axis_y:\n        labels:\n"
        f"          format: {y_fmt}\nrows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    return [w.message for w in result.warnings if w.code == CODE]


def test_a_two_unit_scatter_titled_for_both_units_stays_quiet() -> None:
    assert _scatter("Revenue ($K) vs win rate (%)", ".0%", "currency_compact") == []


def test_a_two_unit_scatter_with_a_stale_tier_warns() -> None:
    assert _scatter("Revenue ($M) vs win rate (%)", ".0%", "currency_compact")


def test_a_stray_percent_in_the_title_is_not_a_unit() -> None:
    chart = (
        "    title: Revenue in thousands from top 10% of accounts\n"
        "    style:\n      axis_y:\n" + DOLLAR_K
    )
    assert _unit_warnings(chart) == []


def test_a_bar_chart_is_checked() -> None:
    board = _board("    title: Pipeline by quarter, $M", [212000, 388000, 412000])
    result = render_board_svg_result(board.replace("type: line", "type: bar"))
    assert not result.chart_errors
    assert [w.code for w in result.warnings].count(CODE) == 1


def test_a_line_chart_is_checked() -> None:
    assert len(_unit_warnings("    title: Pipeline by quarter, $M")) == 1
