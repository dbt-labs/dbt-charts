"""dbt-core plugin: writes ``minimani.json`` next to every ``manifest.json``.

dbt discovers it through the ``plugins`` attribute of any installed ``dbt_*``
module. ``minimani.json`` is the manifest's ``RefIndex`` and nothing else,
built by the same ``ref_index`` the resolver uses, so it changes only when a
ref()/source() relation changes.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import dbt.flags
from dbt.artifacts.schemas.base import ArtifactMixin
from dbt.plugins.manager import dbt_hook, dbtPlugin
from dbt_common.events.base_types import EventLevel
from dbt_common.events.functions import fire_event
from dbt_common.events.types import Note

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.dbt_manifest import LoadedManifest, manifest_relpath, ref_index

if TYPE_CHECKING:
    from dbt.contracts.graph.manifest import Manifest
    from dbt.plugins.contracts import PluginArtifacts

MINIMANI_VERSION = 1


class MinimaniArtifact(ArtifactMixin):
    """Not a dbt schema-versioned artifact: ``ArtifactMixin`` is the type dbt's
    hook signature requires, and dbt only ever calls ``write``."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def write(self, path: str) -> None:
        try:
            Path(path).write_text(
                json.dumps(self.payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _warn(exc)


class ChartsPlugin(dbtPlugin):
    @dbt_hook
    def get_manifest_artifacts(self, manifest: Manifest) -> PluginArtifacts:
        try:
            # Read at call time: dbt rebinds GLOBAL_FLAGS per invocation.
            flags = dbt.flags.GLOBAL_FLAGS
            project_dir = Path(flags.PROJECT_DIR)
            relpath = manifest_relpath(
                FilesystemProject(project_dir), flags.TARGET_PATH
            )
            path = project_dir / PurePosixPath(relpath).parent / "minimani.json"
            return {str(path): MinimaniArtifact(minimani_payload(manifest))}
        except Exception as exc:  # noqa: BLE001 — a dbt Charts bug must never fail the user's dbt run
            _warn(exc)
            return {}


def minimani_payload(manifest: Manifest) -> dict[str, Any]:
    """The manifest's RefIndex as plain JSON-ready dicts."""
    raw = {
        "nodes": {
            unique_id: {
                "resource_type": str(node.resource_type),
                "name": node.name,
                "schema": node.schema,
                "alias": node.alias,
                "relation_name": node.relation_name,
            }
            for unique_id, node in manifest.nodes.items()
        },
        "sources": {
            unique_id: {
                "source_name": source.source_name,
                "name": source.name,
                "schema": source.schema,
                "relation_name": source.relation_name,
            }
            for unique_id, source in manifest.sources.items()
        },
    }
    index = ref_index(LoadedManifest(raw=raw, relpath="", version=""))
    sources: dict[str, dict[str, dict[str, str]]] = {}
    for (source_name, table), (relation, schema) in index.sources.items():
        sources.setdefault(source_name, {})[table] = {
            "relation": relation,
            "schema": schema,
        }
    return {
        "minimani_version": MINIMANI_VERSION,
        "refs": {
            name: {"relation": relation, "schema": schema}
            for name, (relation, schema) in index.refs.items()
        },
        "sources": sources,
    }


def _warn(exc: BaseException) -> None:
    fire_event(
        Note(msg=f"dbt Charts could not write minimani.json: {exc}"),
        level=EventLevel.WARN,
    )


plugins = [ChartsPlugin]
