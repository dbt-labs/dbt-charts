"""Cache-source factory and schema resolver builder.

Builds a LayeredSchemaResolver from an exec env. When the private
``dbt-charts-super-schema`` package is installed, the resolver uses a warm
SuperSchemaSource cache; otherwise it operates in dbt-only mode.

This module provides factory helpers for building resolvers with optional
super-schema caches. Other modules that only need a presence check use their
own ``importlib.util.find_spec("dbt_charts_super_schema")`` probe — that is
intentional and avoids import cycles.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path  # noqa: TID251 — super-schema cache file on disk
from typing import TYPE_CHECKING, Any

from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.inspect.resolver import LayeredSchemaResolver

if TYPE_CHECKING:
    from dbt_charts.core.project import Project
    from dbt_charts_super_schema.inspect.sources.super_schema import SuperSchemaSource

# Probe at module load time — single find_spec call per process.
_SUPER_SCHEMA_AVAILABLE: bool = (
    importlib.util.find_spec("dbt_charts_super_schema") is not None
)


def make_cache_source(cache_path: Path) -> Any:
    """Build a SuperSchemaSource when the private package is available.

    Args:
        cache_path: Absolute path to a ``super_schema.json`` cache file.

    Returns ``None`` when ``dbt-charts-super-schema`` is not installed.
    The resolver degrades to dbt-only mode when ``None`` is returned.
    """
    if not _SUPER_SCHEMA_AVAILABLE:
        return None
    from dbt_charts_super_schema.inspect.sources.super_schema import (  # noqa: PLC0415
        SuperSchemaSource,
    )
    from dbt_charts_super_schema.inspect.storage import (
        InspectionStorage,  # noqa: PLC0415
    )

    return SuperSchemaSource(InspectionStorage(output_path=cache_path))


def make_cache_source_for_project(project: Project) -> Any:
    """Build a SuperSchemaSource reading the committed cache through *project*.

    ``target/super_schema.json`` is read via the Project file-access seam, so
    hosts with a non-filesystem backing store (Cloud's git-blob reader) serve
    the same committed artifact a local checkout reads from disk. Baking stays
    a local workflow — this source is read-only.

    Returns ``None`` when ``dbt-charts-super-schema`` is not installed.
    """
    if not _SUPER_SCHEMA_AVAILABLE:
        return None
    from dbt_charts_super_schema.inspect.sources.super_schema import (  # noqa: PLC0415
        SuperSchemaSource,
    )
    from dbt_charts_super_schema.inspect.storage import (  # noqa: PLC0415
        ProjectInspectionStorage,
    )

    return SuperSchemaSource(ProjectInspectionStorage(project))


def build_resolver(
    env: ExecEnv,
    cache_path: Path | None = None,
    cache_source: SuperSchemaSource | None = None,
) -> LayeredSchemaResolver:
    """Build a LayeredSchemaResolver from an exec env and optional cache.

    Args:
        env: Provides source configs and the project.
        cache_path: Explicit path to a ``super_schema.json`` file — a local
            disk artifact (sanctioned Path edge — like exports). When ``None``,
            the committed cache is read through the project's file-access seam
            via ``make_cache_source_for_project``.
        cache_source: Explicit warm-cache source. Takes priority over
            ``cache_path`` when provided.
    """
    project = env.project
    resolved_cache: Any = cache_source
    if resolved_cache is None:
        resolved_cache = (
            make_cache_source(cache_path)
            if cache_path is not None
            else make_cache_source_for_project(project)
        )
    return LayeredSchemaResolver(
        cache=resolved_cache,
        env=env,
        project=project,
    )
