"""A missing relation reached through ref() or source() names a stale manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.env import ExecEnv, build_exec_env
from dbt_charts.core.execute.query_result import UNKNOWN_RELATION_MANIFEST_NOTE
from dbt_charts.core.execute.run import run_query

_MANIFEST = {
    "nodes": {
        "model.p.ghost": {
            "resource_type": "model",
            "name": "ghost",
            "schema": "analytics",
            "alias": "ghost",
        }
    },
    "sources": {
        "source.p.raw.events": {
            "resource_type": "source",
            "source_name": "raw",
            "name": "events",
            "schema": "raw_schema",
            "identifier": "events",
        }
    },
}


@pytest.fixture
def env(tmp_path: Path) -> ExecEnv:
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(json.dumps(_MANIFEST))
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: ':memory:'\n"
    )
    return build_exec_env(FilesystemProject(tmp_path), read_only=False)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from {{ ref('ghost') }}",
        "select * from {{ source('raw', 'events') }}",
    ],
)
def test_ref_and_source_errors_carry_the_manifest_note(env: ExecEnv, sql: str) -> None:
    result = run_query(env, SqlQuery(sql=sql, source="wh"))

    assert result.error is not None
    assert str(result.error).endswith(UNKNOWN_RELATION_MANIFEST_NOTE)


def test_raw_table_error_has_no_manifest_note(env: ExecEnv) -> None:
    result = run_query(env, SqlQuery(sql="select * from analytics.ghost", source="wh"))

    assert result.error is not None
    assert "manifest" not in str(result.error)
