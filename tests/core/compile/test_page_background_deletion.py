"""Migration coverage for the style.page removal.

See ``v0_7_0.py``'s module docstring and the ``("style", "page")`` entry in
``DELETED_TAILS`` for the removal's rationale and tail-shape derivation.
This file pins that behavior: the Deletion is registered, all three legal
``page:`` shapes migrate and compile, and the deletion reason reaches the
author as a warning.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

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


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


@pytest.mark.parametrize(
    "page_value",
    [
        {"background": "#eeeeee"},
        {},
        None,
    ],
    ids=["page-with-background", "empty-page-mapping", "page-null"],
)
def test_board_authoring_page_migrates_cleanly(
    page_value: object, catalog: YamlSchemaCatalog
) -> None:
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "style": {"page": page_value, "background": "#ffffff"},
        "charts": {"t": {"query": "q", "type": "table"}},
        "rows": ["t"],
    }

    migrated = _migrate(raw, catalog)

    assert "page" not in migrated["style"]
    assert migrated["style"]["background"] == "#ffffff"
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize(
    "page_yaml",
    [
        "page:\n    background: '#eeeeee'\n",
        "page: {}\n",
        "page: null\n",
    ],
    ids=["page-with-background", "empty-page-mapping", "page-null"],
)
def test_board_authoring_page_compiles_after_migration(page_yaml: str) -> None:
    """`migrate_mapping()` skips the currency check, the fallbacks, and the
    whole-document gate that `compile()` applies — pin all three legal shapes
    at `compile()` too, not just at the lower-level migration helper. Also
    pins the Deletion's `reason` actually reaches the author as a warning."""
    result = compile(
        f"""title: Test
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
style:
  {page_yaml}charts:
  t: {{query: q, type: table}}
rows:
- t
"""
    )
    assert any(
        "page canvas this board explicitly requested is gone" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings

    assert result.success
