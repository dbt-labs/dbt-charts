"""Assemble the dbt charts YAML completion catalog for editor autocomplete.

The catalog is the board JSON Schema (``render_yaml_schema``), which already
carries the built-in theme enum. Both the Cloud dashboard editor and the
Playground fetch it to drive schema-aware completions, so its
assembly lives here once rather than being duplicated per host. It is
generated once and cached for the process lifetime.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema


@lru_cache(maxsize=1)
def build_completion_catalog() -> dict[str, Any]:
    """Return the generated dbt charts YAML schema used by editor completions."""
    return render_yaml_schema(introspect())
