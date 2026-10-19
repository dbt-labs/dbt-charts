"""A chart filtered by a static-options variable scales to the rows it draws.

The removed stable-domain feature pinned the measure axis to the union of the
*filtered* option states and never included the unfiltered state the board
actually renders in. On the dundersign funnel board that put ``leads_trend_area``
on a ``[3, 105]`` domain against data reaching 624 — every mark painted outside
the scale, silently for area and as ERR-CHART-PAINTED-NO-MARKS for the bar
beside it. The axis must span the data the chart was handed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    get_default_theme_name,
    get_theme_style,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.render.chart.emitters import get_emitter
from dbt_charts.core.render.chart.spec import RenderBox

# Seven sources, three months. Filtering to any single source caps a monthly
# total at 105; the unfiltered board render — no source selected — reaches 624.
_MONTHLY_COUNTS = {
    "s1": 105,
    "s2": 95,
    "s3": 90,
    "s4": 88,
    "s5": 85,
    "s6": 84,
    "s7": 77,
}
# Volume tapers month over month so the board has a real measure span to scale.
_MONTHS = {"2026-01": 0, "2026-02": -20, "2026-03": -40}
_FILTERED_MAX = max(_MONTHLY_COUNTS.values())
_UNFILTERED_MAX = sum(_MONTHLY_COUNTS.values())

_VALUES = ",\n          ".join(
    f"('{month}', '{source}', {count + delta})"
    for month, delta in _MONTHS.items()
    for source, count in _MONTHLY_COUNTS.items()
)


def _board_yaml(chart_type: str, chart_style: str) -> str:
    return f"""
variables:
  source_pick:
    label: Source
    input: multiselect
    options:
      static: [{", ".join(_MONTHLY_COUNTS)}]

queries:
  leads:
    type: sql
    source: mem
    sql: |
      SELECT month, SUM(n) AS lead_count
      FROM (VALUES
          {_VALUES}
      ) AS t(month, source, n)
      WHERE {{{{ filter('source', source_pick) }}}}
      GROUP BY month
      ORDER BY month

charts:
  trend:
    query: leads
    type: {chart_type}
    x: month
    y: lead_count
{chart_style}
rows:
  - trend
"""


_PINNED_DOMAIN = [0.0, 800.0]
_PINNED_STYLE = """    style:
      axis_y:
        scale:
          continuous:
            domain: [0, 800]
"""


def _resolved_y_scale(chart_type: str, chart_style: str, tmp_path: Path) -> Any:
    """Render the unfiltered board state and return the emitted VL y scale."""
    result = compile_yaml(
        _board_yaml(chart_type, chart_style),
        project_sources=ProjectSourcesConfig(
            sources={"mem": {"type": "duckdb", "path": ":memory:"}}
        ),
    )
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(tmp_path)),
        query_registry=result.query_registry,
    )
    chart = result.board.charts["trend"]
    data = executor.execute_query("leads", {})
    board_style = resolve_chart_style_context(get_theme_style(get_default_theme_name()))
    resolved = resolve_chart_with_runtime_inputs(
        chart,
        data,
        board_style,
        600.0,
        executor,
        {},
        filter_variables=frozenset(),
        link_open="page",
    )
    spec = get_emitter(resolved).emit(
        resolved, RenderBox(width=600.0, height=300.0), regroup((), data)
    )
    return spec.encoding["y"].get("scale", {})


def _upper_bound(y_scale: Any) -> float:
    """The top of the emitted scale — VL spells it `domain` or `domainMax`."""
    domain = y_scale.get("domain")
    if domain is not None:
        return float(domain[1])
    domain_max = y_scale.get("domainMax")
    assert domain_max is not None, f"y scale pins neither edge: {y_scale}"
    return float(domain_max)


@pytest.mark.parametrize("chart_type", ["area", "bar"])
def test_unfiltered_render_scales_to_its_own_data(
    chart_type: str, tmp_path: Path
) -> None:
    """No selection means every row is drawn, so the axis must reach their total."""
    y_scale = _resolved_y_scale(chart_type, "", tmp_path)

    upper = _upper_bound(y_scale)
    assert upper >= _UNFILTERED_MAX, (
        f"{chart_type} axis tops out at {upper}, below the {_UNFILTERED_MAX} it draws "
        f"(single-source states only reach {_FILTERED_MAX})"
    )


@pytest.mark.parametrize("chart_type", ["area", "bar"])
def test_authored_domain_still_pins_the_axis(chart_type: str, tmp_path: Path) -> None:
    """The escape hatch for a deliberately fixed axis: author the domain."""
    y_scale = _resolved_y_scale(chart_type, _PINNED_STYLE, tmp_path)

    assert y_scale.get("domain") == _PINNED_DOMAIN
