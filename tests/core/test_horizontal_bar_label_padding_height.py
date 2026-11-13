"""`axis_x.labels.padding` on a horizontal bar is the label-to-axis gap, so it must not size the board."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run

_BOARD = """
width: 480
queries:
  q:
    columns: [month, stage, accounts]
    values:
      - [Jan, Trial, 10]
      - [Jan, Paid, 20]
      - [Feb, Trial, 17]
      - [Feb, Paid, 27]
      - [Mar, Trial, 24]
      - [Mar, Paid, 14]
      - [Apr, Trial, 11]
      - [Apr, Paid, 21]
charts:
  c:
    query: q
    type: bar
    x: month
    y: accounts
    color: stage
    height: 240
    style:
      stack: zero
{style}
rows:
  - c
"""
_PADDING = """      axis_x:
        labels:
          padding: 25
"""


def _board_height(
    yaml_body: str, local_project: Callable[..., FilesystemProject]
) -> float:
    result = compile_board(yaml_body)
    assert result.success, result.errors
    assert result.board is not None
    executor = Run(
        result.board,
        env=build_exec_env(local_project(Path.cwd())),
    )
    output = render(result.board, executor.ctx, executor.ledger, format="svg").output
    assert isinstance(output, str)
    match = re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', output)
    assert match, output[:200]
    return float(match.group(1))


def test_label_padding_does_not_change_board_height(
    local_project: Callable[..., FilesystemProject],
) -> None:
    plain = _board_height(_BOARD.format(style=""), local_project)
    padded = _board_height(_BOARD.format(style=_PADDING), local_project)
    assert padded == plain
