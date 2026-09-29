"""Board-resolution merge engine.

Provides the generic ``merge_patches`` binary merge and the extends/metas
resolution layer that the compilation pipeline builds on. Public functions:

    merge_marker(field)              -> Merge | None
    strategy(field, nested)          -> str
    merge_patches(lower, upper, nested) -> P
    scope_patch(inherited, own)      -> P | None
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
import warnings
from collections.abc import Callable, Mapping
from copy import deepcopy
from functools import cache
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, TypeVar, overload

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from dbt_charts.core.compile.errors import CompilationError, MergeValidationError
from dbt_charts.core.compile.models.markers import Merge, Strategy
from dbt_charts.core.compile.models.style.authored import (
    PaddingStylePatch,
    TitleStylePatch,
)
from dbt_charts.core.compile.models.style.theme import PaddingStyle, TitleStyle
from dbt_charts.core.diagnostics.codes_compile import ERR_EXTENDS_UNRESOLVED

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory, ProjectPath

P = TypeVar("P", bound=BaseModel)

# Layout fields for the cross-axis clearing rule (policy ruling 2).
# Each of the four fields is its own axis; when upper declares any field
# different from lower's layout field(s), lower's fields are zeroed.
# Public: Cloud derives its "does this board declare content?" check from this
# set, so a new layout axis reaches both places at once.
LAYOUT_FIELDS: frozenset[str] = frozenset({"rows", "cols", "grid", "tabs"})


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
        # Peel Union / X | None — covers both typing.Union and the 3.10+ types.UnionType
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


def strategy(field: FieldInfo, nested: bool) -> Strategy:
    """Return the effective merge strategy for *field* under the given relation.

    Precedence:
    1. Explicit ``Merge(nested=...)`` when ``nested=True`` and marker is set.
    2. Explicit ``Merge(file=...)`` when marker is set.
    3. Type-inferred: BaseModel→deep, dict→by_key, else→override.

    Raises ``TypeError`` for list-typed fields with no explicit ``Merge`` marker.
    Silently defaulting list fields to override would clobber accumulated lists
    from lower layers — callers must declare intent explicitly.
    """
    m = merge_marker(field)
    if nested and m is not None and m.nested is not None:
        return m.nested
    if m is not None:
        return m.file
    t = _unwrap_type(field)
    if _is_model(t):
        return Strategy.DEEP
    if _is_dict(t):
        return Strategy.BY_KEY
    if t is list:
        raise TypeError(
            f"Field {field!r} has a list type but no explicit Merge marker. "
            "Declare an explicit Merge marker, e.g. Merge(Strategy.APPEND), "
            "Merge(Strategy.OVERRIDE), Merge(Strategy.CHILD)."
        )
    return Strategy.OVERRIDE


def _cross_axis_layout_clear(
    lower: P,
    upper: P,
    out: dict[str, Any],
) -> None:
    """Implement policy ruling 2: cross-axis layout closest-wins.

    When upper declares any layout field from a different axis than lower
    (e.g. lower has ``rows`` and upper has ``cols``), the lower axis fields
    are zeroed in *out* before the main merge loop processes them.

    Same-axis fields are left alone (append continues normally).
    ``out`` is mutated in place.
    """
    upper_fields = type(upper).model_fields
    lower_fields = type(lower).model_fields
    upper_layout = {
        name
        for name in LAYOUT_FIELDS
        if name in upper_fields
        and name in upper.model_fields_set
        and getattr(upper, name) is not None
    }
    lower_layout = {
        name
        for name in LAYOUT_FIELDS
        if name in lower_fields and getattr(lower, name) is not None
    }
    if not upper_layout or not lower_layout:
        return  # nothing to clear

    # Same axis = upper uses the same field(s) as lower. Each of the four
    # layout fields is its own axis. Overlap means same-axis: append applies.
    if upper_layout & lower_layout:
        return  # at least one shared field → same axis, normal strategy

    # Different axis: the upper fragment's axis wins; zero out lower's fields.
    for name in lower_layout:
        out[name] = None


def _child_strategy(field: FieldInfo, nested: bool) -> bool:
    """Return True iff this field uses the ``child`` strategy for this merge relation.

    Reads the Merge marker directly — never falls back to type inference — so
    it is safe to call on list-typed fields that carry no full Merge marker.
    """
    m = merge_marker(field)
    if m is None:
        return False
    effective = m.nested if (nested and m.nested is not None) else m.file
    return effective == Strategy.CHILD


def merge_patches(lower: P, upper: P, nested: bool) -> P:
    """Binary merge of two patch models of the same type.

    Semantics per field:
    - ``child``    (nested=True only): result = upper (even when upper is None/unset)
    - unset in upper (name not in model_fields_set): keep lower
    - upper is None (explicit null): clear to None
    - lower is None: take upper
    - else: apply strategy (override / append / by_key / deep)

    The cross-axis layout rule (policy ruling 2) is applied before the main
    loop: when upper declares a layout axis different from lower's, lower's
    layout fields are zeroed first.

    ``strategy()`` is called lazily — only when both lo and hi are non-None
    and the field is set in upper. This avoids raising TypeError for list-typed
    fields with no explicit Merge marker when neither patch sets that field.

    The returned model is constructed with an accurate ``model_fields_set``
    that reflects only fields genuinely authored in this merge, so subsequent
    merges do not mistake default-None values for explicit nulls.
    """
    out: dict[str, Any] = {}
    # Pre-populate with lower values so _cross_axis_layout_clear can read them.
    for name in type(upper).model_fields:
        out[name] = getattr(lower, name)

    _cross_axis_layout_clear(lower, upper, out)

    result_fields_set: set[str] = set()

    for name, field in type(upper).model_fields.items():
        # ``child`` is detected from the marker alone — no type inference needed.
        # It fires even when upper never set the field (that's the point of child).
        if _child_strategy(field, nested):
            out[name] = getattr(upper, name)
            if name in upper.model_fields_set:
                result_fields_set.add(name)
            continue

        lo = out[name]
        hi = getattr(upper, name)

        if name not in upper.model_fields_set:
            # Field not written in upper → keep whatever lo is (may be cross-axis
            # cleared to None, or the original lower value).
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
            # strategy() raises TypeError for list-typed fields with no Merge
            # marker — that case implies both sides carry a list, which is an
            # authoring error that deserves a loud failure.
            out[name] = _combine_field(
                lo,
                hi,
                recurse=lambda a, b: merge_patches(a, b, nested),
                strat=strategy(field, nested),
            )
            result_fields_set.add(name)

    return type(upper).model_construct(_fields_set=result_fields_set, **out)


@overload
def scope_patch(inherited: P | None, own: P) -> P: ...
@overload
def scope_patch(inherited: P | None, own: P | None) -> P | None: ...
def scope_patch(inherited: P | None, own: P | None) -> P | None:
    """The patch in force inside a nested scope, given its parent's.

    A scope that authors nothing (``own is None``) inherits the parent's
    patch unchanged. A scope with nothing to inherit from (``inherited is
    None``, the root or an unstyled lineage) takes its own patch as-is.
    Otherwise ``own`` merges onto ``inherited`` via ``merge_patches(...,
    nested=True)`` — the same nested-board relation used everywhere else.

    Shared by the compile cascade (``compile_board_resolved_style``) and any
    read-only walk that needs to mirror it (design-verb scope offering) —
    both must derive the same in-force patch from the same lineage.
    """
    if own is None:
        return inherited
    if inherited is None:
        return own
    return merge_patches(inherited, own, nested=True)


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
    place in the engine that decides append vs recurse vs dict-union vs override.

    Args:
        lo:      Lower / base value (present and non-None).
        hi:      Upper / patch value (present and non-None).
        recurse: Callable for nested BaseModel merges. Caller supplies its own
                 recursion (either ``merge_patches`` or ``merge_onto_base``).
        strat:   Resolved strategy string (from ``strategy()``).
                 ``"append"`` → concatenate; ``"override"`` → return hi;
                 empty string (default, used by merge_onto_base) and all
                 other values fall through to isinstance dispatch.
    """
    if strat == "append":
        return lo + hi
    if strat == "override":
        return hi
    if isinstance(lo, BaseModel) and isinstance(hi, BaseModel):
        return recurse(lo, hi)
    if isinstance(lo, dict) and isinstance(hi, dict):
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
    - Both base_val and patch_val are BaseModel → recurse via merge_onto_base.
    - Both are dict → key-wise merge (patch keys win, base keys survive).
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
            result[field_name] = _combine_field(
                base_val, patch_val, recurse=merge_onto_base
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
    content: str, relpath: str, *, is_builtin: bool = False
) -> BaseModel:
    """Parse extends-fragment YAML text into a BoardPatch.

    Fragments are partial content — they don't need the AuthoredBoard
    "must have layout/title" invariant, so we validate directly as
    BoardPatch (all-optional, no content requirement). *relpath* is the
    fragment's identity, used only in error messages.

    *is_builtin* skips migration-currency detection entirely: built-in
    theme YAML is package data we ship and keep current by construction,
    so there is nothing to detect. A user-authored fragment (the default)
    still gets checked, against BoardPatch rather than AuthoredBoard — the
    latter's "must have layout/chart/etc" invariant would make every
    current-schema patch look like a migration candidate.

    Note: fragments must use canonical (non-shorthand) form. Chart
    shorthand (``- chart_name`` without a type key) and certain source
    shapes require the AuthoredBoard normalizer and will fail here with a
    CompilationError. Themes and meta files carry style, not charts, so
    this is acceptable and fails loudly rather than silently.

    Raises:
        CompilationError: invalid YAML or schema validation failure.
    """
    import yaml
    from pydantic import ValidationError as PydanticValidationError

    from dbt_charts.core.compile.models.board.patch import (
        BOARD_PATCH_ADAPTER,
        BoardPatch,
    )
    from dbt_charts.core.utils import UniqueKeyLoader

    try:
        data = yaml.load(content, Loader=UniqueKeyLoader) or {}
    except yaml.YAMLError as e:
        raise CompilationError(f"extends: invalid YAML in {relpath}: {e}") from e
    if not isinstance(data, dict):
        raise CompilationError(
            f"extends: {relpath} must be a YAML mapping, got {type(data).__name__}"
        )
    if not is_builtin:
        from dbt_charts.core.compile.migrations import prepare_board_mapping

        data = prepare_board_mapping(data, model=BoardPatch)
    try:
        return BOARD_PATCH_ADAPTER.validate_python(data)
    except PydanticValidationError as e:
        from dbt_charts.core.compile.parse.source_map import (
            build_source_index,
            stamp_diagnostics,
        )
        from dbt_charts.core.compile.parse.yaml_error_formatter import (
            format_validation_errors_structured,
        )

        diags = format_validation_errors_structured(e, content)
        stamp_diagnostics(diags, *build_source_index(content, relpath))
        raise MergeValidationError(
            f"extends: schema error in {relpath}",
            diags,
        ) from e


def _load_fragment(path: ProjectPath) -> BaseModel:
    """Load a YAML extends fragment or meta file from a project path as a BoardPatch.

    Raises:
        CompilationError: file not found, unreadable, invalid YAML, or
            schema validation failure.
    """
    if not path.exists():
        raise CompilationError(f"extends: file not found: {path.relpath}")
    try:
        content = path.read_text()
    except OSError as e:
        raise CompilationError(f"extends: cannot read {path.relpath}: {e}") from e
    return _fragment_from_yaml(content, path.relpath)


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
        # Relative path — anchored to the authoring file's own directory.
        if ctx.board_dir is None:
            raise CompilationError(
                f"extends: relative path {entry!r} cannot be resolved without a "
                "project directory (built-in themes may only extend theme names)"
            )
        return ctx.board_dir / entry

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
            from dbt_charts.core.compile.migrations.migrations import (
                SchemaMigrationWarning,
            )

            warnings.warn(
                f"dbt charts resolved retired `extends:` theme name {entry!r} "
                f"to {redirect!r} in memory. `dct migrate` does not rewrite "
                "this position — update the YAML by hand.",
                SchemaMigrationWarning,
                stacklevel=2,
            )
            return _resolve_entry(redirect, ctx, seen, theme_sink)

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
            theme_file.read_text(encoding="utf-8"), theme_relpath, is_builtin=True
        )
        extends_patch = _merge_extends_inner(
            fragment, ctx, seen | {theme_relpath}, theme_sink
        )
        return merge_patches(extends_patch, _fragment_own_patch(fragment), nested=False)

    fragment_path = _resolve_fragment_file(entry, ctx)
    fragment_relpath = fragment_path.relpath
    if fragment_relpath in seen:
        raise CompilationError(
            f"Circular extends: {fragment_relpath} is already in the extends chain"
        )
    fragment = _load_fragment(fragment_path)
    fragment_ctx = _ExtendCtx(
        board_dir=fragment_path.parent,
        boards_root=ctx.boards_root,
        theme_names=ctx.theme_names,
    )
    extends_patch = _merge_extends_inner(
        fragment, fragment_ctx, seen | {fragment_relpath}, theme_sink
    )
    own_patch = _fragment_own_patch(fragment)
    return merge_patches(extends_patch, own_patch, nested=False)


def _merge_extends_inner(
    node: BaseModel,
    ctx: _ExtendCtx,
    seen: frozenset[str],
    theme_sink: list[str] | None = None,
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
        patch = _resolve_entry(entry, ctx, seen, theme_sink)
        acc = merge_patches(acc, patch, nested=False)
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
    return _merge_extends_inner(node, ctx, frozenset(), theme_sink)


def merge_metas(
    board_dir: ProjectDirectory,
    boards_root: ProjectDirectory,
    theme_sink: list[str] | None = None,
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
        try:
            from dbt_charts.core.compile.migrations import prepare_board_mapping

            fragment = BOARD_PATCH_ADAPTER.validate_python(
                prepare_board_mapping(meta_data, model=BoardPatch)
            )
        except PydanticValidationError as e:
            from dbt_charts.core.compile.parse.source_map import (
                build_source_index,
                stamp_diagnostics,
            )
            from dbt_charts.core.compile.parse.yaml_error_formatter import (
                format_validation_errors_structured,
            )

            meta_content = meta_path.read_text()
            diags = format_validation_errors_structured(e, meta_content)
            stamp_diagnostics(
                diags, *build_source_index(meta_content, meta_path.relpath)
            )
            raise MergeValidationError(
                f"meta schema error in {meta_path.relpath}",
                diags,
            ) from e
        meta_ctx = _ExtendCtx(
            board_dir=meta_path.parent,
            boards_root=boards_root,
            theme_names=get_theme_names(),
        )
        extends_patch = _merge_extends_inner(
            fragment, meta_ctx, frozenset(), theme_sink
        )
        own_patch = _fragment_own_patch(fragment)
        meta_patch: BaseModel = merge_patches(extends_patch, own_patch, nested=False)
        acc = merge_patches(acc, meta_patch, nested=False)
    return acc


def merged_patch(
    board_node: BaseModel,
    board_path: ProjectPath,
    boards_root: ProjectDirectory,
    theme_sink: list[str] | None = None,
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
    """
    # Base: merge of all meta files root→leaf
    base = merge_metas(board_path.parent, boards_root, theme_sink)
    # Middle: extends chain of the board itself
    extends_patch = merge_extends(board_node, board_path, boards_root, theme_sink)
    # Top: board's own explicitly-set fields (identity fields stripped)
    own_patch = _fragment_own_patch(board_node)
    # fold: base < extends < own
    board_contribution = merge_patches(extends_patch, own_patch, nested=False)
    return merge_patches(base, board_contribution, nested=False)


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


def to_title_style(title: TitleStyle | TitleStylePatch) -> TitleStyle:
    """Coerce a cascade-produced title value into a real ``TitleStyle``.

    Same shape as ``to_padding_style`` above: ``ChartsStyle.title``'s
    ``InheritSlot(from_path="Style.title")`` annotation leaves the field typed
    as ``TitleStylePatch`` even after ``apply_inherit`` fills every leaf from
    the board title, so callers re-validate through ``TitleStyle`` here — safe
    for an already-correct ``TitleStyle`` input too, which round-trips
    unchanged. Pydantic's own ``ValidationError`` names any leaf the cascade
    left genuinely incomplete, rather than defaulting it.
    """
    return TitleStyle.model_validate(title.model_dump())
