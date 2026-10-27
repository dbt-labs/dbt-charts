"""Normalization module for transforming input types to compiled types.

Stage: COMPILE (Step 3 of 4)
Purpose: Transform AuthoredBoard input types to Board output types.

Entry Points:
    - normalize_board(board: AuthoredBoard) -> Board

This is the core transformation step of compilation:
1. Resolve all chart references (queries, extends, partials)
2. Resolve remote board references
3. Generate unique IDs for all entities
4. Apply structural defaults (empty description/subtitle)
5. Transform input types → compiled types
6. Create unified layout structure

After this step, all references are resolved and downstream code can
rely on guaranteed field presence.

Cross-file Query References:
    Charts can reference queries from external files using the syntax:
    `query: path/to/file.yml#query_name`

    The path is resolved relative to the current file's directory.
    External queries are loaded once and cached in the query registry.

Dependencies:
    - .models.board.authored (AuthoredBoard)
    - .models.board.normalized (Board, Layout)
    - .models.chart.normalized (Chart)
    - .models.query.normalized (AnyQuery)
    - .jinja (resolve_jinja_template)
    - .errors (ReferenceError, CompilationError)

See also:
    - compile/validate/dispatch.py: Previous step
    - render/sizing.py: Next step
"""

from __future__ import annotations

import copy
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, datetime
from operator import attrgetter
from typing import TYPE_CHECKING, Any, overload

if TYPE_CHECKING:
    from collections.abc import Generator

    from dbt_charts.core.compile.models.style.theme import Style
    from dbt_charts.core.project import ProjectDirectory

from dbt_charts.core.compile.config import cap_html_policy, resolve_html_policy_ceiling
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    LayoutType,
    VariableValues,
)
from dbt_charts.core.compile.models.cache import (
    INHERIT_CACHE,
    CachePatch,
    merge_cache_layers,
)
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    Chart,
    LineChart,
    ScatterChart,
)
from dbt_charts.core.compile.models.primitives import (
    DEFAULT_REGIONAL,
    BoardLinks,
    LanguageCode,
    Regional,
)
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.refs import ChartRef
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.style.theme.page import (
    CLOCK_HIDES_ZONE,
    clock_hides_zone,
)
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.charts import (
    RegisteredChart,
    _collect_charts_from_layout,
    display_text_fields,
    normalize_chart,
)
from dbt_charts.core.compile.normalize.layout import (
    AuthoringPathPrefix,
    build_unified_layout,
    prefixed,
)
from dbt_charts.core.compile.normalize.queries import (
    expand_composed_variable_dependencies,
    normalize_query as normalize_query,  # re-exported (used by compiler.py, tests)
)
from dbt_charts.core.compile.normalize.single_series_allocation import (
    allocate_single_series_slots,
)
from dbt_charts.core.compile.normalize.variables import (
    VariableReferenceErrors,
    build_variable_registry,
    compute_variable_dependencies,
    expand_chart_variable_dependencies,
    generate_layout_variables,
    infer_variable_input,
    promote_column_option_queries,
    promote_inline_option_queries,
    validate_authored_variable_name,
    validate_choice_type,
    validate_variable_references,
    validate_variable_value,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.sharing import share_unchanged
from dbt_charts.core.compile.template.jinja import text_query_references
from dbt_charts.core.compile.template.listings import (
    check_listing_calls,
    reject_listing_calls,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LINKS_NESTED,
    ERR_REGIONAL_NESTED_MISMATCH,
    ERR_TEXT_UNKNOWN_QUERY,
    ERR_VALIDATION_FIELD,
)
from dbt_charts.core.text.currencies import CurrencyCode


def compiled_meta() -> dict[str, str]:
    """Compilation metadata stamp every compiled ``Board`` carries.

    Every `Board` in the tree gets this stamp: `normalize_board` below for
    boards it builds, and the leaf-tab branches in `layout._resolve_tab_items`
    that construct a `Board` directly without going through `normalize_board`.
    """
    return {
        "compiled_at": datetime.now(UTC).isoformat(),
        "version": "0.1.0",
    }


def _theme_from_extends(extends: str | list[str] | None) -> str | None:
    """Extract the effective theme name from an extends spec.

    Scans extends for the highest-priority (last) built-in theme name.
    Returns None if no theme name is found (board extends a named board or path,
    or extends is absent).
    """
    from dbt_charts.core.compile.merge import get_theme_names

    theme_names = get_theme_names()
    if extends is None:
        return None
    if isinstance(extends, str):
        return extends if extends in theme_names else None
    # List: last theme name wins (highest priority).
    for entry in reversed(extends):
        if entry in theme_names:
            return entry
    return None


# (theme name, this board's own merged patch's fields) — everything the
# cascade varies on within one compile.
#
# `repr` of the dumped fields, not `model_dump_json`: JSON writes inf and nan as
# null, so `gap: .inf` would key the same as `gap: null` and silently take the
# wrong style off the memo. A repr the other way round can only miss.
_BoardCascadeKey = tuple[str, str, str | None, CurrencyCode | None, LanguageCode]
_CascadeProducts = tuple[ResolvedStyle, ChartStyleContext]
_ReboundThemeKey = tuple[str, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]


# Board style cascades already resolved, innermost compile last. Every cascade
# in a compile lands in compile_board_resolved_style, and most of them
# re-resolve a style that same board already resolved: normalize_board and
# _propagate_resolved_style each walk the nested-board tree, and sibling boards
# usually share a style block.
#
# One compile's worth, not the process's: a resolved style is large, and near
# all the repetition is inside a single board. A process-lifetime cache would
# retain a lot to catch the little that repeats across boards.
@dataclass
class _CompileMemo:
    cascades: dict[_BoardCascadeKey, _CascadeProducts] = dataclass_field(
        default_factory=dict
    )
    # Themes loaded under authored `palettes:`/`roles:` rebinds, keyed by the
    # theme and both rebind maps; one compile's worth, never the process's.
    rebound_themes: dict[_ReboundThemeKey, Style] = dataclass_field(
        default_factory=dict
    )


# A tuple of memos rather than `_CompileMemo | None`: the empty tuple already
# represents "nothing cached yet," so it needs no `None` sentinel on top; the
# outer frames are never read, only the innermost.
_BOARD_STYLE_CACHES: ContextVar[tuple[_CompileMemo, ...]] = ContextVar(
    "board_style_caches", default=()
)


@contextmanager
def board_style_cache() -> Generator[None]:
    """Memoize board style cascades for the duration of one compile."""
    token = _BOARD_STYLE_CACHES.set((*_BOARD_STYLE_CACHES.get(), _CompileMemo()))
    try:
        yield
    finally:
        _BOARD_STYLE_CACHES.reset(token)


def _current_memo() -> _CompileMemo:
    """The memo for the compile in progress.

    Callers outside a compile (``Board.set_theme``, render) get a throwaway they
    are welcome to write into: nothing is remembered when nothing is compiling.
    """
    caches = _BOARD_STYLE_CACHES.get()
    return caches[-1] if caches else _CompileMemo()


def _theme_base(theme: str, scoped_patch: StylePatch | None) -> Style:
    """The built-in theme loaded under the palette/role rebinds this scope authors."""
    from dbt_charts.core.compile.config import NO_REBINDS, get_theme_style

    if scoped_patch is None:
        return get_theme_style(theme)
    palettes = NO_REBINDS if scoped_patch.palettes is None else scoped_patch.palettes
    roles = NO_REBINDS if scoped_patch.roles is None else scoped_patch.roles
    if not palettes and not roles:
        return get_theme_style(theme)
    memo = _current_memo().rebound_themes
    key = (theme, tuple(sorted(palettes.items())), tuple(sorted(roles.items())))
    if key not in memo:
        memo[key] = get_theme_style(theme, palettes, roles)
    return memo[key]


@overload
def _pin_unauthored_nested_background(
    patch: StylePatch,
    board_style: StylePatch | None,
    *,
    is_nested: bool,
    replacement: str,
) -> StylePatch: ...
@overload
def _pin_unauthored_nested_background(
    patch: StylePatch | None,
    board_style: StylePatch | None,
    *,
    is_nested: bool,
    replacement: str,
) -> StylePatch | None: ...
def _pin_unauthored_nested_background(
    patch: StylePatch | None,
    board_style: StylePatch | None,
    *,
    is_nested: bool,
    replacement: str,
) -> StylePatch | None:
    """Force ``background`` to ``replacement`` unless THIS scope's own raw
    style set it -- CSS ``background-color`` is not inherited, so a nested
    scope's unset background must never be an ancestor's color crossing in
    through ``parent_patch``/``scope_patch``'s ordinary cascade.

    ``replacement`` is ``"transparent"`` for a scope on the same theme as
    its parent, or that theme's own canvas when the scope declares its own
    ``theme:``/``extends:`` -- a switched theme is a declaration on this
    exact scope (like a CSS rule naming a new class), so its own canvas
    applies instead of the CSS initial value.

    Checked on ``board_style``, THIS scope's own raw, unmerged patch --
    never the already-merged ``patch``, which echoes an ancestor's value
    regardless of whether this scope authored anything. Only the top-level
    ``style.background:`` spelling counts: ``style.charts.background:`` is
    a different field (the per-chart card-fill default), unaffected here.

    ``model_dump(exclude_unset=True)``, not `board_style.background is not
    None` directly: a patch model's TYPE_CHECKING stub inherits the
    resolved model's required `str` type, so pyright reads a bare `is not
    None` as statically impossible even though it is meaningful at runtime.

    A single-field ``model_copy(update=...)``, not ``scope_patch``: several
    Style fields (``footer``, ``padding``, ...) are ``NotInherited``, so
    scoping a second, background-only patch onto ``patch`` would drop every
    such field ``patch`` already resolved.
    """
    if not is_nested:
        return patch
    own_fields = (
        board_style.model_dump(exclude_unset=True) if board_style is not None else {}
    )
    if own_fields.get("background") is not None:
        return patch
    if patch is None:
        return StylePatch.model_construct(background=replacement)
    return patch.model_copy(update={"background": replacement})


def _with_nested_layout_gap(
    patch: StylePatch, layout_type: LayoutType | None
) -> StylePatch:
    """``patch`` with its ``style.layout.<type>.gap`` as its ``style.gap``.

    A nested board pitches its items by ``style.gap`` (0 when unset) and never
    reads ``layout.<type>.gap``, so an authored layout gap, this board's or an
    ancestor's, would otherwise validate and do nothing. An explicit
    ``style.gap`` wins. Only authored values count: an unset layout gap leaves
    ``style.gap`` unset rather than taking the theme's.
    """
    if layout_type is None or patch.gap is not None:
        return patch
    if layout_type not in (LayoutType.ROWS, LayoutType.COLS, LayoutType.GRID):
        return patch
    # model_dump, not attribute access: a patch model's TYPE_CHECKING stub
    # types every field as non-optional, so the unauthored cases read as
    # statically impossible (see _pin_unauthored_nested_background).
    own_layout = patch.model_dump(exclude_unset=True).get("layout")
    if own_layout is None:
        return patch
    layout_patch = own_layout.get(layout_type.value)
    if layout_patch is None or layout_patch.get("gap") is None:
        return patch
    return patch.model_copy(update={"gap": layout_patch["gap"]})


# Variable fields the compiler sets itself; never what the author wrote.
_INFERRED_VARIABLE_FIELDS = frozenset(
    {"defined_in_other_file", "input_auto_detected", "authored_fields"}
)


def compile_board_resolved_style(
    board_style: StylePatch | None,
    parent_resolved: ResolvedStyle | None,
    parent_context: ChartStyleContext | None,
    parent_patch: StylePatch | None = None,
    theme_name: str | None = None,
    parent_theme_name: str | None = None,
    *,
    currency: CurrencyCode | None = DEFAULT_REGIONAL.currency,
    language: LanguageCode = DEFAULT_REGIONAL.resolved_language,
    layout_type: LayoutType | None = None,
) -> tuple[ResolvedStyle, ChartStyleContext, StylePatch | None]:
    """Build the authoritative ResolvedStyle + ChartStyleContext for this board scope.

    When this board authors its own ``style:`` (``board_style is not None``),
    merge order is:
    1. Board-to-board relation (``scope_patch(parent_patch, board_style)``).
       Most fields default to the ancestor's authored value crossing in when
       this board leaves them unset; a field marked ``NotInherited`` — a
       per-board or root-only concern (padding/margin,
       footer/timestamp chrome), not thematic identity — never crosses, so
       this board's own value (or the theme's) applies.
    2. Theme defaults (theme_name → compiled theme → resolve_style) fill
       whatever the merged patch left unset.

    ``background`` is the one field this cascade excludes from step 1's
    ordinary inheritance (``_pin_unauthored_nested_background`` below): CSS
    ``background-color`` is not inherited, so a nested board's unset
    background is pinned to transparent (or its own theme's canvas, on a
    theme switch) rather than crossing in from ``parent_patch`` like every
    other field -- this holds even for a board that resolves fresh only
    because its own theme differs, so it always earns its own patch build;
    there is no "reuse the parent's object verbatim" shortcut here, ever
    (see ``layout_sizing.py``'s ``canonical_resolved_key`` for why one
    isn't needed: "same placement" no longer means "same object").

    Returns the resolved style, its chart context, and this board's own
    merged patch — ``None`` when nothing was authored anywhere in this
    board's lineage, otherwise what a caller threads to this board's own
    nested children as their ``parent_patch``.

    The resolved style and chart context come from the same cascade pass —
    ``parent_resolved``/``parent_context`` must be supplied together (both
    None at the root, both set for every nested board) or omitted together.

    ``parent_theme_name`` is the parent scope's own effective theme (also
    None at the root) -- compared against this scope's own effective theme
    to tell whether this board itself authored a `theme:`/`extends:` that
    differs from what it would otherwise have inherited (the cascade
    already falls through to the parent's theme when this board authors
    none, so a difference here can only come from this scope's own
    authoring), which decides the ink-canvas bottom layer below.

    ``layout_type`` is this board's own layout, which a nested board needs to
    read its authored or inherited ``layout.<type>.gap``
    (``_with_nested_layout_gap``). Left
    ``None`` where it is not yet known — ``normalize_board`` resolves a board
    before its layout exists, and the root's ``_propagate_resolved_style``
    re-resolves every nested board with the type afterward.
    """
    from dbt_charts.core.compile.config import get_default_theme_name
    from dbt_charts.core.compile.merge import scope_patch

    if (parent_resolved is None) != (parent_context is None):
        raise ValueError(
            "compile_board_resolved_style requires parent_resolved and "
            "parent_context together — pass both or neither."
        )

    effective_theme = theme_name or get_default_theme_name()
    is_nested = parent_context is not None
    if is_nested and board_style is not None:
        board_style = _block_font_sets_prose_font(board_style)
    scoped_patch = scope_patch(parent_patch, board_style)
    # Role bindings must be final before any token expands, so a rebind
    # authored by this board or an ancestor layer goes into the theme load.
    base = _theme_base(effective_theme, scoped_patch)
    # This scope's own theme differs from its immediate parent's -- the
    # cascade already falls through to the parent's theme when this board
    # authors none (see the docstring above), so a difference here can only
    # come from this scope's own `theme:`/`extends:`.
    own_theme_declared = (
        parent_theme_name is not None and effective_theme != parent_theme_name
    )
    # A nested board paints on top of what its PARENT scope actually
    # composited, never the theme's raw canvas underneath that -- see
    # resolve_style_and_context's own docstring. The root has no parent
    # context, so it keeps the theme canvas (base_background=None).
    bottom_layer = parent_context.board_canvas if parent_context is not None else None
    # What an unauthored nested scope's own fill becomes: this theme's own
    # canvas on a theme switch (a declaration on this exact scope), else
    # transparent (see _pin_unauthored_nested_background's own docstring).
    replacement = base.background if own_theme_declared else "transparent"

    if board_style is None:
        # No style: of its own to merge -- still folds whatever an ancestor
        # authored (`parent_patch`, minus its NotInherited fields), background
        # pinned to `replacement` when this scope is nested and authored no
        # style at all. Always a fresh resolve: an unstyled nested board never
        # reuses its parent's object (layout_sizing.py's canonical_resolved_key
        # keys "same placement" on a value, not identity, precisely so this
        # can be true).
        inherited_patch = scope_patch(parent_patch, None)
        merged_patch = _pin_unauthored_nested_background(
            inherited_patch, None, is_nested=is_nested, replacement=replacement
        )
        if merged_patch is not None and is_nested:
            merged_patch = _with_nested_layout_gap(merged_patch, layout_type)
        return (
            *_shared_with_parent(
                resolve_style_and_context(
                    base,
                    merged_patch,
                    base_background=bottom_layer,
                    currency=currency,
                    language=language,
                ),
                parent_resolved,
                parent_context,
                base,
                language,
            ),
            inherited_patch,
        )

    assert scoped_patch is not None  # board_style is set, so the scope has a patch
    own_patch = _pin_unauthored_nested_background(
        scoped_patch, board_style, is_nested=is_nested, replacement=replacement
    )

    resolve_patch = (
        _with_nested_layout_gap(own_patch, layout_type) if is_nested else own_patch
    )

    cache = _current_memo().cascades
    key = (
        effective_theme,
        repr(resolve_patch.model_dump(exclude_unset=True)),
        bottom_layer,
        currency,
        language,
    )
    cached = cache.get(key)
    if cached is not None:
        # A shallow copy, not the cached object: two boards that merge to
        # the same style must still hold two objects for
        # resolved_chart_variant_key/_style_contexts_by_resolved_style_id
        # (layout_sizing.py), which tell one board's per-chart resolve
        # cache from another's by identity. The copy shares the whole
        # resolved tree -- what the memo skips is the cascade, not the
        # allocation.
        resolved, context = _shared_with_parent(
            cached, parent_resolved, parent_context, base, language
        )
        return copy.copy(resolved), context, own_patch

    resolved = _shared_with_parent(
        resolve_style_and_context(
            base,
            resolve_patch,
            base_background=bottom_layer,
            currency=currency,
            language=language,
        ),
        parent_resolved,
        parent_context,
        base,
        language,
    )
    cache[key] = resolved
    return (*resolved, own_patch)


def _shared_with_parent(
    products: _CascadeProducts,
    parent_resolved: ResolvedStyle | None,
    parent_context: ChartStyleContext | None,
    base: Style,
    language: LanguageCode,
) -> _CascadeProducts:
    """*products*, reusing every subtree equal to the parent board's.

    A board resolved without a parent shares with its theme's unpatched
    products instead. The board still gets its own ``ResolvedStyle`` object
    (see the identity note above).
    """
    if parent_resolved is None or parent_context is None:
        parent_resolved = resolve_style(base, language=language)
        parent_context = resolve_chart_style_context(base, language=language)
    resolved, context = products
    resolved = share_unchanged(resolved, parent_resolved)
    if resolved is parent_resolved:
        resolved = copy.copy(resolved)
    return resolved, share_unchanged(context, parent_context)


def _block_font_sets_prose_font(style: StylePatch) -> StylePatch:
    """A nested block's authored ``font.family``/``font.size`` also set its prose.

    Prose takes its font from the theme's named ``body`` font, which already has
    a family and size, so a block's ``font`` would never reach it the way
    ``font.color`` does. An authored ``text.font`` still wins.
    """
    from dbt_charts.core.compile.merge import merge_patches

    if "font" not in style.model_fields_set:
        return style
    prose = {
        name: getattr(style.font, name)
        for name in ("family", "size")
        if name in style.font.model_fields_set
    }
    if not prose:
        return style
    return merge_patches(StylePatch.model_validate({"text": {"font": prose}}), style)


def _board_regional(board: AuthoredBoard, parent: Regional | None) -> Regional:
    """A nested board writes numbers and dates the way its root does."""
    if parent is None:
        return board.regional or Regional()
    authored = board.regional
    if authored is None:
        return parent
    authored_fields = {
        f"locale.{name}" for name in authored.locale.model_fields_set
    } | (authored.model_fields_set - {"locale"})
    differing = sorted(
        field
        for field in authored_fields
        if attrgetter(_compared(field))(authored)
        != attrgetter(_compared(field))(parent)
    )
    if differing:
        raise CompilationError.from_code(
            ERR_REGIONAL_NESTED_MISMATCH,
            field_path="regional",
            board=board.title
            or "A nested board",  # type-state: silent_fallback — an untitled nested board has no name to cite
            authored=_regional_text(authored, differing),
            root=_regional_text(parent, differing),
        )
    return parent


def _compared(field: str) -> str:
    """A nested board's `language` is the one its root's time locale may name."""
    return "resolved_language" if field == "language" else field


def _board_links(board: AuthoredBoard, parent: BoardLinks | None) -> BoardLinks:
    """Only the root board authors ``links:``; a nested board takes its root's."""
    if parent is None:
        return board.links or BoardLinks()
    if board.links is not None:
        raise CompilationError.from_code(
            ERR_LINKS_NESTED,
            field_path="links",
            board=board.title
            or "A nested board",  # type-state: silent_fallback — an untitled nested board has no name to cite
        )
    return parent


def _regional_text(regional: Regional, fields: list[str]) -> str:
    def _shown(field: str) -> str:
        value = attrgetter(_compared(field))(regional)
        if value is None:
            return "the locale's own currency"
        return value if isinstance(value, str) else "a custom locale"

    return ", ".join(f"{field}: {_shown(field)}" for field in fields)


def _root_width_style_patch(board: AuthoredBoard) -> StylePatch | None:
    """Desugar the root board's ``width:`` into a ``style.frame.width`` patch.

    ``width:`` is normally layout-placement sugar, read only when a board is
    nested inside a parent's rows/cols/grid (``normalize/layout.py``). At the
    root there is no parent to place it into, so it means the same thing as
    ``style.frame.width`` — this is the sole place that equivalence is wired.

    Percentages are meaningless without a parent to be relative to, and
    authoring both ``width:`` and ``style.frame.width:`` is ambiguous — both
    are ``ERR-VALIDATION-FIELD`` rather than silently picking one. This runs
    past the parse gate, so a bare raise here would surface as ``ERR-INTERNAL``.
    """
    if board.width is None:
        return None

    from dbt_charts.core.compile.sizing import parse_dimension

    def reject(msg: str) -> CompilationError:
        return CompilationError.from_code(
            ERR_VALIDATION_FIELD, field_path="width", pydantic_msg=msg
        )

    raw = str(board.width).strip()
    if raw.endswith("%"):
        raise reject(
            f"{board.width!r} cannot be a percentage on the root board — "
            "there is no parent to size it relative to. Use pixels "
            "(e.g. '900' or '900px')."
        )
    pixels = parse_dimension(raw, total=0.0)
    if pixels is None:
        raise reject(
            f"{board.width!r} is not a valid dimension (use e.g. '900' or '900px')."
        )
    if pixels <= 0:
        raise reject(f"{board.width!r} must be positive (got {pixels}).")

    frame_patch = board.style.model_dump().get("frame") if board.style else None
    if isinstance(frame_patch, dict) and frame_patch.get("width") is not None:
        raise reject(
            "Cannot specify both 'width:' and 'style.frame.width:' on the "
            "root board. 'width:' is sugar for 'style.frame.width:' — use "
            "one or the other."
        )

    return StylePatch.model_validate({"frame": {"width": pixels}})


def sync_board_resolved_style(
    board: Any,
    parent_resolved: ResolvedStyle | None = None,
    parent_context: ChartStyleContext | None = None,
    parent_patch: StylePatch | None = None,
) -> None:
    """Re-cascade ``board.resolved_style``/``board.chart_style_context`` from ``board.theme``.

    Called by ``Board.set_theme`` whenever a theme is changed after compile;
    the render layer never calls it directly. Nested boards are re-cascaded
    too, since every field an ancestor board explicitly authored flows down
    via ``parent_patch`` (through the same nested-board ``Merge`` relation
    ``compile_board_resolved_style`` uses at compile time).

    No ``parent_theme_name`` here, unlike the compile-time propagate walk:
    a nested ``board.theme`` is frozen at compile time (sibling boards never
    re-derive it when an ancestor's theme changes later), so comparing it
    against the just-changed parent's theme here would read stale
    inheritance as if this board had pinned its own theme. The nested-theme
    ink-canvas fix (``compile_board_resolved_style``'s ``parent_theme_name``)
    is compile-time only; a ``set_theme()`` re-cascade keeps its pre-fix
    behavior.
    """
    board.resolved_style, board.chart_style_context, own_patch = (
        compile_board_resolved_style(
            board.authored_style,
            parent_resolved,
            parent_context,
            parent_patch,
            theme_name=board.theme,
            currency=board.regional.currency,
            language=board.regional.resolved_language,
            layout_type=board.layout.type,
        )
    )
    for item in board.layout.items or []:
        if item.board is not None:
            sync_board_resolved_style(
                item.board,
                board.resolved_style,
                board.chart_style_context,
                own_patch,
            )


def _propagate_resolved_style(
    layout: Any,
    parent_resolved: ResolvedStyle,
    parent_context: ChartStyleContext,
    parent_patch: StylePatch | None = None,
    parent_theme_name: str | None = None,
) -> None:
    """Walk the layout tree and set resolved_style/chart_style_context on nested Boards."""
    from dbt_charts.core.compile.models.board.normalized import Layout

    if not isinstance(layout, Layout):
        return

    for item in layout.items:
        if item.board is not None:
            (
                item.board.resolved_style,
                item.board.chart_style_context,
                own_patch,
            ) = compile_board_resolved_style(
                item.board.authored_style,
                parent_resolved,
                parent_context,
                parent_patch,
                theme_name=item.board.theme,
                parent_theme_name=parent_theme_name,
                currency=item.board.regional.currency,
                language=item.board.regional.resolved_language,
                layout_type=item.board.layout.type,
            )
            _propagate_resolved_style(
                item.board.layout,
                item.board.resolved_style,
                item.board.chart_style_context,
                own_patch,
                parent_theme_name=item.board.theme,
            )


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug.

    "Raw Data" → "raw_data"
    "My Tab!" → "my_tab"
    """
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"[\s-]+", "_", slug).strip("_")


def _chart_source_path(
    board: AuthoredBoard, chart_name: str, path_prefix: AuthoringPathPrefix
) -> str:
    """Where this chart's definition sits in the file being normalized.

    Two answers:

    - a key in this board's own ``charts:`` block sits at that block's
      coordinates, under this board's prefix — whether the value there is an
      inline chart or a ``ChartRef`` naming one in another file. A ref key
      still has real coordinates in *this* file (the reference string), which
      is what a diagnostic should point a click at; see
      ``_chart_defined_in_other_file`` for the authoring-handle half of this.
    - anything else reached the registry from elsewhere in the tree and takes
      its declaring board's coordinates when that board normalizes it.
    """
    if board.charts is not None and chart_name in board.charts:
        return prefixed(path_prefix, f"charts.{chart_name}")
    return f"charts.{chart_name}"


def _chart_defined_in_other_file(board: AuthoredBoard, chart_name: str) -> bool:
    """True when this board's ``charts.<chart_name>`` is a name, not a definition.

    A ``ChartRef`` value is a reference string — the design walk refuses it,
    and the renderer withholds the authoring handle for it — but the
    coordinates ``_chart_source_path`` returns still name a real spot in this
    file, so a diagnostic anchors there while the handle stays suppressed.
    """
    return board.charts is not None and isinstance(
        board.charts.get(chart_name), ChartRef
    )


def normalize_board(
    board: AuthoredBoard,
    board_id: str | None = None,
    parent_context: dict[str, Any] | None = None,
    query_registry: dict[str, AnyQuery] | None = None,
    chart_registry: dict[str, RegisteredChart] | None = None,
    depth: int = 0,
    base_dir: ProjectDirectory | None = None,
    parent_level: int = 0,
    path_prefix: AuthoringPathPrefix = "",
) -> Board:
    """Normalize a AuthoredBoard into a Board with guaranteed structure.

    Stage: COMPILE (Step 3 of 4: Normalization)

    This is the core transformation from user-provided YAML structure to the
    internal compiled representation. After this step, all references are resolved,
    IDs are assigned, and downstream code can rely on guaranteed field presence.

    Normalization performs:
    - Resolve all chart references (queries, extends, partials)
    - Generate unique IDs for all entities
    - Apply structural defaults (empty description/subtitle)
    - Transform input types to compiled types
    - Create unified Layout structure
    - Propagate default source to queries without explicit source

    Args:
        board: Input AuthoredBoard from parsing/validation step.
        board_id: Optional explicit ID for this board
        parent_context: Context from parent board (for nested boards)
        query_registry: Complete query registry for reference resolution
        chart_registry: Complete chart registry for reference resolution
        depth: Structural nesting depth (for cycle detection and root detection)
        base_dir: ProjectDirectory handle for the board file (used to resolve sub-file refs).
        parent_level: Semantic heading level of the parent board. The current
            board's level = parent_level + (1 if board.title else 0).
            Root call passes 0 so a titled root board gets level=1.

    Returns:
        Board with guarantees:
            - All fields required by compiled types are present
            - All references resolved to actual objects
            - All IDs are unique and deterministic
            - Layout is unified (rows/cols/grid/tabs → Layout type)

    Raises:
        ReferenceError: When a chart references an undefined query
        CompilationError: When normalization fails

    Example:
        >>> board = parse_yaml(yaml_content)
        >>> errors = validate_board(board)
        >>> if not errors:
        ...     compiled = normalize_board(board)
        ...     print(compiled.charts['revenue'].id)  # Guaranteed to exist
        'revenue'
    """
    # Prevent infinite recursion
    MAX_DEPTH = 50
    if depth > MAX_DEPTH:
        raise CompilationError(f"Maximum nesting depth ({MAX_DEPTH}) exceeded")

    if depth > 0 and board.card_gap:
        raise CompilationError(
            "card_gap can only be set on the root board, not on nested boards"
        )

    parent_context = parent_context or {}
    # model_fields_set, not a default comparison: an authored `none` must win.
    inherits_html_policy = (
        "html_policy" not in board.model_fields_set and "html_policy" in parent_context
    )
    html_policy = cap_html_policy(
        parent_context["html_policy"] if inherits_html_policy else board.html_policy,
        resolve_html_policy_ceiling().value,
    )
    regional = _board_regional(board, parent_context.get("regional"))
    links = _board_links(board, parent_context.get("links"))
    if base_dir is None:
        base_dir = parent_context.get("base_dir")

    # ════════════════════════════════════════════════════════════════════
    # STEP 1: Setup — default source, registries, board ID
    # ════════════════════════════════════════════════════════════════════
    default_source = board.get_default_source() or parent_context.get("default_source")
    # Board-global named source configs — threaded (like base_dir) so inline
    # chart queries resolve their source exactly like up-top
    # queries. Set once at the root compile and carried down via parent_context.
    # `{}` here, and nowhere downstream: "this compile has no project config" is
    # a real state at this boundary, but past it `sources` carries the source-scope
    # `cache:` layer, and an optional that resolves to `{}` on its own reads a
    # forgotten argument as "no source policy" — day-old data, no type error.
    # Never `or {}`: an empty registry is the caller's shared dict, and inline
    # file sources registered below must land in it.
    sources: dict[
        str, Any  # type-state: explicit_any — a source config is a per-warehouse dict
    ] = parent_context.get(  # type-state: silent_fallback — no project config is a real root state, as above
        "sources", {}
    )
    # This board's own `cache:` over the one it inherits from the board it's nested
    # in — every query normalized below here (named, inline on a chart, inline on
    # a variable's options) gets it folded in between the source and query layers.
    inherited_cache: CachePatch = parent_context.get("board_cache") or INHERIT_CACHE
    board_cache = merge_cache_layers(inherited_cache, board.cache)
    # The cascade root, threaded from the compile entry alongside `sources` —
    # never read off the process-global config, which only `dct serve` fills in
    # and which one Cloud worker shares across orgs. None (a board normalized
    # with no project attached) resolves to the shipped root at the leaf.
    cache_root: CachePatch | None = parent_context.get("cache_root")

    # Build registries if not provided (allows direct normalize_board() calls in
    # tests). Chart registry goes first: a cross-board-imported chart resolves
    # its own query chain into cross_board_queries (compile/AGENTS.md § cross-
    # board chart imports), which then seeds the query registry — mirroring
    # compile_authored_board's STEP 3b → STEP 4 order.
    cross_board_queries: dict[str, AnyQuery] = {}
    if chart_registry is None:
        from dbt_charts.core.compile.compiler import build_chart_registry

        # `inherited_cache`, not `board_cache`, for the same reason as the query
        # registry below: `_build_registry` folds this board's own `cache:` in.
        chart_registry = build_chart_registry(
            board,
            cross_board_queries,
            base_dir=base_dir,
            sources=sources,
            cache_root=cache_root,
            board_cache=inherited_cache,
        )

    if query_registry is None:
        from dbt_charts.core.compile.compiler import build_query_registry

        # `inherited_cache`, not `board_cache`: the registry folds this board's
        # own `cache:` over whatever it is handed.
        query_registry = build_query_registry(
            board,
            base_dir,
            registry=cross_board_queries,
            default_source=default_source,
            sources=sources,
            cache_root=cache_root,
            board_cache=inherited_cache,
        )

    if not board_id:
        board_id = _generate_board_id(board, depth, parent_context.get("parent_id"))

    # ════════════════════════════════════════════════════════════════════
    # STEP 2: Variables
    # ════════════════════════════════════════════════════════════════════
    # Variable names are globally unique across the board tree; the renderer
    # builds the global registry at render time. Here we collect local defs only.
    local_variables: dict[str, Variable] = {}
    if board.variables:
        for var_name, var_def in board.variables.items():
            validate_authored_variable_name(var_name)
            if isinstance(var_def, Variable):
                local_variables[var_name] = var_def
            else:
                # VariableRef — cross-file reference (e.g., "file.variables.var_name")
                from dbt_charts.core.compile.compiler import load_from_reference

                imported = load_from_reference(
                    var_def,
                    base_dir=base_dir,
                    sources=sources,
                    yaml_path=["variables", var_name],
                )
                # Recorded here because here is where the provenance is known:
                # downstream, an imported variable and a locally authored one
                # are the same object, and the renderer has to tell them apart
                # to decide whether `variables.<name>` in this file is a handle
                # or a reference string nothing here can edit.
                imported.defined_in_other_file = True
                local_variables[var_name] = imported

    # Auto-detect input type when set to "auto" (the default)
    for var in local_variables.values():
        # Not `not var.authored_fields`: a variable the author wrote nothing on
        # has an empty set that is already settled, and a second pass must not
        # read its inferred `input` as authored.
        if "authored_fields" not in var.model_fields_set:
            # `input: auto` asks for inference; it is not a value to judge.
            asked_auto = {"input"} if var.input == "auto" else set()
            var.authored_fields = frozenset(
                var.model_fields_set - _INFERRED_VARIABLE_FIELDS - asked_auto
            )
        infer_variable_input(var)

    # Promote inline SQL in variable options.query to synthetic named queries
    promote_inline_option_queries(
        local_variables,
        query_registry,
        default_source,
        base_dir,
        sources=sources,
        cache_root=cache_root,
        board_cache=board_cache,
    )

    # Promote column-bound options (options.column / variable.column) to synthetic
    # named queries — same pattern as promote_inline_option_queries but for column refs.
    # Invalid identifiers raise CompilationError here, at compile time.
    promote_column_option_queries(
        local_variables,
        query_registry,
        default_source,
        sources=sources,
        cache_root=cache_root,
        board_cache=board_cache,
    )

    expand_composed_variable_dependencies(query_registry)

    # Compute cascading dropdown dependencies from Jinja variable references
    compute_variable_dependencies(local_variables, query_registry)

    # Validate and collect local defaults (global defaults built by renderer)
    variable_defaults: VariableValues = {}
    for var_name, var in local_variables.items():
        validate_choice_type(var_name, var)
        if var.default is not None:
            validate_variable_value(var_name, var, var.default)
            variable_defaults[var_name] = var.default

    # ════════════════════════════════════════════════════════════════════
    # STEP 3: Queries
    # ════════════════════════════════════════════════════════════════════
    # Extract locally defined queries from the global registry (for Board.queries).
    local_queries: dict[str, AnyQuery] = {}
    if board.queries:
        for query_name in board.queries:
            if query_name in query_registry:
                local_queries[query_name] = query_registry[query_name]
            else:
                raise CompilationError(
                    f"Query '{query_name}' not found in registry. "
                    "This may indicate a compilation error."
                )

    # Pull synthetic queries (from promote_inline_option_queries and
    # promote_column_option_queries) into local_queries so that Board.queries
    # contains them.  The executor's _get_query checks board.queries first; having
    # them here means tests and callers do not need to pass a separate query_registry.
    for var in local_variables.values():
        q = var.get_option_query()
        if q and q not in local_queries and q in query_registry:
            local_queries[q] = query_registry[q]

    # ════════════════════════════════════════════════════════════════════
    # STEP 4: Charts
    # ════════════════════════════════════════════════════════════════════
    # Normalize all charts from the global registry — layout resolution needs
    # all of them by name. Then extract locally defined ones for Board.charts.
    # The registry is board-global, so only the charts THIS board declares are
    # addressed under its path — a chart declared elsewhere gets its own
    # `charts:` block's coordinates when its declaring board normalizes it, and
    # prefixing it here would name a `charts:` key this board does not have.
    normalized_charts: dict[str, Chart] = {}
    for chart_name, registered in chart_registry.items():
        normalized_charts[chart_name] = normalize_chart(
            chart_name,
            registered.definition,
            query_registry,
            base_dir,
            default_source,
            source_path=_chart_source_path(board, chart_name, path_prefix),
            defined_in_other_file=_chart_defined_in_other_file(board, chart_name),
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
            written_in=registered.base_dir,
            text_query_keys=registered.text_query_keys,
            link_open=links.open,
        )

    local_charts: dict[str, Chart] = {}
    if board.charts:
        for chart_name in board.charts:
            if chart_name in normalized_charts:
                local_charts[chart_name] = normalized_charts[chart_name]
            else:
                raise CompilationError(
                    f"Chart '{chart_name}' not found in registry. "
                    "Charts must be defined in the board tree or imported."
                )

    charts = local_charts

    # Pull each local chart's cross-board-imported query chain (if any) into
    # local_queries — mirrors the synthetic-option-query precedent above.
    # Needed so validate_variable_references (which walks board.queries) can
    # give the free missing-variable check for queries pulled in by lexical
    # scoping, not just locally-authored ones.
    if board.charts:
        from dbt_charts.core.compile.compiler import pull_cross_board_query_chain

        for chart in local_charts.values():
            if chart.query_name:
                pull_cross_board_query_chain(
                    chart.query_name, query_registry, local_queries
                )
            if isinstance(chart, (BarChart, LineChart, AreaChart, ScatterChart)):
                for layer in chart.layers:
                    if layer.query:
                        pull_cross_board_query_chain(
                            layer.query, query_registry, local_queries
                        )

    for surface, text in (("Board title", board.title), ("Board text", board.text)):
        for query_name in sorted(text_query_references([text])):
            if query_name not in query_registry:
                raise CompilationError.from_code(
                    ERR_TEXT_UNKNOWN_QUERY,
                    surface=surface,
                    query_name=query_name,
                    defined=sorted(query_registry),
                )
            local_queries.setdefault(query_name, query_registry[query_name])
    check_listing_calls(board.title, board.text)
    _pull_chart_text_queries(charts, query_registry, local_queries, False)

    # Expand transitive variable dependencies (A→B→C means chart depends on A, B, C)
    expand_chart_variable_dependencies(charts, local_variables)

    # ════════════════════════════════════════════════════════════════════
    # STEP 5: Style & theme
    # ════════════════════════════════════════════════════════════════════
    # Boards should inherit the same effective theme choice as their charts so
    # nested boards resolve typography/palette consistently. The board canvas
    # background itself is intentionally not copied from chart themes: board
    # chrome follows board/style defaults (white in Playground review mode),
    # while chart surfaces keep the theme-owned canvas/background colors.
    # Theme name is read from extends (highest-priority theme name wins).
    # If no theme is found, inherit from parent or fall back to configured default.
    from dbt_charts.core.compile.config import get_default_theme_name

    theme = (
        _theme_from_extends(board.extends)
        or parent_context.get("theme")
        or get_default_theme_name()
    )

    # Compile board-scoped resolved style before layout so nested boards can inherit.
    # Root-only frame width: `width:` is layout-placement sugar everywhere except
    # the root, where it means `style.frame.width:` (see _root_width_style_patch).
    # Folded into board_style itself (not applied as a separate one-shot patch) so
    # it survives Board.set_theme's later re-cascade via authored_style, exactly
    # like an authored style.frame.width would.
    board_style = board.style
    if depth == 0:
        root_width_patch = _root_width_style_patch(board)
        if root_width_patch is not None:
            from dbt_charts.core.compile.merge import merge_patches

            board_style = (
                merge_patches(board_style, root_width_patch)
                if board_style is not None
                else root_width_patch
            )

    resolved_style, chart_style_context, own_style_patch = compile_board_resolved_style(
        board_style,
        parent_context.get("resolved_style"),
        parent_context.get("chart_style_context"),
        theme_name=theme,
        parent_theme_name=parent_context.get("theme"),
        currency=regional.currency,
        language=regional.resolved_language,
    )
    if clock_hides_zone(resolved_style.timestamp.format, regional.zone):
        raise CompilationError.from_code(
            ERR_VALIDATION_FIELD,
            field_path="style.timestamp.format",
            pydantic_msg=CLOCK_HIDES_ZONE,
        )

    # ════════════════════════════════════════════════════════════════════
    # STEP 6: Layout
    # ════════════════════════════════════════════════════════════════════
    # parent_variables: variables inherited from the parent plus local defaults,
    # used to resolve templated board-import paths. Nested board content keeps its
    # templates intact so they resolve at render time.
    parent_variables: dict[str, Any] = {}
    if parent_context.get("variables"):
        parent_variables.update(parent_context["variables"])
    for var_name, var in local_variables.items():
        if var.default is not None:
            parent_variables[var_name] = var.default

    # Semantic heading level: count only titled ancestors.
    # Bare wrappers (title is empty/None) do not advance the level counter.
    # If style.title.level is an integer override (from theme or board YAML), it wins;
    # that value also becomes the parent_level for descendants (cascade as if structural truth).
    style_level_override = resolved_style.title.level
    if isinstance(style_level_override, int):
        this_level = style_level_override
    else:
        this_level = parent_level + (1 if board.title else 0)

    layout = build_unified_layout(
        board,
        normalized_charts,
        query_registry,
        board_id,
        depth,
        base_dir,
        default_source,
        board_cache,
        chart_registry,
        theme,
        parent_variables,
        resolved_style=resolved_style,
        chart_style_context=chart_style_context,
        parent_level=this_level,
        sources=sources,
        html_policy=html_policy,
        regional=regional,
        links=links,
        cache_root=cache_root,
        path_prefix=path_prefix,
    )

    # The root's walk re-resolves every nested board against its real parent,
    # so a nested board's own walk would only be overwritten.
    if depth == 0:
        _propagate_resolved_style(
            layout,
            resolved_style,
            chart_style_context,
            own_style_patch,
            parent_theme_name=theme,
        )

    # Auto-generate hidden variables for tabs/details widgets
    auto_variables = generate_layout_variables(layout)
    local_variables.update(auto_variables)
    for var_name, var in auto_variables.items():
        if var.default is not None:
            variable_defaults[var_name] = var.default

    # Collect all charts from the layout tree into board.charts.
    # Layout instances are the canonical rendering objects — they are the same
    # objects the allocator will mutate, so allocator mutations propagate
    # directly to board.charts without any post-hoc patchup.
    # Precedence: layout instances win over pre-compiled local_charts entries
    # for charts that appear in both (e.g. a named chart referenced inside a
    # tab's rows). Both are compiled from the same raw def so the values are
    # identical; the layout instance is the one the renderer actually sees.
    # Charts declared in board.charts but absent from the layout (orphans) are
    # kept from the pre-compiled dict.
    layout_charts: dict[str, Chart] = {}
    _collect_charts_from_layout(layout, layout_charts)
    _pull_chart_text_queries(
        {name: c for name, c in layout_charts.items() if name not in charts},
        query_registry,
        local_queries,
        True,
    )
    charts = {**charts, **layout_charts}

    # ════════════════════════════════════════════════════════════════════
    # STEP 7: Assemble Board
    # ════════════════════════════════════════════════════════════════════

    compiled_board = Board(
        id=board_id,
        title=board.title if board.title is not None else "",
        notes=board.notes if board.notes is not None else "",
        tags=board.tags if board.tags is not None else [],
        aliases=board.aliases if board.aliases is not None else [],
        text=board.text if board.text is not None else "",
        base_dir=None if base_dir is None else base_dir.relpath,
        html_policy=html_policy,
        variables=local_variables,  # Local variables for UI controls
        queries=local_queries,
        charts=charts,
        layout=layout,
        variable_defaults=variable_defaults,  # Local variable defaults
        card_gap=board.card_gap,
        links=links,
        regional=regional,
        authored_style=board_style,
        theme=theme,
        resolved_style=resolved_style,
        chart_style_context=chart_style_context,
        level=this_level,
        meta=compiled_meta(),
    )

    # Single-series rhythm allocation — root-only pass that walks the entire
    # board tree in document order and assigns rhythm_slot to single-series-
    # eligible charts. board.charts holds the same instances as the layout tree
    # (guaranteed by the collection step above), so in-place mutations on the
    # layout instances propagate to board.charts automatically.
    if depth == 0:
        allocate_single_series_slots(compiled_board)

    # ════════════════════════════════════════════════════════════════════
    # STEP 8: Variable registry (root board only)
    # ════════════════════════════════════════════════════════════════════
    if depth == 0:
        compiled_board.variable_registry = build_variable_registry(compiled_board)

        # Pre-compute global defaults to avoid repeated comprehension at render time
        compiled_board.variable_defaults = {
            name: var.default
            for name, var in compiled_board.variable_registry.items()
            if var.default is not None
        }

    # ════════════════════════════════════════════════════════════════════
    # STEP 9: Chart focus (root board only)
    # ════════════════════════════════════════════════════════════════════
    # Narrows the board to a single chart and its dependent variables.
    if depth == 0 and board.chart_focus:
        from dbt_charts.core.compile.normalize.chart_focus import focus_on_chart

        compiled_board = focus_on_chart(compiled_board, board.chart_focus)

    # ════════════════════════════════════════════════════════════════════
    # STEP 10: Variable reference validation (root board only)
    # ════════════════════════════════════════════════════════════════════
    # Runs after chart_focus so we validate exactly the board being compiled.
    # Raises VariableReferenceErrors (a CompilationError subclass carrying a
    # list of Diagnostics) so the compiler can report all undefined refs at
    # once without collapsing to a single raise.
    if depth == 0:
        var_errs = validate_variable_references(compiled_board)
        if var_errs:
            registry_keys = (
                sorted(compiled_board.variable_registry.keys())
                if compiled_board.variable_registry
                else []
            )
            raise VariableReferenceErrors(
                [ve.to_diagnostic(registry_keys) for ve in var_errs]
            )

    return compiled_board


def _pull_chart_text_queries(
    charts: dict[str, Chart],
    query_registry: dict[str, AnyQuery],
    local_queries: dict[str, AnyQuery],
    inline: bool,
) -> None:
    """Make every query a chart's display text reads part of the board.

    Adds each to ``local_queries`` (with its composition chain when it is an
    imported chart's query) and its variable dependencies to the chart's own,
    so a focused render keeps the variables the text depends on.

    ``inline`` charts have a generated id, so errors name their layout path.

    Raises:
        CompilationError: ``ERR-TEXT-UNKNOWN-QUERY`` for a name no query matches.
    """
    from dbt_charts.core.compile.compiler import pull_cross_board_query_chain

    for chart in charts.values():
        label = (chart.source_path or chart.id) if inline else chart.id
        for field in display_text_fields(chart.type):
            reject_listing_calls(getattr(chart, field), f"chart '{label}' {field}")
            for name in sorted(text_query_references([getattr(chart, field)])):
                key = chart.text_queries[name]
                if key not in query_registry:
                    raise CompilationError.from_code(
                        ERR_TEXT_UNKNOWN_QUERY,
                        surface=f"Chart '{label}' {field}",
                        query_name=name,
                        defined=sorted(query_registry),
                    )
                pull_cross_board_query_chain(key, query_registry, local_queries)
                local_queries.setdefault(key, query_registry[key])
                chart.variable_dependencies |= query_registry[key].variable_dependencies


def _generate_board_id(
    board: AuthoredBoard,
    depth: int,
    parent_id: str | None = None,
) -> str:
    """Generate a unique board ID.

    Args:
        board: AuthoredBoard to generate ID for
        depth: Nesting depth
        parent_id: Parent board ID if nested

    Returns:
        Unique board ID
    """
    if board.id:
        return board.id

    if parent_id:
        return f"{parent_id}_nested{depth}"

    if board.title:
        slug = slugify(board.title)
        return slug or "untitled_board"

    return "untitled_board"
