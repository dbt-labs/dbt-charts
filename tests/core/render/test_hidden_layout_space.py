"""A hidden layout item takes no space: it is not measured, drawn, or resolved."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.sizing import prose_slot_widths
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.conditions import item_is_drawn

_LONG = "tall " * 400
_CHART = """queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  c:
    query: q
    type: kpi
    value: v
"""


def _board(body: str) -> str:
    return f"text: head\n{body}"


def _geometry(
    yaml: str, local_project: Callable[..., Project]
) -> tuple[float, float, float]:
    """The board's SVG width and height and the y of its last layout item."""
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    out = render(result.board, executor, format="svg")
    assert out.board_error is None, out.board_error
    assert isinstance(out.output, str)
    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', out.output)
    assert box is not None
    resolved, _ = build_resolved_board(result.board, executor, {}, render_first=False)
    return float(box.group(1)), float(box.group(2)), resolved.layout.items[-1].y


_TABLE_CHART = _CHART + "  t:\n    query: q\n    type: table\n    columns:\n      - v\n"
_FLAGS = "queries:\n  flags:\n    type: values\n    rows:\n      - {show: false}\n"
_CASES = {
    "probe-gated-row": (
        _FLAGS
        + "rows:\n  - text: one\n  - visible:\n      query: flags\n"
        + f"      column: show\n    text: {_LONG}\n  - text: after\n",
        "rows:\n  - text: one\n  - text: after\n",
    ),
    "table-under-kpi": (
        _TABLE_CHART + "rows:\n  - c\n  - visible: false\n    rows:\n      - t\n",
        _CHART + "rows:\n  - c\n",
    ),
    "cols-cell": (
        "rows:\n  - cols:\n      - text: one\n      - visible: false\n"
        f"        text: {_LONG}\n  - text: after\n",
        "rows:\n  - cols:\n      - text: one\n  - text: after\n",
    ),
    "row": (
        f"rows:\n  - text: one\n  - visible: false\n    text: {_LONG}\n"
        "  - text: after\n",
        "rows:\n  - text: one\n  - text: after\n",
    ),
    "cell-with-authored-height": (
        "rows:\n  - cols:\n      - text: one\n      - visible: false\n"
        "        height: 600\n        text: x\n  - text: after\n",
        "rows:\n  - cols:\n      - text: one\n  - text: after\n",
    ),
    "grid-item": (
        _CHART + "grid:\n  columns: 1\n  items:\n    - item: c\n    - item:\n"
        "        visible: false\n        text: hidden\n    - item: c\n",
        _CHART + "grid:\n  columns: 1\n  items:\n    - item: c\n    - item: c\n",
    ),
}


@pytest.mark.parametrize("name", list(_CASES))
def test_a_hidden_item_adds_no_height_or_offset(
    name: str, local_project: Callable[..., Project]
) -> None:
    hidden, without = _CASES[name]
    assert _geometry(_board(hidden), local_project) == _geometry(
        _board(without), local_project
    )


def _slots(yaml: str) -> list[float]:
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board is not None
    slots = prose_slot_widths(result.board, 800.0, {}, item_is_drawn)
    return sorted(slots.values())


def test_prose_is_planned_at_the_width_a_visible_cell_is_drawn() -> None:
    hidden = "rows:\n  - cols:\n      - text: one\n      - visible: false\n        text: two\n"
    without = "rows:\n  - cols:\n      - text: one\n"
    assert _slots(_board(hidden)) == _slots(_board(without))
