"""Manifest I/O and template-comparison helpers for the inspect verb.

Pure functions — no agent-api or Pydantic coupling. Used by
``dbt_charts.agent_api.inspect`` to persist ejection state and compare
local templates against built-in versions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path  # noqa: TID251 — local ejection-tracking manifest file
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable

INSPECT_TEMPLATE_MANIFEST = ".inspect-template-manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def load_manifest(target_dir: Path, *, dbt_charts_version: str) -> dict[str, Any]:
    """Load the inspect-template manifest from target_dir, or return a fresh one."""
    manifest_path = target_dir / INSPECT_TEMPLATE_MANIFEST
    if not manifest_path.exists():
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "dbt_charts_version": dbt_charts_version,
            "generated_at": datetime.now(UTC).isoformat(),
            "templates": {},
        }
    with manifest_path.open(encoding="utf-8") as f:
        return json.load(f)


def save_manifest(target_dir: Path, manifest: dict[str, Any]) -> None:
    """Write the inspect-template manifest to target_dir."""
    manifest_path = target_dir / INSPECT_TEMPLATE_MANIFEST
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")


def compare_templates(
    resolved: Path,
    manifest_templates: dict[str, dict[str, Any]],
    templates_pkg: Traversable,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare ejected templates against built-in versions.

    Returns ``(missing, upstream_changed, custom_safe, unchanged)`` name lists.
    ``templates_pkg`` is an ``importlib.resources`` traversable (the package
    containing the canonical ``.yml`` template files).
    """
    missing: list[str] = []
    upstream_changed: list[str] = []
    custom_safe: list[str] = []
    unchanged: list[str] = []

    for name, meta in sorted(manifest_templates.items()):
        filename = meta.get("filename") or f"{name}.yml"
        local_path = resolved / filename
        if not local_path.exists():
            missing.append(filename)
            continue

        built_in = templates_pkg.joinpath(filename).read_text(encoding="utf-8")
        built_in_hash = sha256(built_in.encode("utf-8")).hexdigest()
        baseline_hash = meta.get("source_sha256")
        local_hash = sha256(
            local_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()

        if local_hash == built_in_hash:
            unchanged.append(name)
        elif baseline_hash != built_in_hash:
            upstream_changed.append(name)
        else:
            custom_safe.append(name)

    return missing, upstream_changed, custom_safe, unchanged
