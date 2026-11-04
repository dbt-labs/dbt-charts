"""A nested layout fills the cell its parent gave it, at every depth.

Every chart in a row shares the row's height, whether it sits directly in the
row or inside a nested layout, and the items of a nested stack divide that height.
Asserted on the painted charts, since a right ``item.height`` proves nothing if the
render reads a different one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board, LayoutItem
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.renderer import render

_TALL_TEXT = json.dumps("# Tall card\n\n" + "line\n\n" * 18)
_MID_TEXT = json.dumps("# Mid card\n\n" + "line\n\n" * 12)
_CANVAS = re.compile(r'<svg[^>]*class="marks"[^>]*height="([\d.]+)"')
_TITLE_BASELINE = re.compile(r'<text[^>]* y="([\d.]+)"[^>]*data-authored-kind="title"')
_CHART_BOX = re.compile(r'data-chart-id="(\w+)"[^>]*?data-chart-height="([\d.]+)"')

_PREAMBLE = """
queries:
  q:
    columns: [m, v]
    values:
      - [a, 3]
      - [b, 5]
      - [c, 4]
charts:
"""


def _chart(chart_id: str) -> str:
    return f"""
  {chart_id}:
    title: {chart_id}
    type: bar
    query: q
    x: m
    y: v
"""


RenderBoard = Callable[[str], tuple[Board, list[float], list[float]]]


@pytest.fixture
def render_board(
    local_project: Callable[..., FilesystemProject],
) -> RenderBoard:
    def _render(body: str) -> tuple[Board, list[float], list[float]]:
        ids = sorted(set(re.findall(r"- (chart_\w+)", body)))
        text = _PREAMBLE + "".join(_chart(c) for c in ids) + body
        result = compile(text)
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert isinstance(svg, str)
        return (
            result.board,
            [float(h) for h in _CANVAS.findall(svg)],
            [float(y) for y in _TITLE_BASELINE.findall(svg)],
        )

    return _render


def _row_cells(board: Board) -> list[LayoutItem]:
    assert board.layout.type == "rows"
    row = board.layout.items[0].board
    assert row is not None
    return row.layout.items


def test_nested_cols_charts_match_the_direct_chart_beside_them(
    render_board: RenderBoard,
) -> None:
    board, canvases, _ = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_direct
      - cols:
          - chart_nested_a
          - chart_nested_b
""")

    row_height = _row_cells(board)[1].height
    assert canvases == pytest.approx([row_height] * 3)


def test_nested_cols_titles_share_the_rows_baseline(
    render_board: RenderBoard,
) -> None:
    _, _, baselines = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_direct
      - cols:
          - chart_nested_a
          - cols:
              - chart_nested_b
""")

    assert len(baselines) == 3
    assert baselines == pytest.approx([baselines[0]] * 3, abs=0.5)


def test_nested_cols_fills_a_row_with_no_direct_chart(
    render_board: RenderBoard,
) -> None:
    board, canvases, _ = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - cols:
          - chart_nested_a
          - chart_nested_b
""")

    row_height = _row_cells(board)[0].height
    assert canvases == pytest.approx([row_height] * 2)


def test_nested_cols_fills_at_every_depth(render_board: RenderBoard) -> None:
    board, canvases, _ = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - cols:
          - chart_nested_a
          - cols:
              - chart_nested_b
              - chart_nested_c
""")

    row_height = _row_cells(board)[0].height
    assert canvases == pytest.approx([row_height] * 3)


def test_nested_cols_fills_what_its_own_title_leaves(
    render_board: RenderBoard,
) -> None:
    board, canvases, _ = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - title: Nested title
        cols:
          - chart_nested_a
          - chart_nested_b
""")

    nested = _row_cells(board)[1].board
    assert nested is not None
    assert 0 < nested.layout.content_height < _row_cells(board)[1].height
    assert canvases == pytest.approx([nested.layout.content_height] * 2)


def test_authored_height_in_a_nested_cols_stays_authored(
    render_board: RenderBoard,
) -> None:
    board, canvases, _ = render_board(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - cols:
          - height: 200
            rows:
              - chart_nested_a
          - height: 200
            rows:
              - chart_nested_b
""")

    assert canvases == pytest.approx([200.0, 200.0])


def test_a_root_title_does_not_stretch_a_root_cols(
    render_board: RenderBoard,
) -> None:
    board, canvases, _ = render_board("""
title: Root title
cols:
  - chart_a
  - chart_b
""")

    assert board.layout.type == "cols"
    assert board.layout.items[0].height == pytest.approx(canvases[0])
    assert board.layout.items[0].height < board.layout.height


_MORE_CHARTS = """
  kpi_a:
    type: kpi
    query: one
    value: n
  kpi_b:
    type: kpi
    query: one
    value: n
  pie_a:
    type: pie
    query: q
    color: m
    theta: v
  fixed_a:
    type: bar
    query: q
    x: m
    y: v
    height: 200
  table_a:
    type: table
    query: long
  callout_a:
    type: callout
    message: A short note
  custom_a:
    type: custom
    query: q
    x: m
    svg: |
      {#---
      description: A rectangle.
      channels:
        x:
          description: Column.
      ---#}
      <svg viewBox="0 0 {{ width }} {{ height }}"><rect width="10" height="10"/></svg>
"""

_MORE_QUERIES = """
  one:
    columns: [n]
    values:
      - [7]
  long:
    columns: [m, v]
    values:
""" + "".join(f"      - [row{i}, {i}]\n" for i in range(30))

RenderStack = Callable[[str], tuple[Board, dict[str, float]]]


@pytest.fixture
def render_stack(
    local_project: Callable[..., FilesystemProject],
) -> RenderStack:
    def _render(body: str) -> tuple[Board, dict[str, float]]:
        ids = sorted(set(re.findall(r"\b(chart_\w+)", body)))
        preamble = _PREAMBLE.replace("charts:\n", _MORE_QUERIES + "charts:\n")
        result = compile(
            preamble + _MORE_CHARTS + "".join(_chart(c) for c in ids) + body
        )
        assert result.success, result.errors
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert isinstance(svg, str)
        boxes: dict[str, float] = {}
        for cid, height in _CHART_BOX.findall(svg):
            boxes.setdefault(cid, float(height))
        return result.board, boxes

    return _render


def _beside_tall_text(*stack: str) -> str:
    items = "\n".join(f"          - {item}" for item in stack)
    return f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - rows:
{items}
"""


def _cell(board: Board, index: int = 1) -> LayoutItem:
    return _row_cells(board)[index]


def _stack_end(cell: LayoutItem) -> float:
    assert cell.board is not None
    last = cell.board.layout.items[-1]
    return last.y + last.height


def test_stacked_charts_divide_the_cell(render_stack: RenderStack) -> None:
    board, boxes = render_stack(_beside_tall_text("chart_a", "chart_b"))

    cell = _cell(board)
    assert boxes["chart_a"] == pytest.approx(boxes["chart_b"])
    assert boxes["chart_a"] > cell.height / 3
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_a_chart_takes_what_a_caption_leaves(render_stack: RenderStack) -> None:
    board, boxes = render_stack(_beside_tall_text("text: A caption", "chart_a"))

    cell = _cell(board)
    assert cell.board is not None
    caption, chart = cell.board.layout.items
    assert caption.height < cell.height / 4
    assert boxes["chart_a"] == pytest.approx(chart.height)
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_a_stack_inside_a_stack_fills_too(render_stack: RenderStack) -> None:
    board, boxes = render_stack(
        _beside_tall_text(
            "chart_c",
            """rows:
              - chart_a
              - chart_b""",
        )
    )

    cell = _cell(board)
    assert boxes["chart_a"] == pytest.approx(boxes["chart_b"])
    assert boxes["chart_a"] == pytest.approx(boxes["chart_c"])
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_a_cols_row_in_a_stack_fills_its_share(render_stack: RenderStack) -> None:
    board, boxes = render_stack(
        _beside_tall_text(
            """cols:
              - chart_a
              - chart_b""",
            "chart_c",
        )
    )

    cell = _cell(board)
    assert boxes["chart_a"] == pytest.approx(boxes["chart_b"])
    assert boxes["chart_a"] == pytest.approx(boxes["chart_c"], rel=0.5)
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_a_kpi_stays_compact_and_the_chart_takes_the_rest(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(_beside_tall_text("kpi_a", "chart_a"))

    cell = _cell(board)
    assert boxes["kpi_a"] < cell.height / 4
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_a_pie_keeps_its_size_and_the_chart_takes_the_rest(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(_beside_tall_text("pie_a", "chart_a"))
    _, alone = render_stack(_beside_tall_text("pie_a"))

    assert boxes["pie_a"] == pytest.approx(alone["pie_a"])
    assert _stack_end(_cell(board)) == pytest.approx(_cell(board).height)


def test_a_pie_in_a_nested_cols_matches_a_direct_pie(
    render_stack: RenderStack,
) -> None:
    _, nested = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_a
      - cols:
          - pie_a
""")
    _, direct = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_a
      - pie_a
""")

    assert nested["pie_a"] == pytest.approx(direct["pie_a"])


def test_a_custom_chart_takes_its_share_of_a_stack(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(_beside_tall_text("custom_a", "chart_a"))

    assert boxes["custom_a"] > 300
    assert _stack_end(_cell(board)) == pytest.approx(_cell(board).height)


def test_a_stack_with_nothing_to_grow_stays_at_its_content(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(_beside_tall_text("kpi_a", "kpi_b"))

    cell = _cell(board)
    assert boxes["kpi_a"] == pytest.approx(boxes["kpi_b"])
    assert _stack_end(cell) < cell.height / 2


def test_a_chart_with_an_authored_height_keeps_it(render_stack: RenderStack) -> None:
    board, boxes = render_stack(_beside_tall_text("fixed_a", "chart_a"))
    _, alone = render_stack("""
rows:
  - fixed_a
""")

    assert boxes["fixed_a"] == pytest.approx(alone["fixed_a"])
    assert _stack_end(_cell(board)) == pytest.approx(_cell(board).height)


def test_a_tile_height_stays_a_ceiling(render_stack: RenderStack) -> None:
    board, boxes = render_stack(
        _beside_tall_text(
            """height: 200
            rows:
              - chart_a""",
            "chart_b",
        )
    )

    assert boxes["chart_a"] <= 200.0
    assert _stack_end(_cell(board)) == pytest.approx(_cell(board).height)


@pytest.mark.parametrize("chart_id", ["custom_a", "callout_a", "chart_a"])
def test_a_lone_item_in_a_rows_wrapper_matches_a_direct_item(
    render_stack: RenderStack, chart_id: str
) -> None:
    board, boxes = render_stack(_beside_tall_text(chart_id))
    _, direct = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - {chart_id}
""")

    assert boxes[chart_id] == pytest.approx(direct[chart_id])
    assert boxes[chart_id] == pytest.approx(_cell(board).height)


def test_a_nested_chart_grows_to_a_taller_table_beside_it(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack("""
rows:
  - cols:
      - table_a
      - rows:
          - chart_a
""")

    assert boxes["chart_a"] == pytest.approx(_cell(board).height)
    assert boxes["chart_a"] == pytest.approx(boxes["table_a"], abs=1.0)


def test_a_nested_grid_fills_the_cell(render_stack: RenderStack) -> None:
    board, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - grid:
          columns: 2
          items:
            - item: chart_a
              width: 1
            - item: chart_b
              width: 1
            - item: chart_c
              width: 1
            - item: chart_d
              width: 1
""")

    cell = _cell(board)
    assert boxes["chart_a"] == pytest.approx(boxes["chart_d"])
    assert _stack_end(cell) == pytest.approx(cell.height)


def test_nested_tabs_fill_the_cell(render_stack: RenderStack) -> None:
    _, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_direct
      - tabs:
          items:
            - title: One
              rows:
                - chart_a
            - title: Two
              rows:
                - chart_b
""")

    assert boxes["chart_a"] > boxes["chart_direct"] * 0.8


def test_a_root_stack_stays_at_its_content(render_stack: RenderStack) -> None:
    board, boxes = render_stack("""
title: Root title
rows:
  - chart_a
  - chart_b
""")

    assert boxes["chart_a"] == pytest.approx(board.layout.items[0].height)
    assert board.layout.items[1].y + board.layout.items[1].height < board.layout.height


def test_a_pinned_table_cell_does_not_stretch_a_nested_chart(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack("""
rows:
  - cols:
      - rows:
          - chart_a
      - height: 2000
        rows:
          - table_a
""")

    _, drawn = render_stack("""
rows:
  - table_a
""")

    assert _cell(board).height == pytest.approx(2000.0)
    assert boxes["chart_a"] == pytest.approx(drawn["table_a"], abs=1.0)


def test_a_pinned_table_cell_does_not_stretch_a_direct_chart(
    render_stack: RenderStack,
) -> None:
    _, boxes = render_stack("""
rows:
  - cols:
      - chart_a
      - height: 2000
        rows:
          - table_a
""")
    _, drawn = render_stack("""
rows:
  - table_a
""")

    assert boxes["chart_a"] == pytest.approx(drawn["table_a"], abs=1.0)


def test_a_chart_reused_in_two_rows_fills_each_row(render_stack: RenderStack) -> None:
    board, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - chart_a
  - cols:
      - chart_a
      - chart_b
""")

    tall_row = board.layout.items[0].board
    assert tall_row is not None
    text, chart = tall_row.layout.items
    assert chart.height == pytest.approx(text.height)
    assert chart.height > boxes["chart_b"]


def test_a_pinned_grid_row_stays_pinned(render_stack: RenderStack) -> None:
    board, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - grid:
          columns: 2
          items:
            - item:
                height: 240
                rows:
                  - chart_a
              width: 1
            - item: chart_c
              width: 1
            - item: chart_b
              width: 2
""")

    assert boxes["chart_a"] <= 240.0
    assert boxes["chart_b"] > 400.0
    assert _stack_end(_cell(board, 1)) == pytest.approx(_cell(board, 1).height)


def test_a_kpi_grid_row_stays_compact_beside_a_growing_chart_row(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - grid:
          columns: 2
          items:
            - item: kpi_a
              width: 1
            - item: kpi_b
              width: 1
            - item: chart_a
              width: 2
""")

    assert boxes["kpi_a"] < 150.0
    assert boxes["chart_a"] > 500.0
    assert _stack_end(_cell(board, 1)) == pytest.approx(_cell(board, 1).height)


def test_a_collapsed_details_section_takes_no_spare_height(
    render_stack: RenderStack,
) -> None:
    board, boxes = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - rows:
          - details:
              summary: More
            rows:
              - chart_a
          - chart_b
""")

    stack = _cell(board).board
    assert stack is not None
    assert stack.layout.items[0].height < 80.0
    assert boxes["chart_b"] > 500.0


def test_a_pie_reused_in_two_rows_keeps_each_rows_height(
    render_stack: RenderStack,
) -> None:
    _, reused = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - pie_a
  - cols:
      - text: {_MID_TEXT}
      - pie_a
""")
    _, alone = render_stack(f"""
rows:
  - cols:
      - text: {_TALL_TEXT}
      - pie_a
""")

    assert reused["pie_a"] == pytest.approx(alone["pie_a"])
