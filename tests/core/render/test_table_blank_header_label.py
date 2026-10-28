"""`label: ""` on a table column prints a blank header, not the humanized name."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

_BOARD = """\
queries:
  q:
    type: values
    rows:
      - {{plan_name: Al, seats_n: 3}}
charts:
  tbl:
    query: q
    type: table
    style:
      columns:
        plan_name:
          label: {label}
rows:
  - tbl
"""


@pytest.fixture(autouse=True)
def _reset_config() -> None:
    reset_config()


def _svg(label: str, local_project: Callable[..., FilesystemProject]) -> str:
    result = compile_board(_BOARD.format(label=label))
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    svg = render(result.board, executor, format="svg").output
    assert isinstance(svg, str)
    return svg


def test_empty_label_is_a_blank_header(
    local_project: Callable[..., FilesystemProject],
) -> None:
    blank = _svg('""', local_project)
    assert "Plan Name" not in blank
    assert "Seats" in blank
    assert "Plan Name" in _svg("Plan Name", local_project)
