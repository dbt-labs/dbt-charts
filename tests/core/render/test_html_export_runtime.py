"""What a static HTML export ships around its board.

The board carries no interaction of its own (a board is a picture), so the page
around it must: the host stylesheet always, and every runtime that needs no
host to act (chart hover, table scroll, sort, filter and paging). Only the
variable controls and the link modal wait for a host that can re-run a query
or serve another board.
"""

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.converters.html import to_html
from dbt_charts.core.render_format import RenderFormat

from ..._run import Run
from .._svg_render import pinned_clock, render_board_to_svg

_PAGED_TABLE = """
queries:
  q:
    type: values
    rows:
"""
_PAGED_TABLE += "".join(f"      - {{name: n{i}, value: {i}}}\n" for i in range(30))
_PAGED_TABLE += """
charts:
  t:
    query: q
    type: table
    style:
      pagination:
        enabled: true
        page_rows: 10
rows:
  - t
"""


def _render(fmt: RenderFormat) -> str:
    result = compile(_PAGED_TABLE)
    assert result.success
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
        clock=pinned_clock,
    )
    return render(result.board, executor.ctx, executor.ledger, format=fmt).output


def test_a_static_export_ships_every_runtime_that_needs_no_host() -> None:
    page = to_html(render_board_to_svg(), controls=False)

    assert ".dbt-chart text.dbt-table-cell-inert" in page
    assert "window.dbtChartHover" in page
    assert "window.dbtTableRuntime" in page
    assert "window.dbtControls = {" not in page
    assert "window.dbtLinkModal" not in page


def test_a_live_page_carries_the_controls_runtime() -> None:
    page = to_html(render_board_to_svg(), controls=True)

    assert ".dbt-chart text.dbt-table-cell-inert" in page
    assert "window.dbtControls = {" in page


def test_an_html_export_pools_its_tables_for_the_table_runtime() -> None:
    page = _render("html")

    assert 'data-dbt-table="' in page
    assert page.count("<script") == 1, "the board embeds no pager of its own"


def test_an_svg_export_keeps_its_own_pager() -> None:
    svg = _render("svg")

    assert 'data-dbt-table="' not in svg
    assert "data-dbt-table-page=" in svg
