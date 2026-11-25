"""A value label drawn in `style.roles.ink` is the ink, on every path that places one.

Each case puts the ink (navy, unreadable on its dark canvas) where a label draws
in it: outside a bar, on a bar past the axis, on a funnel ribbon, and on a layer
that takes its series color. The renderer must not move it toward legibility.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import reset_config

from ._svg_render import render_board_svg_result

INK = "#1F3A68"
_STYLE: dict[str, Any] = {
    "roles": {"ink": INK},
    "background": "#161616",
    "font": {"color": "ink"},
    "charts": {
        "color": {
            "categorical": {
                "palette": [INK, INK],
                "single_series_palette": [INK],
            }
        }
    },
}
_BAR = "    type: bar\n    x: cat\n    y: v\n"
_LABELS = "      marks:\n        bar:\n          labels:\n            visible: true\n"
_NARROW = [["a", 145, 1], *([f"k{i}", 8 + i % 3, 1] for i in range(60))]
_LAYER = (
    "    layers:\n      - type: line\n        y: w\n        style:\n"
    "          marks:\n            line:\n              labels:\n"
    "                visible: true\n                font:\n                  color: series\n"
)

# (chart yaml, rows, label texts that must be drawn in the ink)
_CASES: dict[str, tuple[str, list[list[Any]], set[str]]] = {
    "outside a bar": (
        _BAR + "    style:\n" + _LABELS + "            position: above\n",
        [["a", 27, 1], ["b", 23, 1], ["c", 2, 1]],
        {"27", "23", "2"},
    ),
    "past the axis": (
        _BAR + "    style:\n      orientation: vertical\n" + _LABELS,
        _NARROW,
        {"145 →"},
    ),
    "on a ribbon": (
        "    type: funnel\n    x: cat\n    y: v\n",
        [["Visit", 1000, 1], ["Signup", 600, 1], ["Buy", 200, 1]],
        {"−40%", "−67%"},
    ),
    "a layer by series": (
        _BAR + _LAYER + "    style:\n      orientation: vertical\n",
        [["a", 30, 11], ["b", 20, 13], ["c", 25, 9]],
        {"11", "13", "9"},
    ),
}


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


@pytest.mark.parametrize("case", list(_CASES))
def test_a_label_drawn_in_the_ink_is_the_ink(case: str) -> None:
    chart, rows, words = _CASES[case]
    values = "".join(f"      - {json.dumps(row)}\n" for row in rows)
    board = (
        f"title: T\nstyle: {json.dumps(_STYLE)}\n"
        f"queries:\n  q:\n    type: values\n    columns: [cat, v, w]\n    values:\n{values}"
        f"charts:\n  c:\n    query: q\n{chart}rows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    assert not result.chart_errors
    assert isinstance(result.output, str)
    fills = {
        re.sub(r"<[^>]+>", "", body).strip(): fill.group(1)
        for attrs, body in re.findall(
            r"<text\b([^>]*)>(.*?)</text>", result.output, re.S
        )
        if (fill := re.search(r'fill="([^"]*)"', attrs))
    }
    assert {word: fills[word] for word in words} == dict.fromkeys(words, INK)
