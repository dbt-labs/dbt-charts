"""A source that cannot honor the board's time zone is reported, not hidden."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.diagnostics.codes_execute import (
    WARN_SESSION_TIME_ZONE_UNSUPPORTED,
    WARN_SOURCE_TIME_ZONE_OVERRIDDEN,
)
from dbt_charts.core.session_time_zone_check import check_session_time_zones

_SOURCES = """
sources:
  lite:
    type: sqlite
    path: x.db
  duck:
    type: duckdb
    path: ":memory:"
  duck_tokyo:
    type: duckdb
    path: ":memory:"
    settings:
      TimeZone: Asia/Tokyo
  trino_tokyo:
    type: trino
    host: trino.example.com
    catalog: lake
    schema: public
    user: ana
    timezone: Asia/Tokyo
"""

_BOARD = """
regional:
  time_zone: {zone}
queries:
  q:
    source: {source}
    sql: SELECT 1 AS n
charts:
  c:
    query: q
    type: table
rows:
  - c
"""


def _codes(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    source: str,
    zone: str = "America/New_York",
) -> list[str]:
    (tmp_path / "dbt_charts.yml").write_text(_SOURCES)
    project = local_project(tmp_path)
    result = compile(
        _BOARD.format(zone=zone, source=source), base_dir=project.directory()
    )
    assert result.success, [d.message for d in result.errors]
    compiled = len(result.warnings)
    check_session_time_zones(result, project)
    return [d.code for d in result.warnings[compiled:]]


def test_a_warehouse_with_no_session_zone_warns_off_utc(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _codes(tmp_path, local_project, "lite") == [
        WARN_SESSION_TIME_ZONE_UNSUPPORTED.code
    ]


def test_a_warehouse_with_no_session_zone_is_silent_on_a_utc_board(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _codes(tmp_path, local_project, "lite", zone="UTC") == []


def test_a_warehouse_with_a_session_zone_is_silent(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    assert _codes(tmp_path, local_project, "duck") == []


@pytest.mark.parametrize("source", ["duck_tokyo", "trino_tokyo"])
def test_a_source_zone_that_differs_from_the_board_warns(
    tmp_path: Path, local_project: Callable[..., FilesystemProject], source: str
) -> None:
    assert _codes(tmp_path, local_project, source) == [
        WARN_SOURCE_TIME_ZONE_OVERRIDDEN.code
    ]
