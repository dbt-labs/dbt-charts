"""Neutral-leaf dbt manifest loader.

Owns: Project-seam reads, RefIndex derivation, and per-process
content-addressed memo.

LoadedManifest.raw (the plain json.loads dict) is the data contract for all
downstream consumers. Nothing here deserializes through dbt's typed
WritableManifest, so metadata.dbt_schema_version is not read: the handful of
node keys this module touches (resource_type, name, schema, alias,
relation_name, source_name) have been stable across every manifest version,
and gating on the version string only turns a manifest we read correctly into
a hard error. A node whose shape has genuinely drifted is skipped, so the
board author sees ERR-DBT-REF-UNKNOWN-NODE / ERR-DBT-SOURCE-UNKNOWN-TABLE
naming the ref they wrote rather than a KeyError.

Public symbols:
  manifest_relpath(project, target_path)  -> project-relative manifest path
  load_manifest(project, target_path)     -> LoadedManifest | None
  load_manifest_at(project, rel)  -> LoadedManifest
  ref_index(loaded)               -> RefIndex
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import PureWindowsPath
from posixpath import normpath
from typing import TYPE_CHECKING, Any

import yaml

from dbt_charts.core.diagnostics.codes_execute import (
    ERR_DBT_MANIFEST_UNREADABLE,
    ERR_DBT_TARGET_PATH_INVALID,
)
from dbt_charts.core.diagnostics.execution import ExecutionError

if TYPE_CHECKING:
    from dbt_charts.core.project import Project

_DEFAULT_TARGET_PATH = "target"

# Node kinds ref() addresses.
_REFABLE_RESOURCE_TYPES = frozenset({"model", "seed", "snapshot"})

# Per-process memo: (relpath, file_version) → LoadedManifest.
# FIFO-evicting at _MEMO_MAXSIZE; relpath in key avoids collisions when two
# manifests share identical content.
_MEMO_MAXSIZE = 16
_memo: dict[tuple[str, str], LoadedManifest] = {}


@dataclass(frozen=True)
class LoadedManifest:
    """Transport wrapper for a loaded dbt manifest.

    raw:     Plain json.loads dict — the data contract for all downstream
             consumers. dbt 1.11.x mashumaro union types ALL test nodes as
             SingularTest, so test_metadata is inaccessible on typed nodes;
             raw dict access is the only reliable path.
    relpath: Which project-relative path this manifest was loaded from.
    version: ``project.file_version(relpath)`` at load time — the stable
             identity downstream memos key on (``id(raw)`` can be reused
             after GC, silently serving one manifest's derivation for
             another).
    """

    raw: dict[str, Any]
    relpath: str
    version: str


@dataclass(frozen=True)
class RefIndex:
    """Compact derived index for ref()/source() resolution and did-you-mean.

    refs:              name → (relation_name, schema) for model/seed/snapshot.
    sources:           (source_name, table) → (relation_name, schema).
    available_refs:    sorted ref names for unknown-ref diagnostics.
    available_sources: sorted "source.table" strings for unknown-source diagnostics.
    """

    refs: dict[str, tuple[str, str]]
    sources: dict[tuple[str, str], tuple[str, str]]
    available_refs: list[str]
    available_sources: list[str]


def manifest_relpath(project: Project | None, target_path: str | None = None) -> str:
    """Project-relative path of the manifest a ref() resolves against.

    Precedence is dbt's own for ``--target-path``: the source's authored
    ``target_path`` (the flag's stand-in), then ``DBT_TARGET_PATH``, then
    ``dbt_project.yml``'s ``target-path:``, then ``target``. ``project=None``
    (a host with no project to read) skips the ``dbt_project.yml`` step, so
    the path is only ever named in errors.

    Raises ExecutionError (ERR-DBT-TARGET-PATH-INVALID) when the directory is
    absolute, escapes the project, or is an unrendered template.
    """
    directory, origin = target_path, "the source's target_path"
    if not directory:
        directory, origin = (
            os.environ.get("DBT_TARGET_PATH"),  # noqa: TID251 — dbt-convention parity
            "DBT_TARGET_PATH",
        )
    if not directory and project is not None:
        directory, origin = (
            _project_target_path(project.manifest_project()),
            "target-path in dbt_project.yml",
        )
    directory = directory or _DEFAULT_TARGET_PATH
    if "{{" in directory:
        detail = "it is an unrendered Jinja template"
    elif "\\" in directory or PureWindowsPath(directory).anchor:
        detail = "it is not a relative POSIX path"
    elif normpath(directory).startswith(".."):
        detail = "it escapes the dbt project"
    else:
        return f"{directory.rstrip('/')}/manifest.json"
    raise ExecutionError.from_code(
        ERR_DBT_TARGET_PATH_INVALID, origin=origin, detail=f"{directory!r}: {detail}"
    )


def _project_target_path(manifest_project: Project) -> str | None:
    if not manifest_project.exists("dbt_project.yml"):
        return None
    try:
        config = manifest_project.read_yaml("dbt_project.yml")
    except yaml.YAMLError as exc:
        raise ExecutionError.from_code(
            ERR_DBT_TARGET_PATH_INVALID,
            origin="dbt_project.yml",
            detail=f"dbt_project.yml is not valid YAML: {exc}",
        ) from exc
    if not isinstance(config, dict):
        return None
    value = config.get("target-path")
    return str(value) if value else None


def load_manifest(
    project: Project, target_path: str | None = None, *, optional: bool = False
) -> LoadedManifest | None:
    """Load and parse the project's dbt manifest through the Project seam.

    Returns None when the manifest does not exist at ``manifest_relpath``.
    ``optional=True`` is for readers that only enrich their output: an unusable
    target path then reads as no manifest instead of raising.

    Raises ExecutionError (ERR-DBT-MANIFEST-UNREADABLE) when it exists but is
    unreadable or corrupt.
    """
    manifest_project = project.manifest_project()
    try:
        relpath = manifest_relpath(project, target_path)
    except ExecutionError as exc:
        if optional and exc.code is ERR_DBT_TARGET_PATH_INVALID:
            return None
        raise
    if not manifest_project.exists(relpath):
        return None
    return load_manifest_at(manifest_project, relpath)


def ref_index(loaded: LoadedManifest) -> RefIndex:
    """Derive the compact ref/source index from a LoadedManifest.

    Reads from loaded.raw (the pre-upgrade dict). First-wins for cross-package
    name collisions, matching the order nodes appear in the manifest.
    """
    refs: dict[str, tuple[str, str]] = {}
    sources: dict[tuple[str, str], tuple[str, str]] = {}

    for node in loaded.raw.get("nodes", {}).values():
        if node.get("resource_type") not in _REFABLE_RESOURCE_TYPES:
            continue
        name = node.get("name")
        schema = node.get("schema")
        if not name or not schema:
            continue
        if node.get("relation_name"):
            rel = str(node["relation_name"])
        else:
            alias = str(node.get("alias") or name)
            rel = f"{schema}.{alias}"
        refs.setdefault(name, (rel, schema))

    for node in loaded.raw.get("sources", {}).values():
        source_name = node.get("source_name")
        table_name = node.get("name")
        schema = node.get("schema")
        if not source_name or not table_name or not schema:
            continue
        if node.get("relation_name"):
            rel = str(node["relation_name"])
        else:
            rel = f"{schema}.{table_name}"
        sources.setdefault((source_name, table_name), (rel, schema))

    available_refs = sorted(refs)
    available_sources = sorted(f"{s}.{t}" for s, t in sources)
    return RefIndex(
        refs=refs,
        sources=sources,
        available_refs=available_refs,
        available_sources=available_sources,
    )


def load_manifest_at(project: Project, relpath: str) -> LoadedManifest:
    """Load and parse a manifest from a specific project-relative path.

    Memoized per process on project.file_version(relpath). Use this when
    a caller needs to load a specific path rather than the resolved one.

    Raises ExecutionError (ERR-DBT-MANIFEST-UNREADABLE) on an unreadable file
    or corrupt JSON.
    """
    try:
        version = project.file_version(relpath)
        memo_key = (relpath, version)
        if memo_key in _memo:
            return _memo[memo_key]

        raw_text = project.read_text(relpath)
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ExecutionError.from_code(
                ERR_DBT_MANIFEST_UNREADABLE,
                relpath=relpath,
                detail=f"top-level JSON is {type(raw).__name__}, not an object",
            )
    except ExecutionError:
        raise
    except Exception as exc:  # noqa: BLE001 — OSError/JSONDecodeError from the seam
        raise ExecutionError.from_code(
            ERR_DBT_MANIFEST_UNREADABLE,
            relpath=relpath,
            detail=str(exc),
        ) from exc

    loaded = LoadedManifest(raw=raw, relpath=relpath, version=version)
    if len(_memo) >= _MEMO_MAXSIZE:
        _memo.pop(next(iter(_memo)))
    _memo[memo_key] = loaded
    return loaded
