"""An ad-hoc query has no board, yet a preset literal in its SQL still dates."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.query import execute_query
from dbt_charts.core.compile.template.daterange import current_board_clock
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project

_SQL = (
    "SELECT 'hit' AS w FROM (SELECT current_date AS d) t "
    "WHERE {{ filter_date_range('d', 'last_7_days') }}"
)


def test_an_ad_hoc_query_resolves_a_preset_literal(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        'sources:\n  db:\n    type: duckdb\n    path: ":memory:"\n'
    )
    registry = build_adapter_registry(local_project(tmp_path))

    result = execute_query(_SQL, source="db", adapter_registry=registry)

    assert result.success, result.errors
    assert result.data == [{"w": "hit"}]


def test_a_missing_board_clock_is_a_coded_error() -> None:
    with pytest.raises(DbtChartsError):
        current_board_clock()
