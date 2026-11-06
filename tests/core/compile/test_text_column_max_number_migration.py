"""Migration coverage for the 0.4.0 -> 0.5.0 style.text.column.number ->
max_number rename (dbt_charts.core.compile.migrations.versions.v0_5_0).

Not a lossless rename: ``number`` meant a fixed column count, ``max_number``
is a ceiling the renderer may undershoot when there isn't enough text to
fill every column. The authored value passes through unchanged rather than
failing loud -- a deliberate release-time choice (see the module docstring).
"""

from __future__ import annotations

import warnings
from typing import Any

from dbt_charts.core.compile.migrations import SchemaMigrationWarning
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping


def _migrate(raw: JsonObject, catalog: YamlSchemaCatalog) -> JsonObject:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def _minimal_board(style: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "Revenue",
        "rows": ["revenue"],
        "style": style,
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
    }


def test_text_column_number_migrates_to_max_number_in_memory() -> None:
    catalog = load_yaml_schema_catalog()
    raw = _minimal_board({"text": {"column": {"number": 3}}})

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["text"]["column"]["max_number"] == 3
    AuthoredBoard.model_validate(migrated)


def test_text_column_number_rejected_on_a_current_board() -> None:
    """Un-migrated, the retired key fails loud via extra='forbid' -- no
    silent alias, per the no-back-compat rule."""
    import pytest
    from pydantic import ValidationError

    raw = _minimal_board({"text": {"column": {"number": 3}}})

    with pytest.raises(ValidationError, match="number"):
        AuthoredBoard.model_validate(raw)
