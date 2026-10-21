"""An integer column prints whole, a year column prints as a plain year."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.compile.format import inferred_number_format
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import InMemoryBoard

_BOARD = """
title: Types
source: mem
queries:
  cars:
    sql: |
      SELECT 1975 AS model_year, SUM(x) AS units, 2.5 AS mpg
      FROM (SELECT 1234567::INTEGER AS x UNION ALL SELECT 2::INTEGER)
charts:
  t:
    query: cars
    type: table
rows:
  - t
"""


@pytest.mark.parametrize(
    ("name", "values", "expected"),
    [
        ("model_year", [1970, 1982, None], "year"),
        ("units", [1200, 5], "integer"),
        ("year_count", [1200, 50000], "integer"),
        ("units", [1.5, 2], None),
        ("units", [True, False], None),
        ("units", [None], None),
    ],
)
def test_inferred_number_format(
    name: str, values: list[object], expected: str | None
) -> None:
    assert inferred_number_format(name, values) == expected


def test_table_prints_year_and_hugeint_sum_plain(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"
    )
    project = local_project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(_BOARD, path=project.path("charts/_t.yml")),
        project=project,
        adapter_registry=build_adapter_registry(project, read_only=False),
        result_cache=None,
        format="text",
    )
    assert result.status == "ok", [e.message for e in result.validation_errors]
    assert isinstance(result.data, str)
    assert "| 1975 | 1,234,569 | 2.5 |" in result.data
