"""Two layout keys on one board or tab item are an authoring error, not a crash."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board

_CHARTS = "charts:\n  a: {type: kpi, query: {columns: [v], values: [[1]]}, value: v}\n"
_TAB = "tabs:\n  items:\n    - title: T\n      {}\n"

_BOARDS = {
    "root": ("rows: [a]\ncols: [a]\n", ""),
    "tab-rows-cols": (_TAB.format("rows: [a]\n      cols: [a]"), "tabs.items.0"),
    "tab-rows-grid": (
        _TAB.format("rows: [a]\n      grid: {columns: 2, items: [{item: a}]}"),
        "tabs.items.0",
    ),
    "tab-cols-grid": (
        _TAB.format("cols: [a]\n      grid: {columns: 2, items: [{item: a}]}"),
        "tabs.items.0",
    ),
    "nested": ("rows:\n  - rows: [a]\n    cols: [a]\n", "rows.0"),
}


@pytest.mark.parametrize("name", list(_BOARDS))
def test_two_layout_keys_are_a_validation_error(name: str) -> None:
    layout, path = _BOARDS[name]
    result = compile_board(_CHARTS + layout)
    [diagnostic] = result.errors
    assert diagnostic.code == "ERR-VALIDATION-FIELD"
    assert "can only have one layout type" in diagnostic.message
    assert (diagnostic.path or "") == path
