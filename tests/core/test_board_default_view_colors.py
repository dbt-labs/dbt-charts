"""A filtered render colors its series as the board's default view ranks them."""

from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.execute.env import build_exec_env

# Under the default scenario `a` is far larger; under `alt`, `b` is.
_BOARD = """
    title: Scenarios
    source: db
    variables:
      scenario:
        input: select
        default: base
        options:
          static: [base, alt]
    queries:
      q:
        sql: >-
          SELECT m, s, v FROM (VALUES
            ('base', 'Jan', 'a', 100), ('base', 'Jan', 'b', 10),
            ('alt', 'Jan', 'a', 1), ('alt', 'Jan', 'b', 50)
          ) AS t(scenario, m, s, v)
          WHERE scenario = '{{ scenario }}'
    charts:
      c:
        query: q
        type: bar
        x: m
        y: v
        color: s
        style:
          legend:
            position:
              edge: right
    rows:
      - c
    """

_SWATCH = re.compile(
    r'role-legend-symbol"[^>]*>.*?(?:fill|stroke)="(#[0-9a-fA-F]{6})".*?</g>'
    r'<g class="mark-text role-legend-label"[^>]*>.*?<text[^>]*>([^<]+)(?:<title>[^<]*</title>)?</text>'
)


def _fills(tmp_path: Path, scenario: str) -> dict[str, str]:
    reset_config()
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "charts").mkdir(exist_ok=True)
    (tmp_path / "charts" / "board.yml").write_text(dedent(_BOARD))
    project = FilesystemProject(tmp_path)
    result = render_dashboard(
        board=project.path("charts/board.yml").read_board(),
        variables={"scenario": scenario},
        project=project,
        env=build_exec_env(project, read_only=False),
        result_cache=None,
        format="svg",
    )
    assert result.status != "failed", result.validation_errors or result.board_error
    assert isinstance(result.data, str)
    fills = {label: fill for fill, label in _SWATCH.findall(result.data)}
    assert set(fills) == {"a", "b"}, fills
    return fills


def test_a_filter_that_flips_the_ranking_keeps_each_series_color(
    tmp_path: Path,
) -> None:
    assert _fills(tmp_path, "alt") == _fills(tmp_path, "base")
