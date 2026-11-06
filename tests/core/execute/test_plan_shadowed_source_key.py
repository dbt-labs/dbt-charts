"""A board and the project declaring one source name as different kinds: the cache
key folds in the version of the file the resolver's chosen source reads."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.plan import plan
from dbt_charts.core.execute.source_resolver import (
    AllowlistedSourceResolver,
    DefaultSourceResolver,
    SourceResolver,
)

from ..._run import Run

_DUCKDB = {"type": "duckdb", "path": "db.duckdb"}
_CSV = {"type": "csv", "files": {"t": "t.csv"}}
_PINNED_NS = 1_700_000_000_000_000_000


def _build(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    board_source: dict[str, Any],
    project_source: dict[str, Any],
    resolver: Callable[[FilesystemProject], SourceResolver],
) -> Run:
    (tmp_path / "dbt_charts.yml").write_text(
        yaml.dump({"name": "p", "sources": {"x": project_source}})
    )
    compiled = compile_board(
        yaml.dump(
            {
                "queries": {"q": {"sql": "SELECT 1 AS n", "source": "x"}},
                "charts": {"c": {"query": "q", "type": "table"}},
            }
        )
    )
    assert compiled.board is not None, compiled.errors
    compiled.board.sources["x"] = board_source
    project = local_project(tmp_path)
    return Run(
        compiled.board,
        env=build_exec_env(project, resolver=resolver(project)),
        query_registry=compiled.query_registry,
    )


def _key(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    sources: tuple[dict[str, Any], dict[str, Any]],
    resolver: Callable[[FilesystemProject], SourceResolver],
    touched: str | None,
) -> list[str]:
    """The planned key with every file pinned, *touched* one given a new mtime."""
    for name in ("db.duckdb", "t.csv"):
        (tmp_path / name).write_text("n\n1\n")
        os.utime(tmp_path / name, ns=(_PINNED_NS,) * 2)
    if touched is not None:
        os.utime(tmp_path / touched, ns=(_PINNED_NS + 1,) * 2)
    run = _build(tmp_path, local_project, *sources, resolver)
    return list(plan(run.ctx, ["q"])[0].cache_key)


@pytest.mark.parametrize(
    ("board_source", "project_source", "resolver", "real_file", "shadowed_file"),
    [
        # Default: the board's entry, of any type, wins.
        (_DUCKDB, _CSV, DefaultSourceResolver, "db.duckdb", "t.csv"),
        # Allowlisted: the project's entry wins.
        (_CSV, _DUCKDB, AllowlistedSourceResolver, "db.duckdb", "t.csv"),
    ],
    ids=["board-duckdb-shadows-project-csv", "project-duckdb-shadows-board-csv"],
)
def test_key_follows_the_resolved_source(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    board_source: dict[str, Any],
    project_source: dict[str, Any],
    resolver: Callable[[FilesystemProject], SourceResolver],
    real_file: str,
    shadowed_file: str,
) -> None:
    sources = (board_source, project_source)
    before = _key(tmp_path, local_project, sources, resolver, None)
    assert _key(tmp_path, local_project, sources, resolver, shadowed_file) == before
    assert _key(tmp_path, local_project, sources, resolver, real_file) != before


class _RefusingResolver(DefaultSourceResolver):
    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("blocked host")


def test_a_refused_shadowed_source_fails_at_execution_not_in_the_cache_probe(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    for name in ("db.duckdb", "t.csv"):
        (tmp_path / name).write_text("n\n1\n")
    run = _build(tmp_path, local_project, _DUCKDB, _CSV, _RefusingResolver)

    assert "q" not in run.lookup(["q"]).hits
    with pytest.raises(Exception, match="blocked host"):
        run.execute_query("q")


class _Spy(DefaultSourceResolver):
    def __init__(self, spy: Mock) -> None:
        self.spy = spy

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        return self.spy(*args, **kwargs)


def test_two_non_file_declarations_never_reach_the_resolver(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    warehouse = {
        "type": "postgres",
        "host": "h",
        "database": "d",
        "user": "u",
        "password": "{{ env_var('PW', 'x') }}",
    }
    resolver = Mock(side_effect=RuntimeError("never called"))
    run = _build(
        tmp_path, local_project, warehouse, warehouse, lambda _: _Spy(resolver)
    )

    assert "q" not in run.lookup(["q"]).hits
    resolver.assert_not_called()
