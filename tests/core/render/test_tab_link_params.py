"""A tab link's query string carries declared board variables, never render context."""

import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ..._run import Run

TABBED_BOARD = """
title: Tabs
variables:
  vendor:
    options:
      static: [Acme, Globex]
    default: Acme
  siblings:
    options:
      static: [mine, other]
    default: mine
queries:
  q:
    columns: [k, v]
    values:
      - [a, 1]
charts:
  c:
    query: q
    type: bar
    x: k
    y: v
    height: 120
tabs:
  items:
    - title: One
      rows:
        - c
    - title: Two
      rows:
        - c
"""


def test_tab_link_drops_injected_context_and_keeps_declared_variables() -> None:
    result = compile(TABBED_BOARD)
    assert result.success
    executor = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
    )

    rendered = render(
        result.board,
        executor.ctx,
        executor.ledger,
        format="svg",
        builtin_variables={
            "this_dir": {"name": "d"},
            "tree": "a.yml",
            "siblings": ["a.yml"],
        },
    )

    hrefs = re.findall(r'<a href="([^"]*)"[^>]*role="tab"', rendered.output)
    assert len(hrefs) == 1
    params = parse_qs(unescape(hrefs[0]).removeprefix("?"))
    # `siblings` is injected context here but the board declares its own.
    assert sorted(params) == ["_tab_tabs", "siblings", "vendor"]
    assert params["siblings"] == ["mine"]
