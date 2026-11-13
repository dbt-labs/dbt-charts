"""A daterange variable whose value came from a preset publishes the preset id, read from the plan."""

import re
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from ..._run import Run
from .._svg_render import pinned_clock

_BOARD = """
variables:
  span:
    input: daterange
    default: last_30_days
  fixed:
    input: daterange
    default: [2025-01-01, 2025-01-31]
queries:
  q:
    type: values
    rows:
      - {n: 1}
charts:
  k:
    type: kpi
    query: q
    value: n
rows: [k]
"""


def _run(variables: dict[str, object]) -> tuple[Run, str]:
    result = compile(_BOARD)
    assert result.success
    run = Run(
        result.board,
        env=build_exec_env(FilesystemProject(Path.cwd())),
        clock=pinned_clock,
        variables=variables,
    )
    svg = render(
        result.board, run.ctx, run.ledger, format="svg", variables=variables
    ).output
    assert isinstance(svg, str)
    return run, svg


@pytest.mark.parametrize(
    ("variables", "preset"),
    [({}, "last_30_days"), ({"span": "last_7_days"}, "last_7_days")],
)
def test_a_preset_is_in_the_plan_and_published(
    variables: dict[str, object], preset: str
) -> None:
    run, svg = _run(variables)

    assert run.ctx.plan["variables"]["presets"] == {"span": preset}
    assert re.search(rf'data-dbt-variable="span"[^>]*data-dbt-value="{preset}"', svg)


def test_fixed_dates_have_no_preset() -> None:
    run, svg = _run({"span": ["2025-02-01", "2025-02-28"]})

    assert run.ctx.plan["variables"]["presets"] == {}
    assert 'data-dbt-value="last_30_days"' not in svg
