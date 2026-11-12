"""A run-time source compiles as declared, so the plan carries it."""

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.engine import engine_compile
from dbt_charts.core.execute.plan import utc_now
from dbt_charts.core.execute.planned import plan_board

BOARD = """\
queries:
  q:
    source: synth
    sql: select 1 as a
charts:
  t:
    type: table
    query: q
    columns:
      - a
rows:
  - t
"""


def project(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "b.yml").write_text(BOARD, encoding="utf-8")
    return FilesystemProject(tmp_path)


def test_the_plan_carries_a_synthetic_source_beside_the_declared_ones(
    tmp_path: Path,
) -> None:
    compiled = engine_compile(
        project(tmp_path).path("b.yml"),
        synthetic_sources={"synth": {"type": "duckdb", "path": ":memory:"}},
    )
    plan = plan_board(
        compiled,
        None,
        builtins=None,
        now=utc_now(),
        strict=False,
        active_tab_only=False,
        only_charts=None,
    )
    assert set(plan["sources"]["project"]) == {"wh", "synth"}


def test_a_synthetic_source_named_like_a_declared_one_is_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="also declared"):
        engine_compile(
            project(tmp_path).path("b.yml"),
            synthetic_sources={"wh": {"type": "duckdb", "path": ":memory:"}},
        )
