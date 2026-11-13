"""execute_board_sql: SQL run as a transient query of a stored board."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.agent_api.board_sql import execute_board_sql
from dbt_charts.agent_api.query import ExecuteQueryResult
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute import run as run_stage
from dbt_charts.core.execute.env import ExecEnv, build_exec_env
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_BOARD = """
source: db
variables:
  region:
    input: text
    default: US
queries:
  rev:
    sql: |
      select region, amt
      from (values ('US', 10), ('EU', 20)) as t(region, amt)
      where region = {{ region }}
  cost:
    sql: |
      select region, spend
      from (values ('US', 4), ('EU', 5)) as t(region, spend)
      where region = {{ region }}
  uncached:
    cache: false
    sql: select 1 as n
charts:
  c:
    query: rev
    type: table
rows:
  - c
"""


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "rev.yml").write_text(_BOARD)
    return local_project(tmp_path)


@pytest.fixture
def registry(project: FilesystemProject) -> ExecEnv:
    return build_exec_env(project, read_only=False)


@pytest.fixture
def cache() -> Iterator[TrivialDuckDBCache]:
    cache = TrivialDuckDBCache()
    try:
        yield cache
    finally:
        cache.close()


def _run(
    sql: str,
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache | None,
    variables: dict[str, Any] | None = None,
) -> ExecuteQueryResult:
    return execute_board_sql(
        sql, Path("rev.yml"), project, registry, cache, variables=variables
    )


_REV_CACHE = "SELECT region, amt FROM {{ queries.rev.cache }}"


def test_a_cold_cache_ref_runs_the_upstream_and_reports_the_miss(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run(_REV_CACHE, project, registry, cache)

    assert result.success, result.errors
    assert result.data == [{"region": "US", "amt": 10}]
    assert result.ran_on == "warehouse"
    [ref] = result.cache_refs
    assert (ref.query, ref.hit, ref.age_seconds) == ("rev", False, None)


def test_a_cached_entry_is_read_with_no_warehouse_call(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run(_REV_CACHE, project, registry, cache)

    def _no_warehouse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the warehouse was called")

    monkeypatch.setattr(run_stage, "run_route", _no_warehouse)
    result = _run(
        "SELECT sum(amt) AS total FROM {{ queries.rev.cache }}",
        project,
        registry,
        cache,
    )

    assert result.success, result.errors
    assert result.data == [{"total": 10}]
    assert result.ran_on == "cache"
    [ref] = result.cache_refs
    assert ref.query == "rev"
    assert ref.hit is True
    assert ref.age_seconds is not None and 0 <= ref.age_seconds < 60


def test_two_cache_refs_join_in_one_query(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    sql = (
        "SELECT r.region, r.amt - c.spend AS margin "
        "FROM {{ queries.rev.cache }} r JOIN {{ queries.cost.cache }} c "
        "USING (region)"
    )
    cold = _run(sql, project, registry, cache)
    warm = _run(sql, project, registry, cache)

    assert warm.data == [{"region": "US", "margin": 6}]
    assert [(r.query, r.hit) for r in cold.cache_refs] == [
        ("rev", False),
        ("cost", False),
    ]
    assert warm.ran_on == "cache"
    assert [(r.query, r.hit) for r in warm.cache_refs] == [
        ("rev", True),
        ("cost", True),
    ]


def test_an_inline_query_ref_runs_fresh_on_the_boards_source(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run(
        "SELECT region, amt * 2 AS doubled FROM {{ queries.rev }}",
        project,
        registry,
        cache,
    )

    assert result.success, result.errors
    assert result.data == [{"region": "US", "doubled": 20}]
    assert result.ran_on == "warehouse"
    assert result.cache_refs == []


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM {{ queries.typo }}", "SELECT * FROM {{ queries.typo.cache }}"],
    ids=["inline", "cache"],
)
def test_a_reference_to_an_unknown_query_is_a_coded_not_found(
    sql: str,
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run(sql, project, registry, cache)

    assert not result.success
    assert result.error_code == "ERR-BOARD-QUERY-NOT-FOUND"
    assert "typo" in result.errors[0] and "rev" in result.errors[0]


def test_a_variables_override_is_applied_and_selects_its_own_cache_slice(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    us = _run(_REV_CACHE, project, registry, cache)
    eu = _run(_REV_CACHE, project, registry, cache, variables={"region": "EU"})

    assert us.data == [{"region": "US", "amt": 10}]
    assert eu.data == [{"region": "EU", "amt": 20}]
    assert eu.cache_refs[0].hit is False
    assert sorted(e.variables["region"] for e in cache.entries()) == ["EU", "US"]


def test_a_variable_the_board_does_not_declare_is_refused(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run(_REV_CACHE, project, registry, cache, variables={"nope": "x"})

    assert result.success is False
    assert "nope" in result.errors[0]
    assert result.error_code is not None


def test_an_unset_variable_binds_as_the_board_renders_it(
    tmp_path: Path,
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    (tmp_path / "charts" / "days.yml").write_text(
        """
source: db
variables:
  date_range:
    input: daterange
queries:
  by_day:
    sql: select date '2024-01-05' as created_at
charts:
  c:
    query: by_day
    type: table
rows:
  - c
"""
    )
    sql = (
        "SELECT count(*) AS n FROM {{ queries.by_day }} "
        "WHERE {{ filter_date_range('created_at', date_range) }}"
    )
    result = execute_board_sql(sql, Path("days.yml"), project, registry, cache)

    assert result.success, result.errors
    assert result.data == [{"n": 1}]


def test_the_transient_query_writes_no_cache_entry(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    _run(_REV_CACHE, project, registry, cache)
    _run("SELECT 1 AS one", project, registry, cache)
    _run("SELECT region FROM {{ queries.rev }}", project, registry, cache)

    [entry] = cache.entries()
    assert [u.query for u in entry.used_by] == ["rev"]


def test_sql_without_refs_runs_on_the_boards_default_source(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run("SELECT 41 + 1 AS answer", project, registry, cache)

    assert result.data == [{"answer": 42}]
    assert result.ran_on == "warehouse"
    assert result.cache_refs == []


def test_a_cache_ref_with_no_result_cache_is_an_error(
    project: FilesystemProject, registry: ExecEnv
) -> None:
    result = _run(_REV_CACHE, project, registry, None)

    assert result.success is False
    assert result.error_code == "ERR-CACHE-NOT-ATTACHED"
    [error] = result.errors
    assert "{{ queries.rev }}" in error
    assert "DCT_CACHE_PATH" in error


def test_a_cache_ref_to_an_uncached_query_is_an_error(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run(
        "SELECT * FROM {{ queries.uncached.cache }}", project, registry, cache
    )

    assert result.success is False
    assert "uncached" in result.errors[0]


def test_mutating_sql_is_refused(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run("DROP TABLE t", project, registry, cache)

    assert result.success is False
    assert result.error_code == "ERR-MUTATING-SQL"


def test_a_missing_board_is_a_structured_failure(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = execute_board_sql("SELECT 1", Path("nope.yml"), project, registry, cache)

    assert result.success is False
    assert result.error_code == "ERR-FILE-NOT-FOUND"


def test_rows_are_cut_at_the_limit(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = execute_board_sql(
        "SELECT * FROM range(5) t(n)",
        Path("rev.yml"),
        project,
        registry,
        cache,
        limit=2,
    )

    assert result.row_count == 2
    assert result.truncated is True


def test_a_board_without_a_default_source_lends_the_referenced_querys(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    """Queries that name their own source and a board that names none: SQL
    over `{{ queries.x }}` runs where x runs instead of failing for a source."""
    (Path(project.root) / "charts" / "per_query.yml").write_text(
        "queries:\n"
        "  rev:\n"
        "    source: db\n"
        "    sql: select 7 as n\n"
        "charts:\n"
        "  c:\n"
        "    query: rev\n"
        "    type: table\n"
        "rows:\n"
        "  - c\n"
    )
    inline = execute_board_sql(
        "SELECT n * 2 AS twice FROM {{ queries.rev }}",
        Path("per_query.yml"),
        project,
        registry,
        cache,
    )
    assert inline.success, inline.errors
    assert inline.data == [{"twice": 14}] and inline.ran_on == "warehouse"

    # The inline run above cached nothing, so the first `.cache` read runs rev
    # on its own source and caches it; the second is served from the cache.
    cold = execute_board_sql(
        "SELECT n FROM {{ queries.rev.cache }}",
        Path("per_query.yml"),
        project,
        registry,
        cache,
    )
    assert cold.success, cold.errors
    assert cold.data == [{"n": 7}] and cold.ran_on == "warehouse"
    assert [ref.hit for ref in cold.cache_refs] == [False]
    warm = execute_board_sql(
        "SELECT n FROM {{ queries.rev.cache }}",
        Path("per_query.yml"),
        project,
        registry,
        cache,
    )
    assert warm.ran_on == "cache" and [ref.hit for ref in warm.cache_refs] == [True]

    plain = execute_board_sql(
        "SELECT 1 AS n", Path("per_query.yml"), project, registry, cache
    )
    assert plain.success is False
    assert "source" in plain.errors[0]


def test_bad_sql_over_cached_rows_is_a_structured_failure(
    project: FilesystemProject,
    registry: ExecEnv,
    cache: TrivialDuckDBCache,
) -> None:
    result = _run("SELECT nope FROM {{ queries.rev.cache }}", project, registry, cache)

    assert result.success is False
    assert "nope" in result.errors[0]
    assert result.error_code == "ERR-CACHE-SQL-FAILED"
