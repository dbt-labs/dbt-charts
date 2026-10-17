"""A static HTML export ships the host stylesheet even with no runtime.

The board carries no interaction of its own (a board is a picture), so the page
around it must: a static export is still a browser page with real `<a href>`
links, a text cursor and a hover underline. Without the stylesheet the inert
cell text catches the pointer and a row's link is unreachable from its own
values. Only the runtime is gated on a host that can act on it.
"""

from dbt_charts.core.render.converters.html import to_html

from .._svg_render import render_board_to_svg


def test_a_static_export_carries_the_host_stylesheet_but_no_runtime() -> None:
    page = to_html(render_board_to_svg(), controls=False)

    assert ".dbt-chart text.dbt-table-cell-inert" in page
    assert ".dbt-table-row:hover .dbt-table-row-link" in page
    assert "dbtControls" not in page


def test_a_live_page_carries_both() -> None:
    page = to_html(render_board_to_svg(), controls=True)

    assert ".dbt-chart text.dbt-table-cell-inert" in page
    assert "dbtControls" in page
