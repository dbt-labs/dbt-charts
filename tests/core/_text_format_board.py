"""Boards and probes shared by the ``email`` / ``url`` text-format tests."""

from __future__ import annotations

import re
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run


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
    executor = Run(
        board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )
    rendered = render(board, executor.ctx, executor.ledger, format="svg")
    return sorted({e.code for e in rendered.chart_errors})


def link_hrefs(svg: str) -> list[str]:
    return re.findall(r'<a href="([^"]*)"', svg)
