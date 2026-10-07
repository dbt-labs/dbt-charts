"""The gate that makes ``validate_declarations`` reach the shipped declarations."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.migrations import (
    MigrationError,
    MigrationRegistry,
    Move,
)
from dbt_charts.core.compile.migrations.migrations import (
    _build_board_migration_context,
)

from ._migration_catalogs import flat_schema, synthetic_catalog
from ._migration_declarations import validate_declarations

V1 = "0.1.0"
V2 = "0.2.0"


def test_shipped_declarations_are_consistent() -> None:
    _, registry = _build_board_migration_context()

    validate_declarations(registry)


def test_constructing_a_registry_does_not_check_declarations() -> None:
    """``old`` is declared renamed to ``new``, yet V2 still accepts ``old``."""
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("old", "new")})

    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    with pytest.raises(MigrationError, match="still exists in"):
        validate_declarations(registry)


def test_every_reported_successor_is_live_in_the_current_grammar() -> None:
    """A rename chain (one move's successor retired by another) would make the
    successor ``retired_keys`` names itself retired."""
    _, registry = _build_board_migration_context()
    retired_sources = {move.old_path for move in registry.moves}

    chained = [
        move.new_path
        for move in registry.moves
        if move.old_path != move.new_path and move.new_path in retired_sources
    ]

    assert chained == []


def _kpi_move(old_tail: tuple[str, ...]) -> MigrationRegistry:
    catalog, _ = _build_board_migration_context()
    released = catalog.latest_released.version
    move = Move(
        released,
        catalog.dev.version,
        ("charts", "*", *old_tail),
        ("charts", "*", "style", "label", "overflow"),
        chart_type="kpi",
    )
    return MigrationRegistry([move], catalog=catalog)


def test_chart_type_move_source_must_be_retired_on_that_family() -> None:
    with pytest.raises(MigrationError, match="still exists on 'kpi'"):
        validate_declarations(_kpi_move(("style", "value", "format")))


def test_chart_type_move_source_must_be_declared_on_that_family() -> None:
    with pytest.raises(MigrationError, match="no tail 'kpi' charts declare"):
        validate_declarations(_kpi_move(("style", "axis_x")))
