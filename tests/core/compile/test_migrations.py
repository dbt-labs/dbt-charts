from __future__ import annotations

import warnings
from copy import deepcopy
from datetime import date
from typing import cast

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from dbt_charts.core.compile.errors import ParseError
from dbt_charts.core.compile.migrations import (
    ConditionalMove,
    Deletion,
    MigrationConflictError,
    MigrationError,
    MigrationRegistry,
    Move,
    SchemaMigrationWarning,
    SchemaVersionTooOldError,
    UnsupportedSchemaError,
    migrate_mapping,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
    next_minor,
)

from ._migration_catalogs import flat_schema, synthetic_catalog
from ._migration_declarations import checked_registry, validate_declarations

V1 = "0.1.0"
V2 = "0.2.0"
V3 = "0.3.0"


def _chart_schema(type_required: bool) -> JsonObject:
    chart_schema: JsonObject = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "type": {"type": "string"},
        },
        "additionalProperties": False,
    }
    if type_required:
        chart_schema["required"] = ["type"]
    return {
        "type": "object",
        "properties": {
            "charts": {
                "type": "object",
                "additionalProperties": chart_schema,
            }
        },
        "additionalProperties": False,
    }


def _required_chart_type_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    catalog = synthetic_catalog({V1: _chart_schema(False), V2: _chart_schema(True)})
    return catalog, MigrationRegistry([], catalog=catalog)


def test_migrates_a_retired_key_before_current_validation() -> None:
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    result = migrate_mapping({"old": "value"}, catalog=catalog, registry=registry)

    assert result == {"new": "value"}


def test_current_mapping_is_not_rewritten() -> None:
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    raw = {"new": "value"}

    result = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert result == raw
    assert result is not raw


def test_rejects_a_move_whose_source_path_survives_the_transition() -> None:
    """``old`` is declared renamed to ``new``, yet V2 still accepts ``old``.

    Recognition reads a surviving source path as proof that a document predates
    the transition, so a declaration like this would migrate current documents.
    Caught where it is written, not where it misfires.
    """
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("old", "new")})

    with pytest.raises(MigrationError, match="still exists in"):
        checked_registry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)


def _enum_schema(field: str, values: list[str]) -> JsonObject:
    """A one-field grammar restricting *field* to *values* -- unlike
    ``flat_schema`` (bare ``type: string``, so any value validates), this lets
    a stale value fail current-schema validation and engage recognition."""
    return cast(
        JsonObject,
        {
            "type": "object",
            "properties": {field: {"type": "string", "enum": values}},
            "additionalProperties": False,
        },
    )


def test_identity_path_move_remaps_a_value_on_a_stable_key() -> None:
    """A Move whose old_path == new_path remaps a key's *value* without the
    key ever disappearing -- the shape a permanent authoring-sugar key (like
    dbt charts' ``theme:``) needs when its legal *values* narrow but the key
    itself never goes away, so it can never be "renamed" out of a grammar."""
    catalog = synthetic_catalog(
        {
            V1: _enum_schema("color", ["red", "blue"]),
            V2: _enum_schema("color", ["blue", "crimson"]),
        }
    )
    registry = MigrationRegistry(
        [
            Move(
                V1,
                V2,
                ("color",),
                ("color",),
                value_map={"red": "crimson", "blue": "blue"},
            )
        ],
        catalog=catalog,
    )

    result = migrate_mapping({"color": "red"}, catalog=catalog, registry=registry)

    assert result == {"color": "crimson"}


def test_identity_path_move_requires_a_value_map() -> None:
    """An identity-path Move with no value_map would be a silent no-op --
    rejected at registration, same as any other declaration that can't do
    anything a caller would notice."""
    catalog = synthetic_catalog({V1: flat_schema("color"), V2: flat_schema("color")})

    with pytest.raises(MigrationError, match="value_map"):
        checked_registry([Move(V1, V2, ("color",), ("color",))], catalog=catalog)


def test_identity_path_move_rewrites_yaml_text_in_place() -> None:
    """The text-preserving rewriter must not treat an identity-path Move's
    source and destination as separate keys -- doing so would stage the key
    for both a value update and a removal, and dict-merging those two updates
    together would delete the key's rewritten value instead of setting it."""
    catalog = synthetic_catalog(
        {
            V1: _enum_schema("color", ["red", "blue"]),
            V2: _enum_schema("color", ["blue", "crimson"]),
        }
    )
    registry = MigrationRegistry(
        [
            Move(
                V1,
                V2,
                ("color",),
                ("color",),
                value_map={"red": "crimson", "blue": "blue"},
            )
        ],
        catalog=catalog,
    )

    migrated = migrate_yaml_text("color: red\n", catalog=catalog, registry=registry)

    assert yaml.safe_load(migrated) == {"color": "crimson"}


def test_applies_adjacent_moves_in_order() -> None:
    catalog = synthetic_catalog(
        {V1: flat_schema("old"), V2: flat_schema("middle"), V3: flat_schema("new")}
    )
    registry = MigrationRegistry(
        [
            Move(V1, V2, ("old",), ("middle",)),
            Move(V2, V3, ("middle",), ("new",)),
        ],
        catalog=catalog,
    )

    result = migrate_mapping({"old": "value"}, catalog=catalog, registry=registry)

    assert result == {"new": "value"}


def test_declared_move_can_reach_current_schema_without_another_move() -> None:
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old"),
            V2: flat_schema("middle"),
            V3: flat_schema("middle", "optional"),
        }
    )
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("middle",))], catalog=catalog)

    result = migrate_mapping({"old": "value"}, catalog=catalog, registry=registry)

    assert result == {"middle": "value"}


def test_conflicting_destination_does_not_mutate_input() -> None:
    catalog = synthetic_catalog({V1: flat_schema("old", "new"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    raw = {"old": "old value", "new": "new value"}
    original = deepcopy(raw)

    with pytest.raises(MigrationConflictError, match="old.*new"):
        migrate_mapping(raw, catalog=catalog, registry=registry)

    assert raw == original


def test_rejects_unknown_grammar_with_current_diagnostic() -> None:
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    with pytest.raises(UnsupportedSchemaError, match="unexpected"):
        migrate_mapping({"unexpected": "value"}, catalog=catalog, registry=registry)


def test_non_structural_current_rejection_uses_current_diagnostic() -> None:
    catalog, registry = _required_chart_type_context()

    with pytest.raises(UnsupportedSchemaError, match="'type' is a required property"):
        migrate_mapping(
            {"charts": {"revenue": {"query": "revenue"}}},
            catalog=catalog,
            registry=registry,
        )


def test_explicit_non_structural_migration_uses_current_diagnostic() -> None:
    catalog, registry = _required_chart_type_context()

    with pytest.raises(UnsupportedSchemaError, match="'type' is a required property"):
        migrate_yaml_text(
            "charts:\n  revenue:\n    query: revenue\n",
            catalog=catalog,
            registry=registry,
        )


def test_rewrites_a_scalar_relocation_to_a_different_parent() -> None:
    """A move to a genuinely different parent (not a same-position rename)
    goes through the scalar setter, which renormalizes the value (quotes it
    here) -- unlike a same-position rename, which never touches the value's
    original spelling (`test_renames_a_folded_block_scalar_key_in_place_at_root`
    and friends)."""
    catalog = synthetic_catalog(
        {
            V1: flat_schema("old"),
            V2: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {
                        "wrapper": {
                            "type": "object",
                            "properties": {"new": {"type": "string"}},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
        }
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("old",), ("wrapper", "new"))], catalog=catalog
    )

    result = migrate_yaml_text(
        "# preserve me\nold: value\n# preserve me too\n",
        catalog=catalog,
        registry=registry,
    )

    assert 'new: "value"' in result
    assert "old:" not in result
    assert "# preserve me\n" in result
    assert "# preserve me too\n" in result


def _object_schema_pair(*, old_key: str, new_key: str) -> dict[str, JsonObject]:
    return {
        V1: cast(
            JsonObject,
            {
                "type": "object",
                "properties": {old_key: {"type": "object"}},
                "additionalProperties": False,
            },
        ),
        V2: cast(
            JsonObject,
            {
                "type": "object",
                "properties": {new_key: {"type": "object"}},
                "additionalProperties": False,
            },
        ),
    }


def test_renames_a_block_valued_key_in_place_without_reformatting() -> None:
    """A pure rename (old/new path share the same parent) never relocates
    content, so it's not restricted to scalars: the nested mapping under
    `old:` moves to `new:` untouched, and unrelated comments survive."""
    catalog = synthetic_catalog(_object_schema_pair(old_key="old", new_key="new"))
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)

    result = migrate_yaml_text(
        "# preserve me\nold:\n  nested: value\n# preserve me too\n",
        catalog=catalog,
        registry=registry,
    )

    assert result == "# preserve me\nnew:\n  nested: value\n# preserve me too\n"


def test_rejects_non_scalar_relocation_to_a_different_parent() -> None:
    """A move to a genuinely different parent (not a same-position rename)
    still can't relocate a nested block without a redump, so it still fails
    loudly instead of guessing."""
    catalog = synthetic_catalog(
        {
            V1: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {"old": {"type": "object"}},
                    "additionalProperties": False,
                },
            ),
            V2: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {
                        "wrapper": {
                            "type": "object",
                            "properties": {"new": {"type": "object"}},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
        }
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("old",), ("wrapper", "new"))], catalog=catalog
    )

    with pytest.raises(MigrationError, match="only scalar"):
        migrate_yaml_text("old:\n  nested: value\n", catalog=catalog, registry=registry)


def test_current_recognition_schema_accepts_layout_name_shorthand() -> None:
    raw = {"rows": ["revenue_trend"]}

    assert prepare_board_mapping(cast(JsonObject, raw)) == raw


def test_current_pydantic_shorthand_is_not_rejected_by_schema_recognition() -> None:
    raw = {"theme": "vivid"}

    assert prepare_board_mapping(raw) == raw


def test_prepare_mapping_defers_non_structural_rejection_to_pydantic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbt_charts.core.compile.migrations import migrations as _migrations_impl
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    catalog, registry = _required_chart_type_context()
    raw: JsonObject = {"charts": {"revenue": {"query": "revenue"}}}
    # Patch the submodule where prepare_board_mapping lives and calls these names —
    # patching the re-exporting __init__ does not affect the internal call site.
    monkeypatch.setattr(_migrations_impl, "_board_has_historical_schemas", lambda: True)
    monkeypatch.setattr(
        _migrations_impl, "_board_migration_context", lambda: (catalog, registry)
    )

    prepared = prepare_board_mapping(raw)

    assert prepared == raw
    with pytest.raises(ValidationError, match="type"):
        AuthoredBoard.model_validate(prepared)


def test_catalog_version_without_module_is_skipped() -> None:
    """A catalog entry with no matching v*.py file contributes no moves and raises no error."""
    from dbt_charts.core.compile.migrations.migrations import (
        _board_migration_context,
        _load_migration_module,
    )
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        load_yaml_schema_catalog,
    )

    catalog = load_yaml_schema_catalog()
    # Find a real catalog entry with no version module, rather than pinning one by
    # version number — that pin breaks every time the retained catalog changes.
    moduleless = next(
        e
        for e in catalog.entries
        if e.predecessor is not None
        and _load_migration_module(
            f"dbt_charts.core.compile.migrations.versions.v{e.version.replace('.', '_')}"
        )
        is None
    )

    _, registry = _board_migration_context()

    assert registry.transition_from(moduleless.predecessor) == ()


def test_version_module_without_moves_or_deletions_raises_migration_error() -> None:
    """A found version module that lacks both moves() and deletions() raises MigrationError."""
    import sys
    import types

    from dbt_charts.core.compile.migrations.migrations import (
        MigrationError,
        _load_migration_module,
    )

    dotted = "dbt_charts.core.compile.migrations.versions.v9_9_9"
    fake = types.ModuleType(dotted)
    # intentionally no 'moves' or 'deletions' attribute

    sys.modules[dotted] = fake
    try:
        with pytest.raises(MigrationError, match="does not define"):
            _load_migration_module(dotted)
    finally:
        del sys.modules[dotted]


def test_prepare_mapping_current_schema_patch_takes_fast_path_under_boardpatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current-schema, patch-shaped fragment (no charts/layout/title — the
    shape of every theme YAML and meta.yml) must not be treated as a
    migration candidate just because it fails the AuthoredBoard
    "must have layout/chart/etc" invariant. Passing model=BoardPatch checks
    currency against a model that admits partial content."""
    from dbt_charts.core.compile.migrations import migrations as _migrations_impl
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    raw: JsonObject = {"style": {"charts": {"axis_x": {"labels": {"format": "auto"}}}}}

    def _boom() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
        raise AssertionError(
            "prepare_board_mapping must not build the migration registry for a "
            "current-schema patch"
        )

    monkeypatch.setattr(_migrations_impl, "_board_migration_context", _boom)

    prepared = prepare_board_mapping(raw, model=BoardPatch)

    assert prepared == raw


def test_prepare_mapping_old_schema_patch_still_migrates_under_boardpatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old-schema patch fragment (pre-rename field name) must still reach
    real migration when checked with model=BoardPatch — the fast path must
    not swallow genuine migration needs for patch-shaped content."""
    from dbt_charts.core.compile.migrations import migrations as _migrations_impl
    from dbt_charts.core.compile.migrations.migrations import suffix_rename_moves
    from dbt_charts.core.compile.models.board.patch import BoardPatch

    old_schema = _schema_with_axis_field("label")
    new_schema = _schema_with_axis_field("labels")
    catalog = synthetic_catalog({V1: old_schema, V2: new_schema})
    registry = MigrationRegistry(
        suffix_rename_moves(
            _AxisHolderPatch,
            V1,
            V2,
            ((("labels",), ("label",)),),
            catalog=catalog,
        ),
        catalog=catalog,
    )
    monkeypatch.setattr(_migrations_impl, "_board_has_historical_schemas", lambda: True)
    monkeypatch.setattr(
        _migrations_impl, "_board_migration_context", lambda: (catalog, registry)
    )

    raw: JsonObject = {"axis_x": {"label": "auto"}}
    prepared = prepare_board_mapping(raw, model=BoardPatch)

    assert prepared == {"axis_x": {"labels": "auto"}}


def _schema_with_axis_field(field_name: str) -> JsonObject:
    return cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "axis_x": {
                    "type": "object",
                    "properties": {field_name: {"type": "string"}},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    )


class _AxisFieldHolder(BaseModel):
    labels: str | None = None


class _AxisHolderPatch(BaseModel):
    axis_x: _AxisFieldHolder | None = None


# ---------------------------------------------------------------------------
# Deletion primitive
# ---------------------------------------------------------------------------


def _deletion_catalog() -> YamlSchemaCatalog:
    """V1 has key 'dead'; V2 does not."""
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"dead": {"type": "string"}, "live": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"live": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    return synthetic_catalog({V1: old, V2: new})


def test_deletion_strips_key_from_mapping() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    result = migrate_mapping(
        {"dead": "gone", "live": "keep"}, catalog=catalog, registry=registry
    )

    assert result == {"live": "keep"}


def test_deletion_absent_key_is_noop() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    result = migrate_mapping({"live": "keep"}, catalog=catalog, registry=registry)

    assert result == {"live": "keep"}


def test_deletion_reason_is_reported_when_field_present() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",), reason="never consumed by any renderer")],
        catalog=catalog,
    )

    with pytest.warns(SchemaMigrationWarning, match="never consumed by any renderer"):
        result = migrate_mapping(
            {"dead": "gone", "live": "keep"}, catalog=catalog, registry=registry
        )

    assert result == {"live": "keep"}


def test_deletion_reason_is_not_reported_when_field_absent() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",), reason="never consumed by any renderer")],
        catalog=catalog,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        migrate_mapping({"live": "keep"}, catalog=catalog, registry=registry)

    assert not any("never consumed" in str(w.message) for w in caught)


def test_deletion_without_reason_emits_no_reason_warning() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        migrate_mapping(
            {"dead": "gone", "live": "keep"}, catalog=catalog, registry=registry
        )

    messages = [str(w.message) for w in caught]
    assert messages == [
        "dbt charts migrated this YAML in memory; `dct migrate` may be able to "
        "update the file."
    ]


def test_deletion_strips_key_from_yaml_text() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    result = migrate_yaml_text(
        "# comment\ndead: gone\nlive: keep\n",
        catalog=catalog,
        registry=registry,
    )

    assert "dead:" not in result
    assert "live:" in result
    assert "# comment\n" in result


def test_deletion_reason_is_reported_from_yaml_text_rewrite() -> None:
    """The file-rewrite path (dct migrate) must surface a Deletion.reason too,

    not only the in-memory path (migrate_mapping). Before the fix,
    migrate_yaml_text called _apply_deletions and discarded its returned
    messages.
    """
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",), reason="never consumed by any renderer")],
        catalog=catalog,
    )

    with pytest.warns(SchemaMigrationWarning, match="never consumed by any renderer"):
        result = migrate_yaml_text(
            "dead: gone\nlive: keep\n", catalog=catalog, registry=registry
        )

    assert "dead:" not in result


def test_deletion_current_schema_document_untouched() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )
    raw = {"live": "keep"}

    result = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert result == raw
    assert result is not raw


def test_deletion_validates_source_path_must_exist() -> None:
    catalog = _deletion_catalog()

    with pytest.raises(MigrationError, match="absent from"):
        checked_registry(
            [],
            [Deletion(V1, V2, ("nonexistent",))],
            catalog=catalog,
        )


def test_deletion_validates_adjacent_schema() -> None:
    catalog = synthetic_catalog(
        {V1: flat_schema("a"), V2: flat_schema("a"), V3: flat_schema("a")}
    )

    with pytest.raises(MigrationError, match="immediately succeeding"):
        checked_registry(
            [],
            [Deletion(V1, V3, ("a",))],
            catalog=catalog,
        )


def test_deletions_from_returns_empty_for_unknown_schema() -> None:
    catalog = _deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    assert registry.deletions_from("9.9.9") == ()


def _legend_catalog() -> YamlSchemaCatalog:
    """V1 has legend.dead + legend.live; V2 has only legend.live."""
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {
                        "dead": {"type": "boolean"},
                        "live": {"type": "string"},
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {"live": {"type": "string"}},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    )
    return synthetic_catalog({V1: old, V2: new})


def _current_boundary_catalog() -> YamlSchemaCatalog:
    """V2 is latest with 'dead'+'live'; the DEV entry's schema has only 'live'.

    Simulates an unreleased deletion — the latest frozen schema still has the
    field, but the live Pydantic model has removed it.
    """
    return synthetic_catalog(
        {V1: flat_schema("ancient"), V2: flat_schema("dead", "live")},
        current=flat_schema("live"),
    )


def test_deletion_target_path_must_be_absent_from_target_schema() -> None:
    """A tail still present in the target schema must not be declared as a Deletion."""
    catalog = _legend_catalog()

    # "live" exists in both V1 and V2 — declaring a deletion is wrong
    with pytest.raises(MigrationError, match="still exists in"):
        checked_registry(
            [],
            [Deletion(V1, V2, ("legend", "live"))],
            catalog=catalog,
        )


def test_deletion_current_target_path_must_be_absent_from_current_schema() -> None:
    """A tail still present in the current (live) schema must not be deleted to the DEV version."""
    catalog = _current_boundary_catalog()

    # "live" still exists in the current schema — declaring a deletion is wrong
    with pytest.raises(MigrationError, match="still exists in"):
        checked_registry(
            [],
            [Deletion(V2, catalog.dev.version, ("live",))],
            catalog=catalog,
        )


def test_deletion_valid_current_target_accepts_absent_tail() -> None:
    """Deletion to the DEV version is accepted when the tail is absent from current_schema."""
    catalog = _current_boundary_catalog()

    # "dead" is in V2 (source) but not in current_schema — valid deletion
    registry = checked_registry(
        [],
        [Deletion(V2, catalog.dev.version, ("dead",))],
        catalog=catalog,
    )
    assert registry.deletions_from(V2)


def test_deletion_current_source_must_be_latest_schema() -> None:
    """Deletion targeting the DEV version must target the immediately succeeding schema."""
    catalog = _current_boundary_catalog()

    with pytest.raises(MigrationError, match="immediately succeeding"):
        checked_registry(
            [],
            [Deletion(V1, catalog.dev.version, ("ancient",))],
            catalog=catalog,
        )


def test_deletion_unretained_source_raises() -> None:
    """Deletion referencing a schema version not in the catalog raises."""
    catalog = _deletion_catalog()

    with pytest.raises(MigrationError, match="not retained"):
        checked_registry(
            [],
            [Deletion("9.9.9", V2, ("dead",))],
            catalog=catalog,
        )


def test_support_window_exemption_latest_schema_is_always_migratable() -> None:
    """The latest released schema is exempt from the six-month support window.

    Older schemas age out, but the newest release is always transparently
    migratable regardless of how old it is.
    """
    catalog = _deletion_catalog()
    # V1 is the older schema (not latest); V2 is latest.
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("dead",))],
        catalog=catalog,
    )

    # today = far future (well past 6 months from release dates)
    far_future = date(2099, 1, 1)

    # V1 is the older schema and should be subject to the support window
    with pytest.raises(SchemaVersionTooOldError):
        migrate_mapping(
            {"dead": "gone", "live": "keep"},
            catalog=catalog,
            registry=registry,
            today=far_future,
        )

    # V2 is the latest frozen schema — always exempt regardless of date
    pending_catalog = _current_boundary_catalog()
    pending_registry = MigrationRegistry(
        [],
        [Deletion(V2, pending_catalog.dev.version, ("dead",))],
        catalog=pending_catalog,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(
            {"dead": "present", "live": "keep"},
            catalog=pending_catalog,
            registry=pending_registry,
            today=far_future,
        )
    assert result == {"live": "keep"}


def test_deletion_strips_nested_list_item_from_yaml_text() -> None:
    """Deletion in a list-nested mapping (rows/cols) works in the YAML text writer.

    Pre-enumeration computed numeric path segments that set_board_values cannot
    address; tail-matching walks the document instead.
    """
    legend_schema_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "dead": {"type": "string"},
                "live": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    legend_schema_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"live": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    row_item_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "style": {
                    "type": "object",
                    "properties": {"legend": legend_schema_v1},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    )
    row_item_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "style": {
                    "type": "object",
                    "properties": {"legend": legend_schema_v2},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
    )
    old_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": row_item_v1}},
            "additionalProperties": False,
        },
    )
    new_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": row_item_v2}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old_schema, V2: new_schema})
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    yaml_text = (
        "rows:\n  - style:\n      legend:\n        dead: gone\n        live: keep\n"
    )

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "dead:" not in result
    assert "live: keep" in result
    assert "rows:" in result


def test_move_renames_key_inside_list_item_mapping() -> None:
    """A rename applies inside a list-nested item (rows/cols/grid.items/
    tabs.items) in migrate_mapping.

    Coverage of a Move whose path crosses a list container, which the
    board-nesting groups all do. ``_move_value`` handles it: the destination
    walk substitutes the wildcard bindings ``_source_locations`` collected on
    the way down, so it re-descends through the same list index rather than
    needing dict-only ``.get()`` semantics.
    """
    item_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    item_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    old_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": item_v1}},
            "additionalProperties": False,
        },
    )
    new_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": item_v2}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old_schema, V2: new_schema})
    registry = MigrationRegistry(
        [Move(V1, V2, ("rows", "*", "description"), ("rows", "*", "notes"))],
        catalog=catalog,
    )

    result = migrate_mapping(
        {"rows": [{"description": "first"}, {"description": "second"}]},
        catalog=catalog,
        registry=registry,
    )

    assert result == {"rows": [{"notes": "first"}, {"notes": "second"}]}


def test_move_renames_key_inside_list_item_mapping_in_yaml_text() -> None:
    """The same list-nested rename round-trips through the text-preserving
    writer, leaving each item's value byte-identical rather than renormalizing
    it through the scalar setter -- a rename changes only the key token, so the
    writer's ``rename_key_at_path`` path carries no restriction on value shape.
    """
    item_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    item_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    old_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": item_v1}},
            "additionalProperties": False,
        },
    )
    new_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": item_v2}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old_schema, V2: new_schema})
    registry = MigrationRegistry(
        [Move(V1, V2, ("rows", "*", "description"), ("rows", "*", "notes"))],
        catalog=catalog,
    )

    yaml_text = "rows:\n  - description: first\n  - description: second\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert result == "rows:\n  - notes: first\n  - notes: second\n"


def test_renames_a_folded_block_scalar_key_in_place_at_root() -> None:
    """A rename must key-rename block scalars in place, never round-trip the
    value through the scalar setter -- `>` folds the string onto one logical
    line, but the parsed value is still multi-line (a trailing newline), which
    the setter used for genuine relocations refuses to write."""
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    yaml_text = "# preserve me\nold: >\n  a folded value\n  spanning two lines\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "old:" not in result
    assert "# preserve me\n" in result
    assert yaml.safe_load(result)["new"] == yaml.safe_load(yaml_text)["old"]


def test_renames_a_literal_block_scalar_key_in_place_at_root() -> None:
    """Same as the folded case but for `|`, which preserves line breaks
    literally instead of folding them."""
    catalog = synthetic_catalog({V1: flat_schema("old"), V2: flat_schema("new")})
    registry = MigrationRegistry([Move(V1, V2, ("old",), ("new",))], catalog=catalog)
    yaml_text = "# preserve me\nold: |\n  a literal value\n  spanning two lines\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "old:" not in result
    assert "# preserve me\n" in result
    assert yaml.safe_load(result)["new"] == yaml.safe_load(yaml_text)["old"]


def test_renames_a_folded_block_scalar_key_at_a_nested_chart_position() -> None:
    """The same rename, one level deeper -- a chart's own key, not the
    board root -- to prove the fast path isn't root-only."""
    chart_schema = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    chart_schema_v2 = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog(
        {
            V1: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {
                        "charts": {
                            "type": "object",
                            "properties": {"c1": chart_schema},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            V2: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {
                        "charts": {
                            "type": "object",
                            "properties": {"c1": chart_schema_v2},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
        }
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("charts", "c1", "description"), ("charts", "c1", "notes"))],
        catalog=catalog,
    )
    yaml_text = (
        "charts:\n"
        "  c1:\n"
        "    description: >\n"
        "      a folded chart notes\n"
        "      spanning two lines\n"
    )

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "description:" not in result
    assert "notes:" in result
    assert (
        yaml.safe_load(result)["charts"]["c1"]["notes"]
        == yaml.safe_load(yaml_text)["charts"]["c1"]["description"]
    )


def test_renames_a_block_scalar_key_inside_a_list_item_in_yaml_text() -> None:
    """The same rename inside a list-nested item (rows/cols/grid.items/
    tabs.items) -- block scalars must survive there too, not just at a plain
    mapping position."""
    item_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    item_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog(
        {
            V1: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {"items": {"type": "array", "items": item_v1}},
                    "additionalProperties": False,
                },
            ),
            V2: cast(
                JsonObject,
                {
                    "type": "object",
                    "properties": {"items": {"type": "array", "items": item_v2}},
                    "additionalProperties": False,
                },
            ),
        }
    )
    registry = MigrationRegistry(
        [Move(V1, V2, ("items", "*", "description"), ("items", "*", "notes"))],
        catalog=catalog,
    )
    yaml_text = (
        "items:\n"
        "  - description: |\n"
        "      a literal grid item notes\n"
        "      spanning two lines\n"
    )

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "description:" not in result
    assert "notes:" in result
    assert (
        yaml.safe_load(result)["items"][0]["notes"]
        == yaml.safe_load(yaml_text)["items"][0]["description"]
    )


def test_deletion_raises_on_flow_style_yaml() -> None:
    """Deletion in a flow-style mapping raises MigrationError instead of silently no-oping.

    A line like ``legend: {dead: true, live: keep}`` is legal YAML but the text
    editor's line-regex only matches block-mapping key lines, so it cannot remove
    ``dead`` without reformatting the whole mapping.  Without an equality check
    the file comes back byte-identical while the in-memory result has ``dead``
    gone — the user is told to run ``dct migrate`` on a file that already reports
    as current.  The equality check catches this before returning a wrong result.
    """
    catalog = _legend_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    yaml_text = "legend: {dead: true, live: keep}\n"

    with pytest.raises(MigrationError, match="flow-style"):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


def test_version_module_with_only_deletions_is_accepted() -> None:
    """A version module with deletions() but no moves() is valid."""
    import sys
    import types

    from dbt_charts.core.compile.migrations.migrations import _load_migration_module

    dotted = "dbt_charts.core.compile.migrations.versions.v9_9_9"
    fake = types.ModuleType(dotted)
    fake.deletions = lambda *a, **kw: ()  # type: ignore[attr-defined]

    sys.modules[dotted] = fake
    try:
        module = _load_migration_module(dotted)
        assert module is fake
    finally:
        del sys.modules[dotted]


def _orphan_parent_catalog() -> YamlSchemaCatalog:
    """V1 has legend.dead + live; V2 has only live (no legend key at all)."""
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {"dead": {"type": "boolean"}},
                    "additionalProperties": False,
                },
                "live": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"live": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    return synthetic_catalog({V1: old, V2: new})


def test_deletion_cleans_up_orphaned_parent_in_mapping() -> None:
    """Deleting the sole child of a parent dict also removes the empty parent."""
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    result = migrate_mapping(
        {"legend": {"dead": True}, "live": "keep"},
        catalog=catalog,
        registry=registry,
    )

    assert "legend" not in result
    assert result == {"live": "keep"}


def test_deletion_cleans_up_orphaned_parent_in_yaml_text() -> None:
    """Deleting the sole child of a parent mapping also removes the orphaned parent line."""
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    yaml_text = "legend:\n  dead: true\nlive: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "legend" not in result
    assert "live: keep" in result


def _sibling_parent_catalog() -> YamlSchemaCatalog:
    """V1 has legend.{dead,live} + other.dead; V2 has legend.{live} + other.dead."""
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {
                        "dead": {"type": "boolean"},
                        "live": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "other": {
                    "type": "object",
                    "properties": {"dead": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {"live": {"type": "string"}},
                    "additionalProperties": False,
                },
                "other": {
                    "type": "object",
                    "properties": {"dead": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    return synthetic_catalog({V1: old, V2: new})


def test_deletion_does_not_affect_sibling_parent_in_mapping() -> None:
    """A tail key under a different parent must not be deleted.

    The guard in _try_delete_tail checks the parent key before descending.
    Regressing it to an unconditional delete would remove ``other.dead``
    when the declared deletion only targets ``legend.dead``.
    """
    catalog = _sibling_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    result = migrate_mapping(
        {"legend": {"dead": True, "live": "keep"}, "other": {"dead": "preserved"}},
        catalog=catalog,
        registry=registry,
    )

    assert result["other"] == {"dead": "preserved"}


def test_deletion_does_not_affect_sibling_parent_in_yaml_text() -> None:
    """A tail key under a different parent must not be deleted from YAML text.

    The struck path names the ``legend`` block, so the ``dead`` line under
        ``other`` is a different path and survives.
    """
    catalog = _sibling_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )

    yaml_text = "legend:\n  dead: true\n  live: keep\nother:\n  dead: preserved\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "other:\n  dead: preserved" in result
    assert "legend:\n  dead" not in result


def _open_legend_catalog() -> YamlSchemaCatalog:
    """V1 has legend.{dead,live} + open additional properties; V2 has legend.{live}."""
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {
                        "dead": {"type": "boolean"},
                        "live": {"type": "string"},
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": True,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "legend": {
                    "type": "object",
                    "properties": {"live": {"type": "string"}},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": True,
        },
    )
    return synthetic_catalog({V1: old, V2: new})


def test_deletion_does_not_remove_null_valued_key_in_yaml_text() -> None:
    """A null-valued block key unrelated to the deletion must not be pruned.

    Only the paths the walk struck are removed, and an emptied parent is
        struck in its own right — a key the walk never touched is never a
        candidate, however few children it has.
    """
    catalog = _open_legend_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    # description: has a null value — no children, unrelated to the deletion
    yaml_text = "description:\nlegend:\n  dead: true\n  live: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "description:" in result
    assert "dead:" not in result


def test_deletion_does_not_remove_comment_only_block_in_yaml_text() -> None:
    """A key whose only block content is comments must not be pruned.

    The key is not on the ancestor walk of any deleted line, so it is never a
    candidate — candidate scoping, not child counting, provides this guarantee.
    """
    catalog = _open_legend_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "notes:\n  # todo\nlegend:\n  dead: true\n  live: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "notes:" in result
    assert "dead:" not in result


def test_deletion_prunes_parent_with_inline_comment_in_yaml_text() -> None:
    """A parent key with a trailing inline comment is treated as block-only.

    ``legend:  # appearance`` has a null value (the comment is not the value).
    When the deletion empties the block, the parent key line — comment and
    all — must be pruned, not left behind as a dangling null entry that
    diverges from the in-memory result.
    """
    catalog = _open_legend_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    # legend has a trailing comment; dead is its only child → parent must be pruned.
    # keep: provides a surviving root key so the final document is non-empty.
    yaml_text = "keep: survivor\nlegend:  # appearance\n  dead: true\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "keep: survivor" in result
    assert "legend:" not in result
    assert "dead:" not in result


def test_deletion_prunes_style_parent_with_inline_comment_in_yaml_text() -> None:
    """Comment-bearing intermediate keys are pruned when their only child goes.

    Verifies the fixpoint propagation: style: # comment → legend: # comment →
    dead: true.  Both are treated as block-only; both are pruned.
    """
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "style": {
                    "type": "object",
                    "properties": {
                        "legend": {
                            "type": "object",
                            "properties": {"dead": {"type": "boolean"}},
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": True,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "style": {
                    "type": "object",
                    "properties": {
                        "legend": {
                            "type": "object",
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                }
            },
            "additionalProperties": True,
        },
    )
    catalog = synthetic_catalog({V1: old, V2: new})
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("style", "legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "keep: survivor\nstyle:  # style\n  legend:  # legend\n    dead: true\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "keep: survivor" in result
    assert "style:" not in result
    assert "legend:" not in result
    assert "dead:" not in result


def test_deletion_does_not_remove_column_zero_sequence_key_in_yaml_text() -> None:
    """A key followed by a column-0 sequence item must not be pruned.

    ``rows:\\n- item`` is PyYAML's default emit for list-valued keys.  The
    sequence item has the same indentation as ``rows:``, so it fails the
    child_indent > key_indent check — the key has no counted children and
    must not be deleted.
    """
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": {"type": "string"}},
                "legend": {
                    "type": "object",
                    "properties": {
                        "dead": {"type": "boolean"},
                        "live": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": {"type": "string"}},
                "legend": {
                    "type": "object",
                    "properties": {"live": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old, V2: new})
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "rows:\n- a\nlegend:\n  dead: true\n  live: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "rows:\n- a\n" in result
    assert "dead:" not in result


def test_deletion_does_not_remove_preexisting_empty_dict_in_mapping() -> None:
    """A pre-existing empty mapping (authored style: {}) must not be silently deleted.

    _delete_tails_recursive only removes a child dict when this deletion caused
    it to become empty.  An empty dict that existed before the migration ran
    must be passed through unchanged.
    """
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "style": {
                                "type": "object",
                                "properties": {
                                    "legend": {
                                        "type": "object",
                                        "properties": {
                                            "dead": {"type": "boolean"},
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "additionalProperties": True,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "style": {
                                "type": "object",
                                "properties": {
                                    "legend": {
                                        "type": "object",
                                        "additionalProperties": False,
                                    },
                                },
                                "additionalProperties": False,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old, V2: new})
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    # chart a has the deletion target; chart b has a pre-existing empty style;
    # chart c has an empty legend inside style.  The migration must strip
    # legend.dead from a but leave both b's style: {} and c's style: {legend: {}}
    # intact — _try_delete_tail must return False when the key is absent, not True.
    mapping = {
        "charts": {
            "a": {"type": "bar", "style": {"legend": {"dead": True}}},
            "b": {"type": "bar", "style": {}},
            "c": {"type": "bar", "style": {"legend": {}}},
        }
    }

    result = migrate_mapping(mapping, catalog=catalog, registry=registry)

    assert result["charts"]["b"]["style"] == {}
    assert result["charts"]["c"]["style"] == {"legend": {}}


def test_deletion_emptying_board_raises_parse_error_in_yaml_text_migration() -> None:
    """Deleting every key raises ParseError — migrate_paths catches it as MigrateError.

    When the only content is the deleted key, the pruner empties the file and
    load_yaml_mapping raises ParseError on the null document.  migrate_yaml_text
    lets it propagate; migrate_paths widens its handler to record a MigrateError
    instead of crashing the run.
    """
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "legend:\n  dead: true\n"

    with pytest.raises(ParseError, match="Empty YAML document"):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


def test_deletion_emptying_board_with_comment_raises_parse_error() -> None:
    """Interior comment plus deleted key also raises ParseError.

    After the MEDIUM fix prunes interior comments alongside the block, the
    resulting document is empty rather than comment-only; ParseError is still
    raised and migrate_paths still records it as MigrateError.
    """
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "legend:\n  # why we set this\n  dead: true\n"

    with pytest.raises(ParseError, match="Empty YAML document"):
        migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)


def test_deletion_does_not_orphan_interior_comment_in_yaml_text() -> None:
    """Comments inside a pruned block must be deleted along with the block.

    Without the fix, the pruner marks the parent key for deletion but skips
    comment lines during the child scan, leaving them at the wrong indentation.
    """
    catalog = _open_legend_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "legend:\n  # why\n  dead: true\ntitle: T\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "legend" not in result
    assert "# why" not in result
    assert "title: T" in result


def test_pruned_block_does_not_delete_trailing_column_zero_comment() -> None:
    """A column-0 comment after a pruned block must survive.

    A struck key takes the blank and comment lines inside its own block with
        it, bounded by the block's indentation. A column-0 banner comment sits
        outside that extent and must survive.
    """
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "legend:\n  dead: true\n\n# banner\nlive: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "legend" not in result
    assert "# banner" in result
    assert "live: keep" in result


def test_pruned_block_does_not_delete_sibling_leading_comment() -> None:
    """A comment immediately after a pruned block belongs to the next key.

    When a sibling key's leading comment sits directly after the pruned
    block, the pruner must not consume it.
    """
    catalog = _orphan_parent_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("legend", "dead"))],
        catalog=catalog,
    )
    yaml_text = "legend:\n  dead: true\n# describes live\nlive: keep\n"

    result = migrate_yaml_text(yaml_text, catalog=catalog, registry=registry)

    assert "legend" not in result
    assert "# describes live" in result
    assert "live: keep" in result


def test_a_pending_move_is_validated_against_the_real_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending module's moves() are validated against the real packaged catalog.

    ``title``/``id`` are two real, structural top-level fields that are certain
    to keep existing — which is exactly what makes this Move incoherent: it
    declares a rename away from a field the target grammar still accepts.
    Recognition reads a surviving source path as proof that a board predates the
    transition, so such a declaration would migrate perfectly current boards.

    Asserting the rejection rather than the load is the honest test here.
    Immediately after a freeze ``current_schema`` equals the newest frozen
    grammar, so *no* coherent pending Move can exist against the real catalog —
    correctly, since nothing has been renamed yet. The load-and-apply property
    is covered on a synthetic boundary by
    ``test_migrates_old_shape_document_via_pending_move``, which does not
    depend on where in the freeze cycle the repo happens to sit.
    """
    import sys
    import types

    from dbt_charts.core.compile.migrations.migrations import (
        _build_board_migration_context,
    )
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        load_yaml_schema_catalog,
    )

    pending_dotted = (
        "dbt_charts.core.compile.migrations.versions."
        f"v{load_yaml_schema_catalog().dev.version.replace('.', '_')}"
    )
    fake_pending = types.ModuleType(pending_dotted)
    vars(fake_pending).update(
        moves=lambda source, target, *, catalog: (
            Move(source, target, ("title",), ("id",)),
        )
    )

    monkeypatch.setitem(sys.modules, pending_dotted, fake_pending)
    _, registry = _build_board_migration_context()

    with pytest.raises(MigrationError, match="still exists in"):
        validate_declarations(registry)


def _current_boundary_move_catalog() -> YamlSchemaCatalog:
    """V2 is latest with 'old'; the DEV entry's schema has 'new' (unreleased rename)."""
    return synthetic_catalog(
        {V1: flat_schema("ancient"), V2: flat_schema("old")},
        current=flat_schema("new"),
    )


def test_move_current_target_accepts_present_new_path() -> None:
    """A Move to the DEV version validates when the new path exists in current_schema."""
    catalog = _current_boundary_move_catalog()

    registry = checked_registry(
        [Move(V2, catalog.dev.version, ("old",), ("new",))],
        catalog=catalog,
    )

    assert registry.transition_from(V2)


def test_move_current_target_path_must_be_present_in_current_schema() -> None:
    """A Move's new path missing from current_schema must not be declared."""
    catalog = _current_boundary_move_catalog()

    with pytest.raises(MigrationError, match="absent from"):
        checked_registry(
            [Move(V2, catalog.dev.version, ("old",), ("missing",))],
            catalog=catalog,
        )


def test_move_current_source_must_be_latest_schema() -> None:
    """A Move targeting the DEV version must target the immediately succeeding schema."""
    catalog = _current_boundary_move_catalog()

    with pytest.raises(MigrationError, match="immediately succeeding"):
        checked_registry(
            [Move(V1, catalog.dev.version, ("ancient",), ("new",))],
            catalog=catalog,
        )


def test_migrates_old_shape_document_via_pending_move() -> None:
    """An old-shape document reaches the current shape through a pending Move."""
    catalog = _current_boundary_move_catalog()
    registry = MigrationRegistry(
        [Move(V2, catalog.dev.version, ("old",), ("new",))],
        catalog=catalog,
    )

    result = migrate_mapping({"old": "value"}, catalog=catalog, registry=registry)

    assert result == {"new": "value"}


# ---------------------------------------------------------------------------
# ConditionalMove primitive
# ---------------------------------------------------------------------------


def _conditional_move_catalog() -> YamlSchemaCatalog:
    """V1 has charts.*.style.tone + charts.*.support.label;
    V2 has charts.*.support.{label,tone} but NO style.tone.

    Both schemas use additionalProperties: false on the chart so that
    documents with style.tone are recognized as V1 (not V2/current),
    triggering the ConditionalMove. ``type`` carries an ``enum``, mirroring
    the real discriminated chart union (`AuthoredChart`'s per-family ``if``/
    ``then`` branches each declare ``type: {enum: [...]}``) -- the positional
    gate requires exactly that shape to tell a declared chart position from an
    open one. ``rows``/``cols`` recurse via a self-``$ref`` so the gate can
    reach a chart nested under rows -> cols -> charts, the shape real boards
    use.
    """
    chart_v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["kpi", "callout"]},
                "style": {
                    "type": "object",
                    "properties": {"tone": {"type": "string"}},
                    "additionalProperties": False,
                },
                "support": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    chart_v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["kpi", "callout"]},
                "support": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "tone": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    )
    v1: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {"type": "object", "additionalProperties": chart_v1},
                "rows": {"type": "array", "items": {"$ref": "#"}},
                "cols": {"type": "array", "items": {"$ref": "#"}},
            },
            "additionalProperties": True,
        },
    )
    v2: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {"type": "object", "additionalProperties": chart_v2},
                "rows": {"type": "array", "items": {"$ref": "#"}},
                "cols": {"type": "array", "items": {"$ref": "#"}},
            },
            "additionalProperties": True,
        },
    )
    return synthetic_catalog({V1: v1, V2: v2})


def _cond_move(catalog: YamlSchemaCatalog) -> ConditionalMove:
    return ConditionalMove(
        source_schema=V1,
        target_schema=V2,
        chart_type="kpi",
        old_tail=("style", "tone"),
        new_tail=("support", "tone"),
        sibling_tail=("support",),
        drop_warning="Dropped tone from chart {chart}",
    )


def test_conditional_move_validates_and_accepts_correctly() -> None:
    catalog = _conditional_move_catalog()
    registry = checked_registry([], [], [_cond_move(catalog)], catalog=catalog)
    assert registry.conditional_moves_from(V1)


def test_conditional_move_moves_tone_into_existing_support() -> None:
    catalog = _conditional_move_catalog()
    registry = MigrationRegistry([], [], [_cond_move(catalog)], catalog=catalog)
    raw = {
        "charts": {
            "k1": {
                "type": "kpi",
                "style": {"tone": "positive"},
                "support": {"label": "x"},
            }
        }
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(raw, catalog=catalog, registry=registry)

    chart = result["charts"]["k1"]
    assert chart["support"]["tone"] == "positive"
    assert chart["support"]["label"] == "x"
    # style.tone was the only style key → style is cleaned up
    assert "style" not in chart


def test_conditional_move_drops_and_warns_when_sibling_absent() -> None:
    catalog = _conditional_move_catalog()
    registry = MigrationRegistry([], [], [_cond_move(catalog)], catalog=catalog)
    raw = {"charts": {"k1": {"type": "kpi", "style": {"tone": "positive"}}}}

    with pytest.warns(SchemaMigrationWarning, match="k1"):
        result = migrate_mapping(raw, catalog=catalog, registry=registry)

    chart = result["charts"]["k1"]
    assert "tone" not in str(chart)
    assert "style" not in chart


def test_conditional_move_conflict_raises_when_destination_already_set() -> None:
    """Conflict: both style.tone (old) and support.tone (new) present → error.

    Tests _apply_conditional_move directly — schema recognition is not the
    behavior being exercised, only the conflict detection.
    """
    from copy import deepcopy

    from dbt_charts.core.compile.migrations.migrations import _apply_conditional_move

    catalog = _conditional_move_catalog()
    rule = _cond_move(catalog)
    document = {
        "charts": {
            "k1": {
                "type": "kpi",
                "style": {"tone": "positive"},
                "support": {"label": "x", "tone": "negative"},
            }
        }
    }
    result = deepcopy(document)

    with pytest.raises(MigrationConflictError, match="k1"):
        _apply_conditional_move(result, rule, catalog)


def test_conditional_move_skips_non_matching_chart_type() -> None:
    """A callout chart with the same style.tone path must not be touched.

    Tests _apply_conditional_move directly — the synthetic V2 schema
    purposely excludes ``style`` to force V1 recognition for KPI documents,
    but the callout test doesn't need schema round-trip.  The important
    invariant is that _apply_conditional_move_recursive only acts on dicts
    whose ``type`` equals the rule's chart_type *and* whose position the
    source grammar declares as that chart family.
    """
    from copy import deepcopy

    from dbt_charts.core.compile.migrations.migrations import _apply_conditional_move

    catalog = _conditional_move_catalog()
    rule = _cond_move(catalog)
    document = {"charts": {"c1": {"type": "callout", "style": {"tone": "warning"}}}}
    result = deepcopy(document)

    drop_warnings = _apply_conditional_move(result, rule, catalog)

    # callout's style.tone must survive untouched, no warnings emitted
    assert result["charts"]["c1"]["style"]["tone"] == "warning"
    assert not drop_warnings


def test_conditional_move_absent_source_key_is_noop() -> None:
    catalog = _conditional_move_catalog()
    registry = MigrationRegistry([], [], [_cond_move(catalog)], catalog=catalog)
    raw = {"charts": {"k1": {"type": "kpi", "support": {"label": "x"}}}}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(raw, catalog=catalog, registry=registry)

    assert result == {"charts": {"k1": {"type": "kpi", "support": {"label": "x"}}}}


def test_conditional_move_validates_wrong_adjacency_raises() -> None:
    """A ConditionalMove must target the immediately succeeding schema."""
    catalog = synthetic_catalog(
        {V1: flat_schema("a"), V2: flat_schema("a"), V3: flat_schema("a")}
    )

    with pytest.raises(MigrationError, match="immediately succeeding"):
        checked_registry(
            [],
            [],
            [
                ConditionalMove(
                    source_schema=V1,
                    target_schema=V3,
                    chart_type="kpi",
                    old_tail=("a",),
                    new_tail=("a",),
                    sibling_tail=("a",),
                    drop_warning="dropped {chart}",
                )
            ],
            catalog=catalog,
        )


def test_conditional_move_current_target_source_must_be_latest() -> None:
    """ConditionalMove targeting the DEV version must target the immediately succeeding schema."""
    catalog = _current_boundary_catalog()

    with pytest.raises(MigrationError, match="immediately succeeding"):
        checked_registry(
            [],
            [],
            [
                ConditionalMove(
                    source_schema=V1,
                    target_schema=catalog.dev.version,
                    chart_type="kpi",
                    old_tail=("ancient",),
                    new_tail=("live",),
                    sibling_tail=("live",),
                    drop_warning="dropped {chart}",
                )
            ],
            catalog=catalog,
        )


def test_conditional_move_validates_source_tail_must_exist() -> None:
    catalog = _conditional_move_catalog()

    with pytest.raises(MigrationError, match="absent from"):
        checked_registry(
            [],
            [],
            [
                ConditionalMove(
                    source_schema=V1,
                    target_schema=V2,
                    chart_type="kpi",
                    old_tail=("nonexistent", "tone"),
                    new_tail=("support", "tone"),
                    sibling_tail=("support",),
                    drop_warning="dropped {chart}",
                )
            ],
            catalog=catalog,
        )


def test_conditional_move_validates_destination_tail_must_exist() -> None:
    catalog = _conditional_move_catalog()

    with pytest.raises(MigrationError, match="absent from"):
        checked_registry(
            [],
            [],
            [
                ConditionalMove(
                    source_schema=V1,
                    target_schema=V2,
                    chart_type="kpi",
                    old_tail=("style", "tone"),
                    new_tail=("nonexistent", "tone"),
                    sibling_tail=("support",),
                    drop_warning="dropped {chart}",
                )
            ],
            catalog=catalog,
        )


def test_conditional_move_nested_board_dict_recurse() -> None:
    """ConditionalMove reaches chart dicts nested under rows/cols dicts.

    Tests _apply_conditional_move directly rather than through migrate_mapping,
    to isolate the recursive walk's reach from schema recognition -- rows and
    cols recurse via a self-$ref in the synthetic catalog (matching how real
    boards nest), but this test's document never exercises recognition at all.
    """
    from copy import deepcopy

    from dbt_charts.core.compile.migrations.migrations import _apply_conditional_move

    catalog = _conditional_move_catalog()
    rule = _cond_move(catalog)
    document = {
        "rows": [
            {
                "cols": [
                    {
                        "charts": {
                            "nested_kpi": {
                                "type": "kpi",
                                "style": {"tone": "positive"},
                                "support": {"label": "x"},
                            }
                        }
                    }
                ]
            }
        ]
    }
    result = deepcopy(document)

    drop_warnings = _apply_conditional_move(result, rule, catalog)

    chart = result["rows"][0]["cols"][0]["charts"]["nested_kpi"]
    assert chart["support"]["tone"] == "positive"
    assert "style" not in chart
    assert not drop_warnings


def test_conditional_moves_from_returns_empty_for_unknown_schema() -> None:
    catalog = _conditional_move_catalog()
    registry = MigrationRegistry([], [], [_cond_move(catalog)], catalog=catalog)
    assert registry.conditional_moves_from("9.9.9") == ()


def test_version_module_with_only_conditional_moves_is_accepted() -> None:
    """A version module with only conditional_moves() but no moves/deletions is valid."""
    import sys
    import types

    from dbt_charts.core.compile.migrations.migrations import _load_migration_module

    dotted = "dbt_charts.core.compile.migrations.versions.v9_9_9"
    fake = types.ModuleType(dotted)
    fake.conditional_moves = lambda *a, **kw: ()  # type: ignore[attr-defined]

    sys.modules[dotted] = fake
    try:
        module = _load_migration_module(dotted)
        assert module is fake
    finally:
        del sys.modules[dotted]


# ---------------------------------------------------------------------------
# Pending deletions regression: marks.square / marks.tick / marks.trail
# ---------------------------------------------------------------------------


def _marks_boundary_catalog() -> YamlSchemaCatalog:
    """V2 is latest with marks.square/tick/trail; current_schema has only marks.rule.

    Mirrors the actual pending deletion: GlobalMarksStyle removed the three dead
    mark slots, so a board that authored marks.tick (etc.) must be migrated.
    """
    marks_with_dead: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "square": {"type": "object", "additionalProperties": True},
                "tick": {"type": "object", "additionalProperties": True},
                "trail": {"type": "object", "additionalProperties": True},
                "rule": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
    )
    marks_without_dead: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"rule": {"type": "object", "additionalProperties": True}},
            "additionalProperties": False,
        },
    )
    latest_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"marks": marks_with_dead},
            "additionalProperties": True,
        },
    )
    current_schema: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"marks": marks_without_dead},
            "additionalProperties": True,
        },
    )
    return synthetic_catalog(
        {V1: flat_schema("ancient"), V2: latest_schema}, current=current_schema
    )


def test_marks_dead_slots_pending_deletions_strip_their_keys() -> None:
    """marks.square/tick/trail in DELETED_TAILS strip those keys during migration.

    Regression guard: removing any of the three entries from DELETED_TAILS causes
    this test to fail — either the assertion or the migrate_mapping check.
    """
    from dbt_charts.core.compile.migrations.versions.v0_5_0 import DELETED_TAILS

    marks_deletions = {t for t in DELETED_TAILS if t[0] == "marks"}
    assert marks_deletions == {
        ("marks", "square"),
        ("marks", "tick"),
        ("marks", "trail"),
    }

    catalog = _marks_boundary_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V2, catalog.dev.version, tail) for tail in sorted(marks_deletions)],
        catalog=catalog,
    )

    result = migrate_mapping(
        {
            "marks": {
                "tick": {"stroke": {}},
                "square": {"opacity": 0.5},
                "trail": {},
                "rule": {"stroke": {}},
            }
        },
        catalog=catalog,
        registry=registry,
    )

    assert result == {"marks": {"rule": {"stroke": {}}}}


def test_relative_field_paths_does_not_recompute_shared_subtrees() -> None:
    """`_relative_field_paths` must memoize instead of re-walking per branch.

    A prior implementation threaded ``seen`` per branch rather than
    memoizing, so a shared model (``FontStyle``, a style-patch tree, every
    chart family's own fields, ...) was re-walked once per distinct path
    reaching it, and board self-nesting (``rows``/``cols``/``tabs.items``,
    all mutually reachable) multiplied the path count combinatorially --
    a 20s+ cold ``_board_migration_context()`` build and four tests blowing
    their 30s pytest-timeout inside the walk. A wall-clock assertion here
    would be flaky under load, so pin the fix with a call-count assertion
    instead: ``_relative_field_paths`` memoizes on ``(model, seen, tails)``,
    so the number of distinct subproblems it actually computes is small and
    bounded (in the hundreds) regardless of how many absolute paths the
    walk ultimately yields. Both bounds matter: a regression back to
    per-branch recomputation would blow the upper one by orders of
    magnitude, and a regression that stopped routing through the memo
    entirely (e.g. a rewrite that inlines the walk) would pass a
    misses-only-upper-bound check vacuously at 0 -- the lower bound catches
    that.
    """
    from dbt_charts.core.compile.migrations.migrations import (
        _closure,
        _relative_field_paths,
    )
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    _relative_field_paths.cache_clear()
    _closure.cache_clear()

    _relative_field_paths(AuthoredBoard, frozenset(), frozenset({("notes",)}))

    misses = _relative_field_paths.cache_info().misses
    assert 0 < misses < 2000, (
        f"_relative_field_paths computed {misses} distinct (model, seen, tails) "
        "subproblems -- expected each reachable model to be walked once, "
        "not re-derived per branch (and not zero, which would mean the walk "
        "stopped routing through the memo at all)"
    )


# ============================================================================
# Deletion.chart_type — a chart_type-scoped deletion.
# Minimal synthetic fixture, independent of the real conditional_formatting
# sweep: `some_field` is retired from `bar`/`callout` but stays on `table`.
# ============================================================================


def _scoped_deletion_chart(types: list[str], *, has_field: bool) -> JsonObject:
    properties: dict[str, object] = {"type": {"type": "string", "enum": types}}
    if has_field:
        properties["some_field"] = {"type": "string"}
    return cast(
        JsonObject,
        {"type": "object", "properties": properties, "additionalProperties": False},
    )


def _scoped_deletion_board(chart_schemas: list[JsonObject]) -> JsonObject:
    return cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "charts": {
                    "type": "object",
                    "additionalProperties": {"anyOf": chart_schemas},
                },
                "rows": {"type": "array", "items": {"$ref": "#"}},
            },
            "additionalProperties": True,
        },
    )


def _scoped_deletion_catalog() -> YamlSchemaCatalog:
    v1 = _scoped_deletion_board(
        [
            _scoped_deletion_chart(["bar"], has_field=True),
            _scoped_deletion_chart(["callout"], has_field=True),
            _scoped_deletion_chart(["table"], has_field=True),
        ]
    )
    v2 = _scoped_deletion_board(
        [
            _scoped_deletion_chart(["bar"], has_field=False),
            _scoped_deletion_chart(["callout"], has_field=False),
            _scoped_deletion_chart(["table"], has_field=True),
        ]
    )
    return synthetic_catalog({V1: v1, V2: v2})


def test_chart_type_scoped_deletion_validates_against_only_its_own_branch() -> None:
    """A global existence check would wrongly reject this: `some_field` still
    exists on `table`'s branch in V2, but a `chart_type="bar"`-scoped
    Deletion only needs it gone from bar's own branch, which it is."""
    catalog = _scoped_deletion_catalog()
    checked_registry(
        [],
        [Deletion(V1, V2, ("some_field",), chart_type="bar")],
        catalog=catalog,
    )  # must not raise


def test_chart_type_scoped_deletion_still_raises_if_not_actually_removed() -> None:
    catalog = _scoped_deletion_catalog()
    with pytest.raises(MigrationError, match="still exists"):
        checked_registry(
            [],
            [Deletion(V1, V2, ("some_field",), chart_type="table")],
            catalog=catalog,
        )


def test_apply_deletions_strips_only_the_scoped_chart_type() -> None:
    """_apply_deletions / _delete_tails_recursive consumer."""
    catalog = _scoped_deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("some_field",), chart_type="bar")],
        catalog=catalog,
    )
    raw = {
        "charts": {
            "b": {"type": "bar", "some_field": "x"},
            "t": {"type": "table", "some_field": "y"},
        }
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = migrate_mapping(raw, catalog=catalog, registry=registry)
    assert "some_field" not in result["charts"]["b"]
    assert result["charts"]["t"]["some_field"] == "y"


def test_two_chart_type_scoped_deletions_sharing_a_path_both_fire_with_their_own_reason() -> (
    None
):
    """Regression for the reasons-dict/tails-list flattening bug: two Deletions
    sharing the identical path but different chart_type must not collide.
    Before threading Deletion objects through, _apply_deletions built
    ``reasons = {deletion.path: deletion.reason for deletion in deletions if
    deletion.reason}`` and ``tails = [deletion.path for deletion in
    deletions]`` — both keyed on the bare path, so N same-path Deletions
    collapsed to one arbitrary reason (dict-key collision) and a flattened,
    chart_type-blind tails list. A single-entry test would pass even with
    that bug present; this uses two.
    """
    catalog = _scoped_deletion_catalog()
    registry = MigrationRegistry(
        [],
        [
            Deletion(V1, V2, ("some_field",), chart_type="bar", reason="bar reason"),
            Deletion(
                V1, V2, ("some_field",), chart_type="callout", reason="callout reason"
            ),
        ],
        catalog=catalog,
    )
    raw = {
        "charts": {
            "b": {"type": "bar", "some_field": "x"},
            "c": {"type": "callout", "some_field": "z"},
            "t": {"type": "table", "some_field": "y"},
        }
    }
    with pytest.warns(SchemaMigrationWarning) as caught:
        result = migrate_mapping(raw, catalog=catalog, registry=registry)
    assert "some_field" not in result["charts"]["b"]
    assert "some_field" not in result["charts"]["c"]
    assert result["charts"]["t"]["some_field"] == "y"
    messages = [str(w.message) for w in caught.list]
    assert any("bar reason" in m for m in messages), messages
    assert any("callout reason" in m for m in messages), messages


def test_deletion_would_fire_respects_chart_type_scope() -> None:
    """_deletion_would_fire consumer (the read-only recognition mirror)."""
    from dbt_charts.core.compile.migrations.migrations import _deletion_would_fire

    catalog = _scoped_deletion_catalog()
    v1_schema = catalog.schema_for(V1)
    live = catalog.current_schema
    bar_deletion = Deletion(V1, V2, ("some_field",), chart_type="bar")

    table_only: JsonObject = cast(
        JsonObject, {"charts": {"t": {"type": "table", "some_field": "y"}}}
    )
    assert not _deletion_would_fire(
        table_only, [bar_deletion], v1_schema, [v1_schema], live, [live]
    ), "a bar-scoped Deletion must not fire on table's own (still-valid) field"

    with_bar: JsonObject = cast(
        JsonObject, {"charts": {"b": {"type": "bar", "some_field": "x"}}}
    )
    assert _deletion_would_fire(
        with_bar, [bar_deletion], v1_schema, [v1_schema], live, [live]
    )


def test_yaml_text_rewrite_replays_only_the_paths_the_walk_struck() -> None:
    """The dct migrate text rewriter, driven by the in-memory result.

    A quoted `type: "bar"` and a trailing comment on it are not the text
    layer's problem: it replays the paths `_apply_deletions` struck, and those
    come from the parsed document. The two real-board shapes (an inline list
    item under `rows:`, a `charts:` mapping entry) round-trip against the live
    grammar in test_conditional_formatting_deletion_migration.py.
    """
    catalog = _scoped_deletion_catalog()
    registry = MigrationRegistry(
        [],
        [Deletion(V1, V2, ("some_field",), chart_type="bar")],
        catalog=catalog,
    )
    text = (
        "charts:\n"
        '  b:\n    type: "bar"  # my chart\n    some_field: x\n'
        "  t:\n    type: table\n    some_field: y\n"
    )

    result = migrate_yaml_text(text, catalog=catalog, registry=registry)

    assert "some_field: x" not in result
    assert "some_field: y" in result


def test_yaml_text_rewrite_leaves_a_block_scalar_alone() -> None:
    """A `text: |` block scalar whose content reads like a key line.

    The rewriter resolves paths through the YAML node tree, so prose inside a
    block scalar is never a key to it: the line survives and the real key is
    struck.
    """
    old: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {
                "dead": {"type": "boolean"},
                "text": {"type": "string"},
                "live": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    new: JsonObject = cast(
        JsonObject,
        {
            "type": "object",
            "properties": {"text": {"type": "string"}, "live": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    catalog = synthetic_catalog({V1: old, V2: new})
    registry = MigrationRegistry([], [Deletion(V1, V2, ("dead",))], catalog=catalog)

    result = migrate_yaml_text(
        "dead: true\ntext: |\n    dead: true\n    Prose after.\nlive: keep\n",
        catalog=catalog,
        registry=registry,
    )

    assert result.startswith("text: |")
    assert "    dead: true" in result
    assert "live: keep" in result


# ---------------------------------------------------------------------------
# DEV-version boundary invariants
# ---------------------------------------------------------------------------


def test_enforce_support_window_raises_for_the_dev_version() -> None:
    """_enforce_support_window must never treat the DEV version as never-expiring.

    Unreachable through migrate_mapping in practice -- the DEV early return
    and the latest-released exemption both short-circuit before this
    function would ever see it -- so this pins the invariant directly rather
    than proving it via a live call path. A ``released_at is None`` entry
    reaching here means one of those guards broke.
    """
    from dbt_charts.core.compile.migrations.migrations import (
        _enforce_support_window,
    )

    catalog = synthetic_catalog({V1: flat_schema("a"), V2: flat_schema("a")})

    with pytest.raises(MigrationError):
        _enforce_support_window(catalog.dev.version, catalog, today=date.today())


def test_enforce_support_window_raises_for_an_unretained_identifier() -> None:
    """A schema identifier absent from the catalog must raise, not StopIteration."""
    from dbt_charts.core.compile.migrations.migrations import (
        _enforce_support_window,
    )

    catalog = synthetic_catalog({V1: flat_schema("a"), V2: flat_schema("a")})

    with pytest.raises(MigrationError):
        _enforce_support_window("9.9.9", catalog, today=date.today())


def test_recognize_never_probes_the_dev_version_as_a_candidate_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_recognize's probe loop must skip the DEV version outright.

    Nothing sources a transition *from* the DEV version -- it is always the
    newest entry, with no successor to migrate into -- so probing it can
    never yield a verdict. Pinned by making that probe fatal rather than by
    finding a document it would wrongly match today: the appliers are
    positionally gated, so such a document may not exist, and a guard that
    depends on one being found stops guarding the moment they get safer.
    """
    from dbt_charts.core.compile.migrations import migrations as _impl

    catalog = synthetic_catalog({V1: flat_schema("a"), V2: flat_schema("b")})
    registry = MigrationRegistry([], catalog=catalog)

    def _fatal_on_dev(
        mapping: object, identifier: str, catalog: YamlSchemaCatalog, registry: object
    ) -> bool:
        if identifier == catalog.dev.version:
            raise AssertionError("probed the DEV version as a transition source")
        return False

    monkeypatch.setattr(_impl, "_transition_applies", _fatal_on_dev)

    with pytest.raises(UnsupportedSchemaError):
        _impl._recognize({"nonexistent": "value"}, catalog, registry)


def test_synthetic_catalog_raises_when_computed_dev_name_collides() -> None:
    """The computed DEV name must not already be a version the caller gave.

    Silently resolving it would let a version the caller meant to keep
    retained (with its own frozen schema) quietly answer ``schema_for`` with
    the live DEV grammar instead.
    """
    assert next_minor("0.6.1") == "0.7.0"
    with pytest.raises(ValueError, match="collides"):
        synthetic_catalog({"0.7.0": flat_schema("a"), "0.6.1": flat_schema("a")})


def test_retired_theme_renames_dev_entry_wins_key_collisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key both the DEV module and an older frozen module remap must
    resolve to the DEV module's target -- the newest declaration in the
    chain wins any collision. Unreachable with the real ``THEME_RENAMES``
    tables today (the current DEV module's table is empty), so this pins the
    precedence with faked modules swapped into ``sys.modules`` for the real,
    newest two catalog boundaries.
    """
    import sys
    import types

    from dbt_charts.core.compile.migrations.migrations import retired_theme_renames
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        load_yaml_schema_catalog,
    )

    catalog = load_yaml_schema_catalog()
    dev_dotted = (
        "dbt_charts.core.compile.migrations.versions."
        f"v{catalog.dev.version.replace('.', '_')}"
    )
    frozen_dotted = (
        "dbt_charts.core.compile.migrations.versions."
        f"v{catalog.latest_released.version.replace('.', '_')}"
    )

    fake_dev = types.ModuleType(dev_dotted)
    vars(fake_dev).update(
        moves=lambda source, target, *, catalog: (),
        THEME_RENAMES={"retired": "dev-target"},
    )
    fake_frozen = types.ModuleType(frozen_dotted)
    vars(fake_frozen).update(
        moves=lambda source, target, *, catalog: (),
        THEME_RENAMES={"retired": "frozen-target"},
    )

    monkeypatch.setitem(sys.modules, dev_dotted, fake_dev)
    monkeypatch.setitem(sys.modules, frozen_dotted, fake_frozen)
    retired_theme_renames.cache_clear()
    try:
        assert retired_theme_renames()["retired"] == "dev-target"
    finally:
        retired_theme_renames.cache_clear()
