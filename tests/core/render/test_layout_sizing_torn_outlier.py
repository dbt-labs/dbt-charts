"""A torn bar chart's cap band sits on top of the plot it would otherwise have."""

from __future__ import annotations

import re

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env

from ..._run import Run, render_run

_YAML = """
title: Torn Height
queries:
  q:
    columns: [month, revenue]
    values:
      - [A, 92]
      - [B, 86]
      - [C, 145]
      - [D, 255]
charts:
  torn:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      orientation: vertical
      torn_outlier:
        enabled: true
        at: 110
        band: 0.5
  plain:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      orientation: vertical
rows:
  - torn
  - plain
"""


def _chart_heights(tmp_path, orientation: str = "vertical") -> dict[str, float]:
    result = compile_board(_YAML.replace("vertical", orientation))
    assert result.success and result.board is not None, result.errors
    run = Run(result.board, build_exec_env(FilesystemProject(tmp_path)))
    svg = render_run(result.board, run, format="svg").output
    assert isinstance(svg, str)
    heights = {}
    for chart_id in ("torn", "plain"):
        start = svg.index(f'data-authored-path="charts.{chart_id}"')
        found = re.search(
            r'class="marks" width="[0-9.]+" height="([0-9.]+)"', svg[start:]
        )
        assert found is not None, chart_id
        heights[chart_id] = float(found.group(1))
    return heights


def test_a_torn_chart_grows_by_its_cap_band(tmp_path):
    heights = _chart_heights(tmp_path)
    # Both slots share the card padding; the torn one's content is 1.5x the plain one's.
    assert heights["torn"] > 1.3 * heights["plain"], heights


def test_a_horizontal_torn_chart_takes_its_band_from_its_width(tmp_path):
    heights = _chart_heights(tmp_path, "horizontal")
    assert heights["torn"] == heights["plain"], heights
