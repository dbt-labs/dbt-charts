"""Board-resolution merge engine.

Provides the generic ``merge_patches`` binary merge and the extends/metas
resolution layer that the compilation pipeline builds on. Public functions:

    merge_marker(field)              -> Merge | None
    strategy(field)                  -> Strategy
    merge_patches(lower, upper)      -> P
    scope_patch(inherited, own)      -> StylePatch | None
    merge_extends(node, board_path, boards_root) -> BoardPatch
    merge_metas(board_dir, boards_root) -> BoardPatch
    merged_patch(board_node, board_path, boards_root) -> BoardPatch

No field is named in merge_patches; strategy flows from the ``Merge``
annotation, with type-inference as the fallback.

For extends resolution:
  - Theme names (built-in) load the matching theme YAML from the built-in
    themes directory so style data flows through the standard engine path.
  - Relative paths (contain ``/`` or end with ``.yaml``/``.yml``) are
    resolved from the authoring file's own directory — anchored at load
    time before merge (cross-dir anchoring).
  - Named boards (simple identifiers) are looked up in ``boards_root``.
  - Cycle detection raises ``CompilationError`` naming the offending relpath.

File identity is the POSIX project-relative string (``ProjectPath.relpath``).
Project-content reads go through the ``Project`` seam — no direct
``Path.read_text``/``Path.exists`` on project files. Built-in theme YAML is
immutable package data (not project content) and is read via
``importlib.resources``, not the ``Project`` seam.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from functools import cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, TypeVar, overload

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from dbt_charts.core.compile.errors import CompilationError, FileValidationError
from dbt_charts.core.compile.file_refs import anchor_images, resolve_ref
from dbt_charts.core.compile.models.markers import (
    Exclusive,
    Merge,
    NotInherited,
    ResetOnSet,
    Strategy,
)
from dbt_charts.core.compile.models.style.authored import (
    PaddingStylePatch,
    StylePatch,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_EXTENDS_UNRESOLVED,
    WARN_INHERITED_LAYOUT_REPLACED,
)

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory, ProjectPath

P = TypeVar("P", bound=BaseModel)


def deep_merge_dict(
    base: Mapping[str, Any], overlay: Mapping[str, Any]
) -> dict[str, Any]:
    """Recursively merge *overlay* onto *base*, returning a new dict.

    The plain-dict counterpart to ``merge_patches``/``merge_onto_base`` — for
    config sections and Vega-Lite config dicts, which have no BaseModel shape
    to dispatch on. Rebuilds only the branches touched by the overlay; overlay
    leaves are deep-copied so later mutation cannot bleed back into reusable
    defaults.
    """
    result = dict(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(value, Mapping)
        ):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def merge_marker(field: FieldInfo) -> Merge | None:
    """Return the Merge annotation on *field*, or None if absent."""
    for m in field.metadata:
        if isinstance(m, Merge):
            return m
    return None


def _unwrap_type(field: FieldInfo) -> type:
    """Strip Optional / Annotated wrappers and return the base type.

    For ``str | None`` returns ``str``.
    For ``Annotated[dict[str, X] | None, ...]`` returns ``dict``.
    For ``Annotated[list[X] | None, ...]`` where the constraint sits on an
    *inner* Annotated
    (``Annotated[Annotated[list[X], Field(min_length=2)] | None, ...]``,
    a nested constraint that survives ``build_patch_model_ext`` — see
    ``ScaleContinuousStyle.domain``) also returns ``list``: Annotated and
    Union can alternate at any depth, so both are peeled in a loop rather
    than once each.
    Returns ``type(None)`` when the annotation cannot be resolved.
    """
    ann = field.annotation
    while True:
        if typing.get_origin(ann) is Annotated:
            ann = typing.get_args(ann)[0]
            continue
        # Peel Union / X | None — covers both typing.Union and types.UnionType
        origin = typing.get_origin(ann)
        if origin is typing.Union or isinstance(ann, types.UnionType):
            non_none = [a for a in typing.get_args(ann) if a is not type(None)]
            if non_none:
                ann = non_none[0]
                continue
        break
    # Unwrap generic aliases (dict[str, X] → dict, list[str] → list)
    base = typing.get_origin(ann) or ann
    return base  # type: ignore[return-value]


def _is_model(t: type) -> bool:
    # t may be a generic alias (e.g. dict from typing.get_origin) which causes
    # TypeError in issubclass — catch it rather than guard with isinstance.
    try:
        return issubclass(t, BaseModel)
    except TypeError:
        return False


def _is_dict(t: type) -> bool:
    return t is dict


def strategy(field: FieldInfo) -> Strategy:
    """Return the effective merge strategy for *field*.

    Precedence:
    1. Explicit ``Merge(file=...)`` when marker is set.
    2. Type-inferred: BaseModel→deep, dict→by_key, else→override (lists included).
    """
    m = merge_marker(field)
    if m is not None:
        return m.file
    t = _unwrap_type(field)
    if _is_model(t):
        return Strategy.DEEP
    if _is_dict(t):
        return Strategy.BY_KEY
    return Strategy.OVERRIDE


@cache
def _exclusive_groups(model: type[BaseModel]) -> tuple[tuple[str, ...], ...]:
    """The field names of each ``Exclusive`` group declared on *model*."""
    groups: dict[str, list[str]] = {}
    for name, field in model.model_fields.items():
        for marker in field.metadata:
            if isinstance(marker, Exclusive):
                groups.setdefault(marker.group, []).append(name)
    return tuple(tuple(members) for members in groups.values())


def _clear_exclusive_groups(
    upper: BaseModel,
    out: dict[str, Any],  # type-state: explicit_any — merged field values of any model
) -> None:
    """Zero every member of each exclusive group that *upper* sets, in *out*."""
    for members in _exclusive_groups(type(upper)):
        if any(
            name in upper.model_fields_set and getattr(upper, name) is not None
            for name in members
        ):
            for name in members:
                out[name] = None


def _reset_on_set(lower: P, upper: BaseModel) -> P:
    """``lower`` stripped of the fields *upper*'s type declares reset on set.

    Reads the ``merge_reset`` declaration (``ResetOnSet``): when upper sets the
    declared field, the layer replaces what lower put there, so lower's reset
    fields drop and the rest survive.
    """
    reset: ResetOnSet | None = getattr(  # type-state: silent_fallback — opt-in
        type(upper), "merge_reset", None
    )
    if reset is None or getattr(upper, reset.field) is None:
        return lower
    return type(lower).model_construct(
        _fields_set=lower.model_fields_set - reset.resets,
        **{
            name: None if name in reset.resets else getattr(lower, name)
            for name in type(lower).model_fields
        },
    )


def merge_patches(lower: P, upper: P) -> P:
    """Binary merge of two patch models of the same type.

    Semantics per field:
    - unset in upper (name not in model_fields_set): keep lower
    - upper is None (explicit null): clear to None
    - lower is None: take upper
    - else: apply strategy (override / by_key / deep)

    A ``ResetOnSet`` declaration is applied first, then ``Exclusive`` groups
    (when upper sets any member of a group, lower's members are zeroed), then
    the field loop.

    The returned model is constructed with an accurate ``model_fields_set``
    that reflects only fields genuinely authored in this merge, so subsequent
    merges do not mistake default-None values for explicit nulls.
    """
    lower = _reset_on_set(lower, upper)
    out: dict[str, Any] = {}
    # Pre-populate with lower values so exclusive groups can clear them.
    for name in type(upper).model_fields:
        out[name] = getattr(lower, name)

    _clear_exclusive_groups(upper, out)

    result_fields_set: set[str] = set()

    for name, field in type(upper).model_fields.items():
        lo = out[name]
        hi = getattr(upper, name)

        if name not in upper.model_fields_set:
            # Field not written in upper → keep whatever lo is (may be cleared
            # by an exclusive group, or the original lower value).
            out[name] = lo
            if name in lower.model_fields_set or lo is not None:
                result_fields_set.add(name)
        elif hi is None:
            # Explicit null in upper → clear the inherited value.
            out[name] = None
            result_fields_set.add(name)
        elif lo is None:
            # Lower has nothing; upper provides the first value.
            out[name] = hi
            result_fields_set.add(name)
        else:
            # Both lo and hi are set and non-None: resolve the strategy now.
            out[name] = _combine_field(
                lo,
                hi,
                recurse=merge_patches,
                strat=strategy(field),
            )
            result_fields_set.add(name)

    return type(upper).model_construct(_fields_set=result_fields_set, **out)


@cache
def _not_inherited_fields(model: type[BaseModel]) -> frozenset[str]:
    """The field names of *model* marked ``NotInherited``."""
    return frozenset(
        name
        for name, field in model.model_fields.items()
        if any(isinstance(marker, NotInherited) for marker in field.metadata)
    )


@overload
def scope_patch(inherited: StylePatch | None, own: StylePatch) -> StylePatch: ...
@overload
def scope_patch(
    inherited: StylePatch | None, own: StylePatch | None
) -> StylePatch | None: ...
def scope_patch(
    inherited: StylePatch | None, own: StylePatch | None
) -> StylePatch | None:
    """The patch in force inside a nested scope, given its parent's.

    A scope that authors nothing (``own is None``) inherits the parent's
    patch unchanged. A scope with nothing to inherit from (``inherited is
    None``, the root or an unstyled lineage) takes its own patch as-is.
    Otherwise ``own`` merges onto ``inherited`` once the fields marked
    ``NotInherited`` are dropped from it.

    Shared by the compile cascade (``compile_board_resolved_style``) and any
    read-only walk that needs to mirror it (design-verb scope offering) —
    both must derive the same in-force patch from the same lineage.
    """
    if own is None:
        return inherited
    if inherited is None:
        return own
    dropped = _not_inherited_fields(type(inherited))
    kept = type(inherited).model_construct(
        _fields_set=inherited.model_fields_set - dropped,
        **{
            name: None if name in dropped else getattr(inherited, name)
            for name in type(inherited).model_fields
        },
    )
    return merge_patches(kept, own)


# ---------------------------------------------------------------------------
# Base-terminal merge engine
# ---------------------------------------------------------------------------


def _combine_field(
    lo: Any,
    hi: Any,
    *,
    recurse: Callable[[Any, Any], Any],
    strat: str = "",
) -> Any:
    """Combine two present, non-None field values — the ONE shared dispatch ladder.

    Called by both ``merge_patches`` and ``merge_onto_base``. Owns the only
    place in the engine that decides recurse vs dict-union vs override.

    Args:
        lo:      Lower / base value (present and non-None).
        hi:      Upper / patch value (present and non-None).
        recurse: Callable for nested BaseModel merges. Caller supplies its own
                 recursion (either ``merge_patches`` or ``merge_onto_base``).
        strat:   Resolved strategy string (from ``strategy()``).
                 ``"override"`` → return hi;
                 empty string (default, used by merge_onto_base) and all
                 other values fall through to isinstance dispatch.
    """
    if strat == "override":
        return hi
    if isinstance(lo, BaseModel) and isinstance(hi, BaseModel):
        return recurse(lo, hi)
    if isinstance(lo, dict) and isinstance(hi, dict):
        if strat == "deep":
            # Entries present on both sides merge as models, so a theme layer
            # can add one field to an entry a lower layer declared.
            return {
                **lo,
                **{
                    key: recurse(lo[key], value)
                    if isinstance(lo.get(key), BaseModel)
                    and isinstance(value, BaseModel)
                    else value
                    for key, value in hi.items()
                },
            }
        return {**lo, **hi}
    return hi


def merge_onto_base(base: P, patch: BaseModel | None) -> P:
    """Merge a patch (all-Optional) onto a compiled base, returning a validated base-typed result.

    This is the base-terminal mode of the merge engine. It differs from
    ``merge_patches`` in three ways:

    1. **Presence comes only from ``model_fields_set``.** An omitted field inherits;
       a set field contributes its value, including explicit ``None``.
    2. **Terminal: validated.** The result is produced with
       ``type(base).model_validate(result)`` so validators run, then reconstructed
       from those validated values with accurate field-set provenance.
    3. **Set patch-only fields are errors.** Patch declarations absent from the
       compiled base are inert while omitted, but cannot disappear when authored.

    Dispatch (same conceptual walk as merge_patches, different semantics):

    - ``patch is None`` → return *base* unchanged.
    - field omitted from patch → inherit from base.
    - set patch field is None → clear to None; terminal validation decides whether
      the compiled field is nullable.
    - A patch whose type declares ``merge_reset`` and sets that field replaces the
      declared base fields outright (a named font is not a tweak of what the
      theme bound there).
    - Both base_val and patch_val are BaseModel → recurse via merge_onto_base.
    - Both are dict → key-wise merge (patch keys win, base keys survive; a
      ``Merge(Strategy.DEEP)`` dict also merges entries present on both sides).
    - patch_val is BaseModel, base_val is None → seed from
      ``model_dump(exclude_unset=True)`` and let terminal validation accept a
      complete child or reject an incomplete one.
    - Otherwise → patch_val replaces base_val.
    """
    if patch is None:
        return base
    base_fields = type(base).model_fields
    patch_fields_set = patch.model_fields_set
    unknown_fields = patch_fields_set - base_fields.keys()
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise TypeError(
            f"{type(patch).__name__} sets fields absent from "
            f"{type(base).__name__}: {names}"
        )
    if not patch_fields_set:
        return base
    base = _reset_on_set(base, patch)

    result: dict[str, Any] = {}
    result_fields_set = set(base.model_fields_set)
    for field_name in base_fields:
        base_val = getattr(base, field_name)
        if field_name not in patch_fields_set:
            result[field_name] = base_val
            continue

        result_fields_set.add(field_name)
        patch_val = getattr(patch, field_name)
        if patch_val is None:
            result[field_name] = None
        elif base_val is not None:
            marker = merge_marker(base_fields[field_name])
            result[field_name] = _combine_field(
                base_val,
                patch_val,
                recurse=merge_onto_base,
                strat="deep" if marker and marker.file is Strategy.DEEP else "",
            )
        elif isinstance(patch_val, BaseModel):
            result[field_name] = patch_val.model_dump(exclude_unset=True)
        else:
            result[field_name] = patch_val

    validated = type(base).model_validate(result)
    values = {name: getattr(validated, name) for name in base_fields}
    return type(base).model_construct(_fields_set=result_fields_set, **values)


# ---------------------------------------------------------------------------
# Extends / metas resolution layer
# ---------------------------------------------------------------------------

# Fields stripped from a fragment before building its own-field patch.
# Identity fields (id, aliases, schema_version) must not propagate; extends is
# consumed here.
_EXTENDS_STRIP: set[str] = {"id", "aliases", "schema_version", "extends"}


@cache
def get_theme_names() -> frozenset[str]:
    """Cached frozenset of built-in theme stems.

    Public because the extends vocabulary is shared: normalization reads it to
    pick the effective theme, validation to reject a name nothing resolves.
    """
    from dbt_charts.core.compile.config import list_built_in_themes

    return frozenset(list_built_in_themes())


def is_path_ref(entry: str) -> bool:
    """True when entry looks like a file path rather than a plain name.

    A path ref contains ``/`` or ends with ``.yaml`` / ``.yml``. Public for the
    same reason as :func:`get_theme_names` — it is the rule that separates a
    board reference from a theme name, and validation applies it too.
    """
    return "/" in entry or entry.endswith((".yaml", ".yml"))


@dataclasses.dataclass(frozen=True)
class _ExtendCtx:
    """Internal threading context for extends resolution."""

    board_dir: ProjectDirectory | None  # None for theme-only resolution (no project)
    boards_root: ProjectDirectory | None  # project root for named-board lookup
    theme_names: frozenset[str]  # built-in theme stems


def _fragment_from_yaml(
    content: str, relpath: str, written_in: ProjectDirectory | None
) -> BaseModel:
    """Parse extends-fragment YAML text into a BoardPatch.

    Fragments are partial content — they don't need the AuthoredBoard
    "must have layout/title" invariant, so we validate directly as
    BoardPatch (all-optional, no content requirement). *relpath* is the
    fragment's identity, used only in error messages.

    *written_in* is the fragment's directory, which its markdown images are
    anchored at (``anchor_images``) before they are inherited; ``None`` is a
    built-in theme, package data we ship and keep current by construction, so
    it skips migration-currency detection entirely. A user-authored fragment
    still gets checked, against BoardPatch rather than AuthoredBoard — the
    latter's "must have layout/chart/etc" invariant would make every
    current-schema patch look like a migration candidate.

    Note: fragments must use canonical (non-shorthand) form. Chart
    shorthand (``- chart_name`` without a type key) and certain source
    shapes require the AuthoredBoard normalizer and will fail here with a
    CompilationError. Themes and meta files carry style, not charts, so
    this is acceptable and fails loudly rather than silently.

    Raises:
        CompilationError: invalid YAML (``ERR-YAML-SYNTAX``) or schema
            validation failure.
    """
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.models.board.patch import (
        BOARD_PATCH_ADAPTER,
        BoardPatch,
    )
    from dbt_charts.core.compile.parse.parser import load_yaml_text

    loaded = load_yaml_text(content, source=f"extends: {relpath}")
    # An empty fragment file is a documented empty patch, not a fault.
    data = {} if loaded is None else loaded
    if not isinstance(data, dict):
        raise CompilationError(
            f"extends: {relpath} must be a YAML mapping, got {type(data).__name__}"
        )
    if written_in is not None:
        from dbt_charts.core.compile.migrations import prepare_board_mapping

        data = prepare_board_mapping(data, model=BoardPatch)
        anchor_images(data, written_in)
    try:
        return BOARD_PATCH_ADAPTER.validate_python(data)
    except PydanticValidationError as e:
        raise FileValidationError.from_pydantic(
            f"extends: schema error in {relpath}", e, content, relpath
        ) from e


def _load_fragment(path: ProjectPath) -> BaseModel:
    """Load a resolved ``extends:`` fragment as a BoardPatch.

    Raises:
        CompilationError: unreadable, invalid YAML, or schema validation failure.
    """
    try:
        content = path.read_text()
    except OSError as e:
        raise CompilationError(f"extends: cannot read {path.relpath}: {e}") from e
    return _fragment_from_yaml(content, path.relpath, path.parent)


def _fragment_own_patch(fragment: BaseModel) -> BaseModel:
    """Extract a BoardPatch from a fragment's explicitly-set fields, minus identity/extends."""
    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER

    data = fragment.model_dump(exclude_unset=True, exclude=_EXTENDS_STRIP)
    return BOARD_PATCH_ADAPTER.validate_python(data)


def _named_board_path(entry: str, ctx: _ExtendCtx) -> ProjectPath | None:
    """Project board file a plain (non-path, non-theme) ``extends:`` entry
    names, if one exists at the project root — ``None`` otherwise.

    The one lookup both a named-board resolution and the retired-theme
    redirect need, so they can never drift into disagreeing about whether a
    project board shadows a name.
    """
    if ctx.boards_root is None:
        return None
    for ext in (".yaml", ".yml"):
        candidate = ctx.boards_root / f"{entry}{ext}"
        if candidate.exists():
            return candidate
    return None


def _resolve_fragment_file(entry: str, ctx: _ExtendCtx) -> ProjectPath:
    """Resolve an extends entry to a ProjectPath handle.

    Raises:
        CompilationError: Entry is not a known theme, relative path, or named board.
    """
    if is_path_ref(entry):
        return resolve_ref(entry, ctx.board_dir, "file")

    from dbt_charts.core.compile.config import user_facing_theme_names

    if ctx.boards_root is not None:
        # Named board — look up in the project root.
        candidate = _named_board_path(entry, ctx)
        if candidate is not None:
            return candidate
        # The caller already ruled out every theme name, and the board lookup
        # just failed too — the one site that can offer the board arm as a fix.
        raise CompilationError.from_code(
            ERR_EXTENDS_UNRESOLVED,
            entry=entry,
            available=user_facing_theme_names(),
        )

    # Theme-build lane only (no project): a shipped theme YAML extends
    # something that isn't a theme, which is broken package data, not authoring.
    raise CompilationError(
        f"extends: {entry!r} is not a known theme name or a relative path. "
        f"Known themes: {sorted(ctx.theme_names)}"
    )


def _retired_theme_redirect(entry: str, ctx: _ExtendCtx) -> str | None:
    """Return the migrated theme name for a retired ``extends:`` entry, or
    ``None`` if *entry* is not retired or a same-named project board shadows it.

    ``extends:``'s authored type (``ThemeName | str | list[str]``) accepts
    theme names, board names, and paths alike, so a retired builtin name is
    ambiguous with a real project board of the same name in a way ``theme:``
    never is — unlike ``theme:``'s identity-path Move
    (``migrations.py``'s ``_apply_identity_moves``), which runs unconditionally
    on the raw mapping before any board lookup is even possible. This redirect
    is asked first in ``_resolve_entry``, before ``_resolve_fragment_file``
    ever runs, so it re-checks the same named-board condition itself (via
    ``_named_board_path``, shared with ``_resolve_fragment_file``) rather
    than relying on order — a real project board must win either way.
    """
    from dbt_charts.core.compile.migrations.migrations import retired_theme_renames

    replacement = retired_theme_renames().get(entry)
    if replacement is None:
        return None
    # THEME_RENAMES's declared value type (MappedScalar) is shared with
    # Move.value_map's generic contract; every entry it actually holds is a
    # theme-name string.
    assert isinstance(replacement, str)
    if _named_board_path(entry, ctx) is not None:
        return None
    return replacement


def _resolve_entry(
    entry: str,
    ctx: _ExtendCtx,
    seen: frozenset[str],
    theme_sink: list[str] | None = None,
    layer_sink: list[tuple[str, BaseModel]] | None = None,
) -> BaseModel:
    """Resolve one extends entry to a BoardPatch.

    Args:
        entry:      Single extends entry — theme name, board name, or relative path.
        ctx:        Resolution context (board_dir, boards_root, theme_names).
        seen:       Project-relative relpaths already in the current chain (cycle guard).
        theme_sink: When not None (board-compile lane), built-in theme names are
                    recorded here and return EMPTY_PATCH instead of loading the
                    theme YAML. The normalizer applies the theme exactly once via
                    get_theme_style(). When None (theme-build lane), the theme YAML
                    is read via importlib.resources and folded.

    Returns:
        BoardPatch for this entry.

    Raises:
        CompilationError: Unresolvable entry, cycle detected, or I/O / parse error.
    """
    if not is_path_ref(entry):
        redirect = _retired_theme_redirect(entry, ctx)
        if redirect is not None:
            from dbt_charts.core.compile.migrations.migrations import warn_migration

            warn_migration(
                f"dbt charts resolved retired `extends:` theme name {entry!r} "
                f"to {redirect!r} in memory. `dct migrate` does not rewrite "
                "this position — update the YAML by hand.",
                stacklevel=2,
            )
            return _resolve_entry(redirect, ctx, seen, theme_sink, layer_sink)

    if not is_path_ref(entry) and entry in ctx.theme_names:
        if theme_sink is not None:
            # Board-compile lane: record the theme name for the compiler to inject
            # as the effective extends so the normalizer applies it exactly once.
            from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

            theme_sink.append(entry)
            return EMPTY_PATCH
        # Theme-build lane: built-in themes are immutable package data with a
        # names-only extends chain — read via importlib.resources (no Project
        # seam). board_dir is never consulted for a theme, so reuse ctx.
        from dbt_charts.core.compile.config import _built_in_unified_theme_dir

        theme_relpath = f"{entry}.yaml"
        if theme_relpath in seen:
            raise CompilationError(
                f"Circular extends: {theme_relpath} is already in the extends chain"
            )
        theme_file = _built_in_unified_theme_dir.joinpath(theme_relpath)
        fragment = _fragment_from_yaml(
            theme_file.read_text(encoding="utf-8"), theme_relpath, None
        )
        extends_patch = _merge_extends_inner(
            fragment, ctx, seen | {theme_relpath}, theme_sink
        )
        return merge_patches(extends_patch, _fragment_own_patch(fragment))

    fragment_path = _resolve_fragment_file(entry, ctx)
    fragment_relpath = fragment_path.relpath
    if fragment_relpath in seen:
        raise CompilationError(
            f"Circular extends: {fragment_relpath} is already in the extends chain"
        )
    from dbt_charts.core.compile.migrations import migration_notice_file

    with migration_notice_file(fragment_relpath):
        fragment = _load_fragment(fragment_path)
        fragment_ctx = _ExtendCtx(
            board_dir=fragment_path.parent,
            boards_root=ctx.boards_root,
            theme_names=ctx.theme_names,
        )
        extends_patch = _merge_extends_inner(
            fragment,
            fragment_ctx,
            seen | {fragment_relpath},
            theme_sink,
            layer_sink,
        )
    own_patch = _fragment_own_patch(fragment)
    if layer_sink is not None:
        layer_sink.append((fragment_relpath, own_patch))
    return merge_patches(extends_patch, own_patch)


def _merge_extends_inner(
    node: BaseModel,
    ctx: _ExtendCtx,
    seen: frozenset[str],
    theme_sink: list[str] | None = None,
    layer_sink: list[tuple[str, BaseModel]] | None = None,
) -> BaseModel:
    """Recursive core of merge_extends."""
    from dbt_charts.core.compile.models.board.patch import EMPTY_PATCH

    raw = node.extends  # type: ignore[attr-defined]
    if raw is None:
        return EMPTY_PATCH

    entries: list[str] = [raw] if isinstance(raw, str) else list(raw)
    if not entries:
        return EMPTY_PATCH

    acc: BaseModel = EMPTY_PATCH
    for entry in entries:  # low → high priority
        patch = _resolve_entry(entry, ctx, seen, theme_sink, layer_sink)
        acc = merge_patches(acc, patch)
    return acc


def resolve_built_in_theme(name: str) -> BaseModel:
    """Fold a built-in theme's extends chain into a BoardPatch (for get_theme_style).

    Themes are package data read via importlib.resources, with names-only
    extends chains, so the ctx has no project (``board_dir``/``boards_root`` are
    None) — only the theme-name set.
    """
    from dbt_charts.core.compile.models.board.patch import BOARD_PATCH_ADAPTER

    node = BOARD_PATCH_ADAPTER.validate_python({"extends": name})
    ctx = _ExtendCtx(board_dir=None, boards_root=None, theme_names=get_theme_names())
    return _merge_extends_inner(node, ctx, frozenset(), theme_sink=None)


def merge_extends(
    node: BaseModel,
    board_path: ProjectPath,
    boards_root: ProjectDirectory | None = None,
    theme_sink: list[str] | None = None,
    layer_sink: list[tuple[str, BaseModel]] | None = None,
) -> BaseModel:
    """Fold the extends chain of *node* low→high into a BoardPatch.

    Each extends entry is resolved in order — first entry is lowest priority,
    last is highest. Entries can be:

    * Built-in theme names → YAML loaded from built-in themes directory
    * Relative file paths (contain ``/`` or end with ``.yaml``) → loaded
      from *board_path*'s directory (cross-dir anchoring: a fragment's own
      relative paths are resolved from THAT fragment's directory, not root).
    * Named board identifiers (no slashes, no .yaml) → looked up in
      *boards_root*. Requires *boards_root* to be set; raises ``CompilationError``
      otherwise.

    Args:
        node:        AuthoredBoard whose ``extends`` field to fold.
        board_path:   ProjectPath handle for node's source file.
        boards_root:  Root directory for named-board lookup. Optional.
        theme_sink:  When not None (board-compile lane), built-in theme names are
                     collected here instead of being loaded as style fragments.
        layer_sink:  When not None, each fragment's ``(relpath, own patch)`` is
                     appended in priority order, before the layers are folded.

    Returns:
        BoardPatch accumulating all inherited fields. ``EMPTY_PATCH`` when
        ``node.extends`` is ``None`` or an empty list.

    Raises:
        CompilationError: Unresolvable entry, circular extends, or I/O / parse failure.
    """
    ctx = _ExtendCtx(
        board_dir=board_path.parent,
        boards_root=boards_root,
        theme_names=get_theme_names(),
    )
    return _merge_extends_inner(node, ctx, frozenset(), theme_sink, layer_sink)


def merge_metas(
    board_dir: ProjectDirectory,
    boards_root: ProjectDirectory,
    theme_sink: list[str] | None = None,
    layer_sink: list[tuple[str, BaseModel]] | None = None,
) -> BaseModel:
    """Fold all meta.yml files from *boards_root* down to *board_dir* into a BoardPatch.

    Walks from *boards_root* toward *board_dir* collecting every meta.yml
    found on the path. For each meta, its own ``extends`` chain is resolved
    first, then its own fields are merged on top. All meta patches are then
    folded root→leaf (root meta is lowest priority; nearest meta wins).

    File identity is the POSIX project-relative relpath; no symlink resolution
    is needed — symlinks resolve transparently inside the ``Project`` filesystem backing.

    Args:
        board_dir:    The board file's parent directory (ProjectDirectory).
        boards_root:  Upper bound for the meta walk (ProjectDirectory).
        layer_sink:   When not None, each meta's extends fragments and then the
                      meta's own ``(relpath, patch)`` are appended root→leaf,
                      before the layers are folded.

    Returns:
        BoardPatch with all meta layers folded in. ``EMPTY_PATCH`` when no
        meta files exist between *boards_root* and *board_dir*.
    """
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.models.board.patch import (
        BOARD_PATCH_ADAPTER,
        EMPTY_PATCH,
        BoardPatch,
    )
    from dbt_charts.core.compile.parse.meta import find_meta_files, load_meta_file

    # Validate that board_dir is within boards_root before delegating.
    board_dir_relpath = PurePosixPath(board_dir.relpath)
    root_relpath = PurePosixPath(boards_root.relpath)
    try:
        board_dir_relpath.relative_to(root_relpath)
    except ValueError:
        raise ValueError(
            f"directory {board_dir.relpath!r} is not under boards_root "
            f"{boards_root.relpath!r}"
        ) from None

    # A synthetic file in board_dir lets find_meta_files walk the same root→leaf
    # chain without duplicating the parent-chain traversal logic.
    meta_paths = find_meta_files(board_dir / "_", boards_root)

    if not meta_paths:
        return EMPTY_PATCH

    acc: BaseModel = EMPTY_PATCH
    for meta_path in meta_paths:
        # load_meta_file strips the lint: key and returns only board-content data.
        # This prevents BoardPatch (extra="forbid") from rejecting the lint field.
        meta_data, _ = load_meta_file(meta_path)
        from dbt_charts.core.compile.migrations import (
            migration_notice_file,
            prepare_board_mapping,
        )

        with migration_notice_file(meta_path.relpath):
            try:
                meta_mapping = prepare_board_mapping(meta_data, model=BoardPatch)
                anchor_images(meta_mapping, meta_path.parent)
                fragment = BOARD_PATCH_ADAPTER.validate_python(meta_mapping)
            except PydanticValidationError as e:
                raise FileValidationError.from_pydantic(
                    f"meta schema error in {meta_path.relpath}",
                    e,
                    meta_path.read_text(),
                    meta_path.relpath,
                ) from e
            meta_ctx = _ExtendCtx(
                board_dir=meta_path.parent,
                boards_root=boards_root,
                theme_names=get_theme_names(),
            )
            extends_patch = _merge_extends_inner(
                fragment, meta_ctx, frozenset(), theme_sink, layer_sink
            )
            own_patch = _fragment_own_patch(fragment)
            meta_patch: BaseModel = merge_patches(extends_patch, own_patch)
        if layer_sink is not None:
            layer_sink.append((meta_path.relpath, own_patch))
        acc = merge_patches(acc, meta_patch)
    return acc


_Held = tuple[str, int | None, object]  # type-state: object_annotation — compared only


def _replaced_layout_warnings(
    layers: Sequence[tuple[str, BaseModel]],
) -> list[Diagnostic]:
    """One warning per layer that replaces a non-empty layout or tag list.

    *layers* are every ``(source, own patch)`` that contributes to a board,
    low→high; the board's own layer has the empty source. A list set empty has
    nothing to lose when replaced, and restating an equal value loses nothing.
    """
    held: dict[str, _Held] = {}
    warnings: list[Diagnostic] = []
    for source, patch in layers:
        for group in (("tags",), *_exclusive_groups(type(patch))):
            written = [
                name
                for name in group
                if name in patch.model_fields_set and getattr(patch, name) is not None
            ]
            if not written:
                for name in group:
                    if name in patch.model_fields_set:
                        held.pop(name, None)
                continue
            for name in group:
                if (
                    name in held
                    and held[name][1] != 0
                    and held[name][2] != getattr(patch, name)
                ):
                    origin, count, _ = held[name]
                    warnings.append(
                        Diagnostic.from_code(
                            WARN_INHERITED_LAYOUT_REPLACED,
                            message=WARN_INHERITED_LAYOUT_REPLACED.message_template.format(
                                field=written[0],
                                replacer=f"in {source}" if source else "here",
                                inherited=f"the {count} {name}"
                                if count is not None
                                else f"the {name}",
                                source=origin,
                            ),
                            fix=WARN_INHERITED_LAYOUT_REPLACED.fix_template,
                            path=None if source else written[0],
                        )
                    )
                held.pop(name, None)
            for name in written:
                value = getattr(patch, name)
                held[name] = (
                    source,
                    len(value) if isinstance(value, list) else None,
                    value,
                )
    return warnings


def merged_patch(
    board_node: BaseModel,
    board_path: ProjectPath,
    boards_root: ProjectDirectory,
    theme_sink: list[str] | None = None,
    warning_sink: list[Diagnostic] | None = None,
) -> BaseModel:
    """Compute the fully-merged patch for a board: metas folded under board.

    Priority (low → high):
        meta chain (root → leaf) < board extends chain < board own fields

    The result is a BoardPatch with all inherited fields resolved. It does NOT
    apply inheritance (Cascade/Inherit markers) — that is a downstream step.

    Args:
        board_node:  The board's own fields as a BoardPatch (or any BaseModel with
            an ``extends`` attribute). Build from the raw YAML dict before
            calling this function.
        board_path:  ProjectPath handle for the board YAML file.
        boards_root: Directory that bounds the meta walk (typically project.directory(".")).
        theme_sink: When not None (board-compile lane in compiler.py), built-in
            theme names encountered in extends/meta chains are appended here
            instead of being loaded as style fragments. The last entry is the
            effective theme; compiler.py re-injects it so the normalizer applies
            the theme exactly once via get_theme_style(). When None (theme-build
            lane), theme YAMLs are loaded directly as style fragments.
        warning_sink: When not None, merge-time warnings are appended here.
    """
    # Base: merge of all meta files root→leaf
    layers: list[tuple[str, BaseModel]] = []
    base = merge_metas(board_path.parent, boards_root, theme_sink, layers)
    # Middle: extends chain of the board itself
    extends_patch = merge_extends(
        board_node, board_path, boards_root, theme_sink, layers
    )
    # Top: board's own explicitly-set fields (identity fields stripped)
    own_patch = _fragment_own_patch(board_node)
    if warning_sink is not None:
        warning_sink.extend(_replaced_layout_warnings([*layers, ("", own_patch)]))
    # fold: base < extends < own
    board_contribution = merge_patches(extends_patch, own_patch)
    return merge_patches(base, board_contribution)


def to_padding_style(padding: PaddingStyle | PaddingStylePatch) -> PaddingStyle:
    """Coerce a cascade-produced padding value into a real ``PaddingStyle``.

    ``merge_onto_base``'s required-field-seeding branch cannot distinguish a
    ``T | None`` field (which it knows to unwrap) from an ``Annotated[T, ...]``
    field with no ``None`` arm — ``padding``'s ``InheritSlot`` annotation is
    the latter — so it falls through to storing the raw ``PaddingStylePatch``
    it was handed instead of the merged ``PaddingStyle``. That patch is
    fully populated in practice (every side merges to a concrete float), so
    re-validating through ``PaddingStyle`` here is safe — including for an
    already-correct ``PaddingStyle`` input, which round-trips unchanged. A
    ``None`` side means the theme cascade produced a genuinely incomplete
    padding — raise rather than default it, per this repo's
    validate-and-error-fast rule.

    (``isinstance(padding, PaddingStyle)`` is not a usable guard here:
    ``PaddingStylePatch``'s ``TYPE_CHECKING`` stub declares it a subclass of
    ``PaddingStyle`` — the same lie that let the underlying bug through
    mypy in the first place — so a real subclass check would statically
    "prove" the patch branch unreachable.)
    """
    sides = padding.model_dump()
    missing = [side for side, value in sides.items() if value is None]
    if missing:
        raise ValueError(
            f"Chart padding is missing required side(s) {missing}: {padding!r}. "
            "The theme cascade must populate every side of PaddingStyle."
        )
    return PaddingStyle.model_validate(sides)
