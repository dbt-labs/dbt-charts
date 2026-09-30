"""Migration test for the 0.5.0 -> 0.6.0 layer axis_y label -> labels rename.

Layer-level ``axis_y.label:`` (tick-label format patch, ``LayerAxisYLabel`` in
0.5.0) renamed to ``axis_y.labels:`` (``LayerAxisYLabels`` now) so the
layer-level ``axis_y`` grammar matches the chart-level ``AxisYStyle`` spelling
(``style.axis_y.labels``, inherited from ``BaseAxisStyle``) — the only field
name the two ``axis_y`` grammars disagreed on. Declared via
``suffix_rename_moves`` in ``compile/migrations/versions/v0_6_0.py``'s
``AXIS_Y_LABEL_RENAMES``, anchored two segments deep (``("axis_y", "labels")``
-> ``("axis_y", "label")``) — see that module's docstring for why the anchor
is hygiene, not a load-bearing guard against ``TypedLayerBase.label``, the
layer's own unrelated top-level measure-name field.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _board_with_layer_axis_y_label() -> dict[str, Any]:
    return {
        "charts": {
            "combo": {
                "type": "bar",
                "query": "q",
                "x": "month",
                "y": "revenue",
                "layers": [
                    {
                        "type": "line",
                        "y": "growth_pct",
                        "axis_y": {"label": {"format": ".1%"}},
                    }
                ],
            }
        },
        "rows": ["combo"],
    }


def test_layer_axis_y_label_migrates_to_labels(catalog: YamlSchemaCatalog) -> None:
    """A board authoring the old ``layers[].axis_y.label`` shape migrates to
    ``labels`` and still compiles against the current schema."""
    migrated = _migrate(_board_with_layer_axis_y_label(), catalog)

    layer_axis_y = migrated["charts"]["combo"]["layers"][0]["axis_y"]
    assert layer_axis_y["labels"] == {"format": ".1%"}
    assert "label" not in layer_axis_y
    AuthoredBoard.model_validate(migrated)


def test_layer_axis_y_label_and_own_label_coexist_and_migrate_independently(
    catalog: YamlSchemaCatalog,
) -> None:
    """Both fields authored together: only ``axis_y.label`` renames; the
    layer's own ``label`` (measure name) passes through unchanged.

    The load-bearing regression guard for the anchoring: a board carrying
    only the layer's own top-level ``label:`` validates clean against the
    *current* schema already, so ``migrate_mapping`` short-circuits before
    any move runs and never actually exercises the anti-collision behavior.
    Pairing it with the old-grammar ``axis_y.label`` here forces a real
    migration pass while the sibling field is present."""
    raw = _board_with_layer_axis_y_label()
    raw["charts"]["combo"]["layers"][0]["label"] = "Growth"

    migrated = _migrate(raw, catalog)

    layer = migrated["charts"]["combo"]["layers"][0]
    assert layer["label"] == "Growth"
    assert layer["axis_y"]["labels"] == {"format": ".1%"}
    assert "label" not in layer["axis_y"]
    AuthoredBoard.model_validate(migrated)


#: Every ``axis_y.label`` position ``suffix_rename_moves`` resolves at the
#: 0.5.0 boundary — the four layer-carrying positions, each authored either
#: via the ``charts:`` map (``charts.*``) or inline (``rows.*``/``cols.*``/
#: ``grid.items.*.item``), times their tab-scope variants (``tabs.items.*.*``,
#: a distinct model from ``AuthoredBoard`` so it stays covered at every
#: position). Written out rather than re-derived so a change to the
#: resolver's traversal shows up here as a diff instead of passing silently.
#:
#: Deliberately absent: a sub-board nested under ``rows``/``cols``/
#: ``grid.items.*.item`` that declares its own ``charts:`` map
#: (``rows.*.charts.*.layers.*.axis_y.label``) — see the module docstring in
#: ``v0_6_0.py`` for the ``_relative_field_paths`` self-nesting caveat this
#: shares with ``TONES_RENAMES``/``SUPPORT_TABLE_RENAMES``.
EXPECTED_AXIS_Y_LABEL_MOVES = (
    "charts.*.layers.*.axis_y.label",
    "cols.*.*.layers.*.axis_y.label",
    "cols.*.layers.*.axis_y.label",
    "grid.items.*.item.layers.*.axis_y.label",
    "rows.*.*.layers.*.axis_y.label",
    "rows.*.layers.*.axis_y.label",
    "tabs.items.*.cols.*.*.layers.*.axis_y.label",
    "tabs.items.*.cols.*.layers.*.axis_y.label",
    "tabs.items.*.grid.items.*.item.layers.*.axis_y.label",
    "tabs.items.*.rows.*.*.layers.*.axis_y.label",
    "tabs.items.*.rows.*.layers.*.axis_y.label",
)


def test_sub_board_charts_map_strands_layer_axis_y_label() -> None:
    """The one shape this rename does not repair, same ``_relative_field_paths``
    self-nesting gap as ``TONES_RENAMES``/``SUPPORT_TABLE_RENAMES``/``NOTES_RENAMES``.

    A sub-board that declares its own ``charts:`` map is opaque to the walk,
    so ``rows.*.charts.*.layers.*.axis_y.label`` is not among the resolved
    moves. Unlike ``support_table``/``notes``, the stranded position also
    gets no ``Did you mean`` hint: ``_allowed_keys_at_path`` is empty for a
    discriminated per-family layer union at this depth, so the author is told
    nothing about the replacement. Pinned so this is a recorded cost, not a
    surprise.
    """
    from dbt_charts.core.compile import compile

    yaml_content = """title: T
queries:
  q:
    sql: SELECT 1 AS a, 2 AS b, 3 AS c
    source: duckdb
rows:
  - charts:
      c1:
        type: bar
        query: q
        x: a
        y: b
        layers:
          - type: line
            y: c
            axis_y:
              label:
                format: ".1%"
    rows: [c1]
"""

    result = compile(yaml_content)

    assert not any(
        d.message for d in result.warnings if d.code == "WARN-SCHEMA-MIGRATED"
    ), "an unreachable move should not claim to have migrated anything"
    label_errors = [
        e
        for e in result.errors
        if e.code == "ERR-EXTRA-FIELD" and e.message.startswith("Unknown field 'label'")
    ]
    assert label_errors, (
        f"expected a stranded axis_y.label error: {[e.message for e in result.errors]}"
    )
    assert all("labels" not in (e.hint or "") for e in label_errors), (
        "unlike support_table/notes, this stranded position names no replacement"
    )


def test_resolved_move_set_is_exactly_the_documented_positions(
    catalog: YamlSchemaCatalog,
) -> None:
    """The ``axis_y.label`` tail resolves to these 11 positions and no others.

    Pins the position set itself (missing one strands a board that authored
    the field there — see ``test_sub_board_charts_map_strands_layer_axis_y_label``
    above; gaining one means the tail matched somewhere unintended) — not a
    claim about the anchor's necessity, which the module docstring in
    ``v0_6_0.py`` addresses separately."""
    from dbt_charts.core.compile.migrations.versions.v0_6_0 import moves

    axis_y_label_moves = [
        move
        for move in moves("0.5.0", "0.6.0", catalog=catalog)
        if move.old_path[-2:] == ("axis_y", "label")
    ]
    resolved = tuple(sorted(".".join(move.old_path) for move in axis_y_label_moves))
    assert resolved == EXPECTED_AXIS_Y_LABEL_MOVES
    for move in axis_y_label_moves:
        assert move.new_path == (*move.old_path[:-1], "labels")
