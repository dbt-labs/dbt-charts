"""The engine's in-memory migration as the migration tests drive it.

``migrate_mapping`` runs the Rust engine on a raw mapping and, unlike
``prepare_board_mapping``, raises when the document is old and the migration
cannot finish, so a test can pin the refusal.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from dbt_charts import _engine
from dbt_charts.core.compile.migrations import (
    MigrationConflictError,
    MigrationError,
    UnsupportedSchemaError,
)
from dbt_charts.core.compile.migrations.migrations import warn_migration

# Before every release date, so the six-month support window never expires a
# fixture's grammar.
RECOGNITION_DAY = date(2000, 1, 1)


def migrate_mapping(mapping: Any, *, today: date = RECOGNITION_DAY) -> Any:  # noqa: ANN401 — a raw board mapping
    document, notices, outcome = _engine.migrate_mapping(
        mapping, today.isoformat(), "migrate_mapping"
    )
    if outcome == "unrecognized":
        raise UnsupportedSchemaError("Unsupported YAML syntax")
    if outcome == "kept":
        # The engine's notice names a conflict as "... is also set. Choose one value".
        error = (
            MigrationConflictError
            if "Choose one value" in notices[-1]
            else MigrationError
        )
        raise error(notices[-1])
    for notice in notices:
        warn_migration(notice, stacklevel=2)
    return document
