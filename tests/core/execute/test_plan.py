"""Plan stage: the one place a query's variables, route and cache key are decided."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.adapter_registry import build_adapter_registry
from dbt_charts.core.execute.duckdb_cache import compute_cache_key
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.plan import (
    CacheRefComposition,
    FileSource,
    Warehouse,
    plan,
)
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

_BOARD = """\
title: T
variables:
  region:
    data_type: string
    default: west
queries:
  base:
    sql: SELECT '{{ region }}' AS region, 1 AS n
    source: db
  composed:
    sql: SELECT n + 1 AS n FROM {{ queries.base.cache }}
    source: db
  from_file:
    sql: SELECT * FROM sales
    source: sales
  uncached:
    sql: SELECT 1 AS n
    source: db
    cache: false
charts:
  c:
    query: base
    type: table
rows:
  - c
"""


def _executor(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    cache: TrivialDuckDBCache | None = None,
    use_cache: bool = True,
) -> Executor:
    (tmp_path / "sales.csv").write_text("n\n1\n")
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  sales:\n    type: csv\n    files:\n      sales: sales.csv\n"
        "  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    compiled = compile_board(_BOARD)
    assert compiled.board is not None, compiled.errors
    return Executor(
        compiled.board,
        adapter_registry=build_adapter_registry(local_project(tmp_path)),
        query_registry=compiled.query_registry,
        result_cache=cache,
        use_cache=use_cache,
    )


def test_each_route_is_decided_at_plan_time(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(tmp_path, local_project, TrivialDuckDBCache())

    routes = {
        p.name: p.route for p in plan(executor.ctx, ["base", "composed", "from_file"])
    }

    assert routes == {
        "base": Warehouse(),
        "composed": CacheRefComposition(),
        "from_file": FileSource(),
    }


def test_plan_merges_board_defaults_and_strips_the_prefix(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(tmp_path, local_project)

    [planned] = plan(executor.ctx, ["queries.base"], {"region": "east"})
    [defaulted] = plan(executor.ctx, ["base"])

    assert planned.name == "base"
    assert planned.variables["region"] == "east"
    assert defaulted.variables["region"] == "west"
    assert planned.cache_key != defaulted.cache_key


def test_unknown_query_is_an_execution_error(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    with pytest.raises(ExecutionError, match="not found"):
        plan(_executor(tmp_path, local_project).ctx, ["missing"])


def test_use_store_honors_the_call_override_and_the_query_policy(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(tmp_path, local_project)

    stored = {p.name: p.use_store for p in plan(executor.ctx, ["base", "uncached"])}
    no_store = plan(executor.ctx, ["base"], use_cache=False)[0].use_store

    assert stored == {"base": True, "uncached": False}
    assert no_store is False


def test_lookup_and_run_share_the_planned_cache_key_for_a_store_write_and_hit(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    cache = TrivialDuckDBCache(db_path=tmp_path / "c.duckdb")
    writer = _executor(tmp_path, local_project, cache)
    [planned] = plan(writer.ctx, ["base"], {"region": "east"})

    found = writer.lookup(["base"], {"region": "east"})
    writer.run_misses(found, 2)
    assert cache.get(*planned.cache_key, ttl=None) is not None

    reader = _executor(tmp_path, local_project, cache)
    found = reader.lookup(["base"], {"region": "east"})
    assert found.hits["base"].written_at is not None
    assert not found.misses


def test_a_query_on_a_file_source_is_never_tailable_whatever_its_route(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    executor = _executor(tmp_path, local_project, TrivialDuckDBCache())
    query = executor.ctx.board.queries["from_file"]
    composed = query.model_copy(
        update={"sql": "SELECT * FROM {{ queries.base.cache }}"}
    )
    executor.query_registry = {"from_file_cached": composed}

    [planned] = plan(executor.ctx, ["from_file_cached"])

    assert planned.route == CacheRefComposition()
    assert not planned.tailable
    assert plan(executor.ctx, ["base"])[0].tailable


def test_planned_cache_key_is_the_content_key_over_a_mocked_registry() -> None:
    compiled = compile_board(
        "title: T\nqueries:\n  q:\n    sql: SELECT 1 AS value\n"
        + "    source: memory\n"
        + "charts:\n  c:\n    query: q\n    type: table\nrows:\n  - c\n"
    )
    board = compiled.board
    assert board is not None, compiled.errors
    executor = Executor(
        board,
        adapter_registry=MagicMock(),
        query_registry=compiled.query_registry,
    )

    [planned] = plan(executor.ctx, ["q"])

    assert planned.cache_key == compute_cache_key(
        board.queries["q"], board_sources=board.sources
    )
