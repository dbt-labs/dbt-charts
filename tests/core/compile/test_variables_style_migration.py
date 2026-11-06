"""Migration coverage for the 0.4.0 -> 0.5.0 VariablesStyle field removals
(dbt_charts.core.compile.migrations.versions.v0_5_0): style.variables.padding,
style.variables.container_padding, style.variables.popover_rail_background,
and style.variables.value.numeric_variant were removed without replacement.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

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


def test_variables_dead_fields_migrate_away_in_memory() -> None:
    """A 0.4.0 board authoring the four retired variables fields migrates to a
    board with style.variables absent entirely -- the deletions empty the
    dict and orphaned-parent cleanup then drops the empty container."""
    catalog = load_yaml_schema_catalog()
    raw = _minimal_board(
        {
            "variables": {
                "padding": 8,
                "container_padding": 4,
                "popover_rail_background": "#fff",
                "value": {"numeric_variant": "tabular-nums"},
            }
        }
    )

    migrated = _migrate(raw, catalog)

    assert "variables" not in migrated.get("style", {})
    AuthoredBoard.model_validate(migrated)


def test_variables_dead_fields_rejected_on_a_current_board() -> None:
    """Un-migrated, the retired keys fail loud via extra='forbid' -- no
    silent alias, per the no-back-compat rule."""
    from pydantic import ValidationError

    raw = _minimal_board({"variables": {"padding": 8}})

    with pytest.raises(ValidationError, match="padding"):
        AuthoredBoard.model_validate(raw)
