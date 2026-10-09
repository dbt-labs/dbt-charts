"""Shared compile-test helpers for dbt-charts/tests/core/compile/."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile as _compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.cache import CacheInput, CachePatch
from dbt_charts.core.project import ProjectDirectory


def compile_with_board_sources(
    board_yaml: str, base_dir: ProjectDirectory | None = None
) -> CompileResult:
    """Compile a board YAML that still authors the pre-D-09 board-local
    `sources:` block, by lifting it into `project_sources` before compiling.

    Boards can no longer define sources inline (D-01/D-02/D-09) — the registry
    moved to the project level. Rather than hand-migrate every fixture in this
    package that predates that change, pop `sources:` out of the parsed YAML
    and pass it as `project_sources`, which is exactly where a project-level
    registry belongs; the remaining board YAML (queries/charts/`source: <name>`)
    is unaffected. A `sources.default: <name>` shorthand (the removed
    `SourcesSection.default` field) is hoisted to the board-level `source:` key
    when the fixture doesn't already set one, matching the old default-source
    behavior.
    """
    data: dict[str, Any] = yaml.safe_load(board_yaml)
    sources = data.pop("sources", None)
    default_source = sources.pop("default", None) if sources else None
    if default_source and "source" not in data:
        data["source"] = default_source
    project_sources = ProjectSourcesConfig(sources=sources) if sources else None
    return _compile(yaml.dump(data), base_dir=base_dir, project_sources=project_sources)


def project_with_db_source() -> FilesystemProject:
    """A Project whose sources registry resolves the 'db' name to an in-memory
    DuckDB connection — the execute-time counterpart to a compiled board whose
    `source: db` came from `compile_with_board_sources`'s old-style
    `sources: {db: {type: duckdb, path: ':memory:'}}` fixture (D-09: boards
    can no longer define sources inline, so the registry lives on the project).
    """
    project = FilesystemProject(Path.cwd())
    project.__dict__["sources"] = ProjectSourcesConfig(
        sources={"db": {"type": "duckdb", "path": ":memory:"}}
    )
    return project


_CACHE_INPUT = TypeAdapter(CacheInput)


def cache_layer(raw: Any) -> CachePatch:  # type-state: explicit_any — raw authored YAML
    """Parse an authored ``cache:`` value (scalar or block) into a layer."""
    return _CACHE_INPUT.validate_python(raw)
