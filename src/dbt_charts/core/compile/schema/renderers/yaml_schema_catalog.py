"""Load immutable dbt charts YAML schemas packaged with dbt charts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from types import NoneType
from typing import Literal, Protocol, TypeAlias

from importlib_resources import files

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | Literal[None]
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


class _SchemaResource(Protocol):
    def joinpath(self, *_: str) -> _SchemaResource: ...

    def read_bytes(self) -> bytes: ...

    def read_text(self, encoding: str) -> str: ...


@dataclass(frozen=True)
class YamlSchemaEntry:
    """One board-YAML grammar snapshot: a frozen release, or the live DEV entry.

    ``status`` has no default -- see ``load_yaml_schema_catalog_from`` for why
    every entry must name it explicitly. A ``RELEASED`` entry always has
    ``released_at``/``filename``/``sha256`` set; the single ``DEV`` entry
    always has them null, since it names a version that has not shipped yet
    and whose schema is not written to disk (``schema_for`` resolves it to
    the live Pydantic-generated schema instead).
    """

    version: str
    status: Literal["RELEASED", "DEV"]
    released_at: date | None
    filename: str | None
    sha256: str | None
    predecessor: str | Literal[None]

    @classmethod
    def from_json(cls, value: JsonObject) -> YamlSchemaEntry:
        predecessor = value["predecessor"]
        if not isinstance(predecessor, (str, NoneType)):
            raise ValueError(
                "dbt charts YAML schema manifest predecessor must be a string or null."
            )
        return cls(
            version=_manifest_string(value, "version"),
            status=_manifest_status(value),
            released_at=_manifest_optional_date(value, "released_at"),
            filename=_manifest_optional_string(value, "file"),
            sha256=_manifest_optional_string(value, "sha256"),
            predecessor=predecessor,
        )

    def as_json(self) -> JsonObject:
        return {
            "version": self.version,
            "status": self.status,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "file": self.filename,
            "sha256": self.sha256,
            "predecessor": self.predecessor,
        }


@dataclass(frozen=True)
class YamlSchemaCatalog:
    """Frozen dbt charts YAML schemas indexed by version, newest to oldest.

    ``current_schema`` is the live JSON schema generated from the Pydantic models right
    now — it is never written to disk, never sha256-checked, and may differ from the
    latest frozen snapshot when unreleased model changes are in flight.  Use it wherever
    "what does the current grammar accept?" is the question (recognition, migration
    target check), not "what does a specific release accept?"

    The newest entry (``entries[0]``) is always the single ``DEV`` entry --
    named for the predicted next minor version, with ``schema_for`` resolving
    it to ``current_schema``. Every other entry is ``RELEASED``.
    """

    entries: tuple[YamlSchemaEntry, ...]
    _schemas: dict[str, JsonObject]
    current_schema: JsonObject

    @property
    def dev(self) -> YamlSchemaEntry:
        for entry in self.entries:
            if entry.status == "DEV":
                return entry
        raise ValueError("dbt charts YAML schema catalog has no DEV entry.")

    @property
    def latest_released(self) -> YamlSchemaEntry:
        for entry in self.entries:
            if entry.status == "RELEASED":
                return entry
        raise ValueError("dbt charts YAML schema catalog has no RELEASED entry.")

    @property
    def versions(self) -> tuple[str, ...]:
        return tuple(entry.version for entry in self.entries)

    def schema_for(self, version: str) -> JsonObject:
        return self._schemas[version]


def _manifest_string(value: JsonObject, key: str) -> str:
    result = value[key]
    if not isinstance(result, str):
        raise ValueError(f"dbt charts YAML schema manifest {key} must be a string.")
    return result


def _manifest_optional_string(value: JsonObject, key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ValueError(
            f"dbt charts YAML schema manifest {key} must be a string or null."
        )
    return result


def _manifest_optional_date(value: JsonObject, key: str) -> date | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ValueError(
            f"dbt charts YAML schema manifest {key} must be an ISO date or null."
        )
    try:
        return date.fromisoformat(result)
    except ValueError as error:
        raise ValueError(
            f"dbt charts YAML schema manifest {key} must be an ISO date."
        ) from error


def _manifest_status(value: JsonObject) -> Literal["RELEASED", "DEV"]:
    result = value.get("status")
    if result == "RELEASED":
        return "RELEASED"
    if result == "DEV":
        return "DEV"
    raise ValueError(
        'dbt charts YAML schema manifest status must be "RELEASED" or "DEV".'
    )


_DOTTED_VERSION_RE = re.compile(r"^(\d{1,9})\.(\d{1,9})\.(\d{1,9})\Z", re.ASCII)


def parse_dotted_version(value: str) -> tuple[int, int, int] | None:
    """Parse an ``X.Y.Z`` version string; ``None`` if it isn't exactly that shape.

    Never raises. Shared by the migration-stamp and diagnostic-hint call
    sites that compare a manifest version (always this shape) against a
    value that might not be (a hand-authored ``_schema_version``, migration
    test fixtures) -- one strict parser so "genuinely newer", "not
    comparable", and "malformed" agree between the two, rather than each
    risking its own definition of "close enough". A strict ASCII-digit
    regex, not ``int()`` directly: ``int()`` also accepts leading/trailing
    whitespace, a leading ``+``, ``_`` digit-group separators, and
    non-ASCII decimal digits, any of which would otherwise produce a
    confidently wrong comparison. ``\\Z``, not ``$``, anchors the end: ``$``
    also matches immediately before a trailing newline.
    """
    match = _DOTTED_VERSION_RE.match(value)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def next_minor(version: str) -> str:
    """Return the next minor version: ``X.Y.Z`` -> ``X.(Y+1).0``.

    Names a manifest's DEV entry after the release it is predicted to ship
    in -- one minor ahead of whatever version it currently follows. Raises
    on a malformed version rather than guessing; callers always hold a real
    manifest version here, never untrusted input.
    """
    parsed = parse_dotted_version(version)
    if parsed is None:
        raise ValueError(
            f"dbt charts version must be MAJOR.MINOR.PATCH, got {version!r}."
        )
    major, minor, _patch = parsed
    return f"{major}.{minor + 1}.0"


def canonical_schema_bytes(schema: JsonObject) -> bytes:
    """Serialize a schema once so release comparisons are byte-for-byte stable."""
    return (json.dumps(schema, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _stripped_prop(prop: JsonValue) -> JsonValue:
    if not isinstance(prop, dict):
        return prop
    return {key: value for key, value in prop.items() if key != "description"}


def _strip_node_description(node: JsonObject) -> JsonObject:
    """Drop one schema node's own ``description``, plus its fields' (one level
    down, in ``properties``/``additionalProperties``).

    Only strips ``description`` as a schema *keyword*, never as a property
    *name* -- a field can itself be named ``description``, and its entry in
    a ``properties`` mapping must survive untouched.
    """
    stripped = {key: value for key, value in node.items() if key != "description"}
    properties = stripped.get("properties")
    if isinstance(properties, dict):
        stripped["properties"] = {
            name: _stripped_prop(prop) for name, prop in properties.items()
        }
    additional_properties = stripped.get("additionalProperties")
    if isinstance(additional_properties, dict):
        stripped["additionalProperties"] = _stripped_prop(additional_properties)
    return stripped


def structural_schema_bytes(schema: JsonObject) -> bytes:
    """Serialize a schema's structure, ignoring documentation-only ``description`` prose.

    Used by the freeze/verify checks to tell a grammar change (warrants a new
    frozen version) apart from a pure wording edit to a docstring (does not).
    Strips only the four spots the current renderer places ``description``
    (schema root, each ``$defs`` entry, each field's own
    ``properties``/``additionalProperties`` node) -- a ``description`` an
    older renderer version left nested elsewhere in a historical frozen
    snapshot survives and is compared structurally, which can only make an
    unchanged grammar look changed, never the reverse. This also ignores a
    changed ``InheritSlot``/``inherit_from`` fallback note, since
    ``_inherit_note`` folds it into the same field ``description`` string
    (``json_schema.py``); retargeting a field's inheritance source is a
    wording-level change to what the schema documents, not to what it accepts.
    """
    stripped = _strip_node_description(schema)
    defs = stripped.get("$defs")
    if isinstance(defs, dict):
        stripped["$defs"] = {
            name: (_strip_node_description(defn) if isinstance(defn, dict) else defn)
            for name, defn in defs.items()
        }
    return canonical_schema_bytes(stripped)


def load_yaml_schema_catalog() -> YamlSchemaCatalog:
    """Load the package's immutable dbt charts YAML schemas."""
    return load_yaml_schema_catalog_from(
        files("dbt_charts") / "data" / "schemas" / "yaml"
    )


def load_yaml_schema_catalog_from(
    directory: _SchemaResource,
) -> YamlSchemaCatalog:
    """Load and verify a catalog directory; intended for package data and tests."""
    manifest: JsonValue = json.loads(
        directory.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    if not isinstance(manifest, dict) or "schemas" not in manifest:
        raise ValueError("dbt charts YAML schema manifest has no schemas.")
    raw_entries = manifest["schemas"]
    if not isinstance(raw_entries, list):
        raise ValueError("dbt charts YAML schema manifest schemas must be a list.")
    if not raw_entries:
        raise ValueError("dbt charts YAML schema manifest has no schemas.")

    entries = tuple(
        YamlSchemaEntry.from_json(value)
        for value in raw_entries
        if isinstance(value, dict)
    )
    if len(entries) != len(raw_entries):
        raise ValueError("dbt charts YAML schema manifest entries must be objects.")
    if len({entry.version for entry in entries}) != len(entries):
        raise ValueError("dbt charts YAML schema manifest has duplicate versions.")

    dev_count = sum(1 for entry in entries if entry.status == "DEV")
    if dev_count != 1:
        raise ValueError(
            "dbt charts YAML schema manifest must have exactly one DEV entry, "
            f"found {dev_count}."
        )
    if entries[-1].status != "DEV":
        raise ValueError(
            "dbt charts YAML schema manifest DEV entry must be the newest entry."
        )
    previous_version_key: tuple[int, int, int] | None = None
    previous_entry_version: str | None = None
    for entry in entries:
        version_key = parse_dotted_version(entry.version)
        if version_key is None:
            raise ValueError(
                f"dbt charts YAML schema manifest entry {entry.version!r} must "
                "be MAJOR.MINOR.PATCH."
            )
        if previous_version_key is not None and version_key <= previous_version_key:
            label = "DEV entry" if entry.status == "DEV" else "entry"
            raise ValueError(
                f"dbt charts YAML schema manifest {label} {entry.version} must "
                f"be numerically newer than {previous_entry_version}."
            )
        previous_version_key = version_key
        previous_entry_version = entry.version

    current_schema = _compute_current_schema()
    schemas: dict[str, JsonObject] = {}
    predecessor: str | Literal[None] = None
    previous_date: date | Literal[None] = None
    for entry in entries:
        if entry.predecessor != predecessor:
            raise ValueError(
                f"dbt charts YAML schema {entry.version} has predecessor "
                f"{entry.predecessor!r}, expected {predecessor!r}."
            )
        if entry.status == "DEV":
            if (
                entry.released_at is not None
                or entry.filename is not None
                or entry.sha256 is not None
            ):
                raise ValueError(
                    f"dbt charts YAML schema DEV entry {entry.version} must not "
                    "set released_at, file, or sha256."
                )
            schemas[entry.version] = current_schema
        else:
            if (
                entry.released_at is None
                or entry.filename is None
                or entry.sha256 is None
            ):
                raise ValueError(
                    f"dbt charts YAML schema {entry.version} is RELEASED and must "
                    "set released_at, file, and sha256."
                )
            if previous_date is not None and entry.released_at < previous_date:
                raise ValueError(
                    "dbt charts YAML schema manifest release dates must be chronological."
                )
            contents = directory.joinpath(entry.filename).read_bytes()
            digest = hashlib.sha256(contents).hexdigest()
            if digest != entry.sha256:
                raise ValueError(
                    f"dbt charts YAML schema {entry.filename} sha256 does not "
                    "match its manifest."
                )
            schema: JsonValue = json.loads(contents)
            if not isinstance(schema, dict):
                raise ValueError(
                    f"dbt charts YAML schema {entry.filename} must be an object."
                )
            schemas[entry.version] = schema
            previous_date = entry.released_at
        predecessor = entry.version

    return YamlSchemaCatalog(tuple(reversed(entries)), schemas, current_schema)


def _compute_current_schema() -> JsonObject:
    """Generate the live JSON schema from the current Pydantic models."""
    from dbt_charts.core.compile.schema.introspection import introspect
    from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema

    return render_yaml_schema(introspect())
