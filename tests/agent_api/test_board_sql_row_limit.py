"""Transient SQL on a board fetches one row past the caller's limit, not up to the ceiling."""

from pathlib import Path

from dbt_charts.agent_api.board_sql import execute_board_sql
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.column_types import ColumnType, TypedRows

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
    assert sent.limit.limit == 8
    assert result.row_count == 7
    assert result.truncated


def test_a_byte_cut_result_reports_truncated(tmp_path: Path) -> None:
    """Cut at max_result_bytes, the result holds fewer rows than the limit."""
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(BOARD)
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: ':memory:'\n"
    )
    project = FilesystemProject(tmp_path)

    def stopped(*_args: object) -> TypedRows:
        return TypedRows(
            ["n"],
            [(1,), (2,)],
            [ColumnType(kind="integer")],
            truncated_reason="max_result_bytes",
        )

    env = scripted_env(ScriptedBackend(side_effect=stopped), project=project)

    result = execute_board_sql(
        "SELECT n FROM {{ queries.base }}",
        Path("charts/b.yml"),
        project,
        env,
        None,
        limit=7,
    )

    assert result.success, result.errors
    assert result.row_count == 2
    assert result.truncated


VALUES_BOARD = """
queries:
  vals:
    columns: [n]
    values: [[0], [1], [2], [3], [4], [5], [6], [7], [8], [9]]
charts:
  c:
    query: vals
    type: table
rows:
  - c
"""


def test_a_sample_limit_does_not_cut_a_values_upstream_or_its_cache(
    tmp_path: Path,
) -> None:
    """The limit bounds the agent's own query; the shared cache holds each
    upstream whole."""
    from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(VALUES_BOARD)
    project = FilesystemProject(tmp_path)
    env = scripted_env(ScriptedBackend([]), project=project)
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")

    counted = execute_board_sql(
        "SELECT count(*) AS n FROM {{ queries.vals.cache }}",
        Path("charts/b.yml"),
        project,
        env,
        cache,
        limit=3,
    )
    cached = execute_board_sql(
        "SELECT n FROM {{ queries.vals.cache }}",
        Path("charts/b.yml"),
        project,
        env,
        cache,
        limit=50,
    )

    assert counted.success, counted.errors
    assert counted.data == [{"n": 10}]
    assert cached.success, cached.errors
    assert cached.row_count == 10
