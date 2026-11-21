"""Registered view models — typed registry entry shapes.

Purpose: Data classes for registered view registry entries.

A registered view maps a URL route pattern to an editable YAML template
and an optional set of pre-template queries. Python owns route compilation;
YAML owns the visible template structure.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RegisteredView(BaseModel):
    """A single entry in the registered view registry.

    Declares the route, template path, and optional pre-template queries
    for one system or project view.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Stable identifier for this view.")
    route: str = Field(
        description=(
            "URL pattern with named path params in angle brackets, e.g. "
            "'/data/<source>/<schema>/'. Must begin and end with '/'."
        )
    )
    template: str = Field(
        description=(
            "Relative path to the YAML template file within the registered_views "
            "templates directory, e.g. 'data/table-index.yaml'."
        )
    )
    queries: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional pre-template queries keyed by name. Each value is a raw "
            "query dict using the normal dbt charts query schema. Results are "
            "exposed to the template as queries.<name>."
        ),
    )
    experimental: bool = Field(
        default=False,
        description=(
            "When True, this view is hidden from production routing unless "
            "include_experimental is explicitly requested. System views ship stable."
        ),
    )
