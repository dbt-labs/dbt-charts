"""End-to-end: a board whose filter leaves a chart no rows draws the quiet "No results"
card naming the filter, and a KPI over it a muted dash.

Proves the full seam -- compile() -> render() -> the engine payload's `empty_filters` -> the
SVG. Dropping the `variable_defs(...)` scope in `boards.py` or `renderer.py` leaves the
filter line out while every direct `empty_filters` unit test stays green.
"""

from __future__ import annotations

import re

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.compile import compile
from dbt_charts.core.render import render

from ..._run import Run
from ..._scripted_env import ScriptedBackend, scripted_env, typed_rows

_YAML = """
variables:
  region:
    input: select
    label: Region
    options:
      static: [EMEA, APAC]
queries:
  q:
    source: test_source
    sql: SELECT category, amount FROM t WHERE region = '{{ region }}'
charts:
  bars:
    query: q
    type: bar
    x: category
    y: amount
  total:
    query: q
    type: kpi
    value: amount
rows:
  - bars
  - total
"""

_NO_ROWS = typed_rows(
    [],
    {"category": ColumnType(kind="string"), "amount": ColumnType(kind="float")},
)


def _svg(region: str) -> str:
    result = compile(_YAML)
    assert result.success and result.board is not None, result.errors
    backend = ScriptedBackend(side_effect=lambda _source, _query: _NO_ROWS)
    executor = Run(result.board, env=scripted_env(backend))
    rendered = render(
        result.board,
        executor.ctx,
        executor.ledger,
        format="svg",
        variables={"region": region},
    )
    assert rendered.board_error is None, rendered.board_error
    assert isinstance(rendered.output, str)
    return rendered.output


def test_an_empty_chart_names_the_filter_that_reaches_it() -> None:
    svg = _svg("EMEA")

    assert svg.count(">No results</text>") == 1, "the bar chart's headline"
    assert ">Region: EMEA</text>" in svg


def test_an_empty_kpi_draws_a_dash_and_the_filter() -> None:
    svg = _svg("EMEA")
    kpi = svg[svg.index('data-chart-id="total"') :]
    text = re.sub(r"<[^>]+>", "|", kpi)

    assert "|\u2014|" in text
    assert "|No results \u00b7 Region: EMEA|" in text
