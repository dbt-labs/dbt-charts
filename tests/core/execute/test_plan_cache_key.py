"""A cache key hashes a source's identity and never a secret it carries."""

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.source import (
    DatabricksSourceConfig,
    PostgresSourceConfig,
    RedshiftSourceConfig,
    SnowflakeSourceConfig,
)
from dbt_charts.core.execute.plan import cache_key, make_context
from dbt_charts.core.execute.query_plan import QueryPlan

from ..._scripted_env import ScriptedBackend, scripted_env

BOARD = """
queries:
  q:
    sql: SELECT 1 AS n
    source: wh
charts:
  c:
    query: q
    type: kpi
    value: n
rows:
  - c
"""

HTTP_PATH = "/sql/1.0/warehouses/abc123"


def plan_with(declaration: dict[str, object]) -> QueryPlan:
    return {
        "version": 1,
        "board_id": "b",
        "time_zone": "UTC",
        "today": "2026-11-10",
        "week_start": 1,
        "fiscal_year_start_month": 1,
        "prefetch": [],
        "queries": {
            "q": {
                "type": "sql",
                "source": "wh",
                "cache": {"enabled": True, "ttl": None, "prefetch": False},
                "formats": {},
                "depends_on": [],
                "sql": "SELECT 1 AS n",
                "setup_sql": None,
                "dialect": None,
                "variable_dependencies": [],
                "incremental": None,
            }
        },
        "variables": {"query": {}, "render": {}, "presets": {}},
        "sources": {"project": {"wh": declaration}, "inline": {}},
    }  # type: ignore[typeddict-item] — wire JSON built untyped by the test


def key_of(declaration: dict[str, object]) -> tuple[str, str, str]:
    board = compile(BOARD).board
    assert board is not None
    ctx = make_context(board, plan_with(declaration), scripted_env(ScriptedBackend()))
    return cache_key(ctx, "q", lambda: None)


def declared(config: object, **secrets: str) -> dict[str, object]:
    dumped = config.model_dump(exclude_none=True)  # type: ignore[attr-defined] — a source config model
    return {**dumped, **secrets}


CONFIGS = [
    (
        PostgresSourceConfig(
            type="postgres", host="h", user="u", password="p", database="d"
        ),
        {"password": "hunter2-postgres"},
    ),
    (
        RedshiftSourceConfig(
            type="redshift", host="h", user="u", password="p", database="d"
        ),
        {"password": "hunter2-redshift"},
    ),
    (
        SnowflakeSourceConfig(
            type="snowflake",
            account="a",
            user="u",
            private_key="k",
            database="d",
            warehouse="w",
        ),
        {"private_key": "-----BEGIN PRIVATE KEY-----zzz"},
    ),
    (
        DatabricksSourceConfig(
            type="databricks",
            host="h",
            http_path=HTTP_PATH,
            token="t",
            catalog="main",
        ),
        {"token": "dapi-secret-token"},
    ),
]


@pytest.mark.parametrize(("config", "secrets"), CONFIGS)
def test_a_secret_neither_appears_in_nor_changes_a_key(
    config: object, secrets: dict[str, str]
) -> None:
    key = key_of(declared(config, **secrets))

    assert not any(value in part for part in key for value in secrets.values())
    rotated = {name: f"{value}-rotated" for name, value in secrets.items()}
    assert key_of(declared(config, **rotated)) == key


def test_an_identity_field_still_changes_a_key() -> None:
    config = PostgresSourceConfig(
        type="postgres", host="h", user="u", password="p", database="d"
    )

    assert key_of(declared(config)) != key_of(declared(config, host="other-host"))
