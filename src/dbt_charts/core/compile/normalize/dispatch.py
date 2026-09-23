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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

    from dbt_charts.core.project import ProjectDirectory

from dbt_charts.core.compile.config import cap_html_policy, resolve_html_policy_ceiling
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import (
    Board,
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
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.refs import ChartRef
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.charts import (
    _collect_charts_from_layout,
    normalize_chart,
)
from dbt_charts.core.compile.normalize.layout import (
    AuthoringPathPrefix,
    build_unified_layout,
    prefixed,
)
from dbt_charts.core.compile.normalize.queries import (
    normalize_query as normalize_query,  # re-exported (used by compiler.py, tests)
)
from dbt_charts.core.compile.normalize.single_series_allocation import (
    allocate_single_series_slots,
)
from dbt_charts.core.compile.normalize.variables import (
    VariableReferenceErrors,
    build_variable_registry,
    compute_variable_dependencies,
    detect_variable_input_type,
    expand_chart_variable_dependencies,
    generate_layout_variables,
    promote_column_option_queries,
    promote_inline_option_queries,
    validate_choice_type,
    validate_variable_references,
    validate_variable_value,
)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.codes_compile import ERR_VALIDATION_FIELD


def compiled_meta() -> dict[str, str]:
    """Compilation metadata stamp every compiled ``Board`` carries.

    Every `Board` in the tree gets this stamp: `normalize_board` below for
    boards it builds, and the leaf-tab branches in `layout._resolve_tab_items`
    that construct a `Board` directly without going through `normalize_board`.
    """
    return {
        "compiled_at": datetime.now(timezone.utc).isoformat(),
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
# cascade varies on within one compile. Project config
# (`get_config().vega.config`) is the other input and is fixed for a
# compile's duration, so it is deliberately not in the key.
#
# `repr` of the dumped fields, not `model_dump_json`: JSON writes inf and nan as
# null, so `gap: .inf` would key the same as `gap: null` and silently take the
# wrong style off the memo. A repr the other way round can only miss.
_BoardCascadeKey = tuple[str, str, str | None, bool]
_CascadeProducts = tuple[ResolvedStyle, ChartStyleContext]

# Board style cascades already resolved, innermost compile last. Every cascade
# in a compile lands in compile_board_resolved_style, and most of them
# re-resolve a style that same board already resolved: normalize_board and
# _propagate_resolved_style each walk the nested-board tree, and sibling boards
# usually share a style block.
#
# One compile's worth, not the process's: a resolved style is large, and near
# all the repetition is inside a single board. A process-lifetime cache would
# retain a lot to catch the little that repeats across boards.
#
# A tuple of dicts rather than `dict | None`: the empty tuple already
# represents "nothing cached yet," so it needs no `None` sentinel on top; the
# outer frames are never read, only the innermost.
_BOARD_STYLE_CACHES: ContextVar[
    tuple[dict[_BoardCascadeKey, _CascadeProducts], ...]
] = ContextVar("board_style_caches", default=())


@contextmanager
def board_style_cache() -> Generator[None]:
    """Memoize board style cascades for the duration of one compile."""
    token = _BOARD_STYLE_CACHES.set((*_BOARD_STYLE_CACHES.get(), {}))
    try:
        yield
    finally:
        _BOARD_STYLE_CACHES.reset(token)


def _current_board_style_cache() -> dict[_BoardCascadeKey, _CascadeProducts]:
    """The memo for the compile in progress.

    Callers outside a compile (``Board.set_theme``, render) get a throwaway they
    are welcome to write into: nothing is remembered when nothing is compiling.
    """
    caches = _BOARD_STYLE_CACHES.get()
    return caches[-1] if caches else {}


def compile_board_resolved_style(
    board_style: StylePatch | None,
    parent_resolved: ResolvedStyle | None,
    parent_context: ChartStyleContext | None,
    parent_patch: StylePatch | None = None,
    theme_name: str | None = None,
    parent_theme_name: str | None = None,
) -> tuple[ResolvedStyle, ChartStyleContext, StylePatch | None]:
    """Build the authoritative ResolvedStyle + ChartStyleContext for this board scope.

    When this board authors its own ``style:`` (``board_style is not None``),
    merge order is:
       relation (``scope_patch(parent_patch, board_style)``, which merges via
       ``merge_patches(..., nested=True)``). Each style field's own
       ``Merge`` marker settles it: most default to the ancestor's authored
       value crossing in when this board leaves them unset; a field marked
       ``Merge(nested=Strategy.CHILD)`` — a per-board structural or
       root-only concern (spacing/frame, footer/timestamp chrome), not
       thematic identity — always takes this board's own value instead,
       even when that value is unset.
    2. Theme defaults (theme_name → compiled theme → resolve_style) fill
       whatever the merged patch left unset.

    When this board authors no ``style:`` of its own, it reuses
    ``parent_resolved``/``parent_context`` verbatim (or a bare theme resolve
    at the root) — see the fast path below.

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
    """
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.merge import scope_patch

    if (parent_resolved is None) != (parent_context is None):
        raise ValueError(
            "compile_board_resolved_style requires parent_resolved and "
            "parent_context together — pass both or neither."
        )

    effective_theme = theme_name or get_default_theme_name()
    base = get_theme_style(effective_theme)
    # This scope's own theme differs from its immediate parent's -- the
    # cascade already falls through to the parent's theme when this board
    # authors none (see the docstring above), so a difference here can only
    # come from this scope's own `theme:`/`extends:`. render always paints
    # a nested board's background over the PARENT's real, already-composited
    # pixels (`bottom_layer` below), own theme or not -- an opaque own-theme
    # canvas just composites to itself over anything beneath it, so this
    # doesn't change what the bottom layer is. What it does change: this
    # scope's own resolve can never be reused verbatim from the parent (the
    # parent's entire resolved style is for the wrong theme), so it always
    # earns a fresh composite -- folded into `background_authored` below,
    # and it rules out the `board_style is None` branch's "reuse the parent
    # verbatim" shortcut.
    own_theme_declared = (
        parent_theme_name is not None and effective_theme != parent_theme_name
    )
    # A nested board paints on top of what its PARENT scope actually
    # composited, never the theme's raw canvas underneath that -- see
    # resolve_style_and_context's own docstring. The root has no parent
    # context, so it keeps the theme canvas (base_background=None).
    bottom_layer = parent_context.ink_canvas if parent_context is not None else None

    if board_style is None:
        if (
            not own_theme_declared
            and parent_resolved is not None
            and parent_context is not None
        ):
            return parent_resolved, parent_context, parent_patch
        # No style: of its own to merge, but either there is no parent to
        # reuse from (the root) or this scope's own theme rules out the
        # shortcut above -- a fresh resolve against `base`, still folding
        # whatever an ancestor authored (`parent_patch`).
        patches = (parent_patch,) if parent_patch is not None else ()
        return (
            *resolve_style_and_context(
                base,
                *patches,
                base_background=bottom_layer,
                background_authored=own_theme_declared or parent_context is None,
            ),
            parent_patch,
        )

    own_patch = scope_patch(parent_patch, board_style)
    # An authored background composites exactly once over the canvas
    # beneath it; a scope that only inherits one (this board's own raw
    # style: authors no background:) adds nothing (resolve_style_and_context's
    # own docstring has the full rule). Checked on `board_style`, the raw,
    # unmerged patch -- `own_patch.charts.background` would echo the
    # inherited value regardless of whether THIS board authored anything,
    # since it folds the parent's own patch forward. Both authoring
    # spellings count (the common `style.background:`, and the rarer
    # explicit `style.charts.background:` override). Authored means the
    # key is present AND the value is not None -- an explicit
    # `background: null` is not an authored color, so it must not force a
    # second composite either. `model_dump(exclude_unset=True)` (a plain
    # dict, so `.get(...)` types as `Any`), not `board_style.background is
    # not None` directly: a patch model's generated runtime class makes
    # every field truly optional, but its TYPE_CHECKING stub inherits the
    # field's type from the non-patch base (`Style.background: str`,
    # required on the resolved model), so a bare `is not None` on the
    # attribute itself reads as statically-impossible to pyright even
    # though it is meaningful at runtime.
    _own_fields = board_style.model_dump(exclude_unset=True)
    background_authored = (
        own_theme_declared
        or parent_context is None
        or _own_fields.get("background") is not None
        or (
            isinstance(_own_fields.get("charts"), dict)
            and _own_fields["charts"].get("background") is not None
        )
    )

    cache = _current_board_style_cache()
    key = (
        effective_theme,
        repr(own_patch.model_dump(exclude_unset=True)),
        bottom_layer,
        background_authored,
    )
    cached = cache.get(key)
    if cached is not None:
        # A shallow copy, not the cached object: the sizing pass keys its
        # per-chart resolve caches on ``id(resolved_style)`` to tell one board's
        # scope from another's (``resolved_chart_variant_key``), so two boards
        # that merge to the same style must still hold two objects. The copy
        # shares the whole resolved tree — what the memo skips is the cascade,
        # not the allocation.
        return copy.copy(cached[0]), cached[1], own_patch

    resolved = resolve_style_and_context(
        base,
        own_patch,
        base_background=bottom_layer,
        background_authored=background_authored,
    )
    cache[key] = resolved
    return (*resolved, own_patch)


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
    chart_registry: dict[str, Any] | None = None,
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

    # Root-level boards must have renderable content (layout or text). A title-only
    # board is valid at depth>0 (AuthoredBoard.validate_layout allows it for section
    # headers and style wrappers), but is not renderable as a standalone dashboard.
    if (
        depth == 0
        and not board.text
        and not board.charts
        and all(f is None for f in (board.rows, board.cols, board.grid, board.tabs))
    ):
        raise CompilationError(
            "Board must have at least one layout type, text, or chart"
        )

    parent_context = parent_context or {}
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
    sources: dict[str, Any] = parent_context.get("sources") or {}
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
        was_auto = var.input == "auto"
        var.input = detect_variable_input_type(var)
        # Only flag select as refinable — multiselect has multi-value semantics
        # that can't map to datepicker/checkbox/slider, and strong structural
        # signals (bool → checkbox, min/max → slider) shouldn't be overridden.
        if was_auto and var.input == "select":
            var.input_auto_detected = True

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
    for chart_name, chart_def in chart_registry.items():
        normalized_charts[chart_name] = normalize_chart(
            chart_name,
            chart_def,
            query_registry,
            base_dir,
            default_source,
            source_path=_chart_source_path(board, chart_name, path_prefix),
            defined_in_other_file=_chart_defined_in_other_file(board, chart_name),
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
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
                merge_patches(board_style, root_width_patch, nested=False)
                if board_style is not None
                else root_width_patch
            )

    resolved_style, chart_style_context, own_style_patch = compile_board_resolved_style(
        board_style,
        parent_context.get("resolved_style"),
        parent_context.get("chart_style_context"),
        theme_name=theme,
        parent_theme_name=parent_context.get("theme"),
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
        cache_root=cache_root,
        path_prefix=path_prefix,
    )

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
        html_policy=cap_html_policy(
            board.html_policy, resolve_html_policy_ceiling().value
        ),
        variables=local_variables,  # Local variables for UI controls
        queries=local_queries,
        charts=charts,
        layout=layout,
        variable_defaults=variable_defaults,  # Local variable defaults
        card_gap=board.card_gap,
        auto_link=board.auto_link,
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
