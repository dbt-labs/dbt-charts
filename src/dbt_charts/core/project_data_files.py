"""Data files a project holds: inline `source:` paths and database files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import yaml

from dbt_charts.core.compile.file_refs import INLINE_FILE_SOURCE_SUFFIXES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dbt_charts.core.project import Project


_DATABASE_SUFFIXES = {".duckdb": "duckdb", ".sqlite": "sqlite"}


def _project_files(project: Project, suffixes: Sequence[str]) -> list[str]:
    """Project-relative paths in the root and ``data/`` (not recursive)."""
    return sorted(
        rel
        for folder in (".", "data")
        for rel in project.iter_files(folder, recursive=False)
        if not PurePosixPath(rel).name.startswith(".")
        and PurePosixPath(rel).suffix.lower() in suffixes
    )


def data_file_refs(project: Project) -> list[str]:
    """`source:` values (``./data/orders.csv``) for the files a board reads inline."""
    return [
        f"./{rel}" for rel in _project_files(project, list(INLINE_FILE_SOURCE_SUFFIXES))
    ]


def database_entries(project: Project) -> dict[str, str]:
    """Database file -> the `sources:` block that declares it (they have no inline form)."""
    files = _project_files(project, list(_DATABASE_SUFFIXES))
    stems = [re.sub(r"\W", "_", PurePosixPath(rel).stem) for rel in files]
    entries = {}
    for rel, stem in zip(files, stems, strict=True):
        suffix = PurePosixPath(rel).suffix.lower()
        name = stem if stems.count(stem) == 1 else f"{stem}_{suffix[1:]}"
        entries[rel] = yaml.safe_dump(
            {"sources": {name: {"type": _DATABASE_SUFFIXES[suffix], "path": rel}}},
            sort_keys=False,
        )
    return entries


def _listing_lines(project: Project) -> list[str]:
    refs = data_file_refs(project)
    lines = (
        ["Data files (write the path as `source:`): " + ", ".join(refs)] if refs else []
    )
    for rel, block in database_entries(project).items():
        lines.append(f"Database file {rel}; declare it in dbt_charts.yml:\n{block}")
    return lines


def sources_listing(project: Project, configured: Sequence[str]) -> str:
    """Every way to name a source here: configured names, data file paths, and
    the `sources:` entry for each database file.

    Raises:
        ValueError: the project has none.
    """
    lines = _listing_lines(project)
    if configured:
        lines.insert(
            0, "Sources (write the name as `source:`): " + ", ".join(configured)
        )
    if not lines:
        raise ValueError(
            "no sources configured and no data files (csv, json, parquet, duckdb, "
            "sqlite) found in the project root or data/"
        )
    return "\n".join(lines)


def unknown_source_advice(project: Project, source: str) -> str:
    """Text for an unknown *source*: a did-you-mean when a data file has that stem
    (never a guess), then every data file and database entry.

    Returns an empty string when the project holds no data file.
    """
    lines = _listing_lines(project)
    if not lines:
        return ""
    for rel, block in database_entries(project).items():
        if PurePosixPath(rel).stem == source:
            lines.insert(
                0,
                f"Did you mean the database file `{rel}`? Add it under `sources:` as:\n{block}",
            )
    same_stem = [r for r in data_file_refs(project) if PurePosixPath(r).stem == source]
    if same_stem:
        options = " or ".join(f"`source: {ref}`" for ref in same_stem)
        lines.insert(0, f"Did you mean {options}?")
    return "\n" + "\n".join(lines)
