"""``links.open`` is the board's default link mode: a link that sets no ``open``
takes it; an explicit ``open`` wins; implied and in-page links stay a page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedTableChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.board_to_dict import board_to_dict

_ANCHOR_RE = re.compile(r"<a\b[^>]*>")

_BOARD = """
title: Test
{links}
queries:
  q:
    type: values
    rows:
      - {{region: North, revenue: 10, mail: a@b.c}}
      - {{region: South, revenue: 20, mail: d@e.f}}
charts:
  c:
{chart}
rows:
  - c
"""
_LINKS = "links:\n  open: modal\n"
_BAR = """    type: bar
    query: q
    x: region
    y: revenue
    link: {link}
"""
_TABLE = """    type: table
    query: q
    style:
      columns:
        region:
{column}
"""


def _svg(links: str, chart: str) -> str:
    result = compile_board(_BOARD.format(links=links, chart=chart))
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(result.board, executor, format="svg")
    assert not rendered.chart_errors, rendered.chart_errors
    assert isinstance(rendered.output, str)
    return rendered.output


def _anchors(svg: str, needle: str) -> list[str]:
    found = [a for a in _ANCHOR_RE.findall(svg) if needle in a]
    assert found, svg
    return found


def _bar(link: str) -> str:
    return _BAR.format(link=link)


_OBJECT_NO_OPEN = "\n      url: /d?region={{ x }}"


@pytest.mark.parametrize("link", ["/d?region={{ x }}", _OBJECT_NO_OPEN])
def test_a_chart_link_without_open_takes_the_board_default(link: str) -> None:
    anchors = _anchors(_svg(_LINKS, _bar(link)), "/d?region=")
    assert all('data-dbt-link-open="modal"' in a for a in anchors)


@pytest.mark.parametrize(
    ("explicit", "expected"), [("page", None), ("preview", "preview")]
)
def test_an_explicit_open_beats_the_board_default(
    explicit: str, expected: str | None
) -> None:
    link = f"{_OBJECT_NO_OPEN}\n      open: {explicit}"
    for anchor in _anchors(_svg(_LINKS, _bar(link)), "/d?region="):
        stamp = f'data-dbt-link-open="{expected}"'
        assert (stamp in anchor) if expected else ("data-dbt-link-open" not in anchor)


def test_a_board_without_links_keeps_every_link_a_page() -> None:
    svg = _svg("", _bar("/d?region={{ x }}"))
    assert "data-dbt-link-open" not in svg


def test_a_query_only_variable_link_stays_in_place() -> None:
    svg = _svg(_LINKS, _bar("?region={{ x }}"))
    assert "data-dbt-link-open" not in svg


def test_table_column_links_take_the_board_default() -> None:
    column = "          link: /d/{{ region }}\n          header_link: /h"
    svg = _svg(_LINKS, _TABLE.format(column=column))
    for needle in ("/d/North", "/h"):
        assert all('data-dbt-link-open="modal"' in a for a in _anchors(svg, needle)), (
            needle
        )


def test_a_table_column_explicit_open_beats_the_board_default() -> None:
    column = (
        "          link:\n            url: /d/{{ region }}\n            open: page\n"
        "          header_link:\n            url: /h\n            open: new_tab"
    )
    svg = _svg(_LINKS, _TABLE.format(column=column))
    assert "data-dbt-link-open" not in " ".join(_anchors(svg, "/d/North"))
    assert 'target="_blank"' in " ".join(_anchors(svg, "/h"))


def test_an_implied_email_link_stays_a_page() -> None:
    chart = """    type: table
    query: q
    style:
      columns:
        mail:
          format: email
"""
    svg = _svg(_LINKS, chart)
    assert "data-dbt-link-open" not in " ".join(_anchors(svg, "mailto:"))


def test_a_synthesized_automatic_link_takes_the_board_default() -> None:
    compiled = compile_board(
        _BOARD.format(
            links="links:\n  auto: true\n  open: preview\n", chart=_bar("null")
        )
    )
    assert compiled.success and compiled.board is not None, compiled.errors
    chart = compiled.board.charts["c"]
    assert isinstance(chart, BarChart) and chart.link is None
    resolved = resolve(
        chart,
        [{"region": "North", "revenue": 10}],
        resolve_chart_style_context(get_theme_style("clarity")),
        automatic_link_candidate="/auto/{{ x }}",
    )
    assert (resolved.link, resolved.link_open) == ("/auto/{{ x }}", "preview")


_NESTED = """title: Test
links:
  open: modal
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 10}
charts:
  c:
    type: bar
    query: q
    x: region
    y: revenue
    link: /d?region={{ x }}
rows:
  - title: Inner
%s    rows:
      - c
"""


def test_a_nested_board_takes_its_roots_default() -> None:
    result = compile_board(_NESTED % "")
    assert result.success and result.board is not None, result.errors
    assert result.board.charts["c"].link_open == "modal"


@pytest.mark.parametrize("authored", ["open: preview", "open: modal", "auto: true"])
def test_a_nested_board_that_authors_links_is_an_error(authored: str) -> None:
    result = compile_board(_NESTED % f"    links:\n      {authored}\n")
    assert not result.success
    assert [error.code for error in result.errors] == ["ERR-LINKS-NESTED"]
    assert "move `links:` to the root board" in result.errors[0].message


_INACTIVE_TAB = """
title: Test
links:
  open: modal
queries:
  q:
    type: values
    rows:
      - {region: North, revenue: 10}
charts:
  b:
    type: bar
    query: q
    x: region
    y: revenue
  t:
    type: table
    query: q
    style:
      columns:
        region:
          link: /d/{{ region }}
tabs:
  items:
    - title: One
      rows:
        - b
    - title: Two
      rows:
        - t
"""


def _inactive_tab_board() -> tuple[Board, Executor]:
    result = compile_board(_INACTIVE_TAB)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    return result.board, executor


def test_an_inactive_tab_column_link_takes_the_board_default() -> None:
    board, executor = _inactive_tab_board()
    resolved = build_resolved_board(board, executor, board.variable_defaults)[0]
    table = resolved.charts["t"]
    assert isinstance(table, ResolvedTableChart)
    assert table.columns is not None
    assert table.columns["region"].link_open == "modal"


def test_the_board_dict_column_link_takes_the_board_default() -> None:
    board, executor = _inactive_tab_board()
    dumped = board_to_dict(board, executor, board.variable_defaults)

    def linked(node: object) -> list[object]:
        if isinstance(node, dict):
            own = [node["link_open"]] if node.get("link") == "/d/{{ region }}" else []
            return own + [o for v in node.values() for o in linked(v)]
        if isinstance(node, list):
            return [o for v in node for o in linked(v)]
        return []

    assert linked(dumped) == ["modal"]
