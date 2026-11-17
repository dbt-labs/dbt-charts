"""A chart the engine draws in its cell is drawn only inside a board render: a board of one
chart through ``render()``."""

from pathlib import Path
from typing import Any

import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

from .._run import Run


def render_chart_board(
    chart: dict[str, Any],  # type-state: explicit_any — authored chart YAML
    queries: dict[str, Any]
    | None = None,  # type-state: explicit_any — authored queries YAML
    board_style: dict[str, Any]
    | None = None,  # type-state: explicit_any — authored board style YAML
) -> RenderResult:
    """Render a board of the one chart ``c`` over ``queries``; the chart errors are the
    caller's to read."""
    board: dict[str, Any] = {  # type-state: explicit_any — authored board YAML
        "charts": {"c": chart},
        "rows": ["c"],
    }
    if queries:
        board["queries"] = queries
    if board_style:
        board["style"] = board_style
    result = compile_board(yaml.safe_dump(board))
    assert result.success, result.errors
    assert result.board is not None
    run = Run(result.board, env=build_exec_env(FilesystemProject(Path.cwd())))
    return render(result.board, run.ctx, run.ledger, format="svg")


def chart_svg(
    chart: dict[str, Any],  # type-state: explicit_any — authored chart YAML
    queries: dict[str, Any]
    | None = None,  # type-state: explicit_any — authored queries YAML
    board_style: dict[str, Any]
    | None = None,  # type-state: explicit_any — authored board style YAML
) -> str:
    """The SVG of a board of one chart, which must draw without a chart error."""
    rendered = render_chart_board(chart, queries, board_style)
    if rendered.chart_errors:
        raise AssertionError([(e.code, e.message) for e in rendered.chart_errors])
    assert isinstance(rendered.output, str)
    return rendered.output
