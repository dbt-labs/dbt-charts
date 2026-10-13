"""A table cell under an ``email`` / ``url`` format links its value."""

from __future__ import annotations

import json
import re
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

from ..._svg_render import render_board_to_svg
from ..._text_format_board import ROWS, chart_error_codes, link_hrefs, text_format_board


def test_email_and_url_columns_link_their_values() -> None:
    found = link_hrefs(render_board_to_svg(text_format_board(ROWS)))
    assert "mailto:ann%2Bx%40example.com" in found
    assert "https://a.example/p?q=1" in found
    assert len(found) == 2


def test_authored_column_link_wins() -> None:
    chart = (
        "    type: table\n    style:\n      columns:\n        email:\n"
        "          link: '/people/{{ name }}'\n"
    )
    found = link_hrefs(render_board_to_svg(text_format_board(ROWS, chart)))
    assert "/people/Ann" in found
    assert not any(h.startswith("mailto:") for h in found)


def test_filter_link_wins() -> None:
    board = text_format_board(ROWS).replace(
        "queries:", "variables:\n  email:\n    input: text\nqueries:", 1
    )
    found = link_hrefs(render_board_to_svg(board))
    assert "?email=ann%2Bx%40example.com" in found
    assert not any(h.startswith("mailto:") for h in found)


def test_email_column_stays_in_the_text_lane() -> None:
    svg = render_board_to_svg(text_format_board(ROWS))
    text = re.search(r"<text[^>]*>[^<]*ann\+x@example\.com", svg)
    assert text is not None
    assert "tabular" not in text.group(0)
    assert 'text-anchor="end"' not in text.group(0)


def test_scheme_less_url_raises() -> None:
    rows = "      - {site: www.example.com}\n"
    assert chart_error_codes(text_format_board(rows, formats="      site: url\n")) == [
        "ERR-URL-VALUE-NOT-ABSOLUTE"
    ]


def test_number_under_email_is_a_kind_error() -> None:
    rows = "      - {email: 42}\n"
    assert chart_error_codes(
        text_format_board(rows, formats="      email: email\n")
    ) == ["ERR-TABLE-FORMAT-KIND-MISMATCH"]


def test_resolved_board_carries_the_link() -> None:
    result = compile_board(text_format_board(ROWS))
    board = result.board
    assert board is not None
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    out = render(board, executor, format="json").output
    assert "mailto:{{ email | urlencode }}" in json.dumps(out)


def test_json_output_keeps_a_filter_link_over_the_text_link() -> None:
    board_yaml = text_format_board(ROWS).replace(
        "queries:", "variables:\n  email:\n    input: text\nqueries:", 1
    )
    result = compile_board(board_yaml)
    board = result.board
    assert board is not None
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    assert "mailto" not in json.dumps(render(board, executor, format="json").output)


def test_a_url_param_that_is_not_a_declared_variable_keeps_the_text_link() -> None:
    result = compile_board(text_format_board(ROWS))
    board = result.board
    assert board is not None
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    out = render(board, executor, format="json", variables={"email": "x"}).output
    assert "mailto" in json.dumps(out)
