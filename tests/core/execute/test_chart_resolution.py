"""Runtime-input chart resolution: what execute hands compile, and what compile does with it.

The measure axis is resolved from the rows the chart was actually handed. An
authored `scale.continuous.domain` is the only way to pin it; nothing else
rewrites it behind the author's back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs

_ORBIT_SQL = """
SELECT * FROM (VALUES
  ('LEO', 1, 3000),
  ('LEO', 2, 9000),
  ('LEO', 3, 15000),
  ('LEO', 4, 4000),
  ('GTO', 1, 1000),
  ('GTO', 2, 3000),
  ('GTO', 3, 5800),
  ('GTO', 4, 2000)
) AS t(orbit, launch_idx, mass_kg)
WHERE orbit = '{{ orbit_pick }}'
"""

_VARIABLES_YAML = """
variables:
  orbit_pick:
    input: select
    default: LEO
    options:
      static: [LEO, GTO]
"""


def _indent(text: str) -> str:
    return "\n".join(f"      {line}" for line in text.strip("\n").splitlines())


def _board_yaml(chart_yaml: str, sql: str = _ORBIT_SQL) -> str:
    return f"""{_VARIABLES_YAML}
queries:
  payloads:
    type: sql
    source: mem
    sql: |
{_indent(sql)}
charts:
{chart_yaml}
rows:
  - chart1
"""


def _executor(yaml_content: str, tmp_path: Path) -> tuple[Executor, dict]:
    from dbt_charts.core.compile.config import ProjectSourcesConfig

    sources = ProjectSourcesConfig(
        sources={"mem": {"type": "duckdb", "path": ":memory:"}}
    )
    result = compile(yaml_content, project_sources=sources)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    return executor, result.board.charts


def _board_style():
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _resolved_domain(chart, executor, variables) -> list | None:
    """Resolve with runtime inputs and return the final y-domain."""
    data = executor.execute_query("payloads", variables)
    resolved = resolve_chart_with_runtime_inputs(
        chart,
        data,
        _board_style(),
        600.0,
        executor,
        variables,
        filter_variables=frozenset(),
        link_open="page",
    )
    scale = resolved.style.axis_y.scale
    if scale is None or scale.continuous is None:
        return None
    return scale.continuous.domain


@pytest.mark.parametrize("chart_type", ["bar", "line", "area"])
def test_categorical_measure_errors(tmp_path: Path, chart_type: str) -> None:
    """A categorical y would silently bake a NaN axis — refuse it instead."""
    from dbt_charts.core.compile.errors import CompilationError

    categorical_sql = """
SELECT * FROM (VALUES
  ('LEO', 1, 'A'),
  ('GTO', 2, 'B')
) AS t(orbit, launch_idx, category)
WHERE orbit = '{{ orbit_pick }}'
"""
    chart_yaml = f"""
  chart1:
    query: payloads
    type: {chart_type}
    x: launch_idx
    y: category
"""
    executor, charts = _executor(_board_yaml(chart_yaml, categorical_sql), tmp_path)
    chart = charts["chart1"]
    data = executor.execute_query("payloads", {"orbit_pick": "LEO"})

    with pytest.raises(CompilationError, match="not numeric"):
        resolve_chart_with_runtime_inputs(
            chart,
            data,
            _board_style(),
            600.0,
            executor,
            {"orbit_pick": "LEO"},
            filter_variables=frozenset(),
            link_open="page",
        )


def test_swapped_area_encoding_errors(tmp_path: Path) -> None:
    """A stacked area needs a repeatable dimension on x; a measure there is a bug."""
    from dbt_charts.core.compile.errors import CompilationError

    swapped_sql = """
SELECT * FROM (VALUES
  ('LEO', 10, 2020, 's1'),
  ('LEO', 20, 2021, 's2'),
  ('GTO', 30, 2020, 's1'),
  ('GTO', 40, 2021, 's2')
) AS t(orbit, value, year, series)
WHERE orbit = '{{ orbit_pick }}'
"""
    chart_yaml = """
  chart1:
    query: payloads
    type: area
    x: value
    y: year
    color: series
    style:
      stack: center
"""
    executor, charts = _executor(_board_yaml(chart_yaml, swapped_sql), tmp_path)
    chart = charts["chart1"]
    data = executor.execute_query("payloads", {"orbit_pick": "LEO"})

    with pytest.raises(CompilationError, match="repeatable dimension"):
        resolve_chart_with_runtime_inputs(
            chart,
            data,
            _board_style(),
            600.0,
            executor,
            {"orbit_pick": "LEO"},
            filter_variables=frozenset(),
            link_open="page",
        )


def test_authored_domain_pins_the_axis(tmp_path: Path) -> None:
    """The one way to hold an axis still across filter changes: author it."""
    chart_yaml = """
  chart1:
    query: payloads
    type: scatter
    x: launch_idx
    y: mass_kg
    style:
      axis_y:
        scale:
          continuous:
            domain: [0, 999]
"""
    executor, charts = _executor(_board_yaml(chart_yaml), tmp_path)
    chart = charts["chart1"]

    assert _resolved_domain(chart, executor, {"orbit_pick": "LEO"}) == (0, 999)
    assert _resolved_domain(chart, executor, {"orbit_pick": "GTO"}) == (0, 999)
