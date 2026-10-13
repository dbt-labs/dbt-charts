"""A KPI value under an ``email`` / ``url`` format links."""

from __future__ import annotations

from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from ..._svg_render import render_board_to_svg
from ..._text_format_board import chart_error_codes, link_hrefs, text_format_board


def test_kpi_with_an_email_value_links() -> None:
    rows = "      - {email: 'ann@example.com'}\n"
    chart = "    type: kpi\n    value: email\n"
    svg = render_board_to_svg(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    assert link_hrefs(svg) == ["mailto:ann@example.com"]


def test_kpi_chart_link_wins() -> None:
    rows = "      - {email: 'ann@example.com'}\n"
    chart = "    type: kpi\n    value: email\n    link: /who\n"
    svg = render_board_to_svg(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    assert link_hrefs(svg) == ["/who"]


def test_number_under_email_is_a_kind_error() -> None:
    rows = "      - {email: 42}\n"
    chart = "    type: kpi\n    value: email\n"
    board = text_format_board(rows, chart, formats="      email: email\n")
    assert chart_error_codes(board) == ["ERR-TABLE-FORMAT-KIND-MISMATCH"]


def test_empty_value_is_text_without_a_link() -> None:
    rows = "      - {email: ''}\n"
    chart = "    type: kpi\n    value: email\n"
    svg = render_board_to_svg(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    assert link_hrefs(svg) == []


def test_a_chart_link_means_the_value_is_not_checked_as_a_url() -> None:
    rows = "      - {site: www.example.com}\n"
    chart = "    type: kpi\n    value: site\n    link: /who\n"
    board = text_format_board(rows, chart, formats="      site: url\n")
    assert chart_error_codes(board) == []
    assert link_hrefs(render_board_to_svg(board)) == ["/who"]


def test_number_under_email_is_an_error_even_with_a_chart_link() -> None:
    rows = "      - {email: 42}\n"
    chart = "    type: kpi\n    value: email\n    link: /who\n"
    board = text_format_board(rows, chart, formats="      email: email\n")
    assert chart_error_codes(board) == ["ERR-TABLE-FORMAT-KIND-MISMATCH"]


def test_yaml_output_emits_no_link_for_a_format_implied_one() -> None:
    chart = "    type: kpi\n    value: email\n"
    rows = "      - {email: 'ann@example.com'}\n"
    result = compile_board(
        text_format_board(rows, chart, formats="      email: email\n")
    )
    board = result.board
    assert board is not None
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    out = render(board, executor, format="yaml").output
    assert "link:" not in out
    assert "mailto" not in out
