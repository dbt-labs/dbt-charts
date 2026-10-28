"""Table spark bars no longer draw value labels; the 0.11.0 boundary drops the keys."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _board(
    spark: dict[str, Any], theme: dict[str, Any] | None = None
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "queries": {"q": {"type": "values", "rows": [{"n": "a", "v": 1}]}},
        "charts": {
            "t": {
                "type": "table",
                "query": "q",
                "style": {"columns": {"v": {"spark": spark}}},
            }
        },
        "rows": ["t"],
    }
    if theme is not None:
        raw["style"] = {"charts": {"table": {"spark": {"bar": theme}}}}
    return raw


def test_authored_value_labels_are_dropped_with_a_notice(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = _board({"type": "bar", "value_visible": True, "value_suffix": "%", "max": 5})
    _, registry = _board_migration_context()

    with pytest.warns(SchemaMigrationWarning, match="value column") as caught:
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert migrated["charts"]["t"]["style"]["columns"]["v"]["spark"] == {
        "type": "bar",
        "max": 5,
    }
    assert "value_visible" in str(caught[0].message)
    AuthoredBoard.model_validate(migrated)


def test_theme_label_block_is_dropped(catalog: YamlSchemaCatalog) -> None:
    raw = _board(
        {"type": "bar"},
        theme={
            "border": {"radius": 2},
            "label": {"inset_x": 4, "min_size": 9, "height_offset": 5},
        },
    )
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        migrated = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert migrated["style"]["charts"]["table"]["spark"]["bar"] == {
        "border": {"radius": 2}
    }
    AuthoredBoard.model_validate(migrated)


def test_an_old_board_still_compiles() -> None:
    import yaml

    raw = _board({"type": "bar", "value_visible": True})
    result = compile(yaml.safe_dump(raw))
    assert result.success, result.errors
    notices = [w.message for w in result.warnings if w.code == "WARN-SCHEMA-MIGRATED"]
    assert any("value column" in m for m in notices), notices


def test_the_latest_grammar_rejects_the_keys() -> None:
    with pytest.raises(ValidationError, match="value_visible"):
        AuthoredBoard.model_validate(_board({"type": "bar", "value_visible": True}))
    with pytest.raises(ValidationError, match="min_size"):
        AuthoredBoard.model_validate(
            _board({"type": "bar"}, theme={"label": {"min_size": 9}})
        )
