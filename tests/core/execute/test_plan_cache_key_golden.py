"""Planned cache keys are persisted (Cloud's Postgres result cache), so their bytes
must never change: a moved source resolution or version fact that shifts one
cold-starts every cache mid-deploy."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.plan import plan

from ..._run import Run

_BOARD = """\
title: T
queries:
  sourced:
    sql: SELECT 1 AS n
    source: mem
  from_csv:
    sql: SELECT * FROM sales
    source: sales
  from_db_file:
    sql: SELECT * FROM t
    source: dbfile
  from_manifest:
    sql: SELECT * FROM {{ ref('orders') }}
    source: prod
charts:
  c:
    query: sourced
    type: table
rows:
  - c
"""

_PINNED_NS = 1_700_000_000_000_000_000

GOLDEN = {
    "sourced": ["6fe2e0f6071ac2bb", "4531145a17146f20", "0000000000000000"],
    "from_csv": ["eba884da42f1ea97", "4c75abdcf100788a", "0000000000000000"],
    "from_db_file": ["2397a9c68d0d7ca0", "bdd379b02821e0bc", "0000000000000000"],
    "from_manifest": ["91a09aab11774422", "0123c8085cda7940", "0000000000000000"],
}


def _pin(path: Path) -> None:
    os.utime(path, ns=(_PINNED_NS, _PINNED_NS))


@pytest.fixture
def run(tmp_path: Path, local_project: Callable[..., FilesystemProject]) -> Run:
    (tmp_path / "sales.csv").write_text("n\n1\n")
    _pin(tmp_path / "sales.csv")
    (tmp_path / "warehouse.duckdb").write_bytes(b"not read, only stat'ed")
    _pin(tmp_path / "warehouse.duckdb")
    (tmp_path / "dbt_project.yml").write_text(
        "name: test_project\nprofile: test_project\n"
    )
    (tmp_path / "profiles.yml").write_text(
        "test_project:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        "      path: warehouse.duckdb\n"
        "      threads: 1\n"
    )
    (tmp_path / "target").mkdir()
    manifest = tmp_path / "target" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.test_project.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "schema": "main",
                        "alias": "orders",
                    }
                },
                "sources": {},
            }
        )
    )
    _pin(manifest)
    (tmp_path / "dbt_charts.yml").write_text(
        "name: test_project\n"
        "sources:\n"
        "  mem:\n    type: duckdb\n    path: ':memory:'\n"
        "  sales:\n    type: csv\n    files:\n      sales: sales.csv\n"
        "  dbfile:\n    type: duckdb\n    path: warehouse.duckdb\n"
        "  prod:\n    type: dbt_profile\n    profile: test_project\n    target: dev\n"
    )
    compiled = compile_board(_BOARD)
    assert compiled.board is not None, compiled.errors
    return Run(
        compiled.board,
        env=build_exec_env(local_project(tmp_path)),
        query_registry=compiled.query_registry,
    )


def test_planned_cache_keys_are_byte_stable(run: Run) -> None:
    keys = {
        p.name: list(p.cache_key)
        for p in plan(run.ctx, ["sourced", "from_csv", "from_db_file", "from_manifest"])
    }
    assert keys == GOLDEN
