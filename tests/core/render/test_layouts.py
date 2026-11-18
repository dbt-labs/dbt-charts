"""The hooks a live page reflows a board by when a table opens list rows.

A board is a picture: a static render carries none of them. A live render (an
HTML export, or a host with controls) publishes each layout item's top and
height, marks every box that grows with a table (its card, a nested board, the
root) and the rects that stretch with each box, and draws the table's lists shut.
"""

import re
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render_format import RenderFormat

from ..._run import Run
from .._svg_render import pinned_clock

_BOARD = """
queries:
  teams:
    type: values
    rows:
      - team: Platform
        owners: [Alice, Bob, Carol]
      - team: Data
        owners: [Dana]
  totals:
    type: values
    rows:
      - n: 4
charts:
  by_team:
    query: teams
    type: table
    style:
      columns:
        owners:
          list: true
  total:
    query: totals
    type: kpi
    value: n
rows:
  - cols:
      - by_team
      - total
  - grid:
      columns: 2
      items:
        - item: total
          width: 1
        - item: total
          width: 1
"""


def _render(fmt: RenderFormat, board: str = _BOARD) -> str:
    result = compile(board)
    assert result.success, result.errors
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
        clock=pinned_clock,
    )
    return render(result.board, executor.ctx, executor.ledger, format=fmt).output


def _table_card(page: str) -> str:
    start = page.index('id="chart-by_team"')
    return page[page.rindex("<g", 0, start) : page.index("data-dbt-table=", start)]


def test_a_static_render_carries_no_reflow_hook() -> None:
    svg = _render("svg")

    assert "data-dbt-flow" not in svg
    assert "data-dbt-list" not in svg
    assert "Carol</tspan>" in svg


def test_a_live_render_publishes_every_layout_item_top_and_height() -> None:
    page = _render("html")

    items = re.findall(r'data-dbt-flow-y="([\d.]+)" data-dbt-flow-h="([\d.]+)"', page)
    # The rows stack's two items, the cols row's two, and the grid's two.
    assert len(items) == 6, items
    assert re.search(r'<svg xmlns="[^"]*"[^>]*data-dbt-flow-box=""', page)
    assert page.count('data-dbt-flow-box=""') >= 3  # the root, a nested board, the card
    follow = page[page.index('<g data-dbt-flow-follow="">') :]
    assert 'data-role="render-timestamp"' in follow


def test_a_top_timestamp_stays_put_while_the_footer_follows() -> None:
    page = _render(
        "html",
        _BOARD
        + """
style:
  timestamp:
    visible: true
    position: top
""",
    )

    follow = page[page.index('<g data-dbt-flow-follow="">') :]
    assert 'data-role="render-timestamp"' in page
    assert 'data-role="render-timestamp"' not in follow


def test_a_live_table_card_is_a_growing_box_with_stretching_rects() -> None:
    card = _table_card(_render("html"))

    assert 'data-dbt-flow-box=""' in card
    assert re.search(r'class="dbt-box-outer"[^>]*data-dbt-flow-bg=""', card)


def test_a_live_render_sizes_the_table_with_its_lists_shut() -> None:
    page = _render("html")
    svg = _render("svg")

    assert 'data-dbt-list="" display="none"' in page
    assert 'data-dbt-h-open="' in page

    def card_height(out: str) -> float:
        match = re.search(r'id="chart-by_team"[^>]*data-chart-height="([\d.]+)"', out)
        assert match, out[:200]
        return float(match.group(1))

    assert card_height(page) < card_height(svg)


_DETAILS_BOARD = (
    _BOARD.split("rows:\n  - cols:")[0]
    + """rows:
  - details:
      summary: Breakdown
      expanded: true
    rows:
      - by_team
"""
)


def test_an_expanded_details_section_grows_border_and_all_on_a_live_page() -> None:
    page = _render("html", _DETAILS_BOARD)
    svg = _render("svg", _DETAILS_BOARD)

    section = page[page.index('<g data-dbt-flow-box=""><rect x="0" y="0"') :]
    border = section[: section.index("/>")]
    assert 'fill="none"' in border
    assert 'data-dbt-flow-bg=""' in border
    assert "data-dbt-flow" not in svg
