"""Migration coverage for the board-level ``style.color`` removal.

The key was inert — it never reached a renderer — so its removal is a
provable no-op and migration strips it. The tail is declared bare
(``("style", "color")``) even though every chart family's own ``style.color``
is live: ``_live_declares_tail`` fires the deletion only where the current
grammar has stopped declaring the tail. This file pins every position that
distinction decides — board root, nested board, chart — on both the in-memory
and the ``dct migrate`` text path.

``kpi.style.color`` is the same leaf retired one boundary earlier;
``test_kpi_style_color_deletion.py`` pins that half.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import (
    _board_migration_context,
    _deletion_would_fire,
    _schema_path_exists,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._migration_declarations import schema_has_tail

PATH = ("style", "color")


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _line_chart(color: str) -> dict[str, Any]:
    return {
        "query": "q",
        "type": "line",
        "x": "n",
        "y": "n",
        "style": {"color": {"static": color}},
    }


def test_registry_declares_the_deletion() -> None:
    _, registry = _board_migration_context()
    declared = {d.path for d in registry.deletions_from("0.6.0")}
    assert PATH in declared


def test_path_was_in_the_released_grammar_and_is_gone_from_the_live_root(
    catalog: YamlSchemaCatalog,
) -> None:
    """Why the position gate is required, asserted directly.

    The root path is gone, which is what makes the key migratable; the bare
    tail survives on chart families, which is what a global check would read
    as "not removed".
    """
    assert _schema_path_exists(catalog.schema_for("0.6.0"), PATH)
    assert not _schema_path_exists(catalog.current_schema, PATH)
    assert schema_has_tail(catalog.current_schema, PATH)


def test_root_style_color_migrates_cleanly(catalog: YamlSchemaCatalog) -> None:
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "style": {"color": "#bf8700", "background": "#ffffff"},
        "charts": {"t": {"query": "q", "type": "table"}},
        "rows": ["t"],
    }

    migrated = _migrate(raw, catalog)

    assert "color" not in migrated["style"]
    assert migrated["style"]["background"] == "#ffffff"
    AuthoredBoard.model_validate(migrated)


def test_nested_board_style_color_migrates_cleanly(
    catalog: YamlSchemaCatalog,
) -> None:
    """A sub-board's style block is the same schema node as the root's, reached
    by a different route — the gate reads the position, not the depth."""
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "charts": {"t": {"query": "q", "type": "table"}},
        "rows": [{"style": {"color": "#bf8700"}, "rows": ["t"]}],
    }

    migrated = _migrate(raw, catalog)

    assert "style" not in migrated["rows"][0]
    AuthoredBoard.model_validate(migrated)


def test_chart_style_color_survives_the_strip(catalog: YamlSchemaCatalog) -> None:
    """The gate's whole point: one leaf chain, dead on a board, live on a
    chart. Both authoring positions for a chart, since only the inline one
    shares a schema position with a nested board."""
    raw = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "style": {"color": "#bf8700"},
        "charts": {"k": _line_chart("#123456")},
        "rows": ["k", _line_chart("#654321")],
    }

    migrated = _migrate(raw, catalog)

    assert "style" not in migrated
    assert migrated["charts"]["k"]["style"]["color"] == {"static": "#123456"}
    assert migrated["rows"][1]["style"]["color"] == {"static": "#654321"}


def test_a_live_chart_color_alone_is_not_read_as_a_historical_board(
    catalog: YamlSchemaCatalog,
) -> None:
    """The recognition half of the gate, which the appliers cannot cover.

    Without it a board whose only `style.color` is a chart's live one reads as
    predating the grammar that retired the board-level key, and any unrelated
    authoring error in it gets reported as retired syntax.
    """
    _, registry = _board_migration_context()
    deletions = registry.deletions_from("0.6.0")
    source = catalog.schema_for("0.6.0")
    live = catalog.current_schema
    document = {
        "title": "Test",
        "queries": {"q": {"type": "values", "rows": [{"n": 1}]}},
        "charts": {"k": _line_chart("#123456")},
        "rows": ["k"],
    }

    assert not _deletion_would_fire(document, deletions, source, [source], live, [live])


@pytest.mark.parametrize("position", ["root", "nested"])
def test_board_authoring_style_color_compiles_after_migration(
    position: str,
) -> None:
    """`migrate_mapping()` skips the currency check, the fallbacks, and the
    whole-document gate that `compile()` applies — pin it at `compile()` too,
    with a live chart color in the same board so coexistence is covered at the
    layer that fails loud."""
    at_root = position == "root"
    style_block = "style:\n  color: '#bf8700'\n" if at_root else ""
    layout = "- k" if at_root else "- style:\n    color: '#bf8700'\n  rows:\n  - k\n"
    result = compile(
        f"""title: Test
queries:
  q: {{type: values, rows: [{{n: 1}}]}}
{style_block}charts:
  k:
    query: q
    type: line
    x: n
    y: n
    style:
      color:
        static: '#123456'
rows:
{layout}
"""
    )

    assert result.success, [e.message for e in result.errors]


def test_yaml_text_rewrite_strips_only_the_board_keys(
    catalog: YamlSchemaCatalog,
) -> None:
    """The text path replays what the walk struck, so it agrees position for
    position: without that it would strike the chart's key chain too, diverge
    from the in-memory result, and `migrate_yaml_text` would refuse the file.

    Uses the uncapped `migrate_yaml_text`, not `dct migrate`'s capped
    entrypoint — this test only needs the walk, not the cap's own behavior.
    """
    _, registry = _board_migration_context()

    migrated = migrate_yaml_text(
        """title: Test
queries:
  q: {type: values, rows: [{n: 1}]}
style:
  color: '#bf8700'
  background: '#ffffff'
charts:
  k:
    query: q
    type: line
    x: n
    y: n
    style:
      color:
        static: '#123456'
rows:
- style:
    color: '#abcdef'
  rows:
  - k
""",
        catalog=catalog,
        registry=registry,
    )

    assert "'#bf8700'" not in migrated
    assert "'#abcdef'" not in migrated
    assert "static: '#123456'" in migrated
    assert "background: '#ffffff'" in migrated
