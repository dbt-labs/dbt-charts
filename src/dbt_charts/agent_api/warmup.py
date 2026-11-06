"""Build the caches that are a pure function of the installed package.

A long-lived host calls ``warm_process`` once per process, before it serves.
Each cache below is built lazily on first use otherwise, and the first use is a
request. Only a board on an older grammar makes the migration engine build its
declarations, so a current board never reveals the cost and an older one pays
it on somebody's click.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.migrations.migrations import (
    collect_migration_notices,
    package_schemas,
    prepare_board_mapping,
)
from dbt_charts.core.compile.schema.introspection import introspect

# `description:` was renamed to `notes:`, so this document leaves the current
# grammar and takes the path that builds the engine's declarations.
_RETIRED_BOARD = {"title": "warm", "description": "warm", "rows": []}


def warm_process() -> None:
    """Build the frozen-schema set, the migration engine's declarations, the
    authorable schema, and the built-in theme. Idempotent; a process-constant
    cache that belongs here is one keyed on nothing a request supplies."""
    package_schemas()
    with collect_migration_notices():
        prepare_board_mapping(dict(_RETIRED_BOARD))
    introspect()
    get_theme_style()
