"""A board with no width hugs the table's measured column widths, not a flat 800.

The data-aware sizing pass asks each unpinned table how wide its columns need
to be; a chart-local ``style.preferred_width`` pins instead. The data-free
(static) path keeps the theme estimate.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.normalize.chart_focus import focus_on_chart
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.project import Project
from dbt_charts.core.render import layout_sizing
from dbt_charts.core.render.board_resolve import (
    build_resolved_board,
    build_resolved_board_static,
)
from dbt_charts.core.render.chart.table_overflow import collect_table_overflows


def _queries(n_cols: int, text: str = "alpha-bravo-") -> str:
    row = ", ".join(f"c{i}: {text}{i}" for i in range(n_cols))
    return f"""
queries:
  q:
    type: values
    rows:
      - {{{row}}}
      - {{{row}}}
  series:
    type: values
    rows:
      - {{k: a, v: 1}}
      - {{k: b, v: 2}}
"""


_TABLE = "charts:\n  t:\n    query: q\n    type: table\n"
_ONE_ROW = "rows:\n  - t\n"


def _executor(board: Board, project: Project) -> Executor:
    return Executor(
        board,
        adapter_registry=build_adapter_registry(project),
        query_registry=None,
    )


def _build(
    yaml: str, local_project: Callable[..., Project]
) -> tuple[Board, ResolvedBoard, set[str]]:
    result = compile(yaml)
    assert result.success and result.board is not None, result.errors
    executor = _executor(result.board, local_project(Path.cwd()))
    with collect_table_overflows() as overflows:
        resolved, _cache = build_resolved_board(
            result.board, executor, {}, render_first=False
        )
    return result.board, resolved, set(overflows)


def _margins(resolved: ResolvedBoard) -> float:
    return 2 * resolved.page_padding


def test_wide_table_widens_board_without_overflow(local_project) -> None:
    _, resolved, overflowed = _build(_queries(10) + _TABLE + _ONE_ROW, local_project)
    assert 800 + _margins(resolved) < resolved.width <= 1200
    assert not overflowed


def test_narrow_table_narrows_board(local_project) -> None:
    _, resolved, _ = _build(_queries(2) + _TABLE + _ONE_ROW, local_project)
    assert resolved.width < 800


def test_chart_local_preferred_width_pins(local_project) -> None:
    yaml = (
        _queries(10)
        + "charts:\n  t:\n    query: q\n    type: table\n"
        + "    style:\n      preferred_width: 500\n"
        + _ONE_ROW
    )
    _, resolved, _ = _build(yaml, local_project)
    assert resolved.width == 500 + _margins(resolved)


def test_board_level_family_width_is_only_the_data_free_estimate(
    local_project,
) -> None:
    yaml = (
        "style:\n  charts:\n    table:\n      preferred_width: 1000\n"
        + _queries(2)
        + _TABLE
        + _ONE_ROW
    )
    _, resolved, _ = _build(yaml, local_project)
    assert resolved.width < 1000
    fresh = compile(yaml).board
    assert fresh is not None
    static = build_resolved_board_static(fresh)
    assert static.width == 1000 + _margins(static)


def test_wide_table_above_kpi_row_sets_hug(local_project) -> None:
    yaml = (
        _queries(10)
        + "charts:\n  t:\n    query: q\n    type: table\n"
        + "  k1:\n    query: series\n    type: kpi\n    value: v\n"
        + "  k2:\n    query: series\n    type: kpi\n    value: v\n"
        + "rows:\n  - t\n  - cols:\n      - k1\n      - k2\n"
    )
    _, resolved, overflowed = _build(yaml, local_project)
    assert resolved.width > 800 + _margins(resolved)
    assert not overflowed


def test_nested_board_measures_with_its_own_theme(
    local_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml = (
        "theme: paper\n"
        + _queries(4)
        + _TABLE
        + "rows:\n  - theme: stark\n    rows:\n      - t\n"
    )
    seen: list[Any] = []
    real = layout_sizing._resolve_with_runtime_inputs

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        resolved = real(*args, **kwargs)
        seen.append(resolved)
        return resolved

    monkeypatch.setattr(layout_sizing, "_resolve_with_runtime_inputs", spy)
    _, resolved, _ = _build(yaml, local_project)
    final = resolved.charts["t"]
    assert seen, "the width pass must resolve the unpinned table"
    assert seen[0].style.table.font == final.style.table.font
    assert seen[0].style.table.header == final.style.table.header


def test_failing_query_keeps_estimate_and_records_once(
    local_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = compile(_queries(10) + _TABLE + _ONE_ROW)
    assert result.success and result.board is not None, result.errors
    executor = _executor(result.board, local_project(Path.cwd()))

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ExecutionError("warehouse down")

    monkeypatch.setattr(executor, "execute_query", boom)
    monkeypatch.setattr(executor, "execute_chart", boom)
    errors: dict[str, Any] = {}
    resolved, _ = build_resolved_board(
        result.board, executor, {}, render_first=False, resolve_errors=errors
    )
    assert resolved.width == 800 + _margins(resolved)
    assert list(errors) == ["t"]


def test_unbounded_free_text_reaches_max_width(local_project) -> None:
    long_text = "word " * 60
    yaml = (
        f"queries:\n  q:\n    type: values\n    rows:\n"
        f'      - {{a: 1, b: "{long_text}"}}\n' + _TABLE + _ONE_ROW
    )
    _, resolved, _ = _build(yaml, local_project)
    assert resolved.width == 1200


def test_measurement_does_not_poison_final_resolve(
    local_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    widths: list[float] = []
    real = layout_sizing.resolve_chart_with_runtime_inputs

    def spy(chart, data, ctx, width, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        widths.append(width)
        return real(chart, data, ctx, width, *args, **kwargs)

    monkeypatch.setattr(layout_sizing, "resolve_chart_with_runtime_inputs", spy)
    _, resolved, _ = _build(_queries(10) + _TABLE + _ONE_ROW, local_project)
    slot = resolved.layout.items[0].width
    assert slot > 800
    assert widths[0] == 800 and slot in widths


def test_focus_on_unpinned_table_that_owns_its_row_matches_board(
    local_project,
) -> None:
    yaml = _queries(10) + _TABLE + _ONE_ROW
    _, board_view, _ = _build(yaml, local_project)
    _, focused, overflowed = _build(yaml + "chart_focus: t\n", local_project)
    assert focused.width == board_view.width
    assert not overflowed


@pytest.mark.parametrize(
    "layout",
    [
        "style:\n  frame:\n    width: 1200\n" + _ONE_ROW,
        "rows:\n  - cols:\n      - t\n      - l1\n",
    ],
    ids=["authored-frame-width", "cols-half"],
)
def test_focus_renders_table_at_its_own_measured_width(
    layout: str, local_project
) -> None:
    """Focus ignores the source slot: the table hugs its data as if alone."""
    alone = _queries(3) + _TABLE + _ONE_ROW
    sibling = "  l1:\n    query: series\n    type: line\n    x: k\n    y: v\n"
    yaml = _queries(3) + _TABLE + sibling + layout
    _, alone_view, _ = _build(alone, local_project)
    _, focused, _ = _build(yaml + "chart_focus: t\n", local_project)
    assert focused.width == alone_view.width
    result = compile(yaml)
    assert result.success and result.board is not None, result.errors
    api_board = focus_on_chart(result.board, "t")
    api_resolved, _cache = build_resolved_board(
        api_board,
        _executor(api_board, local_project(Path.cwd())),
        {},
        render_first=False,
    )
    assert api_resolved.width == focused.width
