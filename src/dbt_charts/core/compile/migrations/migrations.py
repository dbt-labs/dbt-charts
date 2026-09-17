"""Recognize retained board schemas and apply declared structural migrations."""

from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import re
import types
import warnings
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from functools import cache
from typing import TYPE_CHECKING, Annotated, TypeAlias, cast, get_args, get_origin

import yaml
from importlib_resources import files
from jsonschema import Draft7Validator
from pydantic import BaseModel, ValidationError

from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    JsonValue,
    YamlSchemaCatalog,
    parse_dotted_version,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.authoring.yaml_patch import ScalarLeaf

YamlKeyPath: TypeAlias = tuple[str, ...]

# Where a key actually sits in one document, sequence indices included --
# unlike `YamlKeyPath`, which is the grammar-level tail a `Deletion` declares.
# Segments, never a dotted string: a chart id an author chose may contain a
# dot, and joining would let `charts.a.style.color` also name a chart called
# `a.style.color`.
DocumentPath: TypeAlias = tuple[str | int, ...]

# What a value map may map. Narrower than the setter's `ScalarLeaf`, which grew
# a `list[str]` arm for multi-value controls: a rename maps one spelling of a
# scalar to another, and `_mapped_value` refuses anything else at runtime.
MappedScalar: TypeAlias = str | int | float | bool


class MigrationError(ValueError):
    """A raw YAML migration cannot be safely applied."""


class MigrationConflictError(MigrationError):
    """A move would overwrite an authored destination value."""


class UnsupportedSchemaError(MigrationError):
    """The mapping matches none of the retained grammars."""


class IncompleteMigrationError(MigrationError):
    """A recognized grammar's transitions ran but did not reach the current one.

    A sibling of ``UnsupportedSchemaError`` rather than a subclass: to a caller
    willing to fall back on the parser the two mean opposite things. An
    unsupported mapping was never old and has no skipped migration to report;
    this one demonstrably was old and is being handed on unmodernized.
    """


class SchemaVersionTooOldError(MigrationError):
    """Transparent loading no longer supports the recognized schema."""


class SchemaMigrationWarning(UserWarning):
    """A board was migrated in memory and should be rewritten with ``dct migrate``."""


@dataclasses.dataclass(frozen=True)
class Move:
    """One resolved adjacent structural rename or move."""

    source_schema: str
    target_schema: str
    old_path: YamlKeyPath
    new_path: YamlKeyPath
    # When set, the source value is looked up here before assignment; raises
    # MigrationError for any value absent from the map that actually reaches
    # `_mapped_value` (never passes unmapped values through silently). For an
    # identity-path Move (old_path == new_path), an absent-from-map value
    # never reaches that raise at all -- `move_source_locations`'
    # `_identity_value_would_change` gate leaves it untouched upstream
    # instead. Use only for total, lossless value mappings.
    # Mapping isn't hashable -- excluded from __hash__, kept in __eq__.
    #
    # old_path == new_path is a legal, distinct shape: a value-only remap on a
    # key that never disappears from any grammar (dbt charts' `theme:` sugar,
    # whose legal values narrowed without the key itself being renamed away).
    # `validate_declarations` requires a value_map on that shape -- an identity Move with
    # none would be a silent no-op.
    value_map: Mapping[MappedScalar, MappedScalar] | None = dataclasses.field(
        default=None, hash=False
    )


@dataclasses.dataclass(frozen=True)
class Deletion:
    """One resolved adjacent structural deletion — a path that existed in source but not target."""

    source_schema: str
    target_schema: str
    path: YamlKeyPath
    # Most deletions are mechanical (dead keys nothing ever read) and need no
    # explanation. Set this only when the field was removed for a reason the
    # author benefits from hearing, surfaced alongside the tail that actually
    # fired -- unlike `ConditionalMove.drop_warning`, which is required
    # because every firing there is unrecoverable data loss with nowhere to
    # land the value, a bare deletion is routine enough that a required field
    # would invite filler.
    reason: str | None = None
    # None = the deletion applies wherever the grammar declares the path, same
    # as before this field existed. Set to a chart `type:` literal to scope a
    # deletion to one family of a discriminated chart union while the same
    # path stays valid on other families (e.g. `conditional_formatting`
    # retired from `bar` but kept on `table`/`kpi`) -- mirrors
    # `ConditionalMove.chart_type`'s scoping (`_declares_chart_type`), but for
    # a deletion instead of a relocation. Threaded through both walks
    # (`_delete_tails_recursive`, `_deletion_would_fire`) and
    # `validate_declarations`'s existence checks -- see each for how the
    # scoping is enforced there. The text rewriter needs no scope of its own:
    # it replays the paths the walk struck.
    chart_type: str | None = None


@dataclasses.dataclass(frozen=True)
class ConditionalMove:
    """A relocation that fires only when its destination's parent mapping already exists.

    Restricted to charts whose ``type`` key equals ``chart_type`` *and* whose
    document position is a declared chart of that family in the source
    grammar (``_declares_chart_type``), so a same-named field on an unrelated
    chart family (e.g. callout's own ``style.tone``) is never touched, and
    nor is a free-form value that merely happens to carry a matching
    ``type:`` key (``Variable.default``, a query data row, ...).  ``old_tail``,
    ``new_tail``, and ``sibling_tail`` are relative to the chart mapping
    itself (not the full document root) so the rule applies uniformly
    regardless of nesting depth under rows/cols/grid/tabs.

    When the sibling at ``sibling_tail`` does not exist in the chart, the
    value at ``old_tail`` is dropped (not migrated) and a warning is emitted,
    naming the chart via the ``{chart}`` placeholder in ``drop_warning``.
    """

    source_schema: str
    target_schema: str
    chart_type: str
    old_tail: YamlKeyPath
    new_tail: YamlKeyPath
    sibling_tail: YamlKeyPath
    drop_warning: str  # may use ``{chart}`` placeholder


class MigrationRegistry:
    """Adjacent transitions assembled from tail-rename tables.

    Construction is total: it indexes the declarations it is handed and checks
    nothing. Whether those declarations are *coherent* against ``catalog`` is
    fixed by the shipped code, so the check for it is
    ``validate_declarations`` in ``tests/core/compile/_migration_declarations``,
    run by CI rather than by every process that loads a board.
    """

    def __init__(
        self,
        moves: Iterable[Move],
        deletions: Iterable[Deletion] = (),
        conditional_moves: Iterable[ConditionalMove] = (),
        *,
        catalog: YamlSchemaCatalog,
    ) -> None:
        self.moves = tuple(moves)
        self.deletions = tuple(deletions)
        self.conditional_moves = tuple(conditional_moves)
        self.catalog = catalog
        self._moves_by_source: dict[str, tuple[Move, ...]] = {}
        for move in self.moves:
            moves_for_schema = self._moves_by_source.setdefault(move.source_schema, ())
            self._moves_by_source[move.source_schema] = (*moves_for_schema, move)
        self._deletions_by_source: dict[str, tuple[Deletion, ...]] = {}
        for deletion in self.deletions:
            dels_for_schema = self._deletions_by_source.setdefault(
                deletion.source_schema, ()
            )
            self._deletions_by_source[deletion.source_schema] = (
                *dels_for_schema,
                deletion,
            )
        self._cond_moves_by_source: dict[str, tuple[ConditionalMove, ...]] = {}
        for cond_move in self.conditional_moves:
            existing = self._cond_moves_by_source.setdefault(
                cond_move.source_schema, ()
            )
            self._cond_moves_by_source[cond_move.source_schema] = (
                *existing,
                cond_move,
            )

    def transition_from(self, identifier: str) -> tuple[Move, ...]:
        if identifier not in self._moves_by_source:
            return ()
        return self._moves_by_source[identifier]

    def deletions_from(self, identifier: str) -> tuple[Deletion, ...]:
        if identifier not in self._deletions_by_source:
            return ()
        return self._deletions_by_source[identifier]

    def conditional_moves_from(self, identifier: str) -> tuple[ConditionalMove, ...]:
        if identifier not in self._cond_moves_by_source:
            return ()
        return self._cond_moves_by_source[identifier]


def suffix_rename_moves(
    root_model: type[BaseModel],
    source_schema: str,
    target_schema: str,
    renames: Iterable[tuple[YamlKeyPath, YamlKeyPath]],
    *,
    catalog: YamlSchemaCatalog,
    value_map: Mapping[MappedScalar, MappedScalar] | None = None,
) -> tuple[Move, ...]:
    """Resolve tail-only path renames to Move objects without constructing a registry.

    ``renames`` pairs a *new-path tail* with the corresponding *old-path tail*;
    this walks every occurrence of the new tail in the field tree and substitutes
    the old tail at that same position, keeping the shared prefix. Candidates
    that don't structurally exist in both frozen schemas are silently dropped —
    that keeps this safe to run over the whole field tree without hand-enumerating
    every real position.

    Walk the field tree once and reuse it for every rename tuple — re-walking per
    rename is O(renames * tree_size) and the per-variant axis/scale models made
    tree_size large enough for that to matter.

    ``value_map``, if given, is attached to every ``Move`` this call produces —
    it broadcasts across all of ``renames``, not per-tuple. A rename that needs
    its own value mapping belongs in a separate ``suffix_rename_moves`` call.
    """
    old_schema = catalog.schema_for(source_schema)
    new_schema = catalog.schema_for(target_schema)
    renames = tuple(renames)
    # `_relative_field_paths` filters to these tails *during* the walk rather
    # than after materializing every field path -- see its docstring. That's
    # what keeps this cheap: AuthoredBoard's unfiltered path set is orders of
    # magnitude larger than the handful a rename call actually matches.
    tails = frozenset(new_tail for new_tail, _old_tail in renames)
    try:
        # Dedupe up front: the walk legitimately re-derives the same absolute
        # path from different branches (e.g. a type reachable through more
        # than one union arm at the same position), and every rename tuple
        # below would otherwise re-scan those duplicates for no benefit --
        # Move derivation already dedupes by (old_path, path) regardless.
        paths = list(
            dict.fromkeys(_relative_field_paths(root_model, frozenset(), tails))
        )
        seen: set[tuple[YamlKeyPath, YamlKeyPath]] = set()
        result: list[Move] = []
        for new_tail, old_tail in renames:
            width = len(new_tail)
            for path in paths:
                if len(path) < width or path[-width:] != new_tail:
                    continue
                old_path = path[:-width] + old_tail
                key = (old_path, path)
                if key in seen:
                    continue
                seen.add(key)
                if _schema_path_exists(old_schema, old_path) and _schema_path_exists(
                    new_schema, path
                ):
                    result.append(
                        Move(source_schema, target_schema, old_path, path, value_map)
                    )
        return tuple(result)
    finally:
        # `_relative_field_paths` is `@cache`d to avoid re-walking a shared
        # subtree once per branch within *this* call (see its docstring) --
        # not to persist across calls. Left uncleared, its memo pinned every
        # distinct (model, seen) subproblem it had ever computed for the life
        # of the process -- hundreds of megabytes after a single
        # `_board_migration_context()` build, for a rename that needed a
        # vanishing fraction of what it produced. Clearing here bounds the
        # retention to zero between calls
        # while keeping the in-call memoization that avoids the combinatorial
        # blowup board self-nesting (rows/cols/tabs, all mutually reachable)
        # would otherwise cause.
        _relative_field_paths.cache_clear()


def _apply_identity_moves(
    mapping: Mapping[str, JsonValue],
    registry: MigrationRegistry,
    catalog: YamlSchemaCatalog,
) -> JsonObject:
    """Apply every declared identity-path Move's value-only rename.

    An identity-path Move (``old_path == new_path`` — today, only dbt
    charts' ``theme:`` retired-name rename) has no structural signal the
    rest of this module's recognition machinery can act on: the key it
    touches never disappears from any grammar, so ``_recognize``'s
    JSON-Schema-diff check (``_current_schema_rejections``) can never see
    one as evidence a document is old. Applied directly here instead,
    bypassing ``_recognize``'s currency check entirely (see
    ``prepare_board_mapping``'s cheap ``_theme_value_needs_recheck`` filter
    for when this function is even called), gated purely by whether
    ``move_source_locations``' value gate (``_identity_value_would_change``)
    finds something the value_map would actually rename — a current name or
    a value the map has never heard of is left completely untouched.

    ``extends:`` gets the identical value-only rename but is deliberately
    NOT declared as a Move here: unlike ``theme:``, a plain ``extends:``
    string is genuinely ambiguous with a real project board of the same
    name, so it cannot be rewritten unconditionally on the raw mapping the
    way this function does. See ``merge.py``'s ``_retired_theme_redirect``
    and the module docstring in ``versions/v0_6_0.py``.
    """
    mapping_dict: JsonObject = dict(mapping)
    identity_moves = [move for move in registry.moves if move.old_path == move.new_path]
    firing = [
        move
        for move in identity_moves
        if any(move_source_locations(mapping_dict, move, catalog))
    ]
    if not firing:
        return mapping_dict
    result = copy.deepcopy(mapping_dict)
    for move in firing:
        _apply_move(result, move, catalog)
    warnings.warn(
        "dbt charts migrated this YAML in memory; `dct migrate` may be able "
        "to update the file.",
        SchemaMigrationWarning,
        stacklevel=3,
    )
    return result


def _theme_value_needs_recheck(mapping: Mapping[str, JsonValue]) -> bool:
    """Cheap pre-filter: might a literal ``theme:`` value need an
    identity-path Move, without building the migration registry to find out?

    Restricted to the exact shape a retired/renamed built-in theme name
    takes: a plain string not already a current ``ThemeName`` (checked
    against the generated Literal directly — no registry, no catalog).
    Over-triggering here (a path ref, a garbage value) only costs one extra,
    `@cache`d ``_board_migration_context()`` call: the real precision comes
    from ``move_source_locations``' value gate inside
    ``_apply_identity_moves``, not from this filter. Under-triggering would
    be the actual bug — this must never return False for a genuinely
    retired name.
    """
    from dbt_charts.core.compile.models.schema_names import ThemeName

    theme_value = mapping.get("theme")
    return isinstance(theme_value, str) and theme_value not in get_args(ThemeName)


def prepare_board_mapping(
    mapping: Mapping[str, JsonValue],
    *,
    model: type[BaseModel] | None = None,
    allow_expired: bool = False,
) -> JsonObject:
    """Recognize and migrate a raw board-shaped mapping before model validation.

    ``model`` is the currency check: does this mapping already validate
    against the current schema, so migration can be skipped? Defaults to
    ``AuthoredBoard`` for a real, standalone board. Callers handling a
    patch-shaped fragment (a theme YAML's ``style:``-only content, a
    meta.yml override, an extends target) must pass ``BoardPatch`` —
    ``AuthoredBoard`` carries a "must have layout/chart/text/title/notes"
    invariant that no patch can ever satisfy, which would make every current
    patch look like a migration candidate.

    Identity-path Moves are applied first, gated by the cheap
    ``_theme_value_needs_recheck`` filter so a board with no retired ``theme:``
    value never pays for building the migration registry
    (``test_prepare_mapping_current_schema_patch_takes_fast_path_under_boardpatch``
    pins that) — see ``_apply_identity_moves``'s docstring for why this shape
    cannot go through the currency check below at all. Every other declared
    Move, Deletion, and ConditionalMove is a genuine grammar transition: a
    retired *field* always fails pydantic too (``extra="forbid"``), so the
    currency check below reliably routes it into ``migrate_mapping``, which
    lazily builds the same (``@cache``d) registry.
    """
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    currency_model = model if model is not None else AuthoredBoard

    if _theme_value_needs_recheck(mapping):
        catalog, registry = _board_migration_context()
        mapping = _apply_identity_moves(mapping, registry, catalog)

    try:
        currency_model.model_validate(mapping)
    except ValidationError:
        if not _board_has_historical_schemas():
            return dict(mapping)
    else:
        return dict(mapping)

    catalog, registry = _board_migration_context()
    try:
        return migrate_mapping(
            mapping,
            catalog=catalog,
            registry=registry,
            allow_expired=allow_expired,
        )
    except UnsupportedSchemaError:
        # No boundary matched and the mapping does not satisfy the live schema
        # either. Usually that means it was never old and there is no skipped
        # migration to report — but not always: a deletion or conditional move
        # the positional gate refuses can leave a genuinely old document
        # matching nothing, and it arrives here too. The parser owns the
        # diagnostic either way.
        return dict(mapping)
    except SchemaVersionTooOldError as error:
        # Old enough that transparent migration is not offered at all — no
        # migration was attempted, so "could not finish" would misdescribe it.
        warnings.warn(
            f"dbt charts did not migrate this YAML. {error} Reporting it against "
            "the current schema instead — errors below may name retired syntax "
            "rather than anything you just changed.",
            SchemaMigrationWarning,
            stacklevel=2,
        )
        return dict(mapping)
    except MigrationError as error:
        # The document is old and the migration could not finish: the schema
        # gate rejected the result (Pydantic admits authoring shorthands JSON
        # Schema cannot express), two spellings of a renamed key collided, or a
        # value would not map.
        #
        # Every one of these ends the same way — hand the original to the parser
        # and let it produce a located diagnostic. What must never happen is
        # raising past here: this is the face-loading path, so an escaping
        # migration error is a traceback out of `dct validate` for an author
        # who is mid-migration, which is precisely who this machinery serves.
        # Nor may it be silent: an unannounced skip is why a face that had
        # merely gone stale failed with errors naming fields nobody touched.
        warnings.warn(
            f"dbt charts could not finish migrating this YAML. {error} "
            "Reporting it against the current schema instead — errors below may "
            "name retired syntax rather than anything you just changed.",
            SchemaMigrationWarning,
            stacklevel=2,
        )
        return dict(mapping)


def migrate_board_yaml_text(yaml_text: str) -> str:
    """Rewrite one retained board grammar to the latest frozen (released) grammar.

    Capped at ``catalog.latest_released.version``, never the live schema: this
    is the on-disk rewrite path (``dct migrate``), so it must never write
    syntax no released dbt charts recognizes yet. See ``migrate_yaml_text``'s
    ``stop_target`` docstring.
    """
    catalog, registry = _board_migration_context()
    return migrate_yaml_text(
        yaml_text,
        catalog=catalog,
        registry=registry,
        stop_target=catalog.latest_released.version,
    )


@cache
def _board_migration_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    """Load the package's immutable migration metadata once.

    Walks catalog.entries newest-to-oldest, skipping the oldest (predecessor
    is None). For each remaining entry, imports the corresponding version module
    from ``migrations.versions``. A missing module means no structural migration
    was declared for that boundary — not an error. Each found module's
    ``moves()``, ``deletions()``, and/or ``conditional_moves()`` functions own
    the migration strategy; the collector stays agnostic to the mechanism.

    The walk covers the DEV entry too — it is an ordinary ``catalog.entries``
    member with its own ``predecessor``, so no separate pending-module load is
    needed. Its module (named for the DEV version, e.g. ``versions/v0_7_0.py``)
    declares the ``catalog.latest_released.version → catalog.dev.version``
    boundary for any unreleased renames, key removals, or conditional
    relocations in flight; a missing module there means no grammar change is
    pending yet, same as any other boundary.

    ``catalog.current_schema`` is a fully computed live schema (see
    ``YamlSchemaCatalog``), generated the same way as any frozen snapshot, so
    the DEV boundary's declarations resolve against it exactly as a frozen
    boundary's resolve against a snapshot.

    The ``finally`` clause drops only what this build left in
    ``_schema_branches_cached`` (~1.7k entries, reached through the version
    modules' own ``moves()`` via ``suffix_rename_moves``). It does not bound
    that cache. Document migration re-enters it through the callers listed on
    ``_schema_branches`` and never clears it, and one of them
    (``_plausible_positions`` → ``_without_composition``) hands it a fresh
    dict per call, so those entries accumulate per board migrated -- measured
    at ~+59 each. That is a separate leak, untouched here.
    """
    try:
        return _build_board_migration_context()
    finally:
        _schema_branches_cached.cache_clear()


@cache
def retired_theme_renames() -> dict[MappedScalar, MappedScalar]:
    """Merge every version module's ``THEME_RENAMES`` table, frozen and pending.

    ``extends: <retired-theme-name>`` (``merge.py``'s ``_retired_theme_redirect``)
    needs a retired theme name to keep resolving forever, the same as
    ``theme:``'s Move-based rename already does via ``_apply_identity_moves`` --
    so this walks the same ``catalog.entries`` ``_build_board_migration_context``
    does -- the DEV entry included, an ordinary member with its own
    predecessor -- merging each module's declared table instead of building
    ``Move``/``Deletion``/``ConditionalMove`` objects.

    Walked oldest-first (``catalog.entries`` is newest-first) so a newer
    module's table overrides an older one on a key collision, ending with
    the DEV entry applied last -- the newest declaration always wins, never
    the oldest.
    """
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        load_yaml_schema_catalog,
    )

    catalog = load_yaml_schema_catalog()
    combined: dict[MappedScalar, MappedScalar] = {}
    for entry in reversed(catalog.entries):
        if entry.predecessor is None:
            continue
        dotted = f"dbt_charts.core.compile.migrations.versions.v{entry.version.replace('.', '_')}"
        module = _load_migration_module(dotted)
        if module is not None:
            combined.update(
                getattr(
                    module, "THEME_RENAMES", {}
                )  # type-state: silent_fallback — most frozen versions never touch a theme name; absent THEME_RENAMES means none, not a bug
            )
    return combined


def _build_board_migration_context() -> tuple[YamlSchemaCatalog, MigrationRegistry]:
    from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
        load_yaml_schema_catalog,
    )

    catalog = load_yaml_schema_catalog()
    all_moves: list[Move] = []
    all_deletions: list[Deletion] = []
    all_conditional_moves: list[ConditionalMove] = []
    for entry in catalog.entries:
        if entry.predecessor is None:
            continue
        dotted = f"dbt_charts.core.compile.migrations.versions.v{entry.version.replace('.', '_')}"
        module = _load_migration_module(dotted)
        if module is None:
            continue
        if hasattr(module, "moves"):
            all_moves.extend(
                module.moves(entry.predecessor, entry.version, catalog=catalog)
            )
        if hasattr(module, "deletions"):
            all_deletions.extend(
                module.deletions(entry.predecessor, entry.version, catalog=catalog)
            )
        if hasattr(module, "conditional_moves"):
            all_conditional_moves.extend(
                module.conditional_moves(
                    entry.predecessor, entry.version, catalog=catalog
                )
            )
    return catalog, MigrationRegistry(
        all_moves, all_deletions, all_conditional_moves, catalog=catalog
    )


def _load_migration_module(dotted: str) -> types.ModuleType | None:
    """Import *dotted* migration module, returning None if absent."""
    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError as exc:
        if exc.name != dotted:
            raise
        return None
    if (
        not hasattr(module, "moves")
        and not hasattr(module, "deletions")
        and not hasattr(module, "conditional_moves")
    ):
        raise MigrationError(
            f"{dotted} was found but does not define "
            "moves(source_schema, target_schema, *, catalog) -> tuple[Move, ...] "
            "or deletions(source_schema, target_schema, *, catalog) -> tuple[Deletion, ...] "
            "or conditional_moves(source_schema, target_schema, *, catalog) -> tuple[ConditionalMove, ...]"
        )
    return module


@cache
def _board_has_historical_schemas() -> bool:
    """Whether the packaged manifest retains a predecessor grammar."""
    manifest = json.loads(
        (files("dbt_charts") / "data" / "schemas" / "yaml" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    schemas = manifest.get("schemas") if isinstance(manifest, dict) else None
    if not isinstance(schemas, list):
        raise ValueError("dbt charts YAML schema manifest has no schemas.")
    return len(schemas) > 1


def migrate_mapping(
    mapping: Mapping[str, JsonValue],
    *,
    catalog: YamlSchemaCatalog,
    registry: MigrationRegistry,
    allow_expired: bool = False,
    today: date = date.min,
) -> JsonObject:
    """Return a current-compatible copy of a recognized board mapping.

    The caller chooses ``allow_expired``: normal compilation is bounded by the
    support window, while the explicit migration command may rewrite any
    retained schema.
    """
    identifier = recognized = _recognize(mapping, catalog, registry)
    if identifier == catalog.dev.version:
        return copy.deepcopy(dict(mapping))
    # The latest released schema is always transparently migratable — it is
    # the most recent released grammar, and the support window exists to
    # discourage accumulating decades of silent upgrades, not to penalize the
    # newest format.
    if not allow_expired and identifier != catalog.latest_released.version:
        _enforce_support_window(identifier, catalog, today=today)

    result = copy.deepcopy(dict(mapping))
    drop_warnings: list[str] = []
    while identifier != catalog.dev.version:
        moves = registry.transition_from(identifier)
        deletions = registry.deletions_from(identifier)
        cond_moves = registry.conditional_moves_from(identifier)
        if not moves and not deletions and not cond_moves:
            break
        for move in moves:
            _apply_move(result, move, catalog)
        drop_warnings.extend(_apply_deletions(result, deletions, catalog)[0])
        for cond_move in cond_moves:
            drop_warnings.extend(_apply_conditional_move(result, cond_move, catalog))
        identifier = (moves or deletions or cond_moves)[0].target_schema

    # A transition's declared moves may not cover every field a source schema
    # allowed (a deleted field has no lossless Move). Recognizing an older
    # schema and exhausting its transition chain is not proof the result is
    # current-compatible; check explicitly rather than trust silently reaching
    # the current identifier.
    current_errors = _current_schema_rejections(result, catalog)
    if current_errors:
        # Held until here on purpose: the caller discards `result` on this raise
        # and keeps the original mapping, so nothing was actually dropped. A
        # data-loss warning for a value still sitting in the document sends the
        # author hunting for damage that does not exist.
        raise _incomplete_migration_error(recognized, current_errors[0])

    for msg in drop_warnings:
        # Emitted regardless of allow_expired: a dropped value is data loss, not
        # a routine "file needs rewriting" notice. Authors running dct migrate
        # especially need to see this.
        warnings.warn(msg, SchemaMigrationWarning, stacklevel=2)

    if not allow_expired:
        warnings.warn(
            "dbt charts migrated this YAML in memory; `dct migrate` may be able "
            "to update the file.",
            SchemaMigrationWarning,
            stacklevel=2,
        )
    return result


def _should_stamp_schema_version(current: JsonValue | None, target: str) -> bool:
    """Whether ``dct migrate`` should (re)write ``_schema_version`` to *target*.

    ``False`` (no write needed) only when the existing value already equals
    *target*, or is a well-formed string (``parse_dotted_version``, shared
    with the diagnostic hint's own comparison so the two agree on what
    "malformed" means) and genuinely newer than *target* -- overwriting a
    newer value would erase the one signal
    ``yaml_error_formatter._newer_schema_version_hint`` reads. Any other
    string -- absent, older, or malformed -- gets stamped. (A non-string
    value, e.g. an unquoted YAML float, never reaches here in practice:
    ``migrate_yaml_text``'s completeness check, which runs earlier in the
    same call, already rejects a document carrying one -- no frozen or live
    schema declares ``_schema_version`` as anything but a string.)
    """
    if not isinstance(current, str) or current == target:
        return current != target
    current_parts = parse_dotted_version(current)
    target_parts = parse_dotted_version(target)
    if current_parts is None or target_parts is None:
        return True
    return current_parts <= target_parts


_STAMP_LINE_COMMENT = "  # written automatically by dct migrate"


def _mark_stamp_line_auto_written(yaml_text: str) -> str:
    """Append an explanatory comment to the freshly (re)written ``_schema_version:`` line.

    Only called right after ``set_board_values`` wrote or corrected the stamp
    this call, so the top-level, unindented ``_schema_version:`` line is
    always present and unique -- a board never declares the field twice, and
    no other top-level key can share its name. A trailing YAML comment is
    invisible to the parser, so this never changes the value the completeness
    check already verified.
    """
    lines = yaml_text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("_schema_version:"):
            lines[i] = line + _STAMP_LINE_COMMENT
            break
    return "\n".join(lines)


def _verify_reachable_via_moves_and_deletions(
    mapping: JsonObject,
    catalog: YamlSchemaCatalog,
    registry: MigrationRegistry,
    identifier: str,
) -> tuple[str, ...]:
    """Continue an in-memory walk from *identifier* to the DEV version, Move/Deletion only.

    Used to verify a capped ``migrate_yaml_text`` result is on a genuinely
    completable path (see its docstring) without pretending the on-disk
    writer is more capable than it is: the writer's own loop applies
    ``Move``/``Deletion`` only, never ``ConditionalMove`` (it can't express one
    positionally in text). Calling ``migrate_mapping`` here instead -- which
    does resolve ``ConditionalMove`` -- would make a board whose only
    remaining retired construct needs one verify clean while the actual
    written file still carries it untouched, exactly the "raise rather than
    silently leave or corrupt" guarantee
    ``test_migrate_yaml_text_raises_for_kpi_with_style_tone`` pins for the
    uncapped path. Getting stuck here (a ``ConditionalMove``-only construct,
    or a genuinely undeclared one) is reported the same way that test expects.

    Returns the rejection strings from ``_current_schema_rejections`` -- empty
    means genuinely reachable and valid.
    """
    result = copy.deepcopy(mapping)
    while identifier != catalog.dev.version:
        moves = registry.transition_from(identifier)
        deletions = registry.deletions_from(identifier)
        if not moves and not deletions:
            break
        for move in moves:
            _apply_move(result, move, catalog)
        _apply_deletions(result, deletions, catalog)
        identifier = (moves or deletions)[0].target_schema
    return _current_schema_rejections(result, catalog)


def migrate_yaml_text(
    yaml_text: str,
    *,
    catalog: YamlSchemaCatalog,
    registry: MigrationRegistry,
    stop_target: str | None = None,
) -> str:
    """Rewrite declared moves while preserving unrelated YAML text.

    The general in-memory transformer supports any JSON-shaped value. The
    file writer is narrower, for two shapes only: it moves scalar
    block-mapping leaves, and it renames a key in place when the move is a
    pure rename (old and new path share the same parent -- only the final
    segment's spelling differs). A rename never relocates content, so it
    carries no restriction on the value's shape -- an entire nested block, a
    multi-line `|`/`>` block scalar, or a plain single-line scalar all rename
    as cleanly, byte-identical value included. Any other non-scalar move (a
    genuine relocation to a different parent) fails instead of reformatting a
    board through a YAML dump/load round trip.

    ``stop_target``, when given, stops the walk at that frozen version instead
    of the DEV version -- the pending
    ``catalog.latest_released.version -> catalog.dev.version`` boundary is
    never applied. This is how ``dct migrate`` (via ``migrate_board_yaml_text``)
    avoids writing syntax no released dbt charts recognizes yet; callers that
    want the full walk to the live schema (recognition tests, in-memory
    preview) pass nothing. ``stop_target`` also gates the ``_schema_version``
    stamp: a file is only ever stamped with a frozen, real version number,
    never with the DEV version -- and only alongside a real structural change
    (``staged != raw``), never as the sole reason to rewrite an otherwise-
    untouched file. A structurally current board is always returned
    byte-identical, stamp included. A written or corrected stamp lands on the
    file's first line, with a trailing comment naming ``dct migrate`` as the
    author (see ``_mark_stamp_line_auto_written``).

    The capped result is not itself validated against the frozen target's
    schema -- that schema is closed and predates every field added since the
    freeze (including ``_schema_version`` itself), so a legitimately current
    field would fail it even though nothing is actually wrong. Instead, a
    throwaway copy is walked the *rest* of the way to the DEV version in memory,
    using only Move/Deletion (never ConditionalMove -- see
    ``_verify_reachable_via_moves_and_deletions``, not ``migrate_mapping``:
    the latter also resolves ConditionalMove, which this text writer cannot
    express, so using it here would verify a document the writer itself can
    never produce), and checked against the live schema; only the
    frozen-capped result is written. This proves the file is on a genuinely
    completable path without requiring it to already look current-shaped --
    exactly the state a capped, mid-migration file is expected to be in.

    Emits a ``SchemaMigrationWarning`` per ``Deletion.reason`` whose tail
    actually fired -- the same mechanism ``migrate_mapping`` uses for its
    drop/deletion notices, but without the generic "migrated in memory"
    notice, which does not apply here: this function is the file rewrite
    itself, not a stand-in for one.
    """
    from dbt_charts.core.compile.authoring.yaml_patch import (
        rename_key_at_path,
        set_board_values,
    )
    from dbt_charts.core.compile.parse.parser import load_yaml_mapping

    raw = load_yaml_mapping(yaml_text)
    identifier = recognized = _recognize(raw, catalog, registry)
    if identifier == catalog.dev.version:
        # A structurally-current file is never touched, stamp included: the
        # stamp is written only alongside a real change (see the end of this
        # function), never as the sole reason to rewrite an otherwise-
        # untouched file.
        return yaml_text

    updates: dict[str, ScalarLeaf] = {}
    removals: set[str] = set()
    deletion_reasons: list[str] = []
    staged = copy.deepcopy(raw)
    while identifier != catalog.dev.version and identifier != stop_target:
        moves = registry.transition_from(identifier)
        deletions = registry.deletions_from(identifier)
        if not moves and not deletions:
            break
        for move in moves:
            for parent, key, bindings in list(
                move_source_locations(staged, move, catalog)
            ):
                if isinstance(parent, list):
                    raise MigrationError(
                        f"Cannot rewrite {_format_path(move.old_path)!r}: "
                        "list-item paths require "
                        "manual migration."
                    )
                assert isinstance(key, str)
                value = parent[key]
                source = _substitute_wildcards(move.old_path, bindings)
                destination = _substitute_wildcards(move.new_path, bindings)
                # A pure rename (same parent, only the final segment's spelling
                # differs) never relocates content, so it carries no
                # restriction on the value's shape -- a multi-line block
                # scalar renames as cleanly as a single-line one or a nested
                # mapping. Checked before the scalar branch below, not after:
                # a value_map still needs the scalar setter, since the value
                # itself changes (e.g. html_policy's bool -> string), not just
                # its key.
                if source[:-1] == destination[:-1] and move.value_map is None:
                    yaml_text = rename_key_at_path(
                        yaml_text, ".".join(source), destination[-1]
                    )
                    continue
                if value is None or not isinstance(value, (str, int, float, bool)):
                    raise MigrationError(
                        f"Cannot rewrite {_format_path(move.old_path)!r}: "
                        "only scalar block-mapping moves or same-position "
                        "key renames are supported; migrate this field "
                        "manually."
                    )
                if move.value_map is not None:
                    value = _mapped_value(move.value_map, move.old_path, value)
                updates[".".join(destination)] = value
                # An identity-path Move (source == destination) rewrites the
                # key's value in place -- it must not also be queued as a
                # removal, or the removals/updates dicts below would collide
                # on the same key and the merge would delete the rewritten
                # value instead of setting it.
                if source != destination:
                    removals.add(".".join(source))
            _apply_move(staged, move, catalog)
        reasons, struck_paths = _apply_deletions(staged, deletions, catalog)
        deletion_reasons.extend(reasons)
        yaml_text = _delete_paths_in_yaml_text(yaml_text, struck_paths)
        identifier = (moves or deletions)[0].target_schema

    # Uncapped, staged already reached the DEV version: check it directly
    # against the live schema. Capped, staged deliberately stops short of the
    # DEV version, so verify a throwaway copy can still reach one (see
    # _verify_reachable_via_moves_and_deletions's docstring).
    if stop_target is None:
        current_errors = _current_schema_rejections(staged, catalog)
    else:
        current_errors = _verify_reachable_via_moves_and_deletions(
            staged, catalog, registry, identifier
        )
    if current_errors:
        raise _incomplete_migration_error(recognized, current_errors[0])

    # staged != raw: only stamp a file this call actually changed something
    # in. A structurally-current file (0 loop iterations, staged untouched)
    # must never be rewritten for the stamp alone -- dct migrate on an
    # already-current project stays a true no-op, and a file is never
    # touched solely to add a key the frozen grammar it names does not
    # itself declare.
    if (
        stop_target is not None
        and staged != raw
        and _should_stamp_schema_version(staged.get("_schema_version"), stop_target)
    ):
        staged["_schema_version"] = stop_target
        updates["_schema_version"] = stop_target

    final_text = set_board_values(
        yaml_text,
        {**updates, **dict.fromkeys(removals)},
    )
    if "_schema_version" in updates:
        final_text = _mark_stamp_line_auto_written(final_text)
    # Verify the text-level rewrite produced the same result as the in-memory
    # migration. The text editor cannot handle flow-style mappings (the regex
    # only matches block-mapping key lines) or block-scalar content (which can
    # contain lines that look like YAML keys). Either case silently no-ops or
    # silently corrupts content without the equality check; equality against
    # staged catches both before returning a wrong result.
    reparsed = load_yaml_mapping(final_text)
    if dict(reparsed) != staged:
        raise MigrationError(
            "YAML text rewrite diverged from in-memory migration; "
            "the file may use YAML constructs the text editor cannot handle "
            "(flow-style mappings, block scalars, YAML anchors, quoted keys), "
            "or a deleted key may sit where the text editor matches its name "
            "but the schema does not declare it — migrate this file manually."
        )
    # Held until here on the same reasoning as migrate_mapping: the file is
    # only actually written by this text once the caller sees a clean return,
    # so a reason attached to a rewrite that raised above would describe
    # damage that was never committed to disk.
    for msg in deletion_reasons:
        warnings.warn(msg, SchemaMigrationWarning, stacklevel=2)
    return final_text


def _recognize(
    mapping: Mapping[str, JsonValue],
    catalog: YamlSchemaCatalog,
    registry: MigrationRegistry,
) -> str:
    """Return the grammar whose transition chain *mapping* enters at.

    Recognition asks which retired constructs are present, not whether the whole
    document validates under a frozen grammar. Whole-document validation answered
    a stronger question than migration needs and got it wrong in one direction:
    anything a frozen grammar had never seen scored as a mismatch, so every key
    added since the newest freeze silently disabled migration for the file.

    A document the live grammar already accepts is current by definition and is
    never probed. That gate is not the bug this replaced — that one was the
    *frozen* grammar's whole-document match — and it is load-bearing here: the
    appliers match key chains, so a probe can find something to rewrite in a
    document where nothing is actually retired.

    Otherwise, oldest grammar first: a document carrying constructs from two
    eras has to enter the chain at the older one. Matching nothing usually means
    the document is neither old nor current — but a retired construct the
    positional gate (shared by ``Deletion`` and ``ConditionalMove``) refuses to
    act on also matches nothing, so this is "no transition applies", not
    "nothing retired is here".

    The DEV version is never a probe candidate: nothing sources a transition
    from it (it is always the newest entry, with no successor), so it could
    never match, and a document that already reached it took the early return
    above.
    """
    current_errors = _current_schema_rejections(mapping, catalog)
    if not current_errors:
        return catalog.dev.version
    for identifier in reversed(catalog.versions):
        if identifier == catalog.dev.version:
            continue
        if _transition_applies(mapping, identifier, catalog, registry):
            return identifier
    raise _unsupported_schema_error(current_errors[0])


def _transition_applies(
    mapping: Mapping[str, JsonValue],
    identifier: str,
    catalog: YamlSchemaCatalog,
    registry: MigrationRegistry,
) -> bool:
    """Whether *identifier*'s declared transition would find anything to rewrite.

    A pure dry run: read-only, no copy of *mapping*, and returns on the first
    Move/Deletion/ConditionalMove that would fire. Each kind is asked via the
    same read-only existence check its real applier uses internally
    (``_source_locations``, ``_tail_present``, ...) rather than re-deriving
    what a retired construct looks like — which is what keeps recognition and
    application from ever disagreeing, without paying to build and mutate a
    throwaway copy of the whole document to find out.

    A hit is evidence the document predates the transition only while each kind
    of declaration cannot fire on a current document, and the three get there
    differently. ``Move`` and ``Deletion`` are checked structurally: their source
    path must be gone from the target grammar (``validate_declarations``).
    ``ConditionalMove`` is not, and cannot be — ``("style", "tone")`` survives
    into the live grammar on callout charts. What separates it is the
    ``chart_type`` scoping in ``_conditional_move_would_fire``: it fires only
    on the family the rule names, at a document position the source grammar
    actually declares as that family (``_declares_chart_type``), where the
    tail really is retired.

    That scoping is positional too: a free-form mapping that merely happens to
    carry ``type: kpi`` — a query data row, say — does not match, because its
    position declares no chart at all. It is not identical to ``Deletion``'s
    gate, which additionally narrows through ``_matching_positions`` first;
    see ``_deletion_would_fire`` for why ConditionalMove must not. Neither
    fires on a bare ``type:`` key alone.

    Both structural checks look one grammar ahead, so a path retired at one
    boundary and reintroduced two boundaries later would slip through. No
    declaration does that today; a reintroduction is the thing to look for if
    recognition ever starts migrating a current document.

    A Move whose source path exists always changes something when actually
    applied — either the value relocates, or a conflicting destination raises.
    A ConditionalMove whose old tail is present at a declared chart position
    is the same: it lands, drops with a warning, or conflicts and raises.
    Either outcome still means "this transition applies", so existence alone
    is decisive and neither check needs to know which outcome would follow.
    """
    # mapping is always a concrete dict at every real call site (board YAML
    # always parses to one); Mapping is this function's read-only parameter
    # type, not the value's actual shape -- see _source_locations et al.,
    # which narrow the same way on isinstance(node, dict).
    document = cast(JsonObject, mapping)  # type-state: cast — always a dict here
    for move in registry.transition_from(identifier):
        if any(move_source_locations(document, move, catalog)):
            return True
    deletions = registry.deletions_from(identifier)
    if deletions:
        source_schema = catalog.schema_for(deletions[0].source_schema)
        live = catalog.current_schema
        if _deletion_would_fire(
            document, deletions, source_schema, [source_schema], live, [live]
        ):
            return True
    for cond_move in registry.conditional_moves_from(identifier):
        schema = catalog.schema_for(cond_move.source_schema)
        if _conditional_move_would_fire(document, cond_move, schema, [schema]):
            return True
    return False


def _deletion_would_fire(
    node: JsonValue,
    deletions: Sequence[Deletion],
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
) -> bool:
    """Read-only mirror of ``_delete_tails_recursive``: would any deletion actually fire?

    Same schema-position walk (``_matching_positions``, ``_declares_tail``,
    ``_live_declares_tail``), checked with ``_tail_present`` in place of the
    pop — nothing here mutates *node*, so the same walk serves as both
    appliers' dry run. Takes ``Deletion`` objects, not bare paths, for the same
    ``chart_type`` gate ``_delete_tails_recursive`` applies — see that
    function's docstring for why a plain path-membership check over-fires
    across chart families.

    Both gates matter here, not just when applying: a board carrying only a
    *live* ``style.color`` on a chart must not be read as predating the
    grammar that retired the board-level one.
    """
    if isinstance(node, dict):
        matching = _matching_positions(schema, positions, live, live_positions, node)
        for deletion in deletions:
            if deletion.chart_type is not None and (
                node.get("type") != deletion.chart_type
                or not _declares_chart_type(schema, matching, deletion.chart_type)
            ):
                continue
            if (
                _declares_tail(schema, matching, deletion.path)
                and not _live_declares_tail(live, live_positions, node, deletion.path)
                and _tail_present(node, deletion.path)
            ):
                return True
        return any(
            _deletion_would_fire(
                child,
                deletions,
                schema,
                _child_positions(schema, positions, key),
                live,
                _child_positions(live, live_positions, key),
            )
            for key, child in node.items()
        )
    if isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        live_items = _item_positions(live, live_positions)
        return any(
            _deletion_would_fire(
                item, deletions, schema, item_positions, live, live_items
            )
            for item in node
        )
    return False


def _conditional_move_would_fire(
    node: JsonValue,
    rule: ConditionalMove,
    schema: JsonObject,
    positions: Sequence[JsonObject],
) -> bool:
    """Read-only mirror of ``_apply_conditional_move_recursive``: would *rule* fire?

    Mirrors ``_try_conditional_move_at_chart``'s existence check, not its
    mutation: a declared chart of ``rule.chart_type`` with ``old_tail``
    present always changes something once actually applied, so presence alone
    is decisive (see ``_transition_applies``).
    """
    if isinstance(node, dict):
        if (
            node.get("type") == rule.chart_type
            and _declares_chart_type(schema, positions, rule.chart_type)
            and _tail_present(node, rule.old_tail)
        ):
            return True
        return any(
            _conditional_move_would_fire(
                child, rule, schema, _child_positions(schema, positions, key)
            )
            for key, child in node.items()
        )
    if isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        return any(
            _conditional_move_would_fire(item, rule, schema, item_positions)
            for item in node
        )
    return False


def _current_schema_rejections(
    mapping: Mapping[str, JsonValue], catalog: YamlSchemaCatalog
) -> tuple[str, ...]:
    """Why the live grammar rejects *mapping*, each rejection located.

    Draft7 reports a composite failure by dumping the offending node, which
    reads as an unplaced blob of YAML in a diagnostic. The location is the half
    an author can act on, so it leads.
    """
    rejections: list[str] = []
    for error in Draft7Validator(catalog.current_schema).iter_errors(mapping):
        # A composite failure reports by dumping the whole offending node, so a
        # rejection at `charts` would otherwise serialize every chart into the
        # message. Name the keyword and let the location carry the rest.
        #
        # Deliberately not descending into `error.context` for a more specific
        # sub-error: the authored unions are `anyOf: [<real thing>, {type: null}]`
        # over a discriminated chart family, and both obvious rankings pick the
        # wrong arm — deepest-path lands on an arbitrary family (`'bar' is not
        # one of ['line']`) and `best_match` prefers the null arm (`is not of
        # type 'null'`). A container-level location that is true beats a
        # field-level one that is confidently wrong.
        location = ".".join(str(part) for part in error.absolute_path)
        detail = (
            f"does not match any accepted {error.validator} form"
            if error.validator in ("anyOf", "oneOf", "allOf")
            else error.message
        )
        rejections.append(f"{location}: {detail}" if location else detail)
    return tuple(rejections)


def _unsupported_schema_error(current_error: str) -> UnsupportedSchemaError:
    return UnsupportedSchemaError(
        f"Unsupported YAML syntax; current schema rejected it: {current_error}"
    )


def _incomplete_migration_error(
    recognized: str, current_error: str
) -> IncompleteMigrationError:
    return IncompleteMigrationError(
        f"Recognized schema {recognized} and applied its migration, but the "
        f"result still fails the current schema at {current_error}."
    )


def _enforce_support_window(
    identifier: str, catalog: YamlSchemaCatalog, *, today: date
) -> None:
    """Raise once *identifier* has aged out of the six-month support window.

    Unreachable with a null ``released_at`` (the DEV version's shape) or an
    identifier absent from the catalog through the normal ``migrate_mapping``
    call path -- both are excluded before this is ever called. Raising a
    clear ``MigrationError`` for either rather than tolerating them (a
    never-expiring branch, or a bare ``StopIteration``) means a caller that
    reaches here in error fails loud instead of silently doing the wrong
    thing.
    """
    entry = next((e for e in catalog.entries if e.version == identifier), None)
    if entry is None:
        raise MigrationError(
            f"Schema {identifier} is not a retained grammar; cannot enforce "
            "the transparent-migration support window."
        )
    if entry.released_at is None:
        raise MigrationError(
            f"Schema {identifier} has no release date; the DEV version must "
            "never reach the support-window check."
        )
    current_date = date.today() if today == date.min else today
    cutoff = _subtract_months(current_date, 6)
    if entry.released_at < cutoff:
        raise SchemaVersionTooOldError(
            f"Schema {identifier} is older than the transparent-migration cutoff "
            f"{cutoff.isoformat()}; run `dct migrate` to update the file."
        )


def _subtract_months(value: date, months: int) -> date:
    month = value.month - months
    year = value.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    last_day = (date(year, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def _apply_conditional_move(
    result: JsonObject, rule: ConditionalMove, catalog: YamlSchemaCatalog
) -> list[str]:
    """Apply rule to result, returning a list of drop-warning messages.

    Recurses through the entire document so nested boards under rows/cols/grid
    are covered.  Warnings are collected (not emitted here) so the caller
    controls stacklevel.

    Positionally gated like ``Deletion``: a dict only fires when the *source*
    grammar declares a chart of ``rule.chart_type`` at that exact position
    (``_declares_chart_type``), not merely because the dict happens to carry a
    ``type`` key equal to ``chart_type``. Without the gate, any free-form
    value shaped like a chart -- e.g. ``Variable.default`` -- gets silently
    rewritten.

    Gated on the raw descended schema positions, not whole-subtree validity
    (``_matching_positions``): "does the grammar declare a chart of this
    family here?" doesn't need the node to fully validate. Whole-subtree
    validity fails closed on a value *form* that widened since the source
    grammar froze (e.g. ``KpiChart.link`` gaining a ``false`` arm), which
    ``_strip_post_freeze`` cannot rescue -- it only forgives keys the frozen
    grammar doesn't name, not keys whose accepted value shape changed. That
    would silently re-disable migration for a post-freeze board -- a trap
    that has already recurred twice for ``Deletion``.
    """
    schema = catalog.schema_for(rule.source_schema)
    drop_warnings: list[str] = []
    _apply_conditional_move_recursive(
        result, rule, drop_warnings, "<root>", schema, [schema]
    )
    return drop_warnings


def _apply_conditional_move_recursive(
    node: JsonValue,
    rule: ConditionalMove,
    drop_warnings: list[str],
    last_key: str,
    schema: JsonObject,
    positions: Sequence[JsonObject],
) -> None:
    """Walk node recursively; act on dicts declared as a ``rule.chart_type`` chart here.

    ``positions`` are descended in step with the document, exactly as
    ``_delete_tails_recursive`` does, so ``_declares_chart_type`` can tell a
    chart position from an open free-form one.
    """
    if isinstance(node, dict):
        if node.get("type") == rule.chart_type and _declares_chart_type(
            schema, positions, rule.chart_type
        ):
            _try_conditional_move_at_chart(node, rule, drop_warnings, last_key)
        # Always recurse into children — handles nested boards and sibling charts.
        for key in list(node.keys()):
            _apply_conditional_move_recursive(
                node[key],
                rule,
                drop_warnings,
                key,
                schema,
                _child_positions(schema, positions, key),
            )
    elif isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        for item in node:
            _apply_conditional_move_recursive(
                item,
                rule,
                drop_warnings,
                last_key,
                schema,
                item_positions,
            )


def _try_conditional_move_at_chart(
    chart: dict[str, JsonValue],
    rule: ConditionalMove,
    drop_warnings: list[str],
    chart_id: str,
) -> None:
    """Apply the conditional move to one chart dict in place."""
    # Navigate to old_tail to check existence and read value.
    old_parent: JsonValue = chart
    for part in rule.old_tail[:-1]:
        if not isinstance(old_parent, dict):
            return
        old_parent = old_parent.get(part)
        if old_parent is None:
            return
    if not isinstance(old_parent, dict):
        return
    old_key = rule.old_tail[-1]
    if old_key not in old_parent:
        return
    old_value = old_parent[old_key]

    # Check whether sibling mapping exists.
    sibling: JsonValue = chart
    for part in rule.sibling_tail:
        if not isinstance(sibling, dict):
            sibling = None
            break
        sibling = sibling.get(part)

    if isinstance(sibling, dict):
        # Check for conflict: is new_tail already set in the sibling?
        new_tail_in_sibling = rule.new_tail[len(rule.sibling_tail) :]
        conflict_node: JsonValue = sibling
        for part in new_tail_in_sibling[:-1]:
            if not isinstance(conflict_node, dict):
                break
            conflict_node = conflict_node.get(part)
        if (
            isinstance(conflict_node, dict)
            and new_tail_in_sibling
            and new_tail_in_sibling[-1] in conflict_node
        ):
            raise MigrationConflictError(
                f"Cannot conditionally move {_format_path(rule.old_tail)!r} to "
                f"{_format_path(rule.new_tail)!r} in chart {chart_id!r}: "
                "both fields exist. Choose one value and migrate manually."
            )
        # Pop old_tail (orphan-parent cleanup via _try_delete_tail).
        _try_delete_tail(chart, rule.old_tail)
        # Set value at new_tail (navigating from the sibling root).
        dest: dict[str, JsonValue] = sibling
        for part in new_tail_in_sibling[:-1]:
            child = dest.get(part)
            if not isinstance(child, dict):
                child = {}
                dest[part] = child
            dest = child
        dest[new_tail_in_sibling[-1]] = old_value
    else:
        # Drop the field — sibling doesn't exist so there's nowhere to land it.
        _try_delete_tail(chart, rule.old_tail)
        drop_warnings.append(rule.drop_warning.format(chart=chart_id))


def _apply_deletions(
    document: JsonObject,
    deletions: Sequence[Deletion],
    catalog: YamlSchemaCatalog,
) -> tuple[list[str], list[DocumentPath]]:
    """Strip every declared deletion in one document walk.

    All of a boundary's deletions share its source grammar, so one walk covers
    them: the schema descent is what costs, and re-walking per tail re-expands
    the same ``$ref``/``anyOf``/``allOf`` nodes once per tail.

    Returns the author-facing messages and the concrete document paths struck.
    One message per deletion that both fired (was actually present in the
    document, not merely declared by the grammar) and carries a ``reason`` —
    most deletions are mechanical and carry none. The paths are what
    ``_delete_paths_in_yaml_text`` replays; see ``_delete_tails_recursive`` for
    their shape.

    Takes the ``Deletion`` objects themselves, not bare paths: several
    deletions can share one path with different ``chart_type`` scopes (one
    entry per retired family), and a flattened ``path -> reason`` dict would
    collide on the shared key, silently keeping only one arbitrary family's
    message and losing the chart_type each deletion needs to gate on.
    """
    if not deletions:
        return [], []
    source_schema = catalog.schema_for(deletions[0].source_schema)
    live = catalog.current_schema
    messages: list[str] = []
    struck: list[DocumentPath] = []
    _delete_tails_recursive(
        document,
        deletions,
        source_schema,
        [source_schema],
        live,
        [live],
        messages,
        struck,
        (),
    )
    return messages, struck


def _delete_tails_recursive(
    node: JsonValue,
    deletions: Sequence[Deletion],
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
    messages: list[str],
    struck: list[DocumentPath],
    prefix: DocumentPath,
) -> None:
    """Delete every declared deletion wherever the source grammar declares it, throughout *node*.

    ``positions`` are the schema nodes *node* can correspond to, descended in
    step with the document so the walk keeps its depth-cap-free reach into
    nested faces while knowing where it is.

    A deletion's path must be a **declared** property chain at the position,
    never one reached through an open map. ``charts:`` is keyed by the
    author, so a chart someone named ``bar`` presents exactly the key chain
    that ``("bar", "tooltip")`` — the ``bar`` block under ``style.charts`` —
    means.

    When ``deletion.chart_type`` is set, the deletion additionally only fires
    on a node the *source* grammar declares as a chart of that family
    (``_declares_chart_type``) whose own ``type:`` key matches — the same
    double check ``_apply_conditional_move`` uses for ``ConditionalMove``.
    Without it, a document position that merely validates against *some*
    branch of a discriminated union (``_matching_positions`` narrows by
    whole-node validity, not by which specific arm) would let a
    ``chart_type="bar"``-scoped deletion strip the field from every chart
    family that still declares it, table/kpi included.

    A tail the *live* grammar still declares at this position is left alone
    (``_live_declares_tail``) — a leaf name is routinely retired at one
    position and live at another, and the source grammar declares it at both.
    That gate, not the path's shape, is what lets ``("style", "color")`` strip
    a board's dead override while every chart family keeps its working one.

    ``struck`` collects the concrete document path of every occurrence actually
    removed — segment tuples, sequence indices included, and the emptied parent
    rather than the leaf wherever the deletion took one with it. ``dct
    migrate``'s text rewriter replays exactly these rather than re-deriving
    them from key names, which is what keeps the two paths one rule instead of
    two.

    ``live_positions`` is the same descent through the *current* grammar, kept
    in step so ``_matching_positions`` can tell a field that is new **here**
    from one that merely shares a name with something old somewhere else.

    All of a boundary's deletions travel in one walk because the schema
    descent, not the document traversal, is what costs: re-walking per
    deletion re-expands the same ``$ref``/``anyOf``/``allOf`` nodes once per
    deletion.

    When deleting a tail causes a parent dict to become empty, that parent is
    also removed — the deletion propagates up through the document structure.

    ``messages`` collects one formatted string per occurrence actually
    removed that carries a ``reason`` (checked via ``_tail_present`` before
    the pop, since a declared tail may simply be absent from this particular
    document).
    """
    if isinstance(node, dict):
        matching = _matching_positions(schema, positions, live, live_positions, node)
        for deletion in deletions:
            if deletion.chart_type is not None and (
                node.get("type") != deletion.chart_type
                or not _declares_chart_type(schema, matching, deletion.chart_type)
            ):
                continue
            if not _declares_tail(schema, matching, deletion.path):
                continue
            if _live_declares_tail(live, live_positions, node, deletion.path):
                continue
            fired = _tail_present(node, deletion.path)
            if fired and deletion.reason is not None:
                messages.append(
                    f"`{_format_path(deletion.path)}` was removed: {deletion.reason}"
                )
            _try_delete_tail(node, deletion.path)
            if fired:
                struck.append((*prefix, *_surviving_prefix(node, deletion.path)))
        for key in list(node.keys()):
            child = node[key]
            was_empty_before = isinstance(child, dict) and not child
            _delete_tails_recursive(
                child,
                deletions,
                schema,
                _child_positions(schema, positions, key),
                live,
                _child_positions(live, live_positions, key),
                messages,
                struck,
                (*prefix, key),
            )
            if isinstance(child, dict) and not child and not was_empty_before:
                del node[key]
                struck.append((*prefix, key))
    elif isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        live_items = _item_positions(live, live_positions)
        for index, item in enumerate(node):
            _delete_tails_recursive(
                item,
                deletions,
                schema,
                item_positions,
                live,
                live_items,
                messages,
                struck,
                (*prefix, index),
            )


def _live_declares_tail(
    live: JsonObject,
    live_positions: Sequence[JsonObject],
    node: Mapping[str, JsonValue],
    tail: YamlKeyPath,
) -> bool:
    """Whether the *current* grammar still declares *tail* where this node sits.

    The gate that makes a `Deletion` positional rather than global. A leaf name
    is routinely retired at one position and live at another — `style.color` is
    gone from a board's own style block and works on every chart family — and
    the source grammar declares it at both, so the source-side gate alone
    cannot tell them apart.

    Identity, not validity (`migrations/AGENTS.md`): a mid-migration node
    carries retired spellings and fails whole-node validation under the live
    grammar by construction, so `_matching_positions` would narrow to nothing
    here and the gate would wave every position through. `_plausible_positions`
    asks the question a deletion actually needs — is *this* node one of those
    branches — the same way `move_source_locations` does.

    An unrecognized position yields no plausible branches and reads as "not
    declared", so the deletion falls back to the source-side gate alone. That
    is the pre-gate behavior: this check can only ever suppress a firing, never
    add one.
    """
    return _declares_tail(live, _plausible_positions(live, live_positions, node), tail)


def _surviving_prefix(node: JsonObject, tail: YamlKeyPath) -> YamlKeyPath:
    """*tail* truncated after the first segment that is gone from *node*.

    ``_try_delete_tail`` takes an emptied parent with it, so the key the text
    rewriter has to strike is not always the leaf: deleting ``style.color`` off
    a board whose ``style:`` held nothing else removes ``style:`` itself.
    """
    current: JsonValue = node
    for index, part in enumerate(tail):
        if not isinstance(current, dict) or part not in current:
            return tail[: index + 1]
        current = current[part]
    return tail


def _tail_present(node: JsonObject, tail: YamlKeyPath) -> bool:
    """Whether *tail* currently exists as a key chain rooted at *node* (read-only)."""
    current: JsonValue = node
    for part in tail:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _matching_positions(
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
    node: JsonValue,
) -> list[JsonObject]:
    """Narrow *positions* to the branches *node* is an instance of.

    Whole-subtree validity under the frozen grammar is the first question, and
    it is deliberately strict: a node satisfying no branch yields no positions,
    so no tail is declared there and nothing is deleted. That strictness is what
    keeps a chart the author named ``style`` from being read as a nested board's
    style block and emptied.

    Taken alone it is *too* strict, and in this task's own way: a field added
    since the freeze makes the node invalid under the frozen grammar, which
    disabled every deletion in that subtree — whole-document recognition again,
    scoped to a node. So a rejected node is asked once more with those fields
    removed, position by position (``_strip_post_freeze``).

    Positional, not a global set of newer names: ``theme`` is new at the board
    root and long-standing under ``TextCodeStyle``; ``nice`` is new on
    ``ScaleTargetConfig`` and old on ``BaseScaleStyle``. A name-level diff misses
    both, which is how ``theme:`` — the reported bug — stayed broken for boards
    whose retired construct was a ``Deletion``.

    Stripping only ever removes what the live grammar declares *and the frozen
    one does not, at that exact position*, so it cannot turn one kind of node
    into another: the chart body that must not read as a ``style:`` block is
    still not a valid ``Style`` without its newer fields.
    """
    validator = Draft7Validator(schema)
    matched = [
        position
        for position in positions
        if validator.evolve(schema=position).is_valid(node)
    ]
    if matched:
        return matched
    probe = _strip_post_freeze(node, schema, positions, live, live_positions)
    if probe == node:
        return []
    return [
        position
        for position in positions
        if validator.evolve(schema=position).is_valid(probe)
    ]


def _strip_post_freeze(
    node: JsonValue,
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
) -> JsonValue:
    """A copy of *node* without the fields the frozen grammar could not have known.

    Both grammars are descended together, so "could not have known" is decided
    per position rather than per name, and at every depth — the newer field is
    routinely nested well below the node whose tail is firing (``style.frame:``
    against a root-anchored tail is the common shape after the rebrand rename).
    """
    if isinstance(node, dict):
        newer = _declared_names(live, live_positions) - _declared_names(
            schema, positions
        )
        return {
            key: _strip_post_freeze(
                value,
                schema,
                _child_positions(schema, positions, key),
                live,
                _child_positions(live, live_positions, key),
            )
            for key, value in node.items()
            if key not in newer
        }
    if isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        live_items = _item_positions(live, live_positions)
        return [
            _strip_post_freeze(item, schema, item_positions, live, live_items)
            for item in node
        ]
    return node


def _declared_names(schema: JsonObject, positions: Sequence[JsonObject]) -> set[str]:
    """Property names declared at *positions*, across every branch they expand to."""
    names: set[str] = set()
    for node in positions:
        for branch in _schema_branches(schema, node):
            properties = branch.get("properties")
            if isinstance(properties, dict):
                names.update(properties)
    return names


def _declares_tail(
    schema: JsonObject, positions: Sequence[JsonObject], tail: YamlKeyPath
) -> bool:
    """Whether *tail* is a declared property chain at any of *positions*.

    Declared only — `additionalProperties` is deliberately not consulted, which
    is the whole distinction between a retired key and a key the author chose.
    """
    current = list(positions)
    for part in tail:
        next_nodes: list[JsonObject] = []
        for node in current:
            for branch in _schema_branches(schema, node):
                properties = branch.get("properties")
                child = properties.get(part) if isinstance(properties, dict) else None
                if isinstance(child, dict):
                    next_nodes.append(child)
        if not next_nodes:
            return False
        current = next_nodes
    return True


def _without_composition(node: JsonObject) -> JsonObject | None:
    """*node*'s own declarations, with every composition keyword removed.

    ``None`` when nothing is left — a pure wrapper declares nothing itself and
    contributes no position.
    """
    stripped = {
        key: value
        for key, value in node.items()
        if key not in ("anyOf", "oneOf", "allOf", "if", "then", "else")
    }
    return stripped if stripped.keys() - {"description", "title"} else None


def _open_map_claims(
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
    key: str,
    value: JsonValue,
) -> bool:
    """Whether *key* reads as an author-chosen map key rather than a field.

    A union arm can be an open map (``rows:`` accepts
    ``dict[str, AuthoredChart]`` beside ``AuthoredBoard``), and by keys alone
    the two are indistinguishable: ``{"description": {...}}`` is either a board
    whose ``description`` field is set, or a chart the author *named*
    ``description``. Only the value decides, and only here — this is the one
    question ``_node_could_be``'s identity test cannot answer, so the value is
    consulted at this single point rather than as general validation.

    The declared reading wins whenever it fits, so a value shape the frozen
    grammar never accepted (0.5.0's ``data_table``) still migrates: the map
    reading has to *fit* before it can take precedence.
    """
    declared_fits = any(
        _accepts(schema, position.get("properties"), key, value)
        for position in positions
    )
    # The open-map arm is read from *both* grammars. A position can gain the
    # arm after the freeze -- `GridItem.item` accepts `dict[str, AuthoredChart]`
    # live and in no frozen snapshot -- and a frozen-only read is blind exactly
    # there, which let the author's key be rewritten under `grid.items.*.item`.
    map_fits = any(
        _accepts_value(grammar, position.get("additionalProperties"), value)
        for grammar, group in ((schema, positions), (live, live_positions))
        for position in group
    )
    return map_fits and not declared_fits


def _accepts(
    schema: JsonObject, properties: JsonValue, key: str, value: JsonValue
) -> bool:
    """Whether *properties* declares *key* with a subschema *value* satisfies."""
    if not isinstance(properties, dict):
        return False
    return _accepts_value(schema, properties.get(key), value)


def _accepts_value(schema: JsonObject, subschema: JsonValue, value: JsonValue) -> bool:
    if not isinstance(subschema, dict):
        return False
    return Draft7Validator(schema).evolve(schema=subschema).is_valid(value)


def _plausible_positions(
    schema: JsonObject, positions: Sequence[JsonObject], node: Mapping[str, JsonValue]
) -> list[JsonObject]:
    """Branches at *positions* that *node* could be an instance of.

    Deliberately weaker than ``_matching_positions``, which asks for whole-node
    validity under the frozen grammar. A document a ``Move`` targets is
    mid-migration by construction: it carries retired spellings, and their
    *values* may have retired shapes too (0.5.0's ``data_table`` is not the
    list its successor accepts), so demanding validity refuses the very
    positions the move exists to rewrite.

    Identity is the question a rename actually needs, and it is decided the way
    the schema itself discriminates: the chart union is emitted as ``allOf`` of
    ``if``/``then``, so a ``then`` counts only when its ``if`` holds for this
    node. ``_schema_branches`` expands ``then`` unconditionally — correct for
    "could any value here carry this property?", wrong for "is *this* value one
    of those?" — which is why this walks the composition itself instead of
    reusing it.

    That is the whole open-map hazard: a ``queries:`` map reached through the
    ``dict[str, AuthoredChart]`` arm of ``rows:`` declares no ``type``, so no
    chart family's ``if`` holds and no rename fires inside it.
    """
    plausible: list[JsonObject] = []
    for position in positions:
        _collect_plausible(schema, position, node, frozenset(), plausible)
    return plausible


def _collect_plausible(
    schema: JsonObject,
    position: JsonObject,
    node: Mapping[str, JsonValue],
    seen: frozenset[str],
    out: list[JsonObject],
) -> None:
    """Mirror of ``_schema_branches`` that gates each ``then`` on its ``if``."""
    ref = position.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return
        seen = seen | {ref}
    resolved = _resolve_ref(schema, position)
    # Append the node's *own* declarations only. `_declares_tail` expands
    # whatever it is handed through the ungated `_schema_branches`, so passing
    # a composition through would re-admit every arm this walk just gated --
    # the arms are reached below instead, each on its own merits. A composed
    # node can still carry its own `properties` (that is how `AuthoredChart`
    # holds the fields shared across families), so strip rather than skip.
    own = _without_composition(resolved)
    if own is not None and _node_could_be(schema, own, node):
        out.append(own)
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = resolved.get(keyword)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if isinstance(branch, dict):
                _collect_plausible(schema, branch, node, seen, out)
    conditional = resolved.get("then")
    if isinstance(conditional, dict):
        guard = resolved.get("if")
        if isinstance(guard, dict) and not _node_satisfies(schema, guard, node):
            return
        _collect_plausible(schema, conditional, node, seen, out)


def _node_could_be(
    schema: JsonObject, branch: JsonObject, node: Mapping[str, JsonValue]
) -> bool:
    """Whether *node* carries what *branch* requires to be an instance of it.

    Required properties and an agreeing ``type`` discriminator only — never the
    values' own shapes, which a mid-migration document is expected to fail.
    """
    required = branch.get("required")
    if isinstance(required, list) and any(
        name not in node for name in required if isinstance(name, str)
    ):
        return False
    properties = branch.get("properties")
    declared_type = properties.get("type") if isinstance(properties, dict) else None
    node_type = node.get("type")
    if not isinstance(declared_type, dict) or node_type is None:
        return True
    enum = declared_type.get("enum")
    if isinstance(enum, list) and node_type not in enum:
        return False
    const = declared_type.get("const")
    return const is None or node_type == const


def _node_satisfies(
    schema: JsonObject, guard: JsonObject, node: Mapping[str, JsonValue]
) -> bool:
    """Whether *node* satisfies an ``if`` guard — the union's own discriminator."""
    return Draft7Validator(schema).evolve(schema=guard).is_valid(dict(node))


def _declares_chart_type(
    schema: JsonObject, positions: Sequence[JsonObject], chart_type: str
) -> bool:
    """Whether any of *positions* discriminates a chart of *chart_type* via ``type:``.

    Mirrors ``_declares_tail`` for the discriminated chart union: a position
    only counts when some branch declares ``type`` as an ``enum`` containing
    *chart_type* -- never merely because the document node happens to carry a
    ``type`` key with that value. That is the whole distinction between an
    authored chart and a free-form value shaped like one: for
    ``Variable.default``, ``ConditionalRule.eq`` and ``DuckDBSourceConfig.remote``
    the position's schema declares no ``properties.type``, so no branch here
    matches.
    """
    for position in positions:
        for branch in _schema_branches(schema, position):
            properties = branch.get("properties")
            if not isinstance(properties, dict):
                continue
            type_schema = properties.get("type")
            if not isinstance(type_schema, dict):
                continue
            enum = type_schema.get("enum")
            if isinstance(enum, list) and chart_type in enum:
                return True
    return False


def _child_positions(
    schema: JsonObject, positions: Sequence[JsonObject], key: str
) -> list[JsonObject]:
    """Schema nodes for *key* one step below *positions*.

    Descent *does* follow open maps — reaching inside a user-keyed chart to look
    at it is fine. Only the delete decision is restricted to declared properties.
    """
    children: list[JsonObject] = []
    for node in positions:
        for branch in _schema_branches(schema, node):
            properties = branch.get("properties")
            declared = properties.get(key) if isinstance(properties, dict) else None
            candidate = (
                declared
                if isinstance(declared, dict)
                else branch.get("additionalProperties")
            )
            if isinstance(candidate, dict):
                children.append(candidate)
    return children


def _item_positions(
    schema: JsonObject, positions: Sequence[JsonObject]
) -> list[JsonObject]:
    """Schema nodes for the items of the sequences at *positions*."""
    items: list[JsonObject] = []
    for node in positions:
        for branch in _schema_branches(schema, node):
            candidate = branch.get("items")
            if isinstance(candidate, dict):
                items.append(candidate)
    return items


def _try_delete_tail(node: dict[str, JsonValue], tail: YamlKeyPath) -> bool:
    """Delete tail from node if the key chain exists, following only dict children.

    Returns True if *node* itself became empty after the deletion so the caller
    can remove it from its own parent.
    """
    if len(tail) == 1:
        if tail[0] not in node:
            return False
        node.pop(tail[0])
        return len(node) == 0
    child = node.get(tail[0])
    if isinstance(child, dict):
        if _try_delete_tail(child, tail[1:]):
            del node[tail[0]]
            return len(node) == 0
    return False


def _apply_move(mapping: JsonObject, move: Move, catalog: YamlSchemaCatalog) -> None:
    for parent, key, bindings in list(move_source_locations(mapping, move, catalog)):
        destination = _substitute_wildcards(move.new_path, bindings)
        _move_value(mapping, parent, key, destination, move)


def move_source_locations(
    document: JsonObject, move: Move, catalog: YamlSchemaCatalog
) -> Iterable[tuple[JsonObject | list[JsonValue], str | int, tuple[str, ...]]]:
    """Document positions *move* rewrites, gated on the source grammar.

    The path walk alone is not enough to decide a rename. A resolved path's
    ``*`` segments come from one *declared* arm of a union, but application is
    arm-blind, so wherever an open map sits beside a declared field the final
    segment can land on a key the author chose — a chart id under ``rows.*``,
    a query name inside a sub-board's own ``queries:`` map. Both rewrote the
    author's identifier on disk before this gate existed.

    So the walk carries schema positions alongside the document and gates each
    yield three ways: ``_plausible_positions`` narrows to the branches the node
    could be an instance of, ``_declares_tail`` requires the final segment to
    be a *declared* property there (``additionalProperties`` deliberately not
    consulted — the distinction ``Deletion`` draws, see
    ``_deletion_would_fire``), and ``_open_map_claims`` breaks the remaining
    board-field/chart-id tie by value, since identity alone cannot.

    Both grammars are carried because a position can gain an open-map arm
    after the freeze (``GridItem.item``), and a frozen-only read is blind
    there.

    A trailing ``*`` is ungated: it names no key, so "is this a declared
    property?" has no meaning there. No declaration produces one today.

    A fourth gate applies only to an identity-path Move (``old_path ==
    new_path``, e.g. dbt charts' ``theme:`` sugar): the structural precondition
    every other Move relies on for recognition (``validate_declarations``
    requires the source path be absent from the target grammar) does not hold
    for it by construction -- the key survives every transition unrenamed. Key
    presence is therefore not evidence the document is old; the value is.
    ``_identity_value_would_change`` gates the yield on whether the map would
    actually rename the value found there, so a current name or a value the
    map has never heard of (a path ref, a project-relative board name -- never
    legal syntax for this field at any schema version) is left alone instead
    of being forced through the map.
    """
    source_schema = catalog.schema_for(move.source_schema)
    live = catalog.current_schema
    identity_move = move.old_path == move.new_path and move.value_map is not None
    for parent, key, bindings in _source_locations(
        document, move.old_path, source_schema, [source_schema], live, [live]
    ):
        if identity_move:
            assert move.value_map is not None
            if isinstance(parent, dict):
                assert isinstance(key, str)
                current_value = parent[key]
            else:
                assert isinstance(key, int)
                current_value = parent[key]
            if not _identity_value_would_change(move.value_map, current_value):
                continue
        yield parent, key, bindings


def _identity_value_would_change(
    value_map: Mapping[MappedScalar, MappedScalar], value: JsonValue
) -> bool:
    """True when *value_map* actually renames *value*.

    An identity-path Move's value_map is total over its field's whole bounded
    domain (current names included, mapped to themselves -- see ``THEME_VALUE_MAP``'s
    comment), so "present in the map" alone does not distinguish a retired
    spelling from a current one; only a value the map sends somewhere else does.
    """
    if not isinstance(value, (str, int, float, bool)):
        return False
    return value in value_map and value_map[value] != value


def _source_locations(
    node: JsonValue,
    parts: YamlKeyPath,
    schema: JsonObject,
    positions: Sequence[JsonObject],
    live: JsonObject,
    live_positions: Sequence[JsonObject],
    bindings: tuple[str, ...] = (),
) -> Iterable[tuple[JsonObject | list[JsonValue], str | int, tuple[str, ...]]]:
    if not parts:
        return
    part = parts[0]
    if len(parts) == 1:
        if part == "*" and isinstance(node, dict):
            for key in node:
                yield node, key, bindings + (str(key),)
        elif part == "*" and isinstance(node, list):
            for index in range(len(node)):
                yield node, index, bindings + (str(index),)
        elif isinstance(node, dict) and part in node:
            plausible = _plausible_positions(schema, positions, node)
            live_plausible = _plausible_positions(live, live_positions, node)
            if _declares_tail(schema, plausible, (part,)) and not _open_map_claims(
                schema, plausible, live, live_plausible, part, node[part]
            ):
                yield node, part, bindings
        return
    if part == "*" and isinstance(node, dict):
        for child_key, value in list(node.items()):
            yield from _source_locations(
                value,
                parts[1:],
                schema,
                _child_positions(schema, positions, child_key),
                live,
                _child_positions(live, live_positions, child_key),
                bindings + (str(child_key),),
            )
    elif part == "*" and isinstance(node, list):
        item_positions = _item_positions(schema, positions)
        live_items = _item_positions(live, live_positions)
        for index, value in enumerate(node):
            yield from _source_locations(
                value,
                parts[1:],
                schema,
                item_positions,
                live,
                live_items,
                bindings + (str(index),),
            )
    elif isinstance(node, dict) and part in node:
        yield from _source_locations(
            node[part],
            parts[1:],
            schema,
            _child_positions(schema, positions, part),
            live,
            _child_positions(live, live_positions, part),
            bindings,
        )


def _substitute_wildcards(parts: YamlKeyPath, bindings: tuple[str, ...]) -> list[str]:
    iterator = iter(bindings)
    result: list[str] = []
    for part in parts:
        if part == "*":
            try:
                result.append(next(iterator))
            except StopIteration as exc:
                raise MigrationError(
                    "Destination path has more wildcards than source path"
                ) from exc
        else:
            result.append(part)
    if next(iterator, None) is not None:
        raise MigrationError("Source path has more wildcards than destination path")
    return result


def _descend_move_segment(
    parent: JsonObject | list[JsonValue], part: str, move: Move
) -> JsonValue:
    """Return the child at ``part`` on ``parent``.

    ``part`` is always a string (even a wildcard-bound list index, e.g.
    ``tabs.items.0``) — a list descends by ``int(part)``, a dict by key,
    creating an empty dict for a missing key so the caller can populate it.
    Split out of ``_move_value``'s loop so ``isinstance(parent, list)``
    narrows cleanly: pyright doesn't carry a loop-local narrow across the
    back-edge, but a plain function call re-narrows at each call site.
    """
    if isinstance(parent, list):
        index = int(part)
        if index >= len(parent):
            raise MigrationError(
                f"Cannot move {_format_path(move.old_path)!r} to "
                f"{_format_path(move.new_path)!r}: list index {part!r} "
                "does not exist. Migrate this field manually."
            )
        return parent[index]
    value = parent.get(part)
    if value is None:
        value = {}
        parent[part] = value
    return value


def _move_value(
    mapping: JsonObject,
    source_parent: JsonObject | list[JsonValue],
    source_key: str | int,
    destination: list[str],
    move: Move,
) -> None:
    parent: JsonObject | list[JsonValue] = mapping
    for part in destination[:-1]:
        value = _descend_move_segment(parent, part, move)
        if not isinstance(value, (dict, list)):
            raise MigrationError(
                f"Cannot move {_format_path(move.old_path)!r} to "
                f"{_format_path(move.new_path)!r}: {part!r} "
                "must be a mapping or list. Migrate this field manually."
            )
        parent = value
    if isinstance(parent, list):
        raise MigrationError(
            f"Cannot move {_format_path(move.old_path)!r} to "
            f"{_format_path(move.new_path)!r}: destination ends inside a "
            "list. Migrate this field manually."
        )
    destination_key = destination[-1]
    # An identity-path Move (old_path == new_path) pops and reassigns the same
    # slot -- source_parent and parent are the same object and the key hasn't
    # moved yet, so "destination_key in parent" is trivially true and must not
    # read as a conflict with a *different* occupied field.
    same_slot = source_parent is parent and source_key == destination_key
    if destination_key in parent and not same_slot:
        raise MigrationConflictError(
            f"Cannot move {_format_path(move.old_path)!r} to "
            f"{_format_path(move.new_path)!r}: both fields exist. "
            "Choose one value and migrate manually."
        )
    if isinstance(source_parent, dict):
        assert isinstance(source_key, str)
        popped_value = source_parent.pop(source_key)
    else:
        assert isinstance(source_key, int)
        popped_value = source_parent.pop(source_key)
    if move.value_map is not None:
        parent[destination_key] = _mapped_value(
            move.value_map, move.old_path, popped_value
        )
    else:
        parent[destination_key] = popped_value


def _mapped_value(
    value_map: Mapping[MappedScalar, MappedScalar],
    old_path: YamlKeyPath,
    value: JsonValue,
) -> MappedScalar:
    """Resolve value through a Move's value_map, rejecting anything it can't cover.

    A value_map is declared total over its field's real scalar domain (see
    Move.value_map), so a non-scalar or unmapped value here means the source
    schema admitted a shape the migration author didn't account for -- fail
    loud rather than guess.
    """
    if not isinstance(value, (str, int, float, bool)) or value not in value_map:
        raise MigrationError(
            f"Cannot migrate {_format_path(old_path)!r}: "
            f"value {value!r} has no entry in the value map. "
            "Migrate this field manually."
        )
    return value_map[value]


def _tail_matches(path: tuple[str, ...], tails: frozenset[tuple[str, ...]]) -> bool:
    """Does ``path`` end with one of ``tails``? -- the same right-aligned
    suffix test ``suffix_rename_moves`` applies to a full absolute path."""
    return any(len(path) >= len(tail) and path[-len(tail) :] == tail for tail in tails)


@cache
def _relative_field_paths(
    model: type[BaseModel],
    seen: frozenset[type[BaseModel]],
    tails: frozenset[tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    """Field-path suffixes reachable from ``model``, relative to ``model``,
    kept only if they could contribute to a ``tails`` match.

    A candidate is kept when it already ends with one of ``tails`` (a
    complete match, needs no more context) or is shorter than the widest
    tail (it might still complete a match once an ancestor's field names are
    prepended above ``model`` -- ``suffix_rename_moves`` re-checks the full
    absolute path before trusting one). Everything else is dropped on the
    spot: filtering *during* the walk, rather than after materializing every
    field path and filtering the result, is what keeps this cheap.
    AuthoredBoard's unfiltered path set is vast; a typical rename call
    (a width-1 tail, e.g. ``html_policy:`` -> ``allow_html:``) matches a
    vanishing fraction of it, and every non-matching candidate is
    dropped the moment it's built -- nothing shorter than the tail exists
    to retain.

    ``seen`` is every ancestor model already on this path, checked on entry:
    a model already in ``seen`` contributes nothing and the call returns
    immediately, so a self-referential model's own fields are never reached
    a second time along the same path -- ``AuthoredBoard`` nested inside
    itself (via ``rows``/``cols``/``grid.items.*.item``) is opaque to this
    walk at any depth, not just a second re-entry; only a genuinely
    different model (``TabItem`` via ``tabs.items.*``, a chart family via
    ``rows.*``) keeps yielding fields past that point. Memoized on
    ``(model, seen, tails)``: callers prune ``seen`` to ``_closure(model)``
    before recursing here, which is safe
    because the entry check only ever tests membership of the model being
    entered, and that model is always a member of its own closure -- so the
    prune never discards the one fact the check depends on, it only drops
    ancestors ``model`` could never reach again anyway. ``tails`` is
    threaded down unchanged from the top-level call. The cache is cleared by
    ``suffix_rename_moves`` on return -- see its docstring for why nothing
    here persists across calls.
    """
    if model in seen:
        return ()
    max_width = max(len(tail) for tail in tails)
    paths: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        candidates: list[tuple[str, ...]] = [(name,)]
        if field.annotation is not None:
            for nested, suffix in _nested_models(field.annotation):
                nested_seen = (seen | {model}) & _closure(nested)
                for nested_suffix in _relative_field_paths(nested, nested_seen, tails):
                    candidates.append((name,) + suffix + nested_suffix)
        for candidate in candidates:
            if len(candidate) < max_width or _tail_matches(candidate, tails):
                paths.append(candidate)
    return tuple(paths)


@cache
def _closure(model: type[BaseModel]) -> frozenset[type[BaseModel]]:
    """Every model transitively reachable from ``model``, including itself.

    Used only to prune the ancestor set passed into ``_relative_field_paths``
    -- ``model`` can never recurse back into an ancestor outside its own
    closure, so that part of ``seen`` is dead weight for both correctness
    and the memoization key. A plain visited-set walk is safe here even
    though the underlying graph has cycles (``AuthoredBoard``, ``TabItem``/
    ``TabLayout``): reachability doesn't care how many times a path could
    loop, only whether a destination is reachable at all.
    """
    closure = {model}
    stack = [model]
    while stack:
        current = stack.pop()
        for field in current.model_fields.values():
            if field.annotation is None:
                continue
            for nested, _suffix in _nested_models(field.annotation):
                if nested not in closure:
                    closure.add(nested)
                    stack.append(nested)
    return frozenset(closure)


def _nested_models(
    annotation: type[BaseModel] | type | str,
) -> Iterable[tuple[type[BaseModel], tuple[str, ...]]]:
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation, ()
        return
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (dict, list):
        values = args[1:] if origin is dict else args
        for value in values:
            for nested, suffix in _nested_models(value):
                yield nested, ("*",) + suffix
        return
    for argument in args:
        yield from _nested_models(argument)


def _schema_path_exists(schema: JsonObject, path: YamlKeyPath) -> bool:
    nodes = [schema]
    for part in path:
        next_nodes: list[JsonObject] = []
        for node in nodes:
            for branch in _schema_branches(schema, node):
                if part == "*":
                    child = branch.get("additionalProperties")
                    if child is None:
                        child = branch.get("items")
                else:
                    properties = branch.get("properties")
                    child = (
                        properties.get(part) if isinstance(properties, dict) else None
                    )
                if isinstance(child, dict):
                    next_nodes.append(child)
        nodes = next_nodes
        if not nodes:
            return False
    return True


class _ById:
    """Wraps a JSON schema dict so it can key an ``@cache``d function.

    A schema node is an unhashable ``dict``, so it cannot be a ``functools.cache``
    argument directly. Hashing/comparing by identity (rather than content) is
    correct here because a hit only ever means "the same node object was asked
    about again" -- and holding ``obj`` in the wrapper is what keeps that
    identity valid: a dict's id can be reused after garbage collection, but not
    while something still references it, so as long as a wrapper survives in
    the cache it also keeps its id from ever being handed to a different
    object.
    """

    __slots__ = ("obj",)

    def __init__(self, obj: JsonObject) -> None:
        self.obj = obj

    def __hash__(self) -> int:
        return id(self.obj)

    def __eq__(
        self,
        other: object,  # type-state: object_annotation — __eq__ takes any object per Python's data model
    ) -> bool:
        return isinstance(other, _ById) and other.obj is self.obj


def _schema_branches(
    schema: JsonObject, node: JsonObject, seen: frozenset[str] = frozenset()
) -> list[JsonObject]:
    """Flatten a schema node into every concrete branch a value here could satisfy.

    Follows ``$ref`` (including ``$ref: "#"``), unions (``anyOf``/``oneOf``),
    intersections (``allOf``), and the ``then`` of an ``if``/``then`` pair.

    Unions and intersections flatten alike because every caller asks the same
    question — could a value at this position carry this property? — and in an
    intersection one conjunct declaring it is enough. The discriminated chart
    union is emitted as ``allOf`` of ``if``/``then``, so a walker that
    understood only ``anyOf`` saw no per-family property at all and read an
    inline chart as declaring nothing.

    ``resolved`` itself is always included: a composed node can carry its own
    ``properties`` alongside the composition, which is exactly how
    ``AuthoredChart`` holds the fields shared across families.

    The *seen* guard stops cycles caused by self-referential ``$ref: "#"``.

    A thin wrapper around ``_schema_branches_cached``, which does the real
    work and is what carries the ``@cache`` -- ``schema``/``node`` are plain
    dicts and can't be cache keys themselves, so this wraps each in ``_ById``
    (hashable by identity) before delegating. Both phases call this once per
    path segment for every candidate they check -- ``_schema_path_exists`` at
    registry-build time, ``_declared_names``/``_declares_tail``/
    ``_declares_chart_type``/``_child_positions``/``_item_positions`` at
    document-migration time -- and those paths share long common prefixes (the
    same board/chart/style nodes, over and over), so the same
    ``(schema, node, seen)`` triple recurs constantly.
    """
    return _schema_branches_cached(_ById(schema), _ById(node), seen)


@cache
def _schema_branches_cached(
    schema_key: _ById, node_key: _ById, seen: frozenset[str]
) -> list[JsonObject]:
    schema, node = schema_key.obj, node_key.obj
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref in seen:
            return []
        seen = seen | {ref}
    resolved = _resolve_ref(schema, node)
    result: list[JsonObject] = [resolved]
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = resolved.get(keyword)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if isinstance(branch, dict):
                result.extend(_schema_branches(schema, branch, seen))
    conditional = resolved.get("then")
    if isinstance(conditional, dict):
        result.extend(_schema_branches(schema, conditional, seen))
    return result


def _resolve_ref(root: JsonObject, node: JsonObject) -> JsonObject:
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        return node
    target: JsonValue = root
    for part in ref[2:].split("/"):
        if not isinstance(target, dict):
            return node
        target = target[part]
    return target if isinstance(target, dict) else node


# Matches blank lines and comment-only lines; both are transparent to the
# block-extent scan in `_delete_paths_in_yaml_text`.
_YAML_BLANK_OR_COMMENT_RE = re.compile(r"^\s*(#.*)?$")


def _delete_paths_in_yaml_text(yaml_text: str, paths: Sequence[DocumentPath]) -> str:
    """Remove the block-mapping key at each of *paths*, and its nested block.

    *paths* are the concrete document paths the in-memory walk actually struck
    (``_apply_deletions``' second return). Replaying them is what makes
    ``dct migrate`` and the in-memory migration one rule: this rewriter has no
    schema of its own, and every attempt to re-derive the decision from key
    names alone over-fires on a leaf that is retired at one position and live
    at another (``style.color``: dead on a board, working on eight chart
    families). It is also why there is no ancestor pruning here — the walk
    reports the emptied parent it removed as a path of its own.

    ``set_board_values`` is not the tool despite addressing the same path
    grammar: it refuses to delete a key holding a nested mapping, which is most
    of what a retired grammar leaves behind (``style.page``,
    ``conditional_formatting``).

    A path whose key does not resolve to a block-mapping line of its own is
    skipped rather than guessed at (``_yaml_key_lines``). The file is not
    written on a skip: ``migrate_yaml_text``'s equality check against
    ``staged`` sees the divergence and refuses it.
    """
    if not paths:
        return yaml_text
    lines = yaml_text.split("\n")
    key_lines = _yaml_key_lines(yaml_text)
    to_delete: set[int] = set()
    promotions: list[tuple[int, int]] = []
    for path in paths:
        located = key_lines.get(path)
        if located is None:
            continue
        index, column = located
        to_delete.add(index)
        # The key's whole nested block: every line down to the last one
        # indented past it (blank and comment lines inside that span
        # included). A scalar leaf has none, and the range stays empty.
        block_end = index
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if not _YAML_BLANK_OR_COMMENT_RE.match(line):
                if len(line) - len(line.lstrip(" ")) <= column:
                    break
                block_end = cursor
            cursor += 1
        to_delete.update(range(index + 1, block_end + 1))
        if lines[index].lstrip(" ").startswith("- "):
            promotions.append((block_end, column))
    for block_end, column in promotions:
        _promote_list_item_dash(lines, block_end, column, to_delete)
    return "\n".join(line for i, line in enumerate(lines) if i not in to_delete)


def _promote_list_item_dash(
    lines: list[str], block_end: int, column: int, to_delete: set[int]
) -> None:
    """Move a deleted item-leading key's ``-`` onto the item's next key.

    A sequence item's first key shares its line with the dash (``- style:``),
    so striking that key takes the marker with it and orphans the rest of the
    item at an indentation that no longer parses. Rewriting the next surviving
    key at the same column to carry the dash keeps the item whole. An item that
    held nothing else has no next key to promote and the scan finds none.
    """
    for index in range(block_end + 1, len(lines)):
        line = lines[index]
        if _YAML_BLANK_OR_COMMENT_RE.match(line):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < column:
            return
        if indent == column and index not in to_delete:
            dash = column - 2
            lines[index] = " " * dash + "-" + " " * (column - dash - 1) + line[column:]
            return


def _yaml_key_lines(yaml_text: str) -> dict[DocumentPath, tuple[int, int]]:
    """Document path -> the (line, column) its block-mapping key sits at.

    Composed from the YAML node tree rather than scanned, so sequence indices
    and nesting come from the parser instead of an indentation heuristic.

    A key the rewriter cannot act on is left out, and the caller skips it: a
    flow-style key (``style: {color: x}``) shares its parent's line, so
    deleting the line would take the parent too, and a key inside a block
    scalar is prose, not structure. Both are recognized the same way —
    everything left of the key on its own line must be indentation, optionally
    with the sequence dashes that legitimately precede an item's first key.
    """
    from dbt_charts.core.compile.parse.parser import compose_yaml

    lines = yaml_text.split("\n")
    out: dict[DocumentPath, tuple[int, int]] = {}

    def walk(node: yaml.Node, prefix: DocumentPath) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    continue
                key = str(key_node.value)
                path = (*prefix, key)
                line = key_node.start_mark.line
                column = key_node.start_mark.column
                if line < len(lines) and _is_block_key_line(lines[line], column, key):
                    out[path] = (line, column)
                walk(value_node, path)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, (*prefix, index))

    walk(compose_yaml(yaml_text), ())
    return out


def _is_block_key_line(line: str, column: int, key: str) -> bool:
    """Whether *key* opens a block mapping entry at *column* on *line*."""
    return line[column:].startswith(f"{key}:") and not line[:column].strip(" -")


def _format_path(path: YamlKeyPath) -> str:
    return ".".join(path)
