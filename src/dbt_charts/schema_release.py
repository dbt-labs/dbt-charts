"""Release-time writer and verifier for immutable dbt charts YAML schemas."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaEntry,
    canonical_schema_bytes,
    load_yaml_schema_catalog_from,
    next_minor,
    parse_dotted_version,
    structural_schema_bytes,
)


def freeze_yaml_schema(
    directory: Path,
    versions_directory: Path,
    *,
    version: str,
    released_at: date,
    candidate: dict[str, Any],
) -> YamlSchemaEntry | None:
    """Release the DEV entry under *version*, or renumber it if the grammar is unchanged.

    Requires an existing manifest carrying a DEV entry -- there is no
    bootstrap path; a project with nothing frozen yet has no entry to
    release. Returns the newly ``RELEASED`` entry, or ``None`` when the
    candidate schema is structurally unchanged from the latest released one
    -- a pure ``description`` wording edit is not a schema for release:
    nothing is frozen, only the DEV entry (and its module, if one exists) is
    renumbered to the next predicted minor version when *version* has caught
    up with it.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No dbt charts YAML schema manifest at {directory}; freeze "
            "requires an existing manifest carrying a DEV entry to release."
        )
    version_key = _version_key(version)  # validate shape before any mutation
    catalog = load_yaml_schema_catalog_from(directory)
    if version_key <= _version_key(catalog.latest_released.version):
        raise ValueError(
            f"Cannot freeze dbt charts YAML schema {version!r}: it is not "
            "newer than the latest RELEASED version "
            f"{catalog.latest_released.version!r}."
        )
    released_schema = catalog.schema_for(catalog.latest_released.version)
    # Newest-first, DEV excluded -- catalog.entries' own order, reversed once
    # at write time rather than re-sorted here.
    released_entries = tuple(e for e in catalog.entries if e.status == "RELEASED")
    dev = catalog.dev

    if structural_schema_bytes(candidate) == structural_schema_bytes(released_schema):
        target_dev_version = next_minor(version)
        if dev.version != target_dev_version:
            _rename_dev_module(versions_directory, dev.version, target_dev_version)
            dev = dataclasses.replace(dev, version=target_dev_version)
        _write_manifest(manifest_path, released_entries, dev)
        return None

    snapshot_path = directory / f"{version}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"Frozen dbt charts YAML schema already exists: {snapshot_path}"
        )

    if dev.version != version:
        _rename_dev_module(versions_directory, dev.version, version)
        dev = dataclasses.replace(dev, version=version)

    candidate_bytes = canonical_schema_bytes(candidate)
    snapshot_path.write_bytes(candidate_bytes)

    released = dataclasses.replace(
        dev,
        status="RELEASED",
        released_at=released_at,
        filename=f"{dev.version}.json",
        sha256=hashlib.sha256(candidate_bytes).hexdigest(),
    )
    next_dev = YamlSchemaEntry(
        version=next_minor(released.version),
        status="DEV",
        released_at=None,
        filename=None,
        sha256=None,
        predecessor=released.version,
    )
    _write_manifest(manifest_path, (released, *released_entries), next_dev)
    return released


def _rename_dev_module(
    versions_directory: Path, old_version: str, new_version: str
) -> None:
    """Rename the DEV boundary module if one has been authored yet.

    ``Path.rename``, not ``git mv``: this module ships in the wheel and must
    not gain a subprocess-``git`` runtime dependency. The release engineer's
    commit picks the rename up like any other file change.

    Raises if the destination already exists rather than letting
    ``Path.rename`` silently replace it on POSIX.
    """
    old_path = versions_directory / f"v{old_version.replace('.', '_')}.py"
    if not old_path.exists():
        return
    new_path = versions_directory / f"v{new_version.replace('.', '_')}.py"
    if new_path.exists():
        raise FileExistsError(
            f"Cannot rename DEV boundary module {old_path} to {new_path}: "
            "destination already exists."
        )
    old_path.rename(new_path)


def _write_manifest(
    manifest_path: Path,
    released_entries_newest_first: Sequence[YamlSchemaEntry],
    dev_entry: YamlSchemaEntry,
) -> None:
    oldest_first = tuple(reversed(released_entries_newest_first))
    payload = {"schemas": [entry.as_json() for entry in (*oldest_first, dev_entry)]}
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def verify_released_yaml_schema(
    directory: Path,
    *,
    version: str,
    candidate: dict[str, Any],
) -> None:
    """Ensure the release tag packages the current YAML grammar snapshot.

    A three-part check accepting both release shapes: the live schema must
    match the latest released snapshot structurally (a pure ``description``
    wording edit does not count as a mismatch), that snapshot's version must
    not be newer than the tag (an unchanged-grammar release may sit behind
    it), and the DEV entry must be strictly newer than the tag -- a tag that
    has caught up with the DEV name means that grammar was never frozen.
    """
    catalog = load_yaml_schema_catalog_from(directory)
    if _version_key(catalog.latest_released.version) > _version_key(version):
        raise ValueError(
            f"Latest dbt charts YAML schema {catalog.latest_released.version} is "
            f"newer than the dbt charts release {version}."
        )
    if structural_schema_bytes(
        catalog.schema_for(catalog.latest_released.version)
    ) != structural_schema_bytes(candidate):
        raise ValueError(
            f"dbt charts {version} has an unfrozen YAML grammar. Run "
            f"`just freeze-yaml-schema {version} <released_at>` and commit "
            "the result."
        )
    if _version_key(catalog.dev.version) <= _version_key(version):
        raise ValueError(
            f"dbt charts YAML schema DEV entry {catalog.dev.version} is not newer "
            f"than the dbt charts release {version}; run "
            f"`just freeze-yaml-schema {version} <released_at>` first."
        )


def _version_key(version: str) -> tuple[int, int, int]:
    parsed = parse_dotted_version(version)
    if parsed is None:
        raise ValueError(
            f"dbt charts release version must be MAJOR.MINOR.PATCH, got {version!r}."
        )
    return parsed


def main() -> None:
    """Freeze or verify the current authored grammar for a dbt charts release."""
    parser = argparse.ArgumentParser(
        description="Append or verify a changed dbt charts YAML schema."
    )
    parser.add_argument("version")
    parser.add_argument("released_at", type=date.fromisoformat, nargs="?")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(__file__).parent / "data" / "schemas" / "yaml",
    )
    args = parser.parse_args()
    directory: Path = args.directory
    versions_directory = (
        Path(__file__).parent / "core" / "compile" / "migrations" / "versions"
    )
    candidate = render_yaml_schema(introspect())
    if args.verify:
        verify_released_yaml_schema(
            directory, version=args.version, candidate=candidate
        )
        return
    if args.released_at is None:
        parser.error("released_at is required unless --verify is used")
    frozen = freeze_yaml_schema(
        directory,
        versions_directory,
        version=args.version,
        released_at=args.released_at,
        candidate=candidate,
    )
    if frozen is None:
        catalog = load_yaml_schema_catalog_from(directory)
        print(f"no grammar change; dev={catalog.dev.version}")
    else:
        print(frozen.version)


if __name__ == "__main__":
    main()
