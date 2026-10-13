"""A chart-local color token no palette resolves errors in that chart's tile."""

from __future__ import annotations

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render import render

BOARD = """
queries:
  q:
    type: values
    rows:
      - {{a: x, b: 1}}
      - {{a: y, b: 2}}
charts:
  good:
    type: bar
    query: q
    x: a
    y: b
  bad:
    query: q
{bad_chart}
rows:
  - good
  - bad
"""

BAD_CHARTS = {
    "style": """
    type: bar
    x: a
    y: b
    style:
      color:
        static: nosuch.shade
""",
    "conditional_formatting": """
    type: table
    conditional_formatting:
      b:
        when:
          - gt: 0
            background: nosuch.shade
""",
    "layer": """
    type: bar
    x: a
    y: b
    layers:
      - type: line
        y: b
        style:
          marks:
            line:
              stroke:
                color: nosuch.shade
""",
}


@pytest.mark.parametrize("path", BAD_CHARTS)
def test_unknown_chart_local_color_token_errors_only_that_chart(
    tmp_path, path: str
) -> None:
    project = FilesystemProject(tmp_path)
    result = compile(
        BOARD.format(bad_chart=BAD_CHARTS[path].rstrip("\n")),
        base_dir=project.directory(),
    )
    assert result.success, [d.message for d in result.errors]
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    out = render(result.board, executor, format="svg")

    assert out.board_error is None
    assert [d.code for d in out.chart_errors] == ["ERR-COLOR-TOKEN"]
    message = out.chart_errors[0].message
    assert "'bad'" in message
    assert "nosuch.shade" in message
