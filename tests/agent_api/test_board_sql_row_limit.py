"""Transient SQL on a board fetches one row past the caller's limit, not up to the ceiling."""

from pathlib import Path

from dbt_charts.agent_api.board_sql import execute_board_sql
from dbt_charts.cli.filesystem_project import FilesystemProject

from .._scripted_env import ScriptedBackend, backend_of, scripted_env

BOARD = """
queries:
  base:
    sql: SELECT 1 AS n
    source: wh
charts:
  c:
    query: base
    type: kpi
    value: n
rows:
  - c
"""


def test_the_fetch_is_bounded_to_the_limit_plus_one(tmp_path: Path) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(BOARD)
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: ':memory:'\n"
    )
    project = FilesystemProject(tmp_path)
    env = scripted_env(ScriptedBackend([{"n": i} for i in range(50)]), project=project)

    result = execute_board_sql(
        "SELECT n FROM {{ queries.base }}",
        Path("charts/b.yml"),
        project,
        env,
        None,
        limit=7,
    )

    assert result.success, result.errors
    sent = backend_of(env).execute.call_args.args[1]
    assert sent.limit.fetch_limit == 8
    assert result.row_count == 7
    assert result.truncated
