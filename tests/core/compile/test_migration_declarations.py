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
