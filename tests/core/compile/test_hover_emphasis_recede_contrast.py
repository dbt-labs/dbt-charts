"""Hover emphasis recedes to a contrast target; the 0.11.0 boundary drops `dimmed_opacity`."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)


def _board(hover_emphasis: dict[str, Any]) -> dict[str, Any]:
    return {
        "queries": {"q": {"type": "values", "rows": [{"n": "a", "v": 1}]}},
        "charts": {"c": {"type": "bar", "query": "q", "x": "n", "y": "v"}},
        "rows": ["c"],
        "style": {"charts": {"hover_emphasis": hover_emphasis}},
    }


def test_authored_dimmed_opacity_is_dropped_with_a_notice() -> None:
    _, registry = _board_migration_context()

    with pytest.warns(SchemaMigrationWarning, match="dimmed_opacity"):
        migrated = migrate_mapping(
            _board({"dimmed_opacity": 0.4, "visible": True}),
            catalog=load_yaml_schema_catalog(),
            registry=registry,
        )

    assert migrated["style"]["charts"]["hover_emphasis"] == {"visible": True}
    AuthoredBoard.model_validate(migrated)


def test_an_old_board_still_compiles() -> None:
    result = compile(yaml.safe_dump(_board({"dimmed_opacity": 0.4})))

    assert result.success, result.errors
    notices = [w.message for w in result.warnings if w.code == "WARN-SCHEMA-MIGRATED"]
    assert any("dimmed_opacity" in m for m in notices), notices


def test_the_latest_grammar_rejects_dimmed_opacity() -> None:
    with pytest.raises(ValidationError, match="dimmed_opacity"):
        AuthoredBoard.model_validate(_board({"dimmed_opacity": 0.4}))


def test_recede_contrast_must_be_a_ratio_above_one() -> None:
    AuthoredBoard.model_validate(_board({"recede_contrast": 1.5}))
    with pytest.raises(ValidationError, match="recede_contrast"):
        AuthoredBoard.model_validate(_board({"recede_contrast": 1.0}))
