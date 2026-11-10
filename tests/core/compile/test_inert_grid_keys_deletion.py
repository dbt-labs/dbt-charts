"""The four inert ``grid:`` keys and how each one goes away.

``row_height``, ``default_width`` and ``default_height`` were schema-valid and
ignored, so they leave through the ``Deletion`` boundary declared in
``versions/v0_6_0.py`` (0.5.0 -> 0.6.0) -- authored boards migrate clean.

``gap`` was inert too, but its tail also matches the live
``style.layout.grid.gap``: it is legal only root-anchored
(``validate_declarations``'s ``retired_at_root``), with ``_live_declares_tail``
holding the firing off the layout slot. Its strip names the replacement.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pydantic import ValidationError

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

DELETED_GRID_KEYS = ("row_height", "default_width", "default_height", "gap")


@pytest.fixture
def catalog() -> YamlSchemaCatalog:
    return load_yaml_schema_catalog()


def _migrate(raw: dict[str, Any], catalog: YamlSchemaCatalog) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return migrate_mapping(raw)


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


def test_style_layout_grid_gap_survives_the_grid_deletions(
    catalog: YamlSchemaCatalog,
) -> None:
    """The live style key must survive the root gap's own deletion -- the
    tail matches both."""
    raw = _grid_board(gap="md")
    raw["style"] = {"layout": {"grid": {"gap": 20.0, "columns": 24}}}

    migrated = _migrate(raw, catalog)

    assert migrated["style"]["layout"]["grid"] == {"gap": 20.0, "columns": 24}
    AuthoredBoard.model_validate(migrated)


@pytest.mark.parametrize("key", DELETED_GRID_KEYS)
def test_authoring_a_deleted_grid_key_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        AuthoredBoard.model_validate(_grid_board(**{key: 1}))


def test_grid_gap_strip_names_the_style_key(catalog: YamlSchemaCatalog) -> None:
    """The removal has a working replacement, so the warning names it."""

    with pytest.warns(SchemaMigrationWarning, match="style.layout.grid.gap"):
        migrate_mapping(_grid_board(gap="md"))


def test_the_canonical_four_key_block_compiles() -> None:
    """All four keys together -- the shape this release deleted -- strand
    nothing: the board compiles with its chart intact."""
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

    assert result.success, result.errors
    assert "c1" in result.board.charts


def test_grid_gap_error_points_at_the_style_key() -> None:
    """Where migration cannot finish (an unknown key strands the board), the
    unknown-field error on gap still names the replacement."""
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
    not_a_chart_field: 1
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
        "charts.c1.style.axis_x.grid.gap",
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
        "charts.c1.layers.0.axis_y.grid.gap",
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
        "rows.0.layers.0.axis_y.grid.gap",
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
