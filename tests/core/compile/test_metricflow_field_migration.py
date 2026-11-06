"""Migration tests for MetricFlow-only fields removed from the authored schema.

These keys existed in the 0.5.0 authored schema and are stripped automatically
by dct migrate at the pending (latest->current) boundary:

- chart-level `model:` (semantic-layer chart sugar, declared on every chart
  family's shared base except CalloutChart)
- `Variable.model` / `Variable.dimension` / `Variable.measure` (MetricFlow option-source
  bindings, never consumed)

All tests use the real catalog and migration registry -- no synthetic schema
data.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import SchemaMigrationWarning
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._engine_migrations import migrate_mapping


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


def test_chart_model_sugar_stripped(catalog: YamlSchemaCatalog) -> None:
    """Chart-level model: is stripped and the result validates with an explicit query:."""
    raw: dict[str, Any] = {
        "queries": {"q": {"sql": "SELECT 1 AS revenue", "source": "db"}},
        "charts": {
            "b": {
                "type": "bar",
                "model": "analytics.orders",
                "query": "q",
                "x": "month",
                "y": "revenue",
            }
        },
        "rows": ["b"],
    }

    migrated = _migrate(raw, catalog)

    assert "model" not in migrated["charts"]["b"]
    AuthoredBoard.model_validate(migrated)


def test_variable_model_stripped(catalog: YamlSchemaCatalog) -> None:
    """Variable.model is stripped (shares the one-segment tail with chart sugar)."""
    raw: dict[str, Any] = {
        "variables": {"region": {"model": "geography", "input": "select"}},
        "rows": [],
    }

    migrated = _migrate(raw, catalog)

    assert "model" not in migrated["variables"]["region"]
    AuthoredBoard.model_validate(migrated)


def test_variable_dimension_stripped(catalog: YamlSchemaCatalog) -> None:
    """Variable.dimension is stripped and the result validates."""
    raw: dict[str, Any] = {
        "variables": {"region": {"dimension": "geography.region", "input": "select"}},
        "rows": [],
    }

    migrated = _migrate(raw, catalog)

    assert "dimension" not in migrated["variables"]["region"]
    AuthoredBoard.model_validate(migrated)


def test_variable_measure_stripped(catalog: YamlSchemaCatalog) -> None:
    """Variable.measure is stripped and the result validates."""
    raw: dict[str, Any] = {
        "variables": {"revenue": {"measure": "financial.revenue", "input": "select"}},
        "rows": [],
    }

    migrated = _migrate(raw, catalog)

    assert "measure" not in migrated["variables"]["revenue"]
    AuthoredBoard.model_validate(migrated)
