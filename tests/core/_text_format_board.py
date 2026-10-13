"""Boards and probes shared by the ``email`` / ``url`` text-format tests."""

from __future__ import annotations

import re
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render


def text_format_board(
    rows: str,
    chart: str = "    type: table\n",
    *,
    formats: str = "      email: email\n      site: url\n",
    style: str = "",
) -> str:
    return f"""
title: T
{style}
queries:
  q:
    type: values
    rows:
{rows}
    formats:
{formats}
charts:
  c:
    query: q
{chart}
rows:
  - c
"""


ROWS = (
    "      - {name: Ann, email: 'ann+x@example.com', site: 'https://a.example/p?q=1'}\n"
    "      - {name: Bob, email: '', site: ''}\n"
)


def chart_error_codes(yaml: str) -> list[str]:
    result = compile_board(yaml)
    board = result.board
    assert result.success and board is not None
    executor = Executor(
        board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    rendered = render(board, executor, format="svg")
    return sorted({e.code for e in rendered.chart_errors})


def link_hrefs(svg: str) -> list[str]:
    return re.findall(r'<a href="([^"]*)"', svg)
