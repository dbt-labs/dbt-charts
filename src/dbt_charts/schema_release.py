"""Release-time writer and verifier for immutable dbt charts YAML schemas."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaEntry,
    canonical_schema_bytes,
    compute_current_schema,
    load_yaml_schema_catalog_from,
    next_minor,
    parse_dotted_version,
    structural_schema_bytes,
    warnings_file_name,
)
from dbt_charts.core.diagnostics import REGISTRY


def freeze_yaml_schema(
    directory: Path,
    crate_src: Path,
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
    nothing is frozen, only the DEV entry (and its module in the migrate
    crate's *crate_src*, if one exists) is renumbered to the next predicted
    minor version when *version* has caught up with it.
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
            _rename_dev_module(crate_src, dev.version, target_dev_version)
            dev = dataclasses.replace(dev, version=target_dev_version)
        _write_manifest(manifest_path, released_entries, dev)
        return None

    snapshot_path = directory / f"{version}.json"
    if snapshot_path.exists():
        raise FileExistsError(
            f"Frozen dbt charts YAML schema already exists: {snapshot_path}"
        )

    if dev.version != version:
        _rename_dev_module(crate_src, dev.version, version)
        dev = dataclasses.replace(dev, version=version)

    candidate_bytes = canonical_schema_bytes(candidate)
    snapshot_path.write_bytes(candidate_bytes)
    (directory / warnings_file_name(version)).write_text(
        json.dumps(sorted(REGISTRY.codes(level="warning")), indent=2) + "\n",
        encoding="utf-8",
    )

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


_MODULE_LINE = re.compile(r"mod v\d+_\d+_\d+;")


def _module_name(version: str) -> str:
    return f"v{version.replace('.', '_')}"


def _sorted_module_lines(lib: str) -> str:
    """``lib`` with its version ``mod`` lines in rustfmt's (ASCII) order."""
    lines = lib.split("\n")
    at = [i for i, line in enumerate(lines) if _MODULE_LINE.fullmatch(line)]
    for i, line in zip(at, sorted(lines[i] for i in at), strict=True):
        lines[i] = line
    return "\n".join(lines)


def _rename_dev_module(crate_src: Path, old_version: str, new_version: str) -> None:
    """Rename the migrate crate's DEV boundary module, if one is registered.

    Moves ``v<old>.rs`` to ``v<new>.rs`` and rewrites its ``mod`` line and
    ``MODULES`` entry in ``lib.rs`` and the ``-> <old> boundary`` of its
    module doc. ``Path`` writes, not ``git mv``: this module ships in the
    wheel and must not gain a subprocess-``git`` runtime dependency. The
    release engineer's commit picks the rename up like any other file change.

    Raises if the destination already exists, or if ``lib.rs`` does not
    register the module exactly once, rather than leave a half-renamed crate
    whose module ``Registry::build`` would silently skip.
    """
    old_name, new_name = _module_name(old_version), _module_name(new_version)
    old_path = crate_src / f"{old_name}.rs"
    if not old_path.exists():
        return
    new_path = crate_src / f"{new_name}.rs"
    if new_path.exists():
        raise FileExistsError(
            f"Cannot rename DEV boundary module {old_path} to {new_path}: "
            "destination already exists."
        )
    lib_path = crate_src / "lib.rs"
    lib = lib_path.read_text(encoding="utf-8")
    registrations = (
        (f"mod {old_name};", f"mod {new_name};"),
        (
            f'("{old_version}", {old_name}::declarations)',
            f'("{new_version}", {new_name}::declarations)',
        ),
    )
    for old, new in registrations:
        if lib.count(old) != 1:
            raise ValueError(
                f"{lib_path} must register the DEV boundary module with exactly "
                f"one `{old}`."
            )
        lib = lib.replace(old, new)
    module = old_path.read_text(encoding="utf-8")
    new_path.write_text(
        module.replace(f"-> {old_version} boundary", f"-> {new_version} boundary"),
        encoding="utf-8",
    )
    old_path.unlink()
    lib_path.write_text(_sorted_module_lines(lib), encoding="utf-8")


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

    A check accepting both release shapes: every released schema has its
    frozen warning list, the live schema must
    match the latest released snapshot structurally (a pure ``description``
    wording edit does not count as a mismatch), that snapshot's version must
    not be newer than the tag (an unchanged-grammar release may sit behind
    it), and the DEV entry must be strictly newer than the tag -- a tag that
    has caught up with the DEV name means that grammar was never frozen.
    """
    catalog = load_yaml_schema_catalog_from(directory)
    unlisted = [
        entry.version
        for entry in catalog.entries
        if entry.status == "RELEASED"
        and not (directory / warnings_file_name(entry.version)).is_file()
    ]
    if unlisted:
        raise ValueError(
            "dbt charts YAML schema releases without a frozen warning list: "
            f"{', '.join(unlisted)}."
        )
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


def _migrate_crate_src() -> Path:
    """The migrate crate's sources: in the monorepo, or at an export's root."""
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[2]):
        crate_src = root / "libs" / "chart-svg" / "migrate" / "src"
        if (crate_src / "lib.rs").is_file():
            return crate_src
    raise FileNotFoundError(
        f"The migrate crate (libs/chart-svg/migrate/src) was not found above {here}."
    )


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
    candidate = compute_current_schema()
    if args.verify:
        verify_released_yaml_schema(
            directory, version=args.version, candidate=candidate
        )
        return
    if args.released_at is None:
        parser.error("released_at is required unless --verify is used")
    frozen = freeze_yaml_schema(
        directory,
        _migrate_crate_src(),
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
