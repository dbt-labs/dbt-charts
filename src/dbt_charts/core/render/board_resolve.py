"""Build a data-resolved ResolvedBoard for the render path.

Lives in render/ (imports execute + compile.resolve.resolve) — correct per
the compile→execute→render import boundary.  The pure compile-layer
resolve_board() is NOT imported here; board-baking helpers are called directly.

This is the single board-resolution path: it resolves each chart once via the
render path, absorbs layout sizing (calculate_data_aware_layout), and provides the
static resolvers (build_resolved_board_static, build_resolved_nested_board_static).
"""

from __future__ import annotations

from types import EllipsisType
from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
from dbt_charts.core.compile.models.board.resolved import (
    ChartIdentity,
    ChartResolveFailure,
    ResolvedBoard,
    ResolvedLayout,
    ResolvedLayoutItem,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.render.chart_diagnostics import stamp_chart_diagnostic
from dbt_charts.core.render.prose import plan_board_prose

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Layout, LayoutItem
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.execute.executor import Executor
    from dbt_charts.core.render.layout_sizing import RenderCache, ResolvedChartVariants


# ---------------------------------------------------------------------------
# Static (data-free) resolvers — replacements for resolve_board() and
# resolve_nested_board() from the deleted compile/resolve/resolve_board.py
# ---------------------------------------------------------------------------


def _resolve_chart_static(
    chart: Chart,
    chart_style_context: ChartStyleContext,
) -> ResolvedChart:
    """Resolve a chart for static (data-free) display."""
    return resolve(chart, [], chart_style_context=chart_style_context)


def _resolve_chart_data_aware(
    chart: Chart,
    chart_style_context: ChartStyleContext,
    executor: Executor,
    variables: VariableValues,
    width: float,
) -> ResolvedChart:
    """Resolve a chart against its real, already-cached query rows.

    Used when a tabs layout's sizing pass never walked this item — an
    inactive tab's chart, which skips the render-first vl-convert height
    measurement the active tab gets (that is the expensive part; see
    ``layout_sizing._require_resolved``). ``ResolvedBoard.charts`` must
    still carry the same query rows ``record_board`` records — queries are
    board-global and already executed by the time render reaches here — or
    a data-free resolve would bake presentation facts (e.g. a pie's
    ``presentation_fingerprint``) that artifact replay then contradicts.

    Routes through ``resolve_chart_with_runtime_inputs`` — the same runtime
    entry point ``_require_resolved`` calls for the active tab — so this
    placement carries its own slot width, layer-overlay datasets, and
    variable interpolation instead of resolving against an empty variable
    mapping. This is parity minus the vl-convert measurement (never needed
    for a tab nobody is looking at) and minus the chart-wide auto-link/table
    FK-link/pie-title-font inputs, which are cheap to omit here (an
    inactive placement, not the chart's first) and expensive to recompute
    without also duplicating ``_require_resolved``'s caching.

    ``width`` is ``item.width`` — but the sizing pass never walked this
    item, so it is still the ``LayoutItem`` default of ``0.0``, not a real
    placement width. ``resolve_chart_with_runtime_inputs`` treats that
    literal ``0.0`` as "unmeasured" and passes ``None`` on to ``resolve()``
    in its place, so pie's family-aware default applies (authored
    ``width:`` first, then the family's ``preferred_width``) instead of
    dividing by a literal zero.

    Raises whatever ``resolve()`` raises — the caller is the one tracking
    ``resolve_errors``, matching ``_require_resolved``'s single catch. A
    failed data fetch is one of those raises too: it propagates here rather
    than degrading to empty data, so the caller's catch records it instead of
    resolving the chart as if its query had never failed.
    """
    query_name = chart.query_name
    data = executor.execute_query(query_name, variables) if query_name else []
    return resolve_chart_with_runtime_inputs(
        chart, data, chart_style_context, width, executor, variables
    )


def _resolve_layout_item_static(
    item: LayoutItem, chart_style_context: ChartStyleContext
) -> ResolvedLayoutItem:
    """Build a ResolvedLayoutItem for static (data-free) display."""
    return ResolvedLayoutItem(
        type=item.type,
        chart=(
            _resolve_chart_static(item.chart, chart_style_context)
            if item.chart
            else None
        ),
        board=build_resolved_nested_board_static(item.board) if item.board else None,
        source_path=item.source_path,
        x=item.x or 0.0,
        y=item.y or 0.0,
        width=item.width or 0.0,
        height=item.height or 0.0,
        details_variable=item.details_variable,
        details_summary=item.details_summary,
        details_expanded_summary=item.details_expanded_summary,
        notes=item.notes,
        visible=item.visible,
    )


def _build_static_resolved_layout(
    layout: Layout,
    gap: float = 0.0,
    *,
    chart_style_context: ChartStyleContext,
) -> ResolvedLayout:
    """Build ResolvedLayout for static (data-free) display.

    Produces ResolvedChart so nested-board rendering
    works with render_chart_item.  Called by build_resolved_board_static
    and build_resolved_nested_board_static.
    """
    items = tuple(
        _resolve_layout_item_static(item, chart_style_context) for item in layout.items
    )
    return ResolvedLayout(
        type=layout.type,
        items=items,
        width=layout.width,
        height=layout.height,
        content_width=layout.content_width,
        content_height=layout.content_height,
        columns=layout.columns,
        gap=gap,
        tab_titles=tuple(layout.tab_titles) if layout.tab_titles else (),
        tab_slugs=tuple(layout.tab_slugs) if layout.tab_slugs else (),
        tab_variable=layout.tab_variable,
        default_tab=layout.default_tab if layout.default_tab is not None else 0,
        tab_position=layout.tab_position,
    )


def build_resolved_nested_board_static(board: Board) -> ResolvedBoard:
    """Resolve a nested board using only static/compile-time information.

    Board-level config (page_padding, card_padding, card_gap) are None —
    those apply only to the root renderable board.  Replaces resolve_nested_board()
    and _build_resolved_nested_board() from compile/resolve/resolve_board.py.

    Args:
        board: Nested board from the compile-time layout tree.

    Returns:
        ResolvedBoard with None board constants and charts (empty data).
    """
    return ResolvedBoard(
        id=board.id,
        title=board.title,
        notes=board.notes,
        tags=tuple(board.tags),
        text=board.text,
        html_policy=board.html_policy,
        level=board.level,
        style=board.resolved_style,
        page_padding=None,
        card_padding=None,
        card_gap=None,
        width=board.layout.width or 0.0,
        height=board.layout.height or 0.0,
        layout=_build_static_resolved_layout(
            board.layout, chart_style_context=board.chart_style_context
        ),
        charts={
            name: _resolve_chart_static(chart, board.chart_style_context)
            for name, chart in board.charts.items()
        },
        variables=board.variables,
        queries=board.queries,
        variable_defaults=board.variable_defaults,
        prose_plan=board.prose_plan,
    )


def build_resolved_board_static(board: Board) -> ResolvedBoard:
    """Resolve a root board using only static/compile-time information.

    Replaces resolve_board() from compile/resolve/resolve_board.py for the
    V1 render path and any data-free callers.

    Reads board.resolved_style as-is.  Any post-compile theme mutation must
    have gone through Board.set_theme, which re-cascades resolved_style.
    No database access.  No chart pre-rendering.

    Args:
        board: Compiled board from the YAML → compile pipeline.

    Returns:
        ResolvedBoard with baked board constants, charts (empty data),
        and static layout estimates.
    """
    from dbt_charts.core.compile.sizing import board_container_width, get_board_gap

    frame = board.resolved_style.frame
    page_padding = float(frame.margin)
    card_padding = float(frame.card_padding)
    card_gap = float(frame.card_gap) if board.card_gap else 0.0
    layout_gap = get_board_gap(board)
    width = float(board.layout.width or board_container_width(board))
    height = float(board.layout.height)
    plan_board_prose(board, board.variable_defaults, width - 2 * page_padding)

    return ResolvedBoard(
        id=board.id,
        title=board.title,
        notes=board.notes,
        tags=tuple(board.tags),
        text=board.text,
        html_policy=board.html_policy,
        level=board.level,
        style=board.resolved_style,
        page_padding=page_padding,
        card_padding=card_padding,
        card_gap=card_gap,
        width=width,
        height=height,
        layout=_build_static_resolved_layout(
            board.layout, layout_gap, chart_style_context=board.chart_style_context
        ),
        charts={
            name: _resolve_chart_static(chart, board.chart_style_context)
            for name, chart in board.charts.items()
        },
        variables=board.variables,
        queries=board.queries,
        variable_defaults=board.variable_defaults,
        prose_plan=board.prose_plan,
    )


# ---------------------------------------------------------------------------
# Fused resolver: data-aware sizing + chart resolution in one pass
# ---------------------------------------------------------------------------


def build_resolved_board(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    render_first: bool = True,
    *,
    resolve_errors: dict[str, ChartResolveFailure] | EllipsisType = ...,
    authored_slot_heights: dict[str, float] | EllipsisType = ...,
) -> tuple[ResolvedBoard, RenderCache]:
    """Build a data-resolved ResolvedBoard using the chart resolver.

    Single-resolution pass: resolves each chart once, then feeds those resolved
    charts to the sizing pass so heights are measured from the same resolution
    rather than a second one.

    Fetches data per chart via executor, resolves each chart through
    path, and builds a ResolvedBoard with charts in both the charts dict
    and the layout tree.  Board-baking (page/card padding, gap,
    static dimensions) mirrors build_resolved_board_static exactly.

    Args:
        board: Compiled board from the YAML → compile pipeline.
        executor: Executor for query execution (queries are cached; no re-exec).
        variables: Variable values resolved against board.variable_defaults.
        render_first: When True, Vega-Lite charts are rendered during sizing to
            get actual heights.  Set False for non-SVG formats.
        resolve_errors: When given, populated in place with chart_id -> the
            failure for every chart whose resolution raised. A caller that
            publishes the result (rather than rendering it) must check this:
            those charts are absent from the returned board's ``charts``, and
            nothing else reports them.
        authored_slot_heights: When given, populated in place with
            chart_id -> the real px slot height assigned to every chart under
            an authored ancestor. See
            ``layout_sizing._snapshot_authored_slot_heights``.

    Returns:
        (ResolvedBoard with data-resolved ResolvedChart members,
         RenderCache mapping (chart_id, width, height) to (svg, height) for
         Vega charts rendered during sizing)
    """
    from dbt_charts.core.compile.sizing import board_container_width, get_board_gap
    from dbt_charts.core.execute.category_colors import (
        plan_board_category_colors,
        with_category_colors,
    )
    from dbt_charts.core.render.layout_sizing import calculate_data_aware_layout

    # Board-wide category colors, before anything resolves: a value's swatch is
    # decided once for the whole board, so every chart must see the same plan.
    board = with_category_colors(
        board, plan_board_category_colors(board, executor, variables)
    )

    # Each layout placement is resolved at its own final width. One authored
    # chart may appear in several slots whose finalized presentation differs.
    chart_variants: ResolvedChartVariants = {}
    # Local, never a parameter default: chart ids come from tree position, so
    # two unrelated boards collide routinely, and a map shared across calls
    # would paint one board's healthy chart with another's diagnostic.
    chart_failures: dict[str, ChartResolveFailure] = (
        resolve_errors if resolve_errors is not ... else {}
    )

    # Sizing pass: mutates board.layout dimensions in place; returns render_cache
    # populated with pre-rendered SVGs for charts sized via render-first.
    board, render_cache = calculate_data_aware_layout(
        board,
        executor,
        variables,
        render_first,
        resolved_variants=chart_variants,
        resolve_errors=chart_failures,
        authored_slot_heights=authored_slot_heights,
    )

    frame = board.resolved_style.frame
    page_padding = float(frame.margin)
    card_padding = float(frame.card_padding)
    card_gap = float(frame.card_gap) if board.card_gap else 0.0
    layout_gap = get_board_gap(board)
    width = float(board.layout.width or board_container_width(board))
    height = float(board.layout.height)

    resolved_layout = _build_resolved_layout(
        board.layout,
        {},
        layout_gap,
        board_style=board.resolved_style,
        chart_style_context=board.chart_style_context,
        variable_values=variables,
        executor=executor,
        chart_variants=chart_variants,
        resolve_errors=chart_failures,
    )
    charts = _collect_resolved_layout_charts(resolved_layout)

    return (
        ResolvedBoard(
            id=board.id,
            title=board.title,
            notes=board.notes,
            tags=tuple(board.tags),
            text=board.text,
            html_policy=board.html_policy,
            level=board.level,
            style=board.resolved_style,
            page_padding=page_padding,
            card_padding=card_padding,
            card_gap=card_gap,
            width=width,
            height=height,
            layout=resolved_layout,
            charts=charts,
            variables=board.variables,
            queries=board.queries,
            variable_defaults=board.variable_defaults,
            prose_plan=board.prose_plan,
        ),
        render_cache,
    )


def _build_resolved_layout(
    layout: Layout,
    charts: dict[str, ResolvedChart],
    gap: float = 0.0,
    *,
    board_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    variable_values: VariableValues,
    executor: Executor,
    chart_variants: ResolvedChartVariants | EllipsisType = ...,
    resolve_errors: dict[str, ChartResolveFailure],
) -> ResolvedLayout:
    """Wrap the compile-time Layout in a frozen ResolvedLayout with chart refs.

    Walks every item regardless of which tab is active: ``ResolvedBoard``
    publishes one catalog for the whole board, so every chart needs a real,
    data-resolved entry (see ``_resolve_layout_item``'s ``chart_variants``
    fallback) — not just the active tab's, which is all the sizing pass
    (``calculate_data_aware_layout``) resolves.
    """
    items = tuple(
        _resolve_layout_item(
            item,
            charts,
            board_style=board_style,
            chart_style_context=chart_style_context,
            variable_values=variable_values,
            executor=executor,
            chart_variants=chart_variants,
            resolve_errors=resolve_errors,
        )
        for item in (list(layout.items) if layout.items else [])
    )
    return ResolvedLayout(
        type=layout.type,
        items=items,
        width=layout.width,
        height=layout.height,
        content_width=layout.content_width,
        content_height=layout.content_height,
        columns=layout.columns,
        gap=gap,
        tab_titles=tuple(layout.tab_titles) if layout.tab_titles else (),
        tab_slugs=tuple(layout.tab_slugs) if layout.tab_slugs else (),
        tab_variable=layout.tab_variable,
        default_tab=layout.default_tab if layout.default_tab is not None else 0,
        tab_position=layout.tab_position,
    )


def _collect_resolved_layout_charts(
    layout: ResolvedLayout,
) -> dict[str, ResolvedChart]:
    """Build the compatibility chart catalog from actual final placements."""
    charts: dict[str, ResolvedChart] = {}
    for item in layout.items:
        if item.chart is not None:
            charts.setdefault(item.chart.id, item.chart)
        if item.board is not None:
            for chart_id, chart in item.board.charts.items():
                charts.setdefault(chart_id, chart)
    return charts


def _resolve_layout_item(
    item: LayoutItem,
    charts: dict[str, ResolvedChart],
    *,
    board_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    variable_values: VariableValues,
    executor: Executor,
    chart_variants: ResolvedChartVariants | EllipsisType = ...,
    resolve_errors: dict[str, ChartResolveFailure],
) -> ResolvedLayoutItem:
    """Build a ResolvedLayoutItem for a single layout item."""
    from dbt_charts.core.render.layout_sizing import resolved_chart_variant_key

    # A failed chart is checked for ahead of either lookup, so a resolve failure
    # surfaces as a chart_error rather than being re-resolved by the fallback or
    # returned as None by the catalog branch, which would drop the tile with no
    # trace.
    resolved_chart: ResolvedChart | None = None
    chart_error: ChartResolveFailure | None = None
    if item.chart is not None:
        chart_error = resolve_errors.get(item.chart.id)
        if chart_error is None:
            if chart_variants is ...:
                resolved_chart = charts.get(item.chart.id)
            else:
                variant_key = resolved_chart_variant_key(
                    item.chart.id, item.width, board_style
                )
                if variant_key in chart_variants:
                    resolved_chart = chart_variants[variant_key]
                else:
                    # An inactive tab's chart: the sizing pass never walked
                    # here (it only measures the active tab — that render-first
                    # vl-convert pass is the expensive part), so resolve it
                    # here instead, against real data but without measuring it.
                    try:
                        resolved_chart = _resolve_chart_data_aware(
                            item.chart,
                            chart_style_context,
                            executor,
                            variable_values,
                            item.width,
                        )
                    except DbtChartsError as exc:
                        chart_error = ChartResolveFailure(
                            diagnostic=stamp_chart_diagnostic(
                                exc, item.chart.id, item.chart.source_path
                            ),
                            identity=ChartIdentity.from_normalized(item.chart),
                        )
                        resolve_errors[item.chart.id] = chart_error
    resolved_board = (
        _resolve_nested(
            item.board,
            charts,
            chart_variants,
            variable_values=variable_values,
            executor=executor,
            resolve_errors=resolve_errors,
        )
        if item.board
        else None
    )
    return ResolvedLayoutItem(
        type=item.type,
        chart=resolved_chart,
        chart_error=chart_error,
        board=resolved_board,
        source_path=item.source_path,
        x=item.x,
        y=item.y,
        width=item.width,
        height=item.height,
        details_variable=item.details_variable,
        details_summary=item.details_summary,
        details_expanded_summary=item.details_expanded_summary,
        notes=item.notes,
        visible=item.visible,
        title_shift=item.title_shift,
    )


def _resolve_nested(
    board: Board,
    charts: dict[str, ResolvedChart],
    chart_variants: ResolvedChartVariants | EllipsisType = ...,
    *,
    variable_values: VariableValues,
    executor: Executor,
    resolve_errors: dict[str, ChartResolveFailure],
) -> ResolvedBoard:
    """Resolve a nested board using the pre-resolved charts from the root board.

    Charts in nested boards are always a subset of the root board's charts — the
    compiler populates nested board.charts by reference to the root chart pool.
    Re-using the already-resolved charts avoids a V1 re-resolve that would
    produce ResolvedChart (flat V1) objects, which crash render_resolved_chart.
    """
    from dbt_charts.core.compile.sizing import get_board_gap

    layout_gap = get_board_gap(board)
    resolved_layout = _build_resolved_layout(
        board.layout,
        charts,
        layout_gap,
        board_style=board.resolved_style,
        chart_style_context=board.chart_style_context,
        variable_values=variable_values,
        executor=executor,
        chart_variants=chart_variants,
        resolve_errors=resolve_errors,
    )
    nested_charts = _collect_resolved_layout_charts(resolved_layout)
    return ResolvedBoard(
        id=board.id,
        title=board.title,
        notes=board.notes,
        tags=tuple(board.tags),
        text=board.text,
        html_policy=board.html_policy,
        level=board.level,
        style=board.resolved_style,
        page_padding=None,
        card_padding=None,
        card_gap=None,
        width=board.layout.width or 0.0,
        height=board.layout.height or 0.0,
        layout=resolved_layout,
        charts=nested_charts,
        variables=board.variables,
        queries=board.queries,
        variable_defaults=board.variable_defaults,
        prose_plan=board.prose_plan,
    )
