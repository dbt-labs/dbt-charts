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
    migrate_yaml_text,
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


def _line_chart(color: str) -> dict[str, Any]:
    return {
        "query": "q",
        "type": "line",
        "x": "n",
        "y": "n",
        "style": {"color": {"static": color}},
    }


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
    # The chart's `color.static` is live, so it moves to the line's mark.
    line_stroke = "marks", "line", "stroke", "color"

    def paint(chart: dict[str, Any]) -> Any:
        node = chart["style"]
        for step in line_stroke:
            node = node[step]
        return node

    assert paint(migrated["charts"]["k"]) == "#123456"
    assert paint(migrated["rows"][1]) == "#654321"


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
    )

    assert "'#bf8700'" not in migrated
    assert "'#abcdef'" not in migrated
    assert "#123456" in migrated
    assert "background: '#ffffff'" in migrated
