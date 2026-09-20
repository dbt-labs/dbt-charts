"""Build the caches that are a pure function of the installed package.

A long-lived host calls ``warm_process`` once per process, before it serves.
Each cache below is built lazily on first use otherwise, and the first use is a
request. Only a board on an older grammar needs the migration registry, so a
current board never reveals the cost and an older one pays it on somebody's
click.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.migrations.migrations import _board_migration_context
from dbt_charts.core.compile.schema.introspection import introspect


def warm_process() -> None:
    """Build the migration registry, the authorable schema, and the built-in
    theme. Idempotent; a process-constant cache that belongs here is one keyed
    on nothing a request supplies."""
    _board_migration_context()
    introspect()
    get_theme_style()
