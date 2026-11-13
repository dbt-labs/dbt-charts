"""A funnel's support table: authored formats, share-of-first-stage and ribbon rows."""

from __future__ import annotations

import re

import pytest

from dbt_charts.core.colors import wcag_contrast
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style

from ._svg_render import render_board_to_svg

_BOARD = """
title: T
theme: {theme}
queries:
  q:
    type: values
    rows:
      - {{stage: Visit, crates: 30255}}
      - {{stage: Signup, crates: 21500}}
      - {{stage: Buy, crates: 9000}}
charts:
  c:
    query: q
    type: {chart_type}
    x: stage
    y: crates
{extra}
    support_table:
{table}
rows:
  - c
"""

_MINUS = "−"


def _svg(chart_type: str, table: str, extra: str = "", theme: str = "clarity") -> str:
    return render_board_to_svg(
        _BOARD.format(chart_type=chart_type, table=table, extra=extra, theme=theme)
    )


def _texts(chart_type: str, table: str, extra: str = "") -> list[str]:
    return re.findall(r">([^<>]+)</text>", _svg(chart_type, table, extra))


def _funnel(table: str) -> list[str]:
    return _texts("funnel", table)


def test_a_percent_column_shows_each_stage_as_a_share_of_the_first() -> None:
    texts = _funnel("      - percent: crates\n        label: Share")
    assert {"Share, %", "100", "71", "30"} <= set(texts)


def test_a_percent_column_takes_an_authored_format() -> None:
    texts = _funnel("      - percent: crates\n        format: '.1%'")
    assert {"Crates, %", "100.0", "71.1", "29.7"} <= set(texts)


def test_percent_is_refused_off_a_funnel() -> None:
    result = compile(
        _BOARD.format(
            chart_type="bar",
            table="      - percent: crates",
            extra="    style:\n      orientation: horizontal",
            theme="clarity",
        )
    )
    assert not result.success
    assert [e.code for e in result.errors] == ["ERR-PERCENT-ENTRY-NOT-FUNNEL"]
    assert "`type: funnel`" in str(result.errors)


def test_a_plain_column_shows_the_change_between_stages_on_ribbon_rows() -> None:
    texts = _funnel("      - source: crates")
    assert {"Crates, K", "30.25", "21.5", "9", f"{_MINUS}8.76", f"{_MINUS}12.5"} <= set(
        texts
    )


def test_a_percent_column_shows_the_change_as_a_share_of_the_previous_stage() -> None:
    texts = _funnel("      - percent: crates")
    assert {f"{_MINUS}29", f"{_MINUS}58"} <= set(texts)


def test_a_bar_with_ribbons_gets_ribbon_rows_too() -> None:
    """Ribbon rows follow the ribbons the chart draws, not the type keyword."""
    ribbons = (
        "    style:\n      orientation: horizontal\n      marks:\n        bar:\n"
        "          ribbons:\n            visible: true"
    )
    texts = _texts("bar", "      - source: crates", ribbons)
    assert {f"{_MINUS}8.76", f"{_MINUS}12.5"} <= set(texts)
    assert not [
        t for t in _texts("bar", "      - source: crates") if t.startswith(_MINUS)
    ]


def test_an_aggregate_column_leaves_ribbon_rows_blank() -> None:
    texts = _funnel("      - aggregate: sum\n        source: crates")
    assert not [t for t in texts if t.startswith(_MINUS)]


def _cell_y(svg: str, text: str) -> float:
    match = re.search(rf'y="([\d.]+)"[^>]*>{re.escape(text)}</text>', svg)
    assert match, text
    return float(match.group(1))


def test_a_ribbon_row_sits_midway_between_its_two_stage_rows() -> None:
    svg = _svg("funnel", "      - source: crates")
    visit, signup = _cell_y(svg, "30.25"), _cell_y(svg, "21.5")
    assert _cell_y(svg, f"{_MINUS}8.76") == pytest.approx((visit + signup) / 2, abs=0.6)


@pytest.mark.parametrize(
    ("theme", "quieter"),
    [
        ("clarity", True),
        ("paper", True),
        ("stark", False),
        ("vivid", True),
        ("neon", True),
    ],
)
def test_ribbon_rows_are_quieter_than_stage_rows_at_the_same_size(
    theme: str, quieter: bool
) -> None:
    """Stark prints every number at full strength, so its ribbon rows match."""
    canvas = get_theme_style(theme).background
    svg = _svg("funnel", "      - source: crates", theme=theme)

    def paint(text: str) -> tuple[str, str]:
        tag = re.search(rf"<text[^>]*>{re.escape(text)}</text>", svg)
        assert tag, text
        return (
            re.search(r'fill="([^"]+)"', tag.group(0)).group(1),
            re.search(r'font-size="([^"]+)"', tag.group(0)).group(1),
        )

    stage_fill, stage_size = paint("30.25")
    ribbon_fill, ribbon_size = paint(f"{_MINUS}8.76")
    assert ribbon_size == stage_size
    ribbon, stage = (
        wcag_contrast(ribbon_fill, canvas),
        wcag_contrast(stage_fill, canvas),
    )
    assert ribbon < stage if quieter else ribbon == stage


def _cell_x(svg: str, text: str) -> float:
    match = re.search(rf'x="([\d.]+)"[^>]*>{re.escape(text)}</text>', svg)
    assert match, text
    return float(match.group(1))


def test_the_table_sits_left_of_the_bars_by_default_and_right_on_request() -> None:
    table = "      - source: crates"
    left = _svg("funnel", table)
    right = _svg(
        "funnel", table, "    style:\n      support_table:\n        position: right"
    )
    assert _cell_x(left, "30.25") < 200 < _cell_x(right, "30.25")
