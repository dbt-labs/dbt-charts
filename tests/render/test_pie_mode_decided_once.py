"""A placed pie's mode is decided once, by the size stage, and read by everyone
after it: the title band, the render, the ink inset and the warning detectors."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from dbt_charts.core.board import render_dashboard
from dbt_charts.core.project import InMemoryBoard

ROWS = [
    {"segment": "North America", "sessions": 40},
    {"segment": "Europe", "sessions": 25},
    {"segment": "Asia Pacific", "sessions": 20},
    {"segment": "Latin America", "sessions": 12},
    {"segment": "Other", "sessions": 3},
]

BOARD = """
source: examples_db
queries:
  q: SELECT segment, sessions FROM t
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
    title: "Session mix"
  bars:
    query: q
    type: bar
    x: segment
    y: sessions
    title: "Sessions"
cols:
  - mix
  - bars
"""

ROWS_BOARD = BOARD.replace("cols:\n  - mix\n  - bars", "rows:\n  - mix\n  - bars")
NESTED_BOARD = BOARD.replace(
    "cols:\n  - mix\n  - bars",
    "style:\n  frame:\n    card_padding: 24\nrows:\n  - cols:\n      - rows:\n          - mix\n      - bars",
)

# A grid cell whose wrapper authors its height takes that height without asking
# the height provider.
GRID_BOARD = BOARD.replace(
    "cols:\n  - mix\n  - bars",
    "grid:\n  columns: 12\n  items:\n    - item:\n        height: 300\n        rows:\n"
    "          - mix\n      width: 6\n    - item: bars\n      width: 6",
)


def _registry(rows: list[dict[str, Any]]) -> Mock:
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    registry = Mock()
    registry.execute.return_value = ok
    registry.project_file_sources.return_value = {}
    return registry


@pytest.mark.parametrize("source", [BOARD, ROWS_BOARD, NESTED_BOARD, GRID_BOARD])
def test_the_size_stage_decides_each_placed_pie_once(
    source: str, tmp_path, local_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts.core.render import pie_layout

    calls: list[str] = []
    decide = pie_layout.decide_pie_mode

    def counting(chart: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(chart.id)
        return decide(chart, *args, **kwargs)

    monkeypatch.setattr(pie_layout, "decide_pie_mode", counting)
    project = local_project(tmp_path)
    render_dashboard(
        board=InMemoryBoard(source, path=project.path("charts/_t.yml")),
        adapter_registry=_registry(ROWS),
        format="svg",
        project=project,
        result_cache=None,
    )
    assert calls == ["mix"], calls


GUARD_BOARD = """
source: examples_db
queries:
  q: SELECT segment, sessions FROM t
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
    title: "Session mix"
rows:
{rows}
"""


def _guard_warnings(rows: str, tmp_path, local_project) -> list[Any]:
    from dbt_charts.core.diagnostics import WARN_PIE_SIZED_MODE_MISSES_GUARD

    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(
            GUARD_BOARD.format(rows=rows), path=project.path("charts/_t.yml")
        ),
        adapter_registry=_registry(ROWS),
        format="svg",
        project=project,
        result_cache=None,
    )
    return [
        w for w in result.warnings if w.code == WARN_PIE_SIZED_MODE_MISSES_GUARD.code
    ]


def test_a_pie_drawn_in_its_sized_box_never_warns_about_the_guard(
    tmp_path, local_project
) -> None:
    assert _guard_warnings("  - mix", tmp_path, local_project) == []


def test_a_pie_pinned_into_a_smaller_box_than_it_was_sized_in_warns(
    tmp_path, local_project
) -> None:
    pinned = "  - height: 150\n    rows:\n      - mix"
    (warning,) = _guard_warnings(pinned, tmp_path, local_project)
    assert warning.chart == "mix"


LEGEND_BOARD = """
source: examples_db
style:
  frame:
    width: {width}
queries:
  q: SELECT segment, sessions FROM t
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
    style:
      legend:
        visible: true
        values:
          - Ghost
rows:
  - mix
"""


def _warning_codes(width: int, tmp_path, local_project) -> set[str]:
    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(
            LEGEND_BOARD.format(width=width), path=project.path("charts/_t.yml")
        ),
        adapter_registry=_registry(ROWS),
        format="svg",
        project=project,
        result_cache=None,
    )
    return {w.code for w in result.warnings}


def test_an_unresolved_legend_value_is_not_reported_for_a_legend_the_sized_mode_omits(
    tmp_path, local_project
) -> None:
    from dbt_charts.core.diagnostics import WARN_LEGEND_VALUES_UNRESOLVED

    assert WARN_LEGEND_VALUES_UNRESOLVED.code in _warning_codes(
        900, tmp_path, local_project
    )
    assert WARN_LEGEND_VALUES_UNRESOLVED.code not in _warning_codes(
        360, tmp_path, local_project
    )


TWO_PLACEMENTS_BOARD = """
source: examples_db
style:
  frame:
    width: 900
queries:
  q: SELECT segment, sessions FROM t
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
    style:
      legend:
        visible: true
        values:
          - Ghost
  other_a:
    query: q
    type: bar
    x: segment
    y: sessions
  other_b:
    query: q
    type: bar
    x: segment
    y: sessions
rows:
  - cols: [mix]
  - cols: [mix, other_a, other_b]
"""


def test_a_legend_value_is_reported_while_any_placement_draws_the_legend(
    tmp_path, local_project
) -> None:
    """The pie is full width (direct, legend drawn) in one row and a third of
    it (a table) in the other; the narrow placement must not silence the
    warning for the wide one."""
    from dbt_charts.core.diagnostics import WARN_LEGEND_VALUES_UNRESOLVED

    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(TWO_PLACEMENTS_BOARD, path=project.path("charts/_t.yml")),
        adapter_registry=_registry(ROWS),
        format="svg",
        project=project,
        result_cache=None,
    )
    assert WARN_LEGEND_VALUES_UNRESOLVED.code in {w.code for w in result.warnings}


def test_a_mode_left_on_the_layout_item_by_an_earlier_pass_is_decided_afresh() -> None:
    """Like every sizing field, the item's mode belongs to one pass: a board
    rendered twice sizes its pies twice, not once."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render import render

    yaml = """
title: Mix
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
queries:
  q:
    sql: SELECT segment, sessions FROM t
    source: test_source
rows:
  - mix
"""
    result = compile(yaml)
    assert result.success and result.board is not None, result.errors
    registry = _registry(ROWS)
    executor = Executor(
        result.board, adapter_registry=registry, query_registry=result.query_registry
    )
    item = result.board.layout.items[0]
    item.pie_mode = "full_table"
    render(result.board, executor, format="svg")
    assert item.pie_mode == "direct"


def _placed_pie_modes(yaml: str, rows: list[dict[str, Any]]) -> list[str | None]:
    """Render ``yaml`` and return the mode the size stage left on each placed pie."""
    from dbt_charts.core.compile import compile
    from dbt_charts.core.compile.models.board.normalized import Layout
    from dbt_charts.core.execute import Executor
    from dbt_charts.core.render import render

    result = compile(yaml)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=_registry(rows),
        query_registry=result.query_registry,
    )
    render(result.board, executor, format="svg")

    def walk(layout: Layout) -> list[str | None]:
        found: list[str | None] = []
        for item in layout.items:
            if item.chart is not None and item.chart.type in ("pie", "donut"):
                found.append(item.pie_mode)
            if item.board is not None:
                found += walk(item.board.layout)
        return found

    return walk(result.board.layout)


_SIZED_PIE = """
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
  bars:
    query: q
    type: bar
    x: segment
    y: sessions
queries:
  q:
    sql: SELECT segment, sessions FROM t
    source: test_source
"""


def test_a_grid_pie_with_an_authored_height_is_sized_a_mode() -> None:
    """The grid takes an authored height without asking the height provider,
    so nothing on the measuring path ever decides this pie's mode."""
    grid = (
        "grid:\n  columns: 12\n  items:\n    - item:\n        height: 300\n"
        "        rows:\n          - mix\n      width: 6\n    - item: bars\n"
        "      width: 6\n"
    )
    modes = _placed_pie_modes(_SIZED_PIE + grid, ROWS)
    assert modes and None not in modes, modes


def test_a_pie_placed_twice_at_one_width_records_a_mode_on_each_placement() -> None:
    """The second placement hits the first one's sizing cache; it still carries
    its own mode."""
    twice = "rows:\n  - mix\n  - mix\n"
    modes = _placed_pie_modes(_SIZED_PIE + twice, ROWS)
    assert len(modes) == 2 and None not in modes, modes


_MANY_SLICES = [{"segment": f"Segment {i}", "sessions": 10 + i} for i in range(13)]


def test_a_grid_pie_with_an_authored_height_reports_its_legend_edge_fallback(
    tmp_path, local_project
) -> None:
    """A narrow grid cell moves the table off the authored left edge; the
    warning reads the mode the size stage recorded for that cell."""
    from dbt_charts.core.diagnostics import WARN_LEGEND_POSITION_WIDTH_FALLBACK

    yaml = """
source: examples_db
style:
  frame:
    width: 900
queries:
  q: SELECT segment, sessions FROM t
charts:
  mix:
    query: q
    type: pie
    theta: sessions
    color: segment
    style:
      legend:
        position:
          edge: left
  bars:
    query: q
    type: bar
    x: segment
    y: sessions
grid:
  columns: 12
  items:
    - item:
        height: 300
        rows:
          - mix
      width: 4
    - item: bars
      width: 8
"""
    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(yaml, path=project.path("charts/_t.yml")),
        adapter_registry=_registry(_MANY_SLICES),
        format="svg",
        project=project,
        result_cache=None,
    )
    fallbacks = [
        w for w in result.warnings if w.code == WARN_LEGEND_POSITION_WIDTH_FALLBACK.code
    ]
    assert [w.chart for w in fallbacks] == ["mix"], result.warnings
