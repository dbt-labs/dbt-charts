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
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._migration_declarations import schema_has_tail


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def test_registry_declares_the_deletion() -> None:
    _, registry = _board_migration_context()
    declared = {d.path for d in registry.deletions_from("0.6.0")}
    assert ("style", "page") in declared


def test_tail_was_in_the_released_grammar_and_is_gone_from_the_live_one(
    catalog: YamlSchemaCatalog,
) -> None:
    """Both halves of what makes a Deletion legal, asserted directly.

    Source-present is what makes the key worth migrating; target-absent is
    what stops the tail stripping a slot that still works.
    """
    tail = ("style", "page")
    assert schema_has_tail(catalog.schema_for("0.6.0"), tail)
    assert not schema_has_tail(catalog.current_schema, tail)


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
    with pytest.warns(
        SchemaMigrationWarning,
        match="page canvas this board explicitly requested is gone",
    ):
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

    assert result.success
