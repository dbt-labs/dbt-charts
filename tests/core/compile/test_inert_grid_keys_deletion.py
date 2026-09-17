"""The four inert ``grid:`` keys and how each one goes away.

``row_height``, ``default_width`` and ``default_height`` were schema-valid and
ignored, so they leave through the ``Deletion`` boundary declared in
``versions/v0_6_0.py`` (0.5.0 -> 0.6.0) -- authored boards migrate clean.

``gap`` is declarable but unconverted: its tail also matches the live
``style.layout.grid.gap``, so it is legal only root-anchored (``validate_declarations``'s
``retired_at_root``), with ``_live_declares_tail`` holding the firing off the
layout slot. Today it is fail-loud, with a hint naming the replacement.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.migrations import (
    IncompleteMigrationError,
    SchemaMigrationWarning,
    migrate_mapping,
)
from dbt_charts.core.compile.migrations.migrations import (
    Deletion,
    MigrationError,
    _board_migration_context,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
    load_yaml_schema_catalog,
)

from ._migration_declarations import checked_registry

DELETED_GRID_KEYS = ("row_height", "default_width", "default_height")


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    _, registry = _board_migration_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw, catalog=catalog, registry=registry)


def _grid_board(**grid_keys: Any) -> dict[str, Any]:
    return {
        "charts": {
            "sales": {
                "type": "bar",
                "query": "revenue",
                "x": "month",
                "y": "amount",
            }
        },
        "grid": {"columns": 24, **grid_keys, "items": [{"item": "sales", "width": 12}]},
    }


def test_registry_declares_the_0_5_0_grid_deletions() -> None:
    """Declared at the 0.5.0 -> 0.6.0 boundary, now frozen -- not at
    ``catalog.latest_released.version``, which is 0.6.0 itself post-freeze."""
    _, registry = _board_migration_context()
    deletions = registry.deletions_from("0.5.0")

    declared = {d.path for d in deletions if d.path[0] == "grid"}
    assert declared == {("grid", key) for key in DELETED_GRID_KEYS}


def test_inert_grid_keys_stripped_from_authored_board(
    catalog: YamlSchemaCatalog,
) -> None:
    raw = _grid_board(row_height="120px", default_width=8, default_height=1)

    migrated = _migrate(raw, catalog)

    grid = migrated["grid"]
    assert not set(grid) & set(DELETED_GRID_KEYS)
    assert grid["columns"] == 24
    assert grid["items"] == [{"item": "sales", "width": 12}]
    AuthoredBoard.model_validate(migrated)


def test_nested_board_grid_keys_stripped(catalog: YamlSchemaCatalog) -> None:
    """A grid inside a nested board is reached by the same walk."""
    raw: dict[str, Any] = {
        "charts": {
            "sales": {"type": "bar", "query": "revenue", "x": "month", "y": "amount"}
        },
        "rows": [
            {
                "title": "Section",
                "grid": {
                    "columns": 12,
                    "row_height": "200px",
                    "items": [{"item": "sales"}],
                },
            }
        ],
    }

    migrated = _migrate(raw, catalog)

    assert "row_height" not in migrated["rows"][0]["grid"]
    AuthoredBoard.model_validate(migrated)


def test_a_gap_deletion_is_rejected_on_the_dev_boundary(
    catalog: YamlSchemaCatalog,
) -> None:
    """Root ``grid.gap`` is already gone from 0.6.0, so it has nothing left to
    retire on the 0.6.0 -> 0.7.0 boundary and ``retired_at_root`` does not
    apply.

    Scoped to that boundary: the same tail is accepted at 0.5.0 -> 0.6.0, which
    is where a conversion would go.
    """
    _, registry = _board_migration_context()

    with pytest.raises(MigrationError, match="still exists in"):
        checked_registry(
            registry.moves,
            (
                *registry.deletions,
                Deletion(
                    catalog.latest_released.version,
                    catalog.dev.version,
                    ("grid", "gap"),
                ),
            ),
            registry.conditional_moves,
            catalog=catalog,
        )


def test_style_layout_grid_gap_survives_the_grid_deletions(
    catalog: YamlSchemaCatalog,
) -> None:
    """The live style key must survive a board that also carries a deleted tail."""
    raw = _grid_board(row_height="120px")
    raw["style"] = {"layout": {"grid": {"gap": 20.0, "columns": 24}}}

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["layout"]["grid"] == {"gap": 20.0, "columns": 24}
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("key", DELETED_GRID_KEYS)
def test_authoring_a_deleted_grid_key_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        AuthoredBoard.model_validate(_grid_board(**{key: 1}))


def test_authoring_grid_gap_is_rejected() -> None:
    """``gap`` ships no Deletion today, so the key still fails loud."""
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        AuthoredBoard.model_validate(_grid_board(gap="md"))


def test_grid_gap_error_points_at_the_style_key() -> None:
    """The removal has a working replacement, so the error names it."""
    result = compile(
        """title: Test
queries:
  q1:
    sql: SELECT 1 AS x
    source: test
charts:
  c1:
    type: line
    query: q1
    x: x
    y: x
grid:
  columns: 24
  gap: md
  items:
    - item: c1
"""
    )

    assert not result.success
    err = next(e for e in result.errors if e.hint and "gap" in e.hint)
    assert "style.layout.grid.gap" in err.hint


def test_gap_alongside_the_three_blocks_the_whole_migration(
    catalog: YamlSchemaCatalog,
) -> None:
    """The canonical five-key block cannot migrate: gap has no Deletion.

    The three strip cleanly, the result still fails the current schema on
    ``gap``, and the migration is abandoned — so the author is handed the
    original mapping and sees one unknown-field error per key. Fail-loud and
    intended (``v0_5_0.py`` documents the same outcome for ``style.legend``),
    but it is the likely authored shape, so it is pinned here.
    """
    _, registry = _board_migration_context()
    raw = _grid_board(row_height="120px", default_width=8, default_height=1, gap="md")

    with pytest.raises(IncompleteMigrationError):
        migrate_mapping(raw, catalog=catalog, registry=registry)

    result = compile(
        """title: Test
queries:
  q1:
    sql: SELECT 1 AS x
    source: test
charts:
  c1:
    type: line
    query: q1
    x: x
    y: x
grid:
  columns: 24
  row_height: "120px"
  default_width: 8
  default_height: 1
  gap: md
  items:
    - item: c1
"""
    )

    assert not result.success
    unknown = {e.fields.get("unknown_field") for e in result.errors if e.fields}
    assert unknown >= {"gap", *DELETED_GRID_KEYS}


# An axis `grid:` block, reached three ways. The third has neither a `style`
# segment nor a `charts:` root — the shape that defeats any anchor read off the
# path's names.
AXIS_GRID_BOARDS = {
    "chart style": (
        """charts:
  c1:
    type: line
    query: q1
    x: x
    y: x
    style:
      axis_x:
        grid:
          gap: 4
rows:
  - c1""",
        "charts.c1.line.style.axis_x.grid.gap",
    ),
    "layer axis": (
        """charts:
  c1:
    type: line
    query: q1
    x: x
    y: x
    layers:
      - type: line
        y: x
        axis_y:
          grid:
            gap: 4
rows:
  - c1""",
        "charts.c1.line.layers.0.line.axis_y.grid.gap",
    ),
    "inline chart in rows": (
        """rows:
  - type: line
    query: q1
    x: x
    y: x
    layers:
      - type: line
        y: x
        axis_y:
          grid:
            gap: 4""",
        "rows.0.line.layers.0.line.axis_y.grid.gap",
    ),
}


@pytest.mark.parametrize(
    ("board", "expected_path"), AXIS_GRID_BOARDS.values(), ids=AXIS_GRID_BOARDS
)
def test_axis_gridline_gap_does_not_get_the_layout_hint(
    board: str, expected_path: str
) -> None:
    """An axis ``grid:`` is gridline styling, not a board layout.

    The guard asks the schema which model the parent is, rather than reading
    names off the path — these three paths share the ``grid.gap`` tail with the
    board layout and no name-level rule separates all of them.
    """
    result = compile(
        f"""title: Test
queries:
  q1:
    sql: SELECT 1 AS x
    source: test
{board}
"""
    )

    assert not result.success
    gap_errors = [
        e for e in result.errors if (e.fields or {}).get("unknown_field") == "gap"
    ]
    assert [e.fields["field_path"] for e in gap_errors] == [expected_path]
    assert "style.layout.grid.gap" not in (gap_errors[0].hint or "")
