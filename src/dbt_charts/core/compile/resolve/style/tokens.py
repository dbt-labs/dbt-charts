"""Color-token and emoji-family resolution for the style cascade.

Resolves palette-dotted color tokens (``dbt-grays.gray-30``, ``category[2]``)
and theme-self tokens (``theme.background``) against a compiled ``Style``
tree, and inserts the emoji font family into CSS font-family stacks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from dbt_charts.core.colors import is_color_token
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.theme import Style
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
)
from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY

# `is_color_token` lives in the neutral core.colors leaf so the authored
# models can validate token *shape* without importing this cascade module
# (models -> resolve would close an import cycle).

# Theme-self tokens: strings that name a value elsewhere in the same compiled
# style tree, resolved against the root Style. The canonical use case
# is canvas coupling — any field that should track the theme's background
# color (knockout strokes, halos) sets the field to ``theme.background`` and
# the resolver substitutes the theme's actual ``style.background`` value.
#
# Resolution runs BEFORE ``_resolve_color_tokens`` so the substituted value
# (which may itself be a palette token like ``dbt-creams.cream-025``) is
# carried through the color-token pass. Each theme resolves against its own
# ``style.background``, so cream's arc.stroke ends up at the cream
# canvas color while stark's stays white — without per-theme
# override duplication.
#
# Resolution runs at theme-load time inside ``get_theme_style()`` so cached
# themes carry concrete values.  Board-level patches that introduce self-tokens
# are resolved by ``_resolve_tokens_on_patch`` (which uses the base Style as
# the resolution root) before the deep-merge, so the final merged result never
# carries a raw ``theme.background`` sentinel.
_THEME_SELF_TOKENS = {"theme.background"}

# Fields on Style that hold the resolver's own lookup tables rather than
# styled content. roles maps a role name to a token address
# (ink -> chrome.heading); walking it would resolve the addresses and
# leave the table pointing at hex, so every later lookup through it would fail.
_RESOLUTION_CONTEXT_FIELDS = frozenset({"palettes", "roles"})

# Field names that may hold a bare color string on a model reached through a
# keyed collection. `TableColumnConfig` is the first such model that carries
# free text and URLs beside its colors, and `label: "U.S"` / `link:
# "orders.html"` match the token regex exactly as `negative.text` does — routing
# those through it turns an ordinary board into ERR-INTERNAL. Outside a keyed
# collection the contract at the top of this file is unchanged: every string is
# a token candidate.
_COLOR_FIELD_NAMES = frozenset({"color", "background", "null_color", "glyph_color"})


def _self_token_replacement(token: str, root: Style) -> str:
    """Look up a theme-self token's replacement value on ``root``.

    ``token`` is guaranteed to be a member of ``_THEME_SELF_TOKENS`` by the
    caller; raises ``ValueError`` for any unknown token so a future
    ``_THEME_SELF_TOKENS`` addition without a matching branch fails loudly
    instead of silently leaking the literal sentinel into compiled output.
    """
    if token == "theme.background":
        return root.background
    raise ValueError(f"Unknown theme-self token: {token!r}")


def _resolve_self_tokens(node: Any, root: Any | None = None) -> Any:
    """Walk a Style subtree and substitute theme-self tokens.

    ``root`` is the top-level Style the substitution looks values up
    on; it defaults to ``node`` for the initial top-level call. The recursion
    keeps the same root so deeply nested fields (e.g. ``charts.arc.stroke.color``)
    resolve against the theme root, not against an enclosing sub-model.
    """
    if root is None:
        root = node
    if isinstance(node, BaseModel):
        updates: dict[str, Any] = {}
        for name, value in node:
            new = _resolve_self_tokens(value, root)
            if new is not value:
                updates[name] = new
        return node.model_copy(update=updates) if updates else node
    if isinstance(node, str):
        if node in _THEME_SELF_TOKENS:
            assert isinstance(root, Style), (
                "root Style required for self-token replacement"
            )
            return _self_token_replacement(node, root)
        return node
    if isinstance(node, list):
        new_list = [_resolve_self_tokens(item, root) for item in node]
        return (
            new_list
            if any(a is not b for a, b in zip(new_list, node, strict=True))
            else node
        )
    return node


def _resolve_one_color_token(
    token: str,
    palettes: Mapping[str, str] | None,
    roles: dict[str, str] | None,
    single_series_palette: list[str] | None = None,
) -> str:
    """Resolve a single color token (dotted or bracket form) to hex.

    Tries the direct ``palette.slot`` path first.  If the left-hand side is not
    a known palette name the token is a role-indirected address (dotted alias
    like ``chrome.ink``, or 1-indexed bracket slot like ``category[2]``, or the
    ``single_series[N]`` list-indirected form); try ``color_from_theme()`` with
    the theme palettes/roles/single-series context.  Raises
    ``UnknownColorError`` if both paths fail.
    """
    from dbt_charts.core.compile.resolve.style.palette import (
        UnknownColorError,
        UnknownPaletteError,
        color as resolve_palette_color,
        color_from_theme,
    )

    try:
        return resolve_palette_color(token)
    except (UnknownColorError, UnknownPaletteError):
        pass

    if palettes is not None:
        return color_from_theme(
            token,
            palettes=palettes,
            roles=roles,
            single_series_palette=single_series_palette,
        )

    raise UnknownColorError(
        f"color token '{token}' is not a known palette.slot address and no "
        "theme palettes context is available for role-indirection lookup"
    )


def _resolve_color_tokens(
    node: Any,
    palettes: Mapping[str, str] | None = None,
    roles: dict[str, str] | None = None,
    in_keyed_collection: bool = False,
    single_series_palette: list[str] | None = None,
) -> Any:
    """Walk a Style subtree and resolve every dotted palette token to hex.

    Strings that don't look like a palette token (literal hex, rgb()/rgba(),
    CSS named colors, font-family lists, etc.) pass through unchanged. Lists
    are walked element-by-element so ``charts.palette`` slots that happen to
    be tokens (rare) are also resolved.

    Dict *values* are walked too, and keys are left alone: a dict here is a
    keyed collection of style models (``TableChartStyle.columns`` is
    ``dict[str, TableColumnConfig]``, keyed by column name), never a mapping
    whose keys carry color. This branch used to be absent, on the premise that
    no color-bearing dict existed on Style — ``columns`` was added later and
    every per-column color silently stopped resolving, which is the failure
    ``test_tokens_walk_dicts.py`` pins.

    ``palettes`` and ``roles`` are extracted from the root Style at call time
    and passed through the recursion so role-indirected tokens (e.g.
    ``chrome.ink`` where ``chrome`` maps to a palette name via the theme's
    ``palettes:`` block) resolve correctly alongside direct palette.slot tokens.
    ``single_series_palette`` is caller-supplied (never extracted from a
    ``Style`` node here) for the ``single_series[N]`` form — at theme-load
    time the theme's own single-series ink is itself mid-resolution, so
    that form only resolves when a caller with an already-cascaded value
    (a board or chart style context) threads it through.

    Run once at theme-load time inside ``get_theme_style()`` so cached
    themes carry hex.  Board-level patches that introduce color tokens are
    resolved by ``_resolve_tokens_on_patch`` (O(patch fields)) before the
    deep-merge, so this full-tree walk is not repeated on every cascade call.
    """
    from dbt_charts.core.compile.models.style.theme import Style

    if isinstance(node, Style):
        # Extract palettes/roles context from the root Style for this walk.
        palettes = node.palettes
        roles = node.roles
    if isinstance(node, BaseModel):
        updates: dict[str, Any] = {}
        for name, value in node:
            # `palettes` and `roles` are the resolver's own lookup tables, not
            # styled content: `roles` maps a role name to a token *address*
            # (`ink` -> `chrome.heading`). Resolving those would rewrite the
            # indirection table into hex and destroy the indirection — which is
            # exactly what happened when the dict branch below first landed
            # without this guard.
            if isinstance(node, Style) and name in _RESOLUTION_CONTEXT_FIELDS:
                continue
            # Inside a keyed collection, only a color-named field may claim a
            # bare string. `TableColumnConfig` is the first model the walk
            # reaches that carries free text and URLs beside its colors, and
            # `label: "U.S"` / `link: "orders.html"` match the token regex as
            # readily as `negative.text` does — routing those through it turns
            # an ordinary board into ERR-INTERNAL.
            if in_keyed_collection and isinstance(value, str):
                if name not in _COLOR_FIELD_NAMES:
                    continue
            new = _resolve_color_tokens(
                value, palettes, roles, in_keyed_collection, single_series_palette
            )
            if new is not value:
                updates[name] = new
        return node.model_copy(update=updates) if updates else node
    if isinstance(node, str):
        if is_color_token(node):
            return _resolve_one_color_token(
                node, palettes, roles, single_series_palette
            )
        return node
    if isinstance(node, list):
        new_list = [
            _resolve_color_tokens(
                item, palettes, roles, in_keyed_collection, single_series_palette
            )
            for item in node
        ]
        return (
            new_list
            if any(a is not b for a, b in zip(new_list, node, strict=True))
            else node
        )
    if isinstance(node, dict):
        # Values only, and unconditionally: a dict key here is not a field name
        # to filter on. `SparkConfig.thresholds` is `dict[int | float, str]` —
        # numeric keys, *color* values — so testing the key against the
        # color-field names would skip every threshold color and leak it into
        # the SVG as `fill="negative.text"`. The name-based guard belongs on the
        # model branch above, where a name is what the walk actually has.
        new_dict = {
            k: _resolve_color_tokens(v, palettes, roles, True, single_series_palette)
            for k, v in node.items()
        }
        return new_dict if any(new_dict[k] is not v for k, v in node.items()) else node
    return node


_EMOJI_MODE_TO_FAMILY: dict[str, str] = {
    "monochrome": NOTO_EMOJI_FONT_FAMILY,
}


def insert_emoji_family(family: str, emoji_family: str) -> str:
    """Insert ``emoji_family`` after the primary font in a CSS font-family stack.

    Idempotent: no-op if ``emoji_family`` is already present. Public so callers in
    typography.py / sizing.py / render can append an emoji family to a raw
    family string without the full resolve_style() cascade walk.
    """
    if emoji_family in family:
        return family
    # Assumes commas are CSS font-family stack separators, not quoted name characters.
    first_comma = family.find(",")
    if first_comma == -1:
        return f"{family}, '{emoji_family}'"
    return f"{family[:first_comma]}, '{emoji_family}'{family[first_comma:]}"


def apply_emoji_to_family(
    raw: str,
    emoji_mode: str,
) -> str:
    """Conditionally insert the appropriate emoji family into a font-family stack.

    Maps ``emoji_mode`` to a bundled emoji font family name and inserts it.
    Returns ``raw`` unchanged for ``system-default`` and ``disabled``.
    """
    emoji_family = _EMOJI_MODE_TO_FAMILY.get(emoji_mode)
    if emoji_family is None:
        return raw
    return insert_emoji_family(raw, emoji_family)


def _append_emoji_family(node: Any, emoji_family: str) -> Any:
    """Walk a style subtree and append ``emoji_family`` to every non-None FontStyle.family.

    Mirrors _resolve_color_tokens in structure: walks BaseModel trees recursively,
    returning a patched copy only when a field actually changes.
    FontStyle never appears inside a list/dict field in this schema; if that
    changes, add a list branch mirroring _resolve_color_tokens.
    """
    if isinstance(node, FontStyle):
        if node.family is not None:
            new_family = insert_emoji_family(node.family, emoji_family)
            if new_family != node.family:
                return node.model_copy(update={"family": new_family})
        return node
    if isinstance(node, BaseModel):
        updates: dict[str, Any] = {}
        for name, value in node:
            new = _append_emoji_family(value, emoji_family)
            if new is not value:
                updates[name] = new
        return node.model_copy(update=updates) if updates else node
    return node


def _resolve_tokens_on_patch(patch: Any, base: Style) -> Any:
    """Resolve color tokens and theme-self tokens within a patch object.

    Patches are all-Optional; only non-None fields carry authored values.
    Walks only the non-None fields so the cost is O(patch fields), not O(full
    Style tree).  Returns the patch unchanged when no tokens are present.

    ``base`` is the token-resolved Style coming from ``get_theme_style``; its
    ``palettes``, ``roles``, and ``background`` fields supply the resolution
    context.  Theme-self tokens (``theme.background``) resolve against the
    base's own value so a board patch that says ``background: theme.background``
    inherits the theme hex rather than creating a circular reference.
    ``single_series_palette`` comes off ``base.charts.color.categorical``,
    already resolved to hex at this point, for the ``single_series[N]`` form.
    """
    palettes = base.palettes
    roles = base.roles
    authored_single_series = (
        base.charts.color.categorical.single_series_palette
        if base.charts.color.categorical is not None
        else None
    )
    # A list is the resolved ink list `single_series[N]` indexes; a bare string
    # names a palette, which the token does not read.
    single_series_palette = (
        authored_single_series if isinstance(authored_single_series, list) else None
    )

    def _walk(node: Any) -> Any:
        if isinstance(node, CategoryColorBinding):
            return node.model_copy(
                update={
                    "values": {
                        value: _walk(color) for value, color in node.values.items()
                    }
                }
            )
        if isinstance(node, BaseModel):
            updates: dict[str, Any] = {}
            for name, value in node:
                if value is None:
                    continue
                new = _walk(value)
                if new is not value:
                    updates[name] = new
            return node.model_copy(update=updates) if updates else node
        if isinstance(node, str):
            if node in _THEME_SELF_TOKENS:
                return _self_token_replacement(node, base)
            if is_color_token(node):
                return _resolve_one_color_token(
                    node, palettes, roles, single_series_palette
                )
            return node
        if isinstance(node, list):
            new_list = [_walk(item) for item in node]
            return (
                new_list
                if any(a is not b for a, b in zip(new_list, node, strict=True))
                else node
            )
        if isinstance(node, dict):
            # Bindings only — see the matching branch in _resolve_color_tokens
            # for why every other dict on Style is left alone.
            return {
                key: _walk(value) if isinstance(value, CategoryColorBinding) else value
                for key, value in node.items()
            }
        return node

    return _walk(patch)


__all__ = [
    "expand_palette_refs",
    "apply_emoji_to_family",
    "insert_emoji_family",
    "_EMOJI_MODE_TO_FAMILY",
    "_append_emoji_family",
    "_resolve_color_tokens",
    "_resolve_tokens_on_patch",
]


# Field names holding an authored palette name. A theme may write a *role*
# (`category`) instead of a palette file name; the role map is not final until
# the extends chain has merged, so substitution happens here, right after it is.
_PALETTE_FIELDS = frozenset({"palette", "single_series_palette"})

# Subtrees whose palette name is baked downstream rather than expanded here, so
# the name has to survive: a gradient's stops are carved at resolve (against the
# consumer's surface and ink), and a chart channel scale's likewise.
_NAME_ONLY_FIELDS = frozenset({"gradient", "scale"})


def _unknown_palette(value: str, field: str, palettes: Mapping[str, str]) -> Exception:
    """Build the ERR-PALETTE-UNKNOWN error for an unresolvable palette name."""
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.resolve.style.palette import list_palettes
    from dbt_charts.core.diagnostics.codes_compile import ERR_PALETTE_UNKNOWN

    return CompilationError.from_code(
        ERR_PALETTE_UNKNOWN,
        name=value,
        field_path=field,
        available=sorted({*palettes, *list_palettes()}),
    )


def _stops(value: str, ref: str, field: str, palettes: Mapping[str, str]) -> list[str]:
    """Resolve ``ref`` to stops, reporting any resolver failure as a diagnostic."""
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette

    try:
        return resolve_palette(ref)
    except ValueError as e:
        # Every resolver failure subclasses ValueError — unknown name, a tone
        # (color-token only), an over-request, a surface it cannot carve. Any
        # of them escaping here becomes ERR-INTERNAL, which AGENTS.md classes
        # as a defect tier.
        raise _unknown_palette(value, field, palettes) from e


def _check_resolvable(
    value: str, ref: str, field: str, palettes: Mapping[str, str]
) -> None:
    """Raise if ``ref`` names nothing, without expanding it."""
    _stops(value, ref, field, palettes)


def _resolve_palette_leaf(
    value: str, field: str, palettes: Mapping[str, str], name_only: bool
) -> str | list[str]:
    """Resolve one `palette`/`single_series_palette` leaf found by either
    walk branch below: substitute a role, then check or expand the result.

    Shared so the dict branch (a raw ``Any``-typed field — KPI
    ``background``'s ``{column, scale}`` channel spec — never becomes a
    pydantic model, so its ``palette`` key needs the same treatment a model
    field gets) does not duplicate the model branch's substitute/check/expand
    logic.
    """
    from dbt_charts.core.compile.models.primitives import VEGA_SCHEME_NAMES
    from dbt_charts.core.compile.resolve.style.palette import resolve_palette_ref

    try:
        ref = resolve_palette_ref(value, palettes)
    except ValueError as e:  # malformed :N/_r shorthand
        raise _unknown_palette(value, field, palettes) from e
    # A gradient keeps its *name*: `bake_scale_target_stops` bakes it later,
    # and a Vega scheme is forwarded to Vega-Lite by name and has no stops at
    # all. Expanding either here would change what render receives — but the
    # name is still checked, or a typo in a gradient would sail past compile
    # and land as ERR-INTERNAL at render.
    # The scheme bypass belongs to name-only slots alone. A categorical field
    # can never carry a scheme: nothing expands it, so the literal reaches
    # the SVG as fill="viridis".
    if name_only:
        if ref not in VEGA_SCHEME_NAMES:
            _check_resolvable(value, ref, field, palettes)
        return ref if ref != value else value
    return _stops(value, ref, field, palettes)


def expand_palette_refs(
    node: Any,  # type-state: explicit_any — generic recursive walk over a heterogeneous Pydantic-model/dict/list/scalar style tree
    palettes: Mapping[str, str] | None = None,
    name_only: bool = False,
    path: str = "",
) -> Any:
    """Substitute theme palette roles and expand every palette name to stops.

    Runs immediately after the theme cascade, where ``style.palettes`` is final
    and every consumer downstream is still expecting the ``list[str]`` it has
    always received. That keeps role support to one pass: no resolve-stage seam
    has to know a name might be a role, and the model validator's own
    unknown-name error stays exactly where it was.

    A name that is neither a role nor a shipped palette raises
    ``ERR-PALETTE-UNKNOWN`` here — the first point at which the two can be told
    apart, and the reason the model validator defers an unresolvable name
    instead of rejecting it.

    ``path`` accumulates the authored field path as the walk descends (a
    caller seeds it with the root it started from, e.g. ``"style"`` or
    ``f"charts.{chart.id}.style"``), so a diagnostic raised deep in the tree
    names the real field an author could look up, not just the bare leaf
    name every recursive call shares (``"palette"``).
    """
    from dbt_charts.core.compile.models.style.theme import Style

    if isinstance(node, Style):
        palettes = node.palettes
    if palettes is None:
        return node
    if isinstance(node, BaseModel):
        updates: dict[str, Any] = {}
        for name, value in node:
            # `palettes`/`roles` are the resolver's own lookup tables, not
            # styled content (mirrors `_resolve_color_tokens`'s same guard).
            # No shipped theme binds a role named "palette" or
            # "single_series_palette" today, so this guards a shape nothing
            # currently exercises — without it, the key-aware dict branch
            # below would treat such a role as a leaf to resolve, corrupting
            # the table it is itself part of.
            if isinstance(node, Style) and name in _RESOLUTION_CONTEXT_FIELDS:
                continue
            child_path = f"{path}.{name}" if path else name
            if name in _PALETTE_FIELDS and isinstance(value, str):
                new_value = _resolve_palette_leaf(
                    value, child_path, palettes, name_only
                )
            else:
                new_value = expand_palette_refs(
                    value,
                    palettes,
                    name_only or name in _NAME_ONLY_FIELDS,
                    child_path,
                )
            if new_value is not value:
                updates[name] = new_value
        return node.model_copy(update=updates) if updates else node
    if isinstance(node, dict):
        # Table column scales live in a dict keyed by column name
        # (`charts.table.columns`), reached via the model branch above once
        # traversal hits the column's own typed model. A raw `Any`-typed dict
        # (KPI `background`'s `{column, scale}` channel spec) never becomes a
        # model, so this branch is key-aware too — the same `_PALETTE_FIELDS`
        # check the model branch runs on field names, run here on dict keys.
        new_map = {}
        changed = False
        for k, v in node.items():
            child_path = f"{path}.{k}" if path else k
            if k in _PALETTE_FIELDS and isinstance(v, str):
                new_v = _resolve_palette_leaf(v, child_path, palettes, name_only)
            else:
                new_v = expand_palette_refs(
                    v, palettes, name_only or k in _NAME_ONLY_FIELDS, child_path
                )
            new_map[k] = new_v
            changed = changed or new_v is not v
        return new_map if changed else node
    if isinstance(node, list):
        new_list = [
            expand_palette_refs(item, palettes, name_only, path) for item in node
        ]
        return (
            new_list
            if any(a is not b for a, b in zip(new_list, node, strict=True))
            else node
        )
    return node
