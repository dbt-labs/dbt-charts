"""Layout resolution and unified layout construction."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory

from dbt_charts.core.compile.errors import CompilationError, ReferenceError
from dbt_charts.core.compile.models.board.authored import (
    AUTHORED_BOARD_ADAPTER,
    AuthoredBoard,
    GridLayout,
    LayoutType,
    TabLayout,
)
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
)
from dbt_charts.core.compile.models.cache import INHERIT_CACHE, CachePatch
from dbt_charts.core.compile.models.chart.authored import (
    _BaseChartFields,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.normalize.charts import (
    generate_inline_chart_id,
    normalize_chart,
)
from dbt_charts.core.utils import UniqueKeyLoader

# The enclosing board's own authoring path, threaded down so each item can name
# its absolute position. Two distinguishable states, which is why this is
# optional rather than just an empty string:
#   ""    the file root — a child of it is `rows.0`
#   None  no authored coordinates here at all, so children get no path either:
#         a subtree imported from another board file (its keys belong to *that*
#         file's source map) or a foreach expansion (generated, not authored)
AuthoringPathPrefix = str | None


def _validate_dimension(value: Any, name: str) -> None:
    """Reject zero/negative dimension values at compile time.

    Uses the shared ``parse_dimension`` primitive so the validator and
    the sizing engine agree on what constitutes a valid dimension.
    ``total=1.0`` makes percentages yield their numeric fraction (e.g.
    ``"50%"`` → 0.5) so the sign check works uniformly.
    """
    if value is None:
        return
    from dbt_charts.core.compile.sizing import parse_dimension

    parsed = parse_dimension(str(value), 1.0)
    if parsed is None:
        raise CompilationError(
            f"'{name}: {value}' is not a valid dimension"
            " (use e.g. '200', '200px', or '50%')"
        )
    if parsed <= 0:
        raise CompilationError(f"'{name}: {value}' must be positive (got {parsed})")


def build_unified_layout(
    board: AuthoredBoard,
    charts: dict[str, Chart],
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    parent_variables: dict[str, Any] | None = None,
    *,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    parent_level: int = 0,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    path_prefix: AuthoringPathPrefix = "",
) -> Layout:
    """Build a unified Layout from board layout fields.

    Converts rows/cols/grid/tabs to unified Layout structure.

    Args:
        board: AuthoredBoard with layout fields
        charts: Available charts for resolution
        query_registry: Query registry for inline charts
        board_id: Parent board ID
        depth: Nesting depth
        base_dir: ProjectDirectory handle for resolving external file references
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        theme: Vega-Lite theme to inherit to nested boards
        parent_variables: Variables from parent scope for template resolution
        parent_level: Semantic heading level of the enclosing board. Passed
            through to nested normalize_board calls so children compute their
            own level as parent_level + (1 if they have a title else 0).

    Returns:
        Unified Layout structure
    """
    from dbt_charts.core.compile.normalize.dispatch import slugify

    # Track used chart IDs across all layout items to avoid duplicates
    used_chart_ids = set(charts.keys())
    local_chart_ids = set(board.charts or {})

    if board.rows is not None:
        items = _resolve_layout_items(
            board.rows,
            charts,
            query_registry,
            board_id,
            depth,
            "row",
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )
        return Layout(type=LayoutType.ROWS, items=items)

    elif board.cols is not None:
        items = _resolve_layout_items(
            board.cols,
            charts,
            query_registry,
            board_id,
            depth,
            "col",
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )
        return Layout(type=LayoutType.COLS, items=items)

    elif board.grid:
        grid_obj = board.grid
        items = _resolve_grid_items(
            grid_obj,
            charts,
            query_registry,
            board_id,
            depth,
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )
        return Layout(
            type=LayoutType.GRID,
            items=items,
            columns=grid_obj.columns,
        )

    elif board.tabs:
        tabs_obj = board.tabs
        items, tab_titles = _resolve_tab_items(
            tabs_obj,
            query_registry,
            board_id,
            depth,
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            resolved_style=resolved_style,
            chart_style_context=chart_style_context,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            path_prefix=path_prefix,
        )

        # Compute tab slugs and variable name
        tab_slugs = [slugify(title) for title in tab_titles]
        # Validate slugs
        for i, slug in enumerate(tab_slugs):
            if not slug:
                raise CompilationError(
                    f"Tab title '{tab_titles[i]}' produces an empty slug. "
                    "Tab titles must contain at least one alphanumeric character."
                )
        # Check for slug collisions
        if len(set(tab_slugs)) != len(tab_slugs):
            seen: set[str] = set()
            for slug in tab_slugs:
                if slug in seen:
                    raise CompilationError(
                        f"Duplicate tab slug '{slug}'. Tab titles must produce "
                        "unique slugs (after lowercasing and replacing spaces with _)."
                    )
                seen.add(slug)

        # Use explicit id, or generate a unique name from board_id
        # (avoids collisions when multiple tabs layouts exist in nested boards)
        tab_variable = tabs_obj.id or f"_tab_{board_id}"

        # Resolve default tab from slug
        default_slug = slugify(tabs_obj.default) if tabs_obj.default else tab_slugs[0]
        default_tab = tab_slugs.index(default_slug) if default_slug in tab_slugs else 0

        return Layout(
            type=LayoutType.TABS,
            items=items,
            tab_titles=tab_titles,
            tab_slugs=tab_slugs,
            tab_variable=tab_variable,
            default_tab=default_tab,
            tab_position=tabs_obj.position,
        )

    elif board.charts:
        items = _resolve_layout_items(
            list(board.charts.keys()),
            charts,
            query_registry,
            board_id,
            depth,
            "row",
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )
        return Layout(type=LayoutType.ROWS, items=items)

    # Content-only board. Orphan-chart detection lives post-compile so
    # cross-board references work — see _detect_orphan_charts in board_warnings.py.
    return Layout(type=LayoutType.ROWS, items=[])


def _resolve_layout_items(
    items: list[Any],
    charts: dict[str, Chart],
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    prefix: str,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    used_chart_ids: set[str] | None = None,
    parent_variables: dict[str, Any] | None = None,
    parent_level: int = 0,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    local_chart_ids: set[str] | None = None,
    path_prefix: AuthoringPathPrefix = "",
) -> list[LayoutItem]:
    """Resolve layout items to LayoutItem objects.

    Args:
        items: List of layout items (strings, dicts, charts)
        charts: Available charts
        query_registry: Query registry
        board_id: Parent board ID
        depth: Nesting depth
        prefix: Item prefix for ID generation
        base_dir: ProjectDirectory handle for resolving external file references
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        theme: Vega-Lite theme to inherit to nested boards
        chart_registry: Raw chart definitions for nested board normalization
        used_chart_ids: Set of already-used chart IDs for uniqueness
        parent_variables: Variables from parent scope for template resolution

    Returns:
        List of resolved LayoutItem objects
    """
    resolved: list[LayoutItem] = []

    # Initialize used_chart_ids with existing chart names if not provided
    if used_chart_ids is None:
        used_chart_ids = set(charts.keys())
    local_chart_ids = local_chart_ids or set()

    for idx, item in enumerate(items):
        layout_item = _resolve_single_item(
            item,
            charts,
            query_registry,
            board_id,
            depth,
            f"{prefix}{idx}",
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )
        resolved.append(layout_item)

    return resolved


def _resolve_single_item(
    item: Any,
    charts: dict[str, Chart],
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    item_id: str,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    used_chart_ids: set[str] | None = None,
    parent_variables: dict[str, Any] | None = None,
    parent_level: int = 0,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    local_chart_ids: set[str] | None = None,
    path_prefix: AuthoringPathPrefix = "",
) -> LayoutItem:
    """Resolve a single layout item.

    Args:
        item: Layout item to resolve
        charts: Available charts
        query_registry: Query registry
        theme: Vega-Lite theme to inherit to nested boards
        board_id: Parent board ID
        depth: Nesting depth
        item_id: ID for this item (fallback for inline charts)
        base_dir: ProjectDirectory handle for resolving external file references
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        chart_registry: Raw chart definitions for nested board normalization
        used_chart_ids: Set of already-used chart IDs for uniqueness
        parent_variables: Variables from parent scope for template resolution

    Returns:
        Resolved LayoutItem
    """
    # String reference to chart OR board file import
    if isinstance(item, str):
        # Check for board file import (ends with .yml or .yaml)
        if item.endswith(".yml") or item.endswith(".yaml"):
            return _resolve_board_file_import(
                item,
                query_registry,
                board_id,
                depth,
                item_id,
                base_dir,
                default_source,
                board_cache,
                chart_registry,
                theme,
                parent_variables,
                parent_level=parent_level,
                cache_root=cache_root,
                sources=sources,
            )
        elif item in charts:
            source_path = (
                prefixed(path_prefix, f"charts.{item}")
                if item in (local_chart_ids or set())
                else ""
            )
            return LayoutItem(type="chart", chart=charts[item], source_path=source_path)
        else:
            raise ReferenceError(item, "layout item")

    # dict[str, AuthoredChart]: {"my_chart": AuthoredChart(...)}
    if isinstance(item, dict):
        if len(item) != 1:
            raise CompilationError(
                f"Named chart dict must have exactly one key, got: {list(item.keys())}"
            )
        _chart_name, chart_def = next(iter(item.items()))
        chart_dict = chart_def.model_dump(exclude_none=True)
        if used_chart_ids is None:
            used_chart_ids = set(charts.keys())
        inline_id = generate_inline_chart_id(chart_dict, item_id, used_chart_ids)
        source_path = _layout_source_path(path_prefix, item_id)
        chart = normalize_chart(
            inline_id,
            chart_dict,
            query_registry,
            base_dir,
            default_source,
            source_path=source_path,
            cache_root=cache_root,
            sources=sources,
            board_cache=board_cache,
        )
        return LayoutItem(type="chart", chart=chart, source_path=source_path)

    # Pre-validated AuthoredBoard (typed inline nested board from rows/cols)
    if isinstance(item, AuthoredBoard):
        from dbt_charts.core.compile.normalize.dispatch import normalize_board

        details = item.details
        user_width = str(item.width) if item.width is not None else None
        layout_height = str(item.height) if item.height is not None else None
        _validate_dimension(user_width, "width")
        _validate_dimension(layout_height, "height")

        # Composed here, beside board_id, from the same tree position: both are
        # derived from where the item sits, so they cannot drift.
        nested_path = _layout_source_path(path_prefix, item_id)
        compiled_nested = normalize_board(
            item,
            board_id=f"{board_id}_{item_id}",
            parent_context={
                "parent_id": board_id,
                "base_dir": base_dir,
                "default_source": default_source,
                "cache_root": cache_root,
                "board_cache": board_cache,
                "sources": sources,
                "theme": theme,
                "variables": parent_variables,
            },
            query_registry=query_registry,
            chart_registry=chart_registry,
            depth=depth + 1,
            base_dir=base_dir,
            parent_level=parent_level,
            # An item with no path of its own (a foreach expansion) roots nothing:
            # its children have no authored coordinates either.
            path_prefix=nested_path or None,
        )

        details_kwargs: dict[str, Any] = {}
        if details:
            details_id = item.id or f"_details_{item_id}"
            details_kwargs = {
                "details_variable": details_id,
                "details_summary": details.summary,
                "details_expanded_summary": details.expanded_title or details.summary,
            }
            compiled_nested.meta["details_expanded_default"] = details.expanded

        return LayoutItem(
            type="board",
            board=compiled_nested,
            source_path=nested_path,
            user_width=user_width,
            layout_height=layout_height,
            notes=item.notes,
            visible=item.visible,
            **details_kwargs,
        )

    # Pre-validated chart patch (typed inline chart from rows/cols)
    if isinstance(item, _BaseChartFields):
        if used_chart_ids is None:
            used_chart_ids = set(charts.keys())
        # exclude_none=True: None-defaulted fields must be absent so omitted
        # display text remains omitted after chart normalization.
        # visible is excluded=True on AuthoredChart so it won't appear in chart_dict.
        chart_dict = item.model_dump(exclude_none=True)
        inline_id = generate_inline_chart_id(chart_dict, item_id, used_chart_ids)
        source_path = _layout_source_path(path_prefix, item_id)
        chart = normalize_chart(
            inline_id,
            chart_dict,
            query_registry,
            base_dir,
            default_source,
            source_path=source_path,
            cache_root=cache_root,
            sources=sources,
            board_cache=board_cache,
        )
        return LayoutItem(
            type="chart", chart=chart, source_path=source_path, visible=item.visible
        )

    raise CompilationError(f"Invalid layout item type: {type(item)}")


# Layout keys that should not have templates resolved (processed by layout resolution)
def prefixed(path_prefix: AuthoringPathPrefix, segment: str) -> str:
    """Join a segment onto the enclosing board's path, or "" where there is none."""
    if path_prefix is None:
        return ""
    return f"{path_prefix}.{segment}" if path_prefix else segment


def _tab_source_path(path_prefix: AuthoringPathPrefix, idx: int) -> str:
    """A tab's absolute authoring path. See ``_layout_source_path`` for ``path_prefix``."""
    return prefixed(path_prefix, f"tabs.items.{idx}")


def _layout_source_path(path_prefix: AuthoringPathPrefix, item_id: str) -> str:
    """The item's absolute authoring path, or "" when it has none.

    Dotted throughout, indices included — the one spelling shared with
    ``build_source_index`` keys and pydantic ``loc`` tuples, so a diagnostic naming
    this item resolves to a line.

    ``path_prefix`` is the enclosing board's own path, empty at the file root.
    ``None`` means the subtree came from an imported board file: its keys live in
    *that* file's source map, so naming them against this one would point at
    whatever happens to sit at those coordinates here.
    """
    match = re.fullmatch(r"(row|col|grid)(\d+)", item_id)
    if not match:
        return ""
    key = {"row": "rows", "col": "cols", "grid": "grid.items"}[match.group(1)]
    return prefixed(path_prefix, f"{key}.{match.group(2)}")


def _resolve_board_file_import(
    file_path: str,
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    item_id: str,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    parent_variables: dict[str, Any] | None = None,
    parent_level: int = 0,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
) -> LayoutItem:
    """Load and resolve a board file import.

    Board file imports allow including external YAML board files in layouts.
    The imported board inherits variables from the parent context.

    Args:
        file_path: Path to the YAML file resolved relative to base_dir
        charts: Available charts
        query_registry: Query registry
        board_id: Parent board ID
        depth: Nesting depth
        item_id: ID for this item
        base_dir: ProjectDirectory handle for resolving relative file paths
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        chart_registry: Raw chart definitions for nested board normalization
        theme: Vega-Lite theme to inherit
        used_chart_ids: Set of already-used chart IDs for uniqueness
        parent_variables: Variables from parent scope for template resolution
                     If False, skip YAML content resolution so interactive variables
                     remain as templates and are resolved at render time.

    Returns:
        LayoutItem containing the compiled nested board

    Raises:
        CompilationError: If file not found or invalid
    """
    from dbt_charts.core.compile.normalize.dispatch import normalize_board
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    # Resolve the file path (might contain Jinja templates for parameterized imports
    # like `partials/{{ chart_type }}.yml`). This always uses parent_variables so
    # file paths with interactive variable defaults still resolve at compile time.
    parent_variables = parent_variables or {}
    resolved_path = resolve_jinja_template(file_path, parent_variables, strict=False)

    if base_dir is None:
        raise CompilationError(
            f"Cannot resolve board file import '{resolved_path}': "
            "no base directory context (board was compiled without a base directory)"
        )

    try:
        nested_path = base_dir / resolved_path
    except ValueError as e:
        raise CompilationError(str(e)) from e

    if not nested_path.exists():
        raise CompilationError(
            f"Board file not found: '{resolved_path}' (resolved from '{file_path}')"
        )

    # Load and parse the YAML file
    try:
        yaml_content = nested_path.read_text()
        board_data = yaml.load(yaml_content, Loader=UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise CompilationError(
            f"Failed to parse board file '{resolved_path}': {e}"
        ) from e
    except OSError as e:
        raise CompilationError(
            f"Failed to read board file '{resolved_path}': {e}"
        ) from e

    if not isinstance(board_data, dict):
        raise CompilationError(
            f"Board file '{resolved_path}' must contain a YAML dictionary"
        )

    # Convert raw YAML dict to AuthoredBoard; model_validate handles all nested models.
    nested_board = AUTHORED_BOARD_ADAPTER.validate_python(board_data)

    # An imported file never reaches validate_board — it is loaded here, after
    # validation ran on the importing board. Its `theme:`/`extends:` is inert
    # for the same reason every nested board's is (no chain is folded for it),
    # so the same check has to run, or the name is silently dropped.
    from dbt_charts.core.compile.validate.dispatch import validate_theme_names

    theme_errors = validate_theme_names(nested_board)
    if theme_errors:
        raise theme_errors[0]

    # Build parent context — the child board's refs resolve relative to the
    # imported file's own directory.
    parent_context = {
        "parent_id": board_id,
        "base_dir": nested_path.parent,
        "default_source": default_source,
        "cache_root": cache_root,
        "board_cache": board_cache,
        "sources": sources,
        "theme": theme,
        "variables": parent_variables,  # Pass variables to child
    }

    # Compile the nested board
    compiled_nested = normalize_board(
        nested_board,
        board_id=f"{board_id}_{item_id}",
        parent_context=parent_context,
        query_registry=query_registry,
        chart_registry=chart_registry,
        depth=depth + 1,
        base_dir=nested_path.parent,
        parent_level=parent_level,
        # The imported board's keys live in its own file's source map. Naming them
        # against the importing file would point at whatever sits at those
        # coordinates here — so the subtree carries no path, as the import itself
        # never has.
        path_prefix=None,
    )

    return LayoutItem(type="board", board=compiled_nested)


def _resolve_grid_items(
    grid: GridLayout,
    charts: dict[str, Chart],
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    used_chart_ids: set[str] | None = None,
    parent_variables: dict[str, Any] | None = None,
    parent_level: int = 0,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    local_chart_ids: set[str] | None = None,
    path_prefix: AuthoringPathPrefix = "",
) -> list[LayoutItem]:
    """Resolve grid layout items.

    Auto-calculates col/row positions for items without explicit positioning.
    Items flow left-to-right, wrapping to the next row when they exceed
    the column count.

    Args:
        grid: GridLayout definition
        charts: Available charts
        query_registry: Query registry
        board_id: Parent board ID
        depth: Nesting depth
        base_dir: ProjectDirectory handle for resolving external file references
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        chart_registry: Raw chart definitions for nested board normalization
        theme: Vega-Lite theme to inherit to nested boards
        parent_variables: Variables from parent scope for template resolution

    Returns:
        List of LayoutItem with grid positioning
    """
    resolved: list[LayoutItem] = []
    columns = grid.columns

    # Track grid occupancy for auto-positioning
    # For items without explicit position, place left-to-right, top-to-bottom
    current_col = 0
    current_row = 0

    for idx, grid_item in enumerate(grid.items):
        item = grid_item.item
        layout_item = _resolve_single_item(
            item,
            charts,
            query_registry,
            board_id,
            depth,
            f"grid{idx}",
            base_dir,
            default_source,
            board_cache,
            chart_registry,
            theme,
            used_chart_ids,
            parent_variables,
            parent_level=parent_level,
            cache_root=cache_root,
            sources=sources,
            local_chart_ids=local_chart_ids,
            path_prefix=path_prefix,
        )

        # Get span values (default to 1)
        # Support 'width' as alias for 'col_span' and 'height' as alias for 'row_span'
        # This is more intuitive for users (width: 6 means span 6 columns)
        col_span = grid_item.col_span or grid_item.width or 1
        row_span = grid_item.row_span or grid_item.height or 1

        # Use explicit position if provided, otherwise auto-calculate
        if grid_item.col is not None:
            item_col = grid_item.col
        else:
            # Auto-position: check if item fits in current row
            if current_col + col_span > columns:
                # Wrap to next row
                current_col = 0
                current_row += 1
            item_col = current_col
            # Advance position for next item
            current_col += col_span

        item_row = grid_item.row if grid_item.row is not None else current_row

        # Set grid positioning
        layout_item.col = item_col
        layout_item.row = item_row
        layout_item.col_span = col_span
        layout_item.row_span = row_span
        if grid_item.notes and not layout_item.notes:
            layout_item.notes = grid_item.notes
        resolved.append(layout_item)

    return resolved


def _resolve_tab_items(
    tabs: TabLayout,
    query_registry: dict[str, AnyQuery],
    board_id: str,
    depth: int,
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    chart_registry: dict[str, Any] | None = None,
    theme: str | None = None,
    *,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    parent_level: int = 0,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    path_prefix: AuthoringPathPrefix = "",
) -> tuple[list[LayoutItem], list[str]]:
    """Resolve tab layout items.

    Args:
        tabs: TabLayout definition
        query_registry: Query registry
        board_id: Parent board ID
        theme: Vega-Lite theme to inherit to nested boards
        depth: Nesting depth
        base_dir: ProjectDirectory handle for resolving external file references
        default_source: Default source for inline queries
        board_cache: The declaring dashboard's `cache:` layer, inherited
            by every query normalized below it
        chart_registry: Raw chart definitions for nested board normalization

    Returns:
        Tuple of (items, tab_titles)
    """
    from dbt_charts.core.compile.normalize.dispatch import (
        compiled_meta,
        normalize_board,
    )

    resolved: list[LayoutItem] = []
    titles: list[str] = []

    for idx, tab_item in enumerate(tabs.items):
        titles.append(tab_item.title)

        # Tab items can have nested layouts. exclude_unset (not exclude_none)
        # so an explicit `style: {formats: null, ...}` round-trips as an
        # authored null rather than collapsing into "never mentioned" — the
        # same explicit-null-vs-unset distinction merge_patches relies on for
        # every other nested-board path, which only this dict round-trip loses.
        tab_dict = tab_item.model_dump(exclude_unset=True)

        if any(tab_dict.get(k) is not None for k in ["rows", "cols", "grid", "tabs"]):
            # Tab is a nested board. Pop TabItem-only fields not on AuthoredBoard,
            # then model_validate so style errors include the "style." loc prefix.
            tab_dict.pop("icon", None)
            nested_board = AUTHORED_BOARD_ADAPTER.validate_python(tab_dict)
            tab_path = _tab_source_path(path_prefix, idx)
            compiled_nested = normalize_board(
                nested_board,
                board_id=f"{board_id}_tab{idx}",
                parent_context={
                    "parent_id": board_id,
                    "base_dir": base_dir,
                    "default_source": default_source,
                    "cache_root": cache_root,
                    "board_cache": board_cache,
                    "sources": sources,
                    "theme": theme,
                },
                query_registry=query_registry,
                chart_registry=chart_registry,
                depth=depth + 1,
                base_dir=base_dir,
                parent_level=parent_level,
                path_prefix=tab_path or None,
            )
            resolved.append(
                LayoutItem(
                    type="board",
                    board=compiled_nested,
                    source_path=_tab_source_path(path_prefix, idx),
                    notes=tab_item.notes,
                )
            )

        elif tab_item.text:
            # Content-only tab. resolved_style/chart_style_context here are the
            # parent's — a transient placeholder that normalize_board's later
            # _propagate_resolved_style pass overwrites from authored_style,
            # same as every other nested board (see the nested-board branch above).
            content_board = Board(
                id=f"{board_id}_tab{idx}",
                title=tab_item.title,
                notes=tab_item.notes or "",  # type-state: silent_fallback — notes: str
                text=tab_item.text,
                layout=Layout(type=LayoutType.ROWS, items=[]),
                theme=theme,
                authored_style=tab_item.style,
                resolved_style=resolved_style,
                chart_style_context=chart_style_context,
                level=parent_level + 1,
                meta=compiled_meta(),
            )
            resolved.append(
                LayoutItem(
                    type="board",
                    board=content_board,
                    source_path=_tab_source_path(path_prefix, idx),
                    notes=tab_item.notes,
                )
            )

        else:
            # Empty tab. Same transient-placeholder note as the content-only
            # branch above — _propagate_resolved_style resolves the real value.
            empty_board = Board(
                id=f"{board_id}_tab{idx}",
                title=tab_item.title,
                notes=tab_item.notes or "",  # type-state: silent_fallback — notes: str
                layout=Layout(type=LayoutType.ROWS, items=[]),
                theme=theme,
                authored_style=tab_item.style,
                resolved_style=resolved_style,
                chart_style_context=chart_style_context,
                level=parent_level + 1,
                meta=compiled_meta(),
            )
            resolved.append(
                LayoutItem(
                    type="board",
                    board=empty_board,
                    source_path=_tab_source_path(path_prefix, idx),
                    notes=tab_item.notes,
                )
            )

    return resolved, titles
