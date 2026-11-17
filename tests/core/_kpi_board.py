"""A KPI is drawn only inside a board render: one KPI board through ``render()``."""

from pathlib import Path
from typing import Any

import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render
from dbt_charts.core.render.render_result import RenderResult

from .._run import Run


def render_kpi_board(
    chart: dict[str, Any],  # type-state: explicit_any — authored chart YAML
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> RenderResult:
    """Render a board of one KPI over *rows*; the chart errors are the caller's to read."""
    text = yaml.safe_dump(
        {
            "queries": {"q": {"type": "values", "rows": rows}},
            "charts": {"k": {"type": "kpi", "query": "q", **chart}},
            "rows": ["k"],
        }
    )
    result = compile_board(text)
    assert result.success, result.errors
    assert result.board is not None
    run = Run(result.board, env=build_exec_env(FilesystemProject(Path.cwd())))
    return render(result.board, run.ctx, run.ledger, format="svg")


def kpi_svg(
    chart: dict[str, Any],  # type-state: explicit_any — authored chart YAML
    rows: list[dict[str, Any]],  # type-state: explicit_any — query rows
) -> str:
    """The SVG of a board of one KPI, which must draw without a chart error."""
    rendered = render_kpi_board(chart, rows)
    if rendered.chart_errors:
        raise AssertionError([(e.code, e.message) for e in rendered.chart_errors])
    assert isinstance(rendered.output, str)
    return rendered.output
