"""Migration coverage for the 0.4.0 -> 0.5.0 style.board: -> style.frame:
rename (dbt_charts.core.compile.migrations.versions.v0_5_0), the rebrand
wave's authored-grammar rename that accompanies the board->board model
rename. A walk to the live grammar then moves `style.frame.width` to the root
`width:` (v0_11_0).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: JsonObject, catalog: YamlSchemaCatalog) -> JsonObject:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def test_registry_covers_the_0_4_0_transition(catalog: YamlSchemaCatalog) -> None:
    _, registry = _board_migration_context()

    moves = registry.transition_from("0.4.0")

    assert moves
    assert all(move.target_schema == "0.5.0" for move in moves)
    assert any(move.old_path == ("style", "board") for move in moves)
    assert any(move.new_path == ("style", "frame") for move in moves)


def test_style_board_key_migrates_to_frame_in_memory(
    catalog: YamlSchemaCatalog,
) -> None:
    """A board authored against 0.4.0 with style.board: round-trips through
    migration to the current style.frame: shape."""
    raw: dict[str, Any] = {
        "title": "Revenue",
        "style": {"board": {"width": 960, "min_height": 400}},
        "rows": ["revenue"],
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
    }

    migrated = _migrate(raw, catalog)

    assert migrated["style"] == {"frame": {"min_height": 400}}
    assert migrated["width"] == 960
    AuthoredBoard.model_validate(migrated)


def test_style_board_key_yaml_text_round_trip_preserves_comments(
    catalog: YamlSchemaCatalog,
) -> None:
    """The file-preserving writer used by `dct migrate` renames style.board:
    to style.frame: in place, leaving surrounding comments and formatting
    untouched (a real ``dct migrate`` invocation writes exactly this)."""
    _, registry = _board_migration_context()
    yaml_text = (
        "title: Revenue\n"
        "style:\n"
        "  # keep this comment\n"
        "  board:\n"
        "    width: 960\n"
        "    min_height: 400\n"
        "rows: [revenue]\n"
        "charts:\n"
        "  revenue:\n"
        "    type: bar\n"
        "    query: revenue\n"
        "    x: month\n"
        "    y: amount\n"
    )

    migrated = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "# keep this comment" in migrated
    doc = load_yaml_mapping(migrated)
    assert doc["style"] == {"frame": {"min_height": 400}}
    assert doc["width"] == 960
    AuthoredBoard.model_validate(doc)


def test_style_board_and_interactive_legend_migrate_together(
    catalog: YamlSchemaCatalog,
) -> None:
    """The Move and the Deletion apply in the same hop.

    Both live on the same 0.4.0 -> 0.5.0 boundary, so a document carrying
    both a retired style.board: key and a retired interactive_legend: key
    must come out fully migrated -- style.frame: present, interactive_legend
    gone -- in one pass, not two.
    """
    raw: dict[str, Any] = {
        "title": "Revenue",
        "style": {"board": {"width": 960, "min_height": 400}},
        "rows": ["revenue"],
        "charts": {
            "revenue": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
                "style": {"legend": {"interactive_legend": True, "visible": True}},
            }
        },
    }

    migrated = _migrate(raw, catalog)

    assert migrated["style"] == {"frame": {"min_height": 400}}
    assert migrated["width"] == 960
    legend = migrated["charts"]["revenue"]["style"]["legend"]
    assert "interactive_legend" not in legend
    assert legend["visible"] is True
    AuthoredBoard.model_validate(migrated)


def test_style_board_key_rejected_on_a_current_board() -> None:
    """Un-migrated, the retired key fails loud via extra='forbid' — no
    silent alias, per the rebrand wave's no-back-compat rule."""
    from pydantic import ValidationError

    raw: dict[str, Any] = {
        "title": "Revenue",
        "style": {"board": {"width": 960}},
        "rows": ["revenue"],
    }

    with pytest.raises(ValidationError, match="board"):
        AuthoredBoard.model_validate(raw)
