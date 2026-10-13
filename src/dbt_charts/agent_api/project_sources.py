"""Read a project's declared ``sources:`` names and types without rendering
or validating them.

`dct cloud status`'s "This checkout" section needs only a source's name,
declared type, and whether that type needs a warehouse connection -- never
the full config. Going through ``Project.sources`` (``load_project_sources``)
renders every entry's Jinja (``{{ env_var(...) }}``) and validates it against
its typed ``SourceConfig`` -- exactly the two things this surface must not do:
an unset env_var raises before rendering finishes, and the onboarding shell
this feature is for is precisely the one without the warehouse env vars
Cloud holds. Reading the raw mapping instead means neither failure mode can
reach this path at all: nothing here is ever rendered, so nothing here is
ever a secret.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.project import Project


class DeclaredSource(BaseModel):
    """One source name and its declared type, read raw -- never the full
    entry, which can carry warehouse-specific fields alongside it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Source name as boards reference it.")
    type: str | None = Field(
        default=None,
        description=(
            "The raw `type:` value, or null when the entry has no `type:` "
            "key or the value is itself templated (e.g. `{{ env_var(...) "
            "}}`) and so cannot be read as a plain string without rendering."
        ),
    )
    needs_connection: bool = Field(
        description=(
            "Whether this type needs a warehouse connection, classified "
            "from core's source registry (false for a file source, an "
            "unrecognized type, or a null type)."
        )
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this entry's own fields could not be read, when it could "
            "not -- e.g. the entry itself is not a mapping. The source "
            "still counts and is still named; only its type/needs_connection "
            "are unavailable."
        ),
    )


class ProjectSourcesReadResult(BaseModel):
    """The project's declared sources, or why they could not be read."""

    model_config = ConfigDict(frozen=True)

    sources: list[DeclaredSource] = Field(
        default_factory=list, description="Empty when `error` is set."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why dbt_charts.yml's sources: section could not be read at "
            "all, when it could not -- never a rendered credential or an "
            "env var's value, because nothing here is ever rendered."
        ),
    )


def read_project_sources(project: Project) -> ProjectSourcesReadResult:
    """The project's declared sources: names, raw declared types, and
    whether each needs a warehouse connection.

    Covers only ``dbt_charts.yml``'s own top-level ``sources:`` block. A
    ``type: dbt_profile`` entry is itself a ``sources:`` entry here (counted,
    named, classified as needing a connection); the warehouse its
    ``profile:``/``target:`` names inside ``profiles.yml`` is not read by
    this function -- resolving that reference requires dbt's own profile
    loader, which this function deliberately never invokes.
    """
    try:
        doc = project.config_document()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return ProjectSourcesReadResult(error=str(exc))
    if doc is None:
        return ProjectSourcesReadResult()
    filename, parsed = doc
    if parsed is None:
        return ProjectSourcesReadResult()
    if not isinstance(parsed, dict):
        return ProjectSourcesReadResult(error=f"{filename} must decode to a mapping")
    raw_sources = parsed.get("sources")
    if raw_sources is None:
        return ProjectSourcesReadResult()
    if not isinstance(raw_sources, dict):
        return ProjectSourcesReadResult(
            error=f"{filename}: sources section must decode to a mapping"
        )
    return ProjectSourcesReadResult(
        sources=[
            _declared_source(str(name), entry)
            for name, entry in sorted(raw_sources.items(), key=_sort_key)
        ]
    )


def _sort_key(item: tuple[Any, Any]) -> str:
    """Sort by the string form of a source name -- `sorted()` on the raw
    dict's own key type crashes outright on a mix of int and string keys."""
    return str(item[0])


def _declared_source(name: str, entry: object) -> DeclaredSource:
    if not isinstance(entry, dict):
        return DeclaredSource(
            name=name,
            type=None,
            needs_connection=False,
            error=f"entry must be a mapping, got {type(entry).__name__}",
        )
    raw_type = entry.get("type")
    if not isinstance(raw_type, str) or "{{" in raw_type:
        return DeclaredSource(name=name, type=None, needs_connection=False)
    # Lazy: core.compile.models.source pulls in core.compile, which `dct
    # cloud` must not load at module-import time -- see the identical note
    # on the agent_api.boards import in cli/commands/cloud.py.
    from dbt_charts.core.compile.models.source import (
        FILE_SOURCE_TYPES,
        VALID_SOURCE_TYPES,
    )

    needs_connection = (
        raw_type in VALID_SOURCE_TYPES and raw_type not in FILE_SOURCE_TYPES
    )
    return DeclaredSource(name=name, type=raw_type, needs_connection=needs_connection)
