"""dbt charts YAML schemas are strict, frozen package contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import (
    render_yaml_schema,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    canonical_schema_bytes,
    load_yaml_schema_catalog,
    load_yaml_schema_catalog_from,
    next_minor,
    structural_schema_bytes,
)
from dbt_charts.schema_release import freeze_yaml_schema, verify_released_yaml_schema


def _write_schema_file(directory: Path, filename: str, schema: dict) -> str:
    """Write *schema* as canonical JSON and return its sha256 hex digest."""
    contents = canonical_schema_bytes(schema)
    (directory / filename).write_bytes(contents)
    return hashlib.sha256(contents).hexdigest()


def _released_entry(
    version: str,
    *,
    sha256: str,
    predecessor: str | None,
    released_at: str = "2026-07-30",
) -> dict:
    return {
        "version": version,
        "status": "RELEASED",
        "released_at": released_at,
        "file": f"{version}.json",
        "sha256": sha256,
        "predecessor": predecessor,
    }


def _dev_entry(version: str, *, predecessor: str | None) -> dict:
    return {
        "version": version,
        "status": "DEV",
        "released_at": None,
        "file": None,
        "sha256": None,
        "predecessor": predecessor,
    }


def _write_manifest(directory: Path, entries: list[dict]) -> None:
    (directory / "manifest.json").write_text(
        json.dumps({"schemas": entries}), encoding="utf-8"
    )


def test_yaml_schema_closes_authored_objects() -> None:
    schema = render_yaml_schema(introspect())

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["BarChart"]["additionalProperties"] is False
    assert any(
        branch.get("additionalProperties")
        for branch in schema["properties"]["charts"]["anyOf"]
    )


def test_yaml_schema_matches_the_authored_board_boundary() -> None:
    schema = render_yaml_schema(introspect())
    valid = {
        "charts": {
            "revenue": {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "query": {"sql": "select month, revenue from revenue"},
            }
        }
    }

    jsonschema.Draft7Validator(schema).validate(valid)
    AuthoredBoard.model_validate(valid)

    invalid = {**valid, "not_a_dbt_charts_key": True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(invalid)
    with pytest.raises(ValidationError):
        AuthoredBoard.model_validate(invalid)


def test_yaml_schema_accepts_layout_name_shorthand() -> None:
    board = {
        "charts": {
            "revenue": {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "query": {"sql": "select month, revenue from revenue"},
            }
        },
        "rows": ["revenue"],
    }

    AuthoredBoard.model_validate(board)
    jsonschema.Draft7Validator(render_yaml_schema(introspect())).validate(board)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.6.1", "0.7.0"), ("0.9.0", "0.10.0"), ("1.0.0", "1.1.0")],
)
def test_next_minor(version: str, expected: str) -> None:
    assert next_minor(version) == expected


def test_packaged_catalog_loads_newest_schema_first() -> None:
    catalog = load_yaml_schema_catalog()

    assert catalog.entries
    assert catalog.entries[0].version == catalog.dev.version
    assert catalog.schema_for(catalog.dev.version) is catalog.current_schema
    assert (
        catalog.schema_for(catalog.latest_released.version)["additionalProperties"]
        is False
    )


def test_schema_for_dev_version_is_current_schema(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    catalog = load_yaml_schema_catalog_from(tmp_path)

    assert catalog.schema_for(catalog.dev.version) is catalog.current_schema


def test_manifest_rejects_zero_dev_entries(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(tmp_path, [_released_entry("0.3.0", sha256=sha, predecessor=None)])

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_manifest_rejects_two_dev_entries(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
            _dev_entry("0.5.0", predecessor="0.4.0"),
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_manifest_rejects_a_dev_entry_that_is_not_newest(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.4.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _dev_entry("0.3.0", predecessor=None),
            _released_entry("0.4.0", sha256=sha, predecessor="0.3.0"),
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_manifest_rejects_a_dev_entry_numerically_older_than_its_predecessor(
    tmp_path: Path,
) -> None:
    """A DEV entry positioned last (so it clears the position check) but
    numerically older than the RELEASED entry it follows must still be
    rejected -- "DEV is newest" means version-newest, not just list-last."""
    sha = _write_schema_file(tmp_path, "0.6.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.5.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_manifest_rejects_a_released_entry_older_than_its_predecessor(
    tmp_path: Path,
) -> None:
    """The strict-increase walk checks every consecutive pair, not just the
    entries flanking DEV: a RELEASED entry that is numerically older than
    the RELEASED entry immediately before it must be caught even though a
    later DEV entry is newer than both."""
    sha_060 = _write_schema_file(tmp_path, "0.6.0.json", {"type": "object"})
    sha_055 = _write_schema_file(tmp_path, "0.5.5.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.6.0", sha256=sha_060, predecessor=None),
            _released_entry("0.5.5", sha256=sha_055, predecessor="0.6.0"),
            _dev_entry("0.7.0", predecessor="0.5.5"),
        ],
    )

    with pytest.raises(
        ValueError, match="entry 0.5.5 must be numerically newer than 0.6.0"
    ):
        load_yaml_schema_catalog_from(tmp_path)


def test_manifest_rejects_a_version_that_is_not_major_minor_patch(
    tmp_path: Path,
) -> None:
    """An unparseable version is a hard error, not a skipped comparison.

    The strict-increase walk can only compare versions it parsed, so a
    version it cannot parse must stop the load rather than bypass the
    ordering check for that entry -- the silent-skip shape this loader
    deliberately does not have."""
    sha_060 = _write_schema_file(tmp_path, "0.6.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.6.0", sha256=sha_060, predecessor=None),
            _dev_entry("0.7", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        load_yaml_schema_catalog_from(tmp_path)


def test_released_entry_missing_sha256_is_rejected(tmp_path: Path) -> None:
    _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            {
                "version": "0.3.0",
                "status": "RELEASED",
                "released_at": "2026-07-30",
                "file": "0.3.0.json",
                "sha256": None,
                "predecessor": None,
            },
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    with pytest.raises(ValueError, match="RELEASED"):
        load_yaml_schema_catalog_from(tmp_path)


def test_released_entry_missing_file_is_rejected(tmp_path: Path) -> None:
    _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            {
                "version": "0.3.0",
                "status": "RELEASED",
                "released_at": "2026-07-30",
                "file": None,
                "sha256": "irrelevant",
                "predecessor": None,
            },
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    with pytest.raises(ValueError, match="RELEASED"):
        load_yaml_schema_catalog_from(tmp_path)


def test_released_entry_missing_released_at_is_rejected(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            {
                "version": "0.3.0",
                "status": "RELEASED",
                "released_at": None,
                "file": "0.3.0.json",
                "sha256": sha,
                "predecessor": None,
            },
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    with pytest.raises(ValueError, match="RELEASED"):
        load_yaml_schema_catalog_from(tmp_path)


def test_dev_entry_carrying_sha256_is_rejected(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            {
                "version": "0.4.0",
                "status": "DEV",
                "released_at": None,
                "file": None,
                "sha256": "should-not-be-set",
                "predecessor": "0.3.0",
            },
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_dev_entry_carrying_file_is_rejected(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            {
                "version": "0.4.0",
                "status": "DEV",
                "released_at": None,
                "file": "0.4.0.json",
                "sha256": None,
                "predecessor": "0.3.0",
            },
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_dev_entry_carrying_released_at_is_rejected(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            {
                "version": "0.4.0",
                "status": "DEV",
                "released_at": "2026-08-01",
                "file": None,
                "sha256": None,
                "predecessor": "0.3.0",
            },
        ],
    )

    with pytest.raises(ValueError, match="DEV"):
        load_yaml_schema_catalog_from(tmp_path)


def test_entry_with_no_status_is_rejected(tmp_path: Path) -> None:
    sha = _write_schema_file(tmp_path, "0.3.0.json", {"type": "object"})
    _write_manifest(
        tmp_path,
        [
            {
                "version": "0.3.0",
                "released_at": "2026-07-30",
                "file": "0.3.0.json",
                "sha256": sha,
                "predecessor": None,
            },
        ],
    )

    with pytest.raises(ValueError, match="status"):
        load_yaml_schema_catalog_from(tmp_path)


def test_catalog_rejects_a_modified_frozen_snapshot(tmp_path: Path) -> None:
    schema_path = tmp_path / "0.3.0.json"
    schema_path.write_text('{"type": "object"}\n', encoding="utf-8")
    _write_manifest(
        tmp_path,
        [
            {
                "version": "0.3.0",
                "status": "RELEASED",
                "released_at": "2026-07-30",
                "file": "0.3.0.json",
                "sha256": "not-the-schema-hash",
                "predecessor": None,
            },
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    with pytest.raises(ValueError, match="sha256"):
        load_yaml_schema_catalog_from(tmp_path)


def test_structural_schema_bytes_ignores_description_wording() -> None:
    """A ``description`` edit at every injection point (root, a ``$defs``
    entry's own doc, a ``$defs`` entry's *field* doc -- where most real
    descriptions live, a root-level field, ``additionalProperties``) must not
    affect structural equality -- only the shape a document is validated
    against matters."""
    old = {
        "type": "object",
        "description": "old root doc",
        "properties": {
            "x": {"type": "string", "description": "old field doc"},
        },
        "additionalProperties": {"type": "string", "description": "old extra doc"},
        "$defs": {
            "Thing": {
                "type": "object",
                "description": "old def doc",
                "properties": {
                    "y": {"type": "integer", "description": "old nested field doc"}
                },
            }
        },
    }
    new = {
        "type": "object",
        "description": "new root doc",
        "properties": {
            "x": {"type": "string", "description": "new field doc"},
        },
        "additionalProperties": {"type": "string", "description": "new extra doc"},
        "$defs": {
            "Thing": {
                "type": "object",
                "description": "new def doc",
                "properties": {
                    "y": {"type": "integer", "description": "new nested field doc"}
                },
            }
        },
    }

    assert structural_schema_bytes(old) == structural_schema_bytes(new)
    assert canonical_schema_bytes(old) != canonical_schema_bytes(new)


def _perturb_descriptions(value: object) -> object:
    """Append text to every ``description`` string anywhere in *value*.

    Deliberately blind recursion (unlike production's position-aware
    ``_strip_node_description``): it targets every dict entry keyed
    ``"description"`` whose value is a string, wherever nested, so this
    fixture can't miss a real injection point the way a hand-built schema
    could.
    """
    if isinstance(value, dict):
        return {
            key: (
                f"{child} (perturbed)"
                if key == "description" and isinstance(child, str)
                else _perturb_descriptions(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_perturb_descriptions(item) for item in value]
    return value


def test_structural_schema_bytes_ignores_description_anywhere_in_the_real_schema() -> (
    None
):
    """Regression guard against a hand-built fixture missing where real
    descriptions live: perturb every ``description`` string in the live
    generated dbt charts YAML schema and confirm structural equality survives
    while canonical (byte-for-byte) equality does not."""
    schema = render_yaml_schema(introspect())
    perturbed = _perturb_descriptions(schema)
    assert isinstance(perturbed, dict)

    assert structural_schema_bytes(schema) == structural_schema_bytes(perturbed)
    assert canonical_schema_bytes(schema) != canonical_schema_bytes(perturbed)


def test_structural_schema_bytes_preserves_a_field_literally_named_description() -> (
    None
):
    """A field named ``description`` is a property key, not the JSON Schema
    documentation keyword -- stripping must not mistake one for the other."""
    old = {"type": "object", "properties": {"description": {"type": "string"}}}
    new = {"type": "object", "properties": {"description": {"type": "integer"}}}

    assert structural_schema_bytes(old) != structural_schema_bytes(new)


def test_structural_schema_bytes_detects_real_grammar_changes() -> None:
    old = {"type": "object", "properties": {"x": {"type": "string"}}}
    new = {"type": "object", "properties": {"x": {"type": "integer"}}}

    assert structural_schema_bytes(old) != structural_schema_bytes(new)


def test_freeze_ignores_description_only_change(tmp_path: Path) -> None:
    """A candidate differing from the latest RELEASED snapshot only in
    ``description`` prose is not a schema for release: no snapshot is
    written, only DEV is renumbered."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    old_candidate = {
        "type": "object",
        "additionalProperties": False,
        "description": "old doc",
    }
    sha = _write_schema_file(directory, "0.3.0.json", old_candidate)
    (versions_directory / "v0_4_0.py").write_text("", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    new_candidate = {**old_candidate, "description": "new, clearer doc"}
    result = freeze_yaml_schema(
        directory,
        versions_directory,
        version="0.4.0",
        released_at=date(2026, 8, 1),
        candidate=new_candidate,
    )

    assert result is None
    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.dev.version == "0.5.0"
    assert catalog.latest_released.version == "0.3.0"
    assert not (directory / "0.4.0.json").exists()


def test_verify_accepts_description_only_grammar_change(tmp_path: Path) -> None:
    directory = tmp_path / "schemas"
    directory.mkdir()

    candidate = {
        "type": "object",
        "additionalProperties": False,
        "description": "old doc",
    }
    sha = _write_schema_file(directory, "0.3.0.json", candidate)
    _write_manifest(
        directory,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    verify_released_yaml_schema(
        directory,
        version="0.3.0",
        candidate={**candidate, "description": "new, clearer doc"},
    )


def test_freeze_unchanged_grammar_renumbers_dev_without_writing(
    tmp_path: Path,
) -> None:
    """An unchanged candidate returns None, writes no entry, and only renumbers DEV."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.3.0.json", candidate)
    (versions_directory / "v0_4_0.py").write_text("", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    result = freeze_yaml_schema(
        directory,
        versions_directory,
        version="0.4.0",
        released_at=date(2026, 8, 1),
        candidate=candidate,
    )

    assert result is None
    catalog = load_yaml_schema_catalog_from(directory)
    assert [e.version for e in catalog.entries] == ["0.5.0", "0.3.0"]
    assert catalog.dev.version == "0.5.0"
    assert catalog.latest_released.version == "0.3.0"
    assert not (versions_directory / "v0_4_0.py").exists()
    assert (versions_directory / "v0_5_0.py").exists()


def test_freeze_changed_grammar_releases_dev_and_appends_next(tmp_path: Path) -> None:
    """A changed candidate flips DEV to RELEASED and appends the next DEV."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    old_candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.3.0.json", old_candidate)
    _write_manifest(
        directory,
        [
            _released_entry("0.3.0", sha256=sha, predecessor=None),
            _dev_entry("0.4.0", predecessor="0.3.0"),
        ],
    )

    new_candidate = {**old_candidate, "title": "Board"}
    result = freeze_yaml_schema(
        directory,
        versions_directory,
        version="0.4.0",
        released_at=date(2026, 8, 1),
        candidate=new_candidate,
    )

    assert result is not None
    assert result.version == "0.4.0"
    assert result.status == "RELEASED"
    assert result.released_at == date(2026, 8, 1)
    assert (directory / "0.4.0.json").read_bytes() == canonical_schema_bytes(
        new_candidate
    )

    catalog = load_yaml_schema_catalog_from(directory)
    assert [e.version for e in catalog.entries] == ["0.5.0", "0.4.0", "0.3.0"]
    assert catalog.dev.version == "0.5.0"
    assert catalog.latest_released.version == "0.4.0"


def test_freeze_renumbers_dev_upward_on_a_major_bump(tmp_path: Path) -> None:
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    old_candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", old_candidate)
    (versions_directory / "v0_7_0.py").write_text("", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    new_candidate = {**old_candidate, "title": "Board"}
    result = freeze_yaml_schema(
        directory,
        versions_directory,
        version="1.0.0",
        released_at=date(2026, 8, 1),
        candidate=new_candidate,
    )

    assert result is not None
    assert result.version == "1.0.0"
    assert not (versions_directory / "v0_7_0.py").exists()
    assert (versions_directory / "v1_0_0.py").exists()

    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.latest_released.version == "1.0.0"
    assert catalog.dev.version == "1.1.0"


def test_freeze_renumbers_dev_downward_on_a_patch_with_grammar_change(
    tmp_path: Path,
) -> None:
    """A patch release that ships a grammar change renumbers DEV downward."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    old_candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", old_candidate)
    (versions_directory / "v0_7_0.py").write_text("", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    new_candidate = {**old_candidate, "title": "Board"}
    result = freeze_yaml_schema(
        directory,
        versions_directory,
        version="0.6.1",
        released_at=date(2026, 8, 1),
        candidate=new_candidate,
    )

    assert result is not None
    assert result.version == "0.6.1"
    assert result.predecessor == "0.6.0"
    assert not (versions_directory / "v0_7_0.py").exists()
    assert (versions_directory / "v0_6_1.py").exists()

    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.latest_released.version == "0.6.1"
    assert catalog.dev.version == "0.7.0"


def test_freeze_raises_without_an_existing_manifest(tmp_path: Path) -> None:
    """The no-manifest bootstrap path is gone: freeze now requires a DEV entry to release."""
    directory = tmp_path / "schemas"
    versions_directory = tmp_path / "versions"

    with pytest.raises(FileNotFoundError, match=str(directory)):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.3.0",
            released_at=date(2026, 7, 30),
            candidate={"type": "object", "additionalProperties": False},
        )


def test_freeze_rejects_a_release_version_already_used_by_a_released_entry(
    tmp_path: Path,
) -> None:
    """Freezing DEV under a version an earlier release already claimed must
    not rename the DEV module onto the frozen one -- it must raise before
    touching the tree."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    old_candidate = {"type": "object", "additionalProperties": False}
    older_sha = _write_schema_file(directory, "0.5.0.json", old_candidate)
    newer_candidate = {**old_candidate, "title": "0.6.0"}
    newer_sha = _write_schema_file(directory, "0.6.0.json", newer_candidate)
    (versions_directory / "v0_6_0.py").write_text("frozen 0.6.0\n", encoding="utf-8")
    (versions_directory / "v0_7_0.py").write_text("dev 0.7.0\n", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.5.0", sha256=older_sha, predecessor=None),
            _released_entry("0.6.0", sha256=newer_sha, predecessor="0.5.0"),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    candidate = {**newer_candidate, "title": "0.7.0 draft"}
    with pytest.raises(ValueError, match="0.6.0"):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.6.0",
            released_at=date(2026, 8, 1),
            candidate=candidate,
        )

    assert (versions_directory / "v0_6_0.py").read_text(encoding="utf-8") == (
        "frozen 0.6.0\n"
    )
    assert (versions_directory / "v0_7_0.py").exists()
    assert load_yaml_schema_catalog_from(directory).dev.version == "0.7.0"


def test_freeze_rejects_a_release_version_not_newer_than_the_latest_released_entry(
    tmp_path: Path,
) -> None:
    """The monotonicity precondition (``version > latest_released.version``)
    must fire before the unchanged-grammar renumber path ever compares
    candidate bytes -- a release version behind the latest RELEASED entry is
    rejected outright, even when its grammar matches DEV's."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    older_sha = _write_schema_file(directory, "0.5.0.json", candidate)
    newer_sha = _write_schema_file(directory, "0.6.0.json", candidate)
    (versions_directory / "v0_6_0.py").write_text("frozen 0.6.0\n", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.5.0", sha256=older_sha, predecessor=None),
            _released_entry("0.6.0", sha256=newer_sha, predecessor="0.5.0"),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(
        ValueError, match="not newer than the latest RELEASED version '0.6.0'"
    ):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.5.2",
            released_at=date(2026, 8, 1),
            candidate=candidate,
        )

    assert (versions_directory / "v0_6_0.py").read_text(encoding="utf-8") == (
        "frozen 0.6.0\n"
    )
    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.dev.version == "0.7.0"
    assert [e.version for e in catalog.entries] == ["0.7.0", "0.6.0", "0.5.0"]


def test_freeze_rejects_a_changed_release_version_behind_a_later_released_entry(
    tmp_path: Path,
) -> None:
    """A changed-grammar release whose tag sits behind a later RELEASED entry
    must raise rather than append a DEV entry (``next_minor(released.version)``)
    that lands on that already-RELEASED version and writes a manifest with
    duplicate versions."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    older_sha = _write_schema_file(directory, "0.5.0.json", candidate)
    newer_sha = _write_schema_file(directory, "0.6.0.json", candidate)
    (versions_directory / "v0_6_0.py").write_text("frozen 0.6.0\n", encoding="utf-8")
    (versions_directory / "v0_7_0.py").write_text("dev 0.7.0\n", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.5.0", sha256=older_sha, predecessor=None),
            _released_entry("0.6.0", sha256=newer_sha, predecessor="0.5.0"),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(ValueError, match="0.6.0"):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.5.5",
            released_at=date(2026, 8, 1),
            candidate={**candidate, "title": "changed"},
        )

    assert (versions_directory / "v0_6_0.py").read_text(encoding="utf-8") == (
        "frozen 0.6.0\n"
    )
    assert (versions_directory / "v0_7_0.py").exists()
    assert not (directory / "0.5.5.json").exists()
    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.dev.version == "0.7.0"
    assert [e.version for e in catalog.entries] == ["0.7.0", "0.6.0", "0.5.0"]


def test_freeze_rejects_an_existing_snapshot_before_renaming_the_dev_module(
    tmp_path: Path,
) -> None:
    """An orphaned snapshot at the target version must raise before the DEV
    module is renamed onto it -- renaming first and raising second leaves the
    tree half-mutated: an unreferenced module renamed with no snapshot
    written to match it."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", candidate)
    (versions_directory / "v0_7_0.py").write_text("dev 0.7.0\n", encoding="utf-8")
    (directory / "0.8.0.json").write_bytes(b"orphaned snapshot")
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(FileExistsError, match="0.8.0.json"):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.8.0",
            released_at=date(2026, 8, 1),
            candidate={**candidate, "title": "changed"},
        )

    assert (versions_directory / "v0_7_0.py").read_text(encoding="utf-8") == (
        "dev 0.7.0\n"
    )
    assert not (versions_directory / "v0_8_0.py").exists()
    assert (directory / "0.8.0.json").read_bytes() == b"orphaned snapshot"


def test_freeze_rejects_a_malformed_version_before_mutating(tmp_path: Path) -> None:
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", candidate)
    (versions_directory / "v0_7_0.py").write_text("dev 0.7.0\n", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(ValueError, match="0.7"):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.7",
            released_at=date(2026, 8, 1),
            candidate={**candidate, "title": "changed"},
        )

    assert (versions_directory / "v0_7_0.py").exists()
    assert not (versions_directory / "v0_7.py").exists()
    assert not (directory / "0.7.json").exists()
    catalog = load_yaml_schema_catalog_from(directory)
    assert catalog.dev.version == "0.7.0"


def test_rename_dev_module_raises_instead_of_clobbering_an_existing_destination(
    tmp_path: Path,
) -> None:
    """A stray module already sitting at the rename target must not be
    silently overwritten -- ``Path.rename`` replaces an existing destination
    on POSIX with no diagnostic."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", candidate)
    (versions_directory / "v0_7_0.py").write_text("dev 0.7.0\n", encoding="utf-8")
    (versions_directory / "v0_8_0.py").write_text("stray 0.8.0\n", encoding="utf-8")
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(FileExistsError, match="v0_8_0.py"):
        freeze_yaml_schema(
            directory,
            versions_directory,
            version="0.8.0",
            released_at=date(2026, 8, 1),
            candidate={**candidate, "title": "changed"},
        )

    assert (versions_directory / "v0_8_0.py").read_text(encoding="utf-8") == (
        "stray 0.8.0\n"
    )
    assert (versions_directory / "v0_7_0.py").exists()


def test_release_verification_requires_a_current_yaml_schema(tmp_path: Path) -> None:
    directory = tmp_path / "schemas"
    directory.mkdir()
    versions_directory = tmp_path / "versions"
    versions_directory.mkdir()

    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.2.0.json", candidate)
    _write_manifest(
        directory,
        [
            _released_entry("0.2.0", sha256=sha, predecessor=None),
            _dev_entry("0.3.0", predecessor="0.2.0"),
        ],
    )
    freeze_yaml_schema(
        directory,
        versions_directory,
        version="0.3.0",
        released_at=date(2026, 7, 30),
        candidate=candidate,
    )

    verify_released_yaml_schema(directory, version="0.3.0", candidate=candidate)

    with pytest.raises(ValueError, match="unfrozen YAML grammar"):
        verify_released_yaml_schema(
            directory,
            version="0.4.0",
            candidate={
                "type": "object",
                "additionalProperties": False,
                "title": "Board",
            },
        )


def test_verify_accepts_unchanged_grammar_below_the_dev_name(tmp_path: Path) -> None:
    """A patch release below the DEV name verifies clean when the grammar is unchanged."""
    directory = tmp_path / "schemas"
    directory.mkdir()
    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", candidate)
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    verify_released_yaml_schema(directory, version="0.6.1", candidate=candidate)


def test_verify_rejects_dev_version_at_or_below_the_tag(tmp_path: Path) -> None:
    directory = tmp_path / "schemas"
    directory.mkdir()
    candidate = {"type": "object", "additionalProperties": False}
    sha = _write_schema_file(directory, "0.6.0.json", candidate)
    _write_manifest(
        directory,
        [
            _released_entry("0.6.0", sha256=sha, predecessor=None),
            _dev_entry("0.7.0", predecessor="0.6.0"),
        ],
    )

    with pytest.raises(ValueError, match="0.7.0"):
        verify_released_yaml_schema(directory, version="0.7.0", candidate=candidate)
