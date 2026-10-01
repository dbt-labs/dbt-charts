"""Migration coverage for the retired ``style.text.column.gap``.

Prose columns sit on the board's card grid, so the gutter between two columns is
``cols.gap + 2 * card_padding`` and is no longer authored on the text. The
declaration is a ``Deletion`` in ``compile/migrations/versions/v0_9_0.py``; an
authored value is dropped with a warning when a board loads, and `dct migrate`
deletes it on disk.
"""

from __future__ import annotations

import warnings

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    load_yaml_schema_catalog,
)

BOARD = """title: T
text: Some prose.
style:
  text:
    column:
      max_number: 2
      gap: 24
      rule:
        width: 1
        color: "#ccc"
rows:
  - text: A nested block.
    style:
      text:
        column:
          gap: 40
"""


def test_an_authored_column_gap_is_dropped_with_a_warning() -> None:
    result = compile(BOARD)

    assert any(
        "column gap" in d.message
        for d in result.warnings
        if d.code == "WARN-SCHEMA-MIGRATED"
    ), result.warnings
    assert result.success, [f"{e.code}: {e.message}" for e in result.errors]
    column = result.board.resolved_style.text.column
    assert column.max_number == 2
    assert column.rule is not None


def test_the_mapping_loses_the_gap_and_keeps_its_siblings() -> None:
    import yaml

    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with pytest.warns(SchemaMigrationWarning):
        result = migrate_mapping(
            yaml.safe_load(BOARD), catalog=catalog, registry=registry
        )

    column = result["style"]["text"]["column"]
    assert column["max_number"] == 2
    assert "gap" not in str(result)


def test_the_text_rewrite_replay_drops_the_gap_and_keeps_its_siblings() -> None:
    catalog = load_yaml_schema_catalog()
    _, registry = _board_migration_context()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_yaml_text(BOARD, catalog=catalog, registry=registry)

    assert "gap:" not in result
    assert "max_number: 2" in result
    assert "rule:" in result
