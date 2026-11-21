"""Schema documentation generation for AI integrations.

Delegates to the two-layer IR pipeline:
  introspect() → AuthorableSchema → render_prompt() → str

The project-config surface (`dbt_charts.yml`) walks and renders through its own
pair, so only this generated document gains its keys.
"""

from __future__ import annotations

from dbt_charts.core.compile.schema.facets import board_keys_with, keys_with
from dbt_charts.core.compile.schema.introspection import (
    introspect,
    introspect_project_config,
)
from dbt_charts.core.compile.schema.renderers.prompt import (
    render_project_config,
    render_prompt,
)


def get_schema_for_prompt() -> str:
    """Get complete schema documentation: board grammar, then project config."""
    return (
        render_prompt(introspect())
        + "\n\n"
        + render_project_config(introspect_project_config())
    )


__all__ = ["board_keys_with", "get_schema_for_prompt", "keys_with"]
