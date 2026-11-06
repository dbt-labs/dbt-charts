"""A query's ``target:`` never chose a connection; the 0.11.0 boundary drops the key."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _board(query: dict[str, Any]) -> dict[str, Any]:
    return {
        "queries": {"q": query},
        "charts": {"t": {"type": "table", "query": "q"}},
        "rows": ["t"],
    }


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    return migrate_mapping(raw)


def test_a_query_target_is_dropped_with_a_notice_naming_the_source_fix(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = _board({"sql": "SELECT 1 AS n", "source": "warehouse", "target": "prod"})

    with pytest.warns(SchemaMigrationWarning, match="dbt_profile") as caught:
        migrated = _migrate(raw, catalog)

    assert migrated["queries"]["q"] == {"sql": "SELECT 1 AS n", "source": "warehouse"}
    assert "target" in str(caught[0].message)
    AuthoredBoard.model_validate(migrated)


def test_a_nested_board_query_target_is_dropped(catalog: YamlSchemaCatalog) -> None:
    nested = _board({"sql": "SELECT 1 AS n", "source": "warehouse", "target": "prod"})
    raw = {"rows": [nested]}

    with pytest.warns(SchemaMigrationWarning, match="dbt_profile"):
        migrated = _migrate(raw, catalog)

    assert "target" not in migrated["rows"][0]["queries"]["q"]


def test_a_chart_inline_query_target_is_dropped(catalog: YamlSchemaCatalog) -> None:
    raw = {
        "charts": {
            "t": {
                "type": "table",
                "query": {
                    "sql": "SELECT 1 AS n",
                    "source": "warehouse",
                    "target": "prod",
                },
            }
        },
        "rows": ["t"],
    }

    with pytest.warns(SchemaMigrationWarning, match="dbt_profile"):
        migrated = _migrate(raw, catalog)

    assert "target" not in migrated["charts"]["t"]["query"]


def test_keys_an_author_named_target_survive(catalog: YamlSchemaCatalog) -> None:
    raw = {
        "queries": {
            "target": {
                "sql": "SELECT 1 AS target",
                "source": "warehouse",
                "formats": {"target": "currency"},
            },
            "rows": {
                "type": "values",
                "rows": [{"target": 1}],
            },
        },
        "charts": {"t": {"type": "table", "query": "target"}},
        "rows": ["t"],
    }

    migrated = _migrate(raw, catalog)

    assert migrated["queries"]["target"]["formats"] == {"target": "currency"}
    assert migrated["queries"]["rows"]["rows"] == [{"target": 1}]


def test_a_board_authoring_a_query_target_still_compiles() -> None:
    board_yaml = """
queries:
  q:
    sql: SELECT 1 AS n
    source: warehouse
    target: prod
charts:
  t:
    type: table
    query: q
rows:
  - t
"""
    result = compile(board_yaml)

    assert result.success, result.errors
    notices = [w.message for w in result.warnings if w.code == "WARN-SCHEMA-MIGRATED"]
    assert any("dbt_profile" in m for m in notices), notices


def test_the_latest_grammar_rejects_the_key() -> None:
    with pytest.raises(ValidationError, match="target"):
        AuthoredBoard.model_validate(
            _board({"sql": "SELECT 1 AS n", "source": "warehouse", "target": "prod"})
        )
