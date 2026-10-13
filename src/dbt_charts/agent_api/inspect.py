"""Inspect template-management verbs (OSS).

Profiler verbs (inspect_table, inspect_all, audit_tables, bake_schema_metadata)
live in the private ``dbt-charts-super-schema`` package and are exposed via the
CLI entry-point plugin. This module ships the template-management verbs that
belong in the OSS wheel: list, eject, validate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from dbt_charts import __version__
from dbt_charts.core.inspect import INSPECT_TEMPLATES
from dbt_charts.core.inspect.manifest_utils import (
    INSPECT_TEMPLATE_MANIFEST,
    MANIFEST_SCHEMA_VERSION,
    compare_templates,
    load_manifest,
    save_manifest,
)


class InspectTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str


class ValidateTemplatesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    missing: list[str] = []
    upstream_changed: list[str] = []
    custom_safe: list[str] = []
    unchanged: list[str] = []


def list_templates() -> list[InspectTemplate]:
    """List all built-in inspect templates."""
    return [InspectTemplate(name=name) for name in INSPECT_TEMPLATES]


def eject_templates(
    target_dir: Path,
    *,
    templates: list[str] | None = None,
    force: bool = False,
) -> list[Path]:
    """Write built-in inspect templates to target_dir.

    Returns paths of files actually written (skipped files are omitted).
    Raises ValueError for unknown template names.
    """
    from importlib.resources import files as _pkg_files

    to_eject = templates if templates is not None else list(INSPECT_TEMPLATES)

    invalid = [t for t in to_eject if t not in INSPECT_TEMPLATES]
    if invalid:
        raise ValueError(f"Unknown templates: {', '.join(invalid)}")

    target_dir.mkdir(parents=True, exist_ok=True)
    templates_pkg = _pkg_files("dbt_charts.core.inspect.templates")
    manifest = load_manifest(target_dir, dbt_charts_version=__version__)
    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    manifest["dbt_charts_version"] = __version__
    manifest["generated_at"] = datetime.now(UTC).isoformat()
    manifest_templates = manifest.setdefault("templates", {})

    ejected: list[Path] = []

    for name in to_eject:
        source_file = templates_pkg.joinpath(f"{name}.yml")
        target_file = target_dir / f"{name}.yml"

        if target_file.exists() and not force:
            continue

        content = source_file.read_text(encoding="utf-8")
        source_hash = sha256(content.encode("utf-8")).hexdigest()
        target_file.write_text(content, encoding="utf-8")

        manifest_templates[name] = {
            "filename": f"{name}.yml",
            "ejected_at": datetime.now(UTC).isoformat(),
            "source_version": __version__,
            "source_sha256": source_hash,
        }
        ejected.append(target_file)

    save_manifest(target_dir, manifest)
    return ejected


def validate_ejected_templates(target_dir: Path) -> ValidateTemplatesResult:
    """Compare ejected templates against built-in versions using the manifest.

    Returns a ValidateTemplatesResult. Call .success to check overall status.
    Raises FileNotFoundError if the manifest is missing.
    """
    from importlib_resources import files as _pkg_files

    manifest_path = target_dir / INSPECT_TEMPLATE_MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing manifest at {manifest_path}. Run eject_templates() first."
        )

    manifest = load_manifest(target_dir, dbt_charts_version=__version__)
    manifest_templates: dict[str, dict[str, Any]] = manifest.get("templates", {})
    templates_pkg = _pkg_files("dbt_charts.core.inspect.templates")

    missing, upstream_changed, custom_safe, unchanged = compare_templates(
        target_dir, manifest_templates, templates_pkg
    )

    return ValidateTemplatesResult(
        success=not missing and not upstream_changed,
        missing=missing,
        upstream_changed=upstream_changed,
        custom_safe=custom_safe,
        unchanged=unchanged,
    )
