"""Data-aware layout sizing for the render pipeline.

Stage: RENDER
Purpose: Calculate layout dimensions using actual query data and rendered chart heights.

This module owns all data-aware sizing logic that requires an executor or vl-convert:
- Render-first Vega sizing (render charts to get true heights, cache SVGs)
- Table sizing from actual row counts
- Cols height alignment (re-render shorter Vega items at max height)

It injects a HeightProvider callback into the pure layout algorithms in
render/sizing.py.

Entry Point:
    - calculate_data_aware_layout(board, executor, variables, render_first)
      Called from renderer.py after query pre-execution.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from types import EllipsisType
from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    LayoutItem,
    VariableValues,
)
from dbt_charts.core.compile.models.board.resolved import (
    ChartIdentity,
    ChartResolveFailure,
)
from dbt_charts.core.compile.models.chart.authored import ChartSupportTable
from dbt_charts.core.compile.models.chart.normalized import (
    NON_ASPECT_RATIO_TYPES,
    Chart,
    TableChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedChart,
    ResolvedPieChart,
    ResolvedTableChart,
)
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.sizing import board_container_width, get_board_gap
from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.chart_resolution import resolve_chart_with_runtime_inputs
from dbt_charts.core.render.chart.spec_builders import additive_padding
from dbt_charts.core.render.chart_diagnostics import stamp_chart_diagnostic
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.sizing import (
    HeightProvider,
    active_layout_items,
    calculate_layout_height,
    calculate_layout_items,
    get_chart_content_height,
    get_item_content_height,
    get_title_height,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
    from dbt_charts.core.compile.models.style.theme import PaddingStyle
    from dbt_charts.core.compile.resolve import AutomaticLinkCandidate
    from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
    from dbt_charts.core.execute.executor import Executor

_log = logging.getLogger(__name__)
_FIXED_ASPECT_PIE_TYPES = frozenset({"pie", "arc"})

# Compiled patterns for VL SVG title-gap measurement used in title-offset calibration.
# Role-title group's inner <g> y (relative to the VL outer container, so negative when
# the title is above the plot):  <g class="mark-group role-title"><g transform="translate(x,y)">
_ROLE_TITLE_GROUP_Y_RE = re.compile(
    r'role-title[^<]*<g[^>]*transform="translate\([^,]+,(-?[0-9.]+)\)"'
)
# Title text element baseline y (relative to role-title group): <text ... transform="translate(x, y)">
_TITLE_TEXT_Y_RE = re.compile(r'<text[^>]*transform="translate\([^,]+,(-?[0-9.]+)\)"')

# Sizing-pass render cache: (chart_id, width, height) → (svg_string, actual_height).
# Populated during the sizing pass; consumed by the render pass to avoid re-calling
# vl-convert for charts already rendered at the correct dimensions.
RenderCache = dict[tuple[str, float, float], tuple[str, float]]
ResolvedChartVariantKey = tuple[str, float, int]
ResolvedChartVariants = dict[ResolvedChartVariantKey, ResolvedChart]
ResolvedChartCanonicalKey = tuple[str, int]


def _support_table_of(chart: Chart | ResolvedChart) -> ChartSupportTable | None:
    """The chart's support_table attachment, or None when it declares no slot.

    Layout walks heterogeneous chart types and only the cartesian families
    declare this slot at all, so a missing attribute is the ordinary case here
    rather than bad input.
    """
    return getattr(
        chart, "support_table", None
    )  # type-state: silent_fallback — optional slot


def resolved_chart_variant_key(
    chart_id: str, width: float, resolved_style: ResolvedStyle
) -> ResolvedChartVariantKey:
    """Identify one final chart placement by chart, slot width, and board scope.

    The style enters by identity, not by value: ``id(resolved_style)`` is what
    separates one board's placements from another's, here and in the
    ``(chart_id, id(resolved_style))`` canonical key below. Two boards whose
    styles are equal must therefore still hold two objects — hand them one and
    a chart resolved for the first board is served to the second, at the first
    board's width (the canonical key carries no width of its own).
    """
    return chart_id, width, id(resolved_style)


def _canonical_pie_title_font(
    chart: ResolvedChart | EllipsisType,
) -> ResolvedFontStyle | EllipsisType:
    if not isinstance(chart, ResolvedPieChart):
        return ...
    title_font = chart.style.title_font
    assert title_font is not None
    return title_font


@dataclass
class SizingRenderCtx:
    """Render context threaded through data-aware sizing.

    When present, the data-aware height provider renders Vega-Lite charts via
    vl-convert instead of using aspect-ratio estimates. Results are cached
    in render_cache for reuse by the render pass.
    """

    resolved_style: ResolvedStyle
    chart_style_context: ChartStyleContext
    render_cache: RenderCache = field(default_factory=dict)
    # Pre-resolved charts: chart_id → ResolvedChart.
    # When populated, the sizing pass renders these directly via
    # render_resolved_chart instead of resolving again through _require_resolved.
    pre_resolved: dict[str, ResolvedChart] = field(default_factory=dict)
    # First finalized placement per chart. Repeated pies reuse its title
    # typography while resolving width-dependent presentation independently.
    canonical_resolved: dict[ResolvedChartCanonicalKey, ResolvedChart] = field(
        default_factory=dict
    )
    resolved_variants: ResolvedChartVariants = field(default_factory=dict)
    # Charts whose resolve raised, keyed by chart id. An entry here means the
    # chart has no resolved variant at any width and must be drawn as an error
    # tile instead.
    resolve_errors: dict[str, ChartResolveFailure] = field(default_factory=dict)
    executor: Executor | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    # Maps chart_id → corrected spec.width for support_table charts.
    # autosize:pad makes outer SVG wider than spec.width by a constant overhead;
    # the render-first pass measures this and stores the shrunk spec.width here so
    # _align_cols_heights can reuse it instead of item.width (which would re-overflow).
    support_table_corrected_widths: dict[str, float] = field(default_factory=dict)
    # Maps (chart_id, slot_width) → natural rendered height.
    # Populated during the sizing pass; used to deduplicate renders of the same
    # chart at the same width and to drive the close-enough skip in _align_cols_heights.
    natural_heights: dict[tuple[str, float], float] = field(default_factory=dict)


def _style_contexts_by_resolved_style_id(
    board: Board,
) -> dict[int, ChartStyleContext]:
    """Map every board's ``id(resolved_style)`` to its own ``chart_style_context``.

    ``resolved_style``/``chart_style_context`` are always produced together by
    the same cascade pass (``compile_board_resolved_style``) and stored on the
    same ``Board`` — this walks the layout tree once so the height provider can
    look up the chart_style_context matching whichever nested board's
    resolved_style it was handed, instead of always reading the root's.
    """
    contexts = {id(board.resolved_style): board.chart_style_context}
    for item in board.layout.items:
        if item.board is not None:
            contexts.update(_style_contexts_by_resolved_style_id(item.board))
    return contexts


def _nested_render_ctx(
    render_ctx: SizingRenderCtx,
    nested_board: Board,
) -> SizingRenderCtx:
    """Build a SizingRenderCtx for a nested board, inheriting the shared cache."""

    return SizingRenderCtx(
        render_cache=render_ctx.render_cache,
        pre_resolved=render_ctx.pre_resolved,
        canonical_resolved=render_ctx.canonical_resolved,
        resolved_variants=render_ctx.resolved_variants,
        resolve_errors=render_ctx.resolve_errors,
        natural_heights=render_ctx.natural_heights,
        resolved_style=nested_board.resolved_style,
        chart_style_context=nested_board.chart_style_context,
        executor=render_ctx.executor,
        variables=render_ctx.variables,
        support_table_corrected_widths=render_ctx.support_table_corrected_widths,
    )


def _require_resolved(
    render_ctx: SizingRenderCtx,
    chart: Chart,
    executor: Executor,
    variables: dict[str, Any],
    width: float,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
) -> ResolvedChart | None:
    """Return the resolved chart for this exact placement, or None if it failed.

    The first placement resolves chart-wide inputs (text templates, data fetch,
    style cascade, auto-link, FK links, title typography, layout_padding). Each
    distinct pie width gets its own finalized presentation policy while retaining
    that chart-wide title typography.

    A ``DbtChartsError`` from resolution is a per-chart failure, not a board one:
    it is recorded on ``render_ctx.resolve_errors`` and reported as ``None``.
    This is the single catch for the whole sizing pass — every call site degrades
    on ``None`` rather than naming its own tuple of exception types, which is how
    the per-site guards this replaced had drifted out of sync with each other.

    ``width`` is the actual layout pixel width of the card and is baked into
    placement-dependent resolved state.

    ``resolved_style``/``chart_style_context`` must be the chart's OWN board's
    (the nested board's, if the chart lives inside one) — not the root board's.
    Mark/series colors and layout padding are baked in at resolve time via
    ``chart_style_context``/``build_chart_style_context``, so passing the
    wrong context here bakes the wrong theme's palette into the chart
    permanently.
    """
    chart_id = chart.id
    # Keyed by chart id alone, unlike the variant cache: every resolve-time
    # validation reads the chart's data, none reads its width or style, so a
    # failure at one placement is a failure at all of them. If a check ever
    # becomes width- or style-conditional this is wrong — the first probe to
    # fail would poison placements that would have rendered fine.
    #
    # spark_bar's render-time numeric check (below, in the "spark_bar" branch)
    # writes into this same dict from outside this function and used to be
    # exactly that violation: it read the cascaded style.spark_bar.max_bars
    # to decide which rows to validate, so two placements of the same chart
    # under different max_bars could disagree, and whichever resolved first
    # would poison the other. It now validates the full query result — a
    # property of the data, never of width or style — so the invariant this
    # comment describes holds for it too.
    if chart_id in render_ctx.resolve_errors:
        return None
    variant_key = resolved_chart_variant_key(chart_id, width, resolved_style)
    canonical_key = chart_id, id(resolved_style)
    if variant_key in render_ctx.resolved_variants:
        return render_ctx.resolved_variants[variant_key]
    if chart_id in render_ctx.pre_resolved:
        resolved = render_ctx.pre_resolved[chart_id]
        render_ctx.resolved_variants[variant_key] = resolved
        return resolved
    canonical = render_ctx.canonical_resolved.get(canonical_key)
    if canonical is not None and not isinstance(canonical, ResolvedPieChart):
        render_ctx.resolved_variants[variant_key] = canonical
        return canonical

    from dbt_charts.core.compile.resolve import (  # noqa: PLC0415
        auto_link_excludes_x,
        should_fetch_table_fk_links,
        should_synthesize_auto_link,
    )
    from dbt_charts.core.render.chart.auto_link import (  # noqa: PLC0415
        get_auto_link_context,
        synthesize_auto_link,
    )

    try:
        qn = chart.query_name
        data = executor.execute_query(qn, variables) if qn else []

        auto_link_enabled = get_auto_link_context()
        automatic_link_candidate: AutomaticLinkCandidate = None
        if should_synthesize_auto_link(chart, data, auto_link_enabled):
            synthesized = synthesize_auto_link(
                chart,
                executor,
                exclude_x=auto_link_excludes_x(chart, data),
            )
            if synthesized:
                automatic_link_candidate = synthesized

        table_column_links: dict[str, str] = {}
        table_column_rows: list[dict[str, str]] = []
        if should_fetch_table_fk_links(chart, auto_link_enabled):
            assert isinstance(chart, TableChart)
            from dbt_charts.core.render.chart.auto_link import (  # noqa: PLC0415
                fetch_column_rows_for_link,
                fk_column_links_from_executor,
            )

            chart_query = chart.query
            assert chart_query is not None
            table_column_links = fk_column_links_from_executor(
                chart_query,
                executor.adapter_registry,
            )
            if table_column_links:
                table_column_rows = fetch_column_rows_for_link(
                    executor.adapter_registry,
                    chart_query,
                    board=executor.board,
                )

        resolved = resolve_chart_with_runtime_inputs(
            chart,
            data,
            chart_style_context,
            width,
            executor,
            variables,
            automatic_link_candidate=automatic_link_candidate,
            table_column_links=table_column_links,
            table_column_rows=table_column_rows,
            pie_title_font=_canonical_pie_title_font(
                canonical if canonical is not None else ...
            ),
        )
    except DbtChartsError as exc:
        _log.warning("Chart %r failed to resolve: %s", chart_id, exc)
        render_ctx.resolve_errors[chart_id] = ChartResolveFailure(
            diagnostic=stamp_chart_diagnostic(exc, chart_id, chart.source_path),
            identity=ChartIdentity.from_normalized(chart),
        )
        return None

    render_ctx.resolved_variants[variant_key] = resolved
    render_ctx.canonical_resolved.setdefault(canonical_key, resolved)
    return resolved


# Grow cap for the sizer-side branch below is the same constant the renderer
# uses in its corresponding short-circuit — single-source from
# ``dbt_charts.core.render.chart.table._PAGINATION_GROW_CAP`` so the two sides
# stay in lockstep (mismatch would reintroduce broken-chrome-over-hidden-rows).
# Same deferred-import pattern as ``_PAGINATION_CONTROL_HEIGHT`` below to keep
# this module free of an unconditional dependency on the chart-render layer.


def _get_table_height_from_data(
    chart: TableChart,
    resolved: ResolvedTableChart,
    executor: Executor,
    variables: dict[str, Any] | None,
    card_padding: float,
    width: float | None = None,
) -> float:
    """Calculate table height from actual row count.

    ``resolved`` is this exact chart placement's already-resolved table (the
    same value the renderer uses), so row heights, padding, header dimensions,
    and pagination all come from its ``style`` — no separate cascade re-run.

    ``width`` drives the title font size and must match what the renderer will
    see at layout time. When ``None``, uses the resolved table-family width
    preference so both stay in lockstep.

    If the query fails, sizes for an error message display.
    """
    from dbt_charts.core.render.chart.table import (
        compute_table_title_block_layout,
        pivot_table_data,
    )
    from dbt_charts.core.render.chart.table_support import reserve_header_band

    tc = resolved.style.table  # TableChartStyle — board-merged, all with defaults
    title_font = resolved.style.title_font

    effective_width = width if width is not None else tc.preferred_width

    row_height = int(tc.row.height)
    # Hidden header contributes no vertical extent; a visible one may wrap to a
    # second line, which reserve_header_band accounts for (the renderer's exact
    # line count needs column widths this stage does not have).
    # Cascade-guaranteed, same as the renderer asserts at table.py.
    assert tc.font.size is not None, (
        "TableChartStyle.font.size must be set after cascade"
    )
    header_height = reserve_header_band(tc, int(tc.font.size))
    padding_y = int(tc.outer_padding)
    title_height = compute_table_title_block_layout(
        chart_title=chart.title,
        chart_subtitle=chart.subtitle,
        table_width=effective_width,
        tc=tc,
        padding=padding_y,
        title_style=resolved.style.title,
        card_padding=card_padding,
        title_font=title_font,
    ).height
    bottom_padding = int(tc.bottom_padding)

    # Chart-local pagination is pre-merged into resolved.style.pagination at
    # resolve time — read the resolved page_rows off the baked value.
    pagination = resolved.style.pagination
    resolved_page_rows = (
        pagination.page_rows if pagination is not None and pagination.enabled else None
    )

    # Header-body gap also vanishes when header is hidden — the renderer
    # uses the same zero-out so sizer + renderer agree on total height.
    header_body_gap = int(row_height * 0.25) if tc.header.visible else 0

    try:
        data = executor.execute_chart(chart, variables)
        # Match the renderer: a pivot collapses long-form rows into one wide row
        # per row-dim key. Counting raw rows here would over-reserve height by
        # the pivot's fan-out factor (e.g. a 32x32 board = 1024 rows → 32 after
        # pivot). Call pivot_table_data exactly as the renderer does — it owns
        # the not-a-pivot pass-through, so the two can't disagree about which
        # shapes reshape. row_role_spec must be threaded through too, or a query
        # whose total partition varies its rows-dim label is counted as N rows
        # where the renderer draws 1.
        wide_rows, pivot_levels, __ = pivot_table_data(
            data,
            rows=chart.rows or [],
            columns=chart.columns,
            values=chart.values,
            row_role_spec=tc.row.role,
        )
        row_count = len(wide_rows)
        # A multi-dim or multi-measure pivot draws one group-header row per
        # descriptor level ABOVE the leaf header, each the height of the leaf
        # header row — the renderer's same formula. Reserving only the leaf band
        # leaves the slot short and the renderer paginates rows away.
        # A hidden header reserves 0 and stays 0.
        if pivot_levels is not None:
            header_height *= 1 + len(pivot_levels)
    except (ExecutionError, ChartDataError) as exc:
        # Migrate-or-fail: a failed query (ExecutionError) or a bad pivot/data
        # contract (ChartDataError — e.g. a typo'd pivot field, absent role
        # column) warrants a static height fallback here. render then draws the
        # per-chart error card for the SAME ChartDataError, so suppressing it at
        # sizing time reserves a sane slot instead of crashing the whole board's
        # layout before that card can render. ValueError/OSError/RuntimeError/
        # LookupError are bug-class — let them surface loudly, not masked.
        _log.warning("Table height estimation failed for chart %r: %s", chart.id, exc)
        return (
            title_height
            + header_height
            + header_body_gap
            + row_height
            + padding_y
            + bottom_padding
        )

    # Grow-by-2 rule for unconstrained-layout table sizing: don't paginate
    # small overflows. If total_rows is within ``_PAGINATION_GROW_CAP`` rows
    # of the resolved page_rows, report height for every row (no chrome).
    # Otherwise cap at page_rows and reserve pagination chrome. The cap
    # keeps a 100-row table from asking for 100 rows worth of slot —
    # pagination still fires when the overflow is genuinely large. The
    # narrowing pattern keeps pyright happy: inside the branch
    # ``resolved_page_rows`` is known non-None, so the assignment to the
    # int-typed ``row_count`` is type-safe without a suppression.
    from dbt_charts.core.render.chart.table import _PAGINATION_GROW_CAP

    if (
        resolved_page_rows is not None
        and row_count > resolved_page_rows + _PAGINATION_GROW_CAP
    ):
        multi_page = True
        # Ceil division: the real page count this row_count/resolved_page_rows
        # split will produce, before row_count is overwritten below. Needed
        # only to decide whether the static-export cap note reservation
        # below applies -- not otherwise used for sizing.
        estimated_total_pages = -(-row_count // resolved_page_rows)
        row_count = resolved_page_rows
    else:
        multi_page = False
        estimated_total_pages = 1
    height = (
        title_height
        + header_height
        + header_body_gap
        + (row_count * row_height)
        + padding_y
        + bottom_padding
    )
    if multi_page:
        from dbt_charts.core.render.chart.table import (
            _PAGINATION_CAP_NOTE_HEIGHT,
            _PAGINATION_CONTROL_HEIGHT,
            _STATIC_MULTI_PAGE_MAX_PAGES,
        )

        height += _PAGINATION_CONTROL_HEIGHT
        # A static export whose real page count exceeds the pre-render cap
        # draws a "Showing pages 1-N of M" note on its own line below the
        # pager (see static_multi_page in table.py) -- unlike the pager
        # itself, which _PAGINATION_CONTROL_HEIGHT above reserves for and
        # draws in BOTH modes, this note is static-export-only. Reserved
        # unconditionally anyway: this pass has no way to know whether the
        # eventual render is interactive (dct serve/Cloud, never emits the
        # note) or static (dct render, might) -- controls_are_interactive()
        # only becomes meaningful once renderer.py opens that scope around
        # the MAIN pass, after sizing has finished. The cost is an unused
        # 20px band on an interactive table past the cap; the alternative
        # (never reserving it) is the explicit-slot invariant break this
        # code exists to fix.
        if estimated_total_pages > _STATIC_MULTI_PAGE_MAX_PAGES:
            height += _PAGINATION_CAP_NOTE_HEIGHT
    return height


def build_chart_datasets(
    resolved: ResolvedChart, executor: ChartDataProvider, variables: dict[str, Any]
) -> dict[str | None, list[dict[str, Any]]]:
    """Execute every query this chart references and map query_name -> rows.

    Always includes the base chart's own query (``resolved.query_name``,
    when set) — every chart, not just layered ones, resolves its rows from
    this one map now (``session.emit_chart`` / ``ChartFeature.apply`` no
    longer take a separate ``data`` parameter alongside it). Cartesian
    families additionally get each typed overlay layer's own ``query:``
    override (the target-vs-actuals idiom: the base and an overlay read
    different tables) — layers with no override share the base chart's own
    ``query_name`` (baked at resolve time, see ``_resolve_one_layer``), so
    every layer key is already present via the base entry; the loop below
    only adds genuinely distinct override queries. The executor already ran
    every one of these during the board's pre-execution pass
    (``collect_all_query_names``), so every lookup here is a cache hit, never
    a new query execution.
    """
    from dbt_charts.core.compile.models.chart.resolved._layer import (
        LayeredResolvedChart,
    )

    datasets: dict[str | None, list[dict[str, Any]]] = {
        resolved.query_name: (
            executor.execute_query(resolved.query_name, variables)
            if resolved.query_name is not None
            else []
        )
    }
    # Only families that carry chart.layers (`.layers` is declared per-family,
    # not on the shared cartesian base — heatmap has no overlay concept) can
    # have per-layer query overrides.
    if not isinstance(resolved, LayeredResolvedChart) or not resolved.layers:
        return datasets
    for layer in resolved.layers:
        if layer.query_name is not None and layer.query_name not in datasets:
            datasets[layer.query_name] = executor.execute_query(
                layer.query_name, variables
            )
    return datasets


def rows_for_query(
    query_name: str | None,
    datasets: dict[str | None, list[dict[str, Any]]] | None,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve the rows for ``query_name`` from ``datasets``.

    ``datasets`` (see ``build_chart_datasets``) is a complete query_name -> rows
    map for any chart with typed overlay layers — it carries the base chart's
    own entry alongside every layer override, so a caller resolving "the rows
    for query X" (base or layer) reads one map instead of juggling a separate
    base-rows parameter. ``fallback`` covers the two cases where that map
    can't answer the question: ``query_name`` is None (no override authored —
    the layer shares whatever rows the caller already has), or ``datasets``
    itself is absent (a caller — e.g. a unit test — that never built the full
    per-query map).
    """
    if query_name is None or datasets is None:
        return fallback
    rows = datasets.get(query_name)
    return rows if rows is not None else fallback


def _measure_vl_title_plot_gap(svg: str) -> float | None:
    """Return the gap (px) from title baseline to plot top in a VL chart SVG.

    VL positions the title group relative to the outer container (the plot).
    The role-title group sits above the outer container at ``role_title_group_y``
    (negative). The title text baseline sits at ``text_y`` within that group.
    Absolute title baseline = outer_y + role_title_group_y + text_y, so:

        gap = outer_y - (outer_y + role_title_group_y + text_y)
            = -(role_title_group_y + text_y)

    This is independent of outer_y, which means we never need to measure the
    outer container at all. Prior code read text_y as if it were absolute
    (missing role_title_group_y), overstating the gap by the title group's
    absolute y offset.

    Returns None when the expected VL structure is absent (no title, or chart
    family whose SVG layout differs from the standard cartesian pattern).
    """
    group_m = _ROLE_TITLE_GROUP_Y_RE.search(svg)
    if group_m is None:
        return None
    role_title_group_y = float(group_m.group(1))
    role_title_pos = svg.find("role-title-text")
    if role_title_pos == -1:
        return None
    text_m = _TITLE_TEXT_Y_RE.search(svg, role_title_pos)
    if text_m is None:
        return None
    text_y = float(text_m.group(1))
    return -(role_title_group_y + text_y)


def _support_table_title_corrected_offset(
    chart_svg: str, probe_title_offset: float
) -> float | None:
    """Compute the corrected title.offset given the probe SVG and its title.offset.

    apply_chart_support_table_post_pass sets title.offset = strip_h (the probe
    value; passed here as ``probe_title_offset``). VL adds a baseline gap on
    top so actual gap = baseline + probe_title_offset. This function measures
    the gap from the probe SVG and returns the corrected total title.offset that
    eliminates the over-allocation:

        gap_probe = baseline + probe_title_offset
        corrected_offset = max(0, 2·probe_title_offset − gap_probe)
                         = max(0, probe_title_offset − baseline)

    Clamped to 0 when baseline ≥ strip_h: the VL natural spacing already
    accommodates the strip, so no extra offset is needed, and going negative
    would place the title baseline at the strip top (descender overlap).

    Returns None when the SVG lacks the expected structure or the correction
    would be a no-op (corrected_offset ≈ probe_title_offset, i.e. baseline ≈ 0).
    """
    gap_probe = _measure_vl_title_plot_gap(chart_svg)
    if gap_probe is None:
        return None
    corrected_offset = max(0.0, 2.0 * probe_title_offset - gap_probe)
    if abs(corrected_offset - probe_title_offset) < 1.0:
        return None  # No meaningful correction needed.
    return corrected_offset


def _render_chart_to_svg(
    resolved: ResolvedChart,
    executor: Executor,
    variables: dict[str, Any],
    width: float,
    *,
    height: float | None = None,
    resolved_style: ResolvedStyle,
    padding: dict[str, int | float] | None = None,
    title_offset_override: float | None = None,
) -> tuple[str, float, float, float | None]:
    """Render a pre-resolved chart to SVG, returning (svg, width, height, probe_title_offset).

    Uses the single already-resolved ResolvedChart — no re-resolution.
    The executor's query cache makes data fetch free (no re-execution).

    ``probe_title_offset`` is the spec's title.offset BEFORE any
    ``title_offset_override`` is applied — it equals the value set by
    apply_chart_support_table_post_pass (= strip_h for titled top support_table charts,
    None for charts without a title.offset block). Callers use this to drive
    title-offset calibration without re-running support_table_strip_height.

    When ``title_offset_override`` is set, the VL spec's title.offset is patched
    to that value before vl-convert converts the spec to SVG.
    """
    from dbt_charts.core.render.chart.artifacts import RenderArtifact
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
    from dbt_charts.core.render.converters.chart import render_chart_artifact
    from dbt_charts.core.render.svg_utils import extract_svg_dimensions

    datasets = build_chart_datasets(resolved, executor, variables)
    data = datasets[resolved.query_name]
    artifact = render_resolved_chart(
        resolved,
        data,
        resolved_style,
        width=width,
        height=height,
        padding=padding,
        datasets=datasets,
    )
    # Read the probe title.offset BEFORE any override (set by apply_chart_support_table_post_pass).
    probe_title_offset: float | None = None
    if artifact.kind == "vega_spec" and isinstance(artifact.payload, dict):
        spec_root = artifact.payload
        spec_target = spec_root["hconcat"][0] if "hconcat" in spec_root else spec_root
        title_block = (
            spec_target.get("title") if isinstance(spec_target, dict) else None
        )
        if isinstance(title_block, dict):
            raw = title_block.get("offset")
            if isinstance(raw, (int, float)):
                probe_title_offset = float(raw)
    if (
        title_offset_override is not None
        and artifact.kind == "vega_spec"
        and isinstance(artifact.payload, dict)
    ):
        spec = artifact.payload
        spec_target_for_patch = spec["hconcat"][0] if "hconcat" in spec else spec
        title_block_patch = (
            spec_target_for_patch.get("title")
            if isinstance(spec_target_for_patch, dict)
            else None
        )
        if isinstance(title_block_patch, dict):
            title_block_patch["offset"] = title_offset_override
            artifact = RenderArtifact(kind="vega_spec", payload=spec)
    svg = render_chart_artifact(
        artifact,
        "svg",
        resolved_style,
        width=width,
        height=height,
        chart_id=resolved.id,
    )
    dims = extract_svg_dimensions(svg)
    return svg, dims.width, dims.height, probe_title_offset


def _make_data_aware_height_provider(
    render_ctx: SizingRenderCtx,
    executor: Executor,
    variables: dict[str, Any],
    card_padding: float,
    style_contexts: dict[int, ChartStyleContext],
) -> HeightProvider:
    """Create a HeightProvider that uses render-first sizing for Vega charts.

    For Vega-Lite charts: renders via vl-convert, caches SVG, returns actual height.
    For tables: uses actual row counts from executor.
    For everything else: delegates to the static compile-time calculator.

    ``style_contexts`` maps ``id(resolved_style)`` to the chart_style_context
    from the SAME cascade pass — the ``HeightProvider`` protocol (``render/
    sizing.py``) threads each nested board's own ``resolved_style`` through the
    ``resolved_style`` parameter below, but this closure is built once over the
    root's ``render_ctx``. Looking up by identity keeps a chart nested under a
    ``theme:``-overridden board resolving against its own chart_style_context
    instead of silently baking the root board's.

    Render-first Vega sizing (vl-convert renders) only fires when
    render_ctx.executor is not None. When render_ctx.executor is None
    (render_first=False), the provider falls back to static aspect-ratio
    estimates for Vega charts while still sizing tables from row counts.
    """

    def _vertical_inset(resolved: ResolvedChart) -> float:
        """card_pad on top+bottom plus any chart-local style.<family>.padding.

        Reads the padding baked onto the resolved chart at construction time —
        keeps the sizing pass aligned with the render path's
        additive_padding(card_pad, chart_padding) so SVG-family slot heights
        carry the chart-local override (otherwise authoring
        style.table.padding.bottom = 40 short-changes natural_heights and
        cross-column alignment drifts).
        """
        padding = resolved.layout_padding
        return 2 * card_padding + padding.top + padding.bottom

    def _unresolved_height(
        chart: Chart, width: float, resolved_style: ResolvedStyle
    ) -> float:
        """Slot height for a chart whose resolve failed.

        The counterpart to ``_vertical_inset``, reading the board's chart
        padding instead of the chart's own: the chart-local override is baked
        onto the resolved chart, which is exactly what is missing here.
        """
        board_padding = resolved_style.chart_defaults.padding
        return (
            get_chart_content_height(chart, width=width, resolved_style=resolved_style)
            + 2 * card_padding
            + board_padding.top
            + board_padding.bottom
        )

    def provider(
        item: LayoutItem,
        card_gap: float,
        gap: float,
        width: float,
        variable_values: dict[str, Any] | None,
        resolved_style: ResolvedStyle,
    ) -> float:
        if item.type == "chart" and item.chart:
            card_pad = card_padding

            # Resolve the table for this exact placement, then use actual row
            # counts for the height.
            if isinstance(item.chart, TableChart):
                resolved_table = _require_resolved(
                    render_ctx,
                    item.chart,
                    executor,
                    render_ctx.variables,
                    width,
                    resolved_style,
                    style_contexts[id(resolved_style)],
                )
                # Row-count sizing structurally needs the resolved table; with
                # no resolution there is no row count to read, so the slot gets
                # the same board-padding estimate every other unresolved chart
                # gets and the error tile is drawn into it.
                if resolved_table is None:
                    return _unresolved_height(item.chart, width, resolved_style)
                assert isinstance(resolved_table, ResolvedTableChart)
                return _get_table_height_from_data(
                    item.chart,
                    resolved_table,
                    executor,
                    variables,
                    card_padding=card_pad,
                    width=width,
                ) + _vertical_inset(resolved_table)

            # callout has no try/except around _render_chart_to_svg, unlike
            # spark_bar just below. This is latent, not dead: callout renders
            # from a static authored `message:` and does no data-shape
            # validation today, so nothing here raises ChartDataError yet.
            # The moment callout gains data-driven validation, one bad
            # callout will return output=None from renderer.py's board-level
            # handler and take down the whole board — the same failure mode
            # the spark_bar branch below is guarded against. Add the same
            # try/except DbtChartsError guard then; don't assume it was
            # considered and declined.
            if item.chart.type == "callout":
                resolved_callout = _require_resolved(
                    render_ctx,
                    item.chart,
                    executor,
                    render_ctx.variables,
                    width,
                    resolved_style,
                    style_contexts[id(resolved_style)],
                )
                if resolved_callout is None:
                    return _unresolved_height(item.chart, width, resolved_style)
                _chart_svg, _, actual_height, _ = _render_chart_to_svg(
                    resolved_callout,
                    executor,
                    variables,
                    width,
                    height=None,
                    resolved_style=resolved_style,
                )
                render_ctx.render_cache[(item.chart.id, width, actual_height)] = (
                    _chart_svg,
                    actual_height,
                )
                render_ctx.natural_heights[(item.chart.id, width)] = actual_height
                return actual_height

            # spark_bar: natural-height SVG renderer — renders content-first.
            # Returns natural_height + (top+bottom) padding so the slot includes
            # the SVG-family translate wrap and any chart-local padding override.
            # Not cached: rendering.py skips render_cache for SVG_LAYOUT_PADDED_TYPES,
            # so the final render pass re-renders regardless.
            if item.chart.type == "spark_bar":
                resolved_spark_bar = _require_resolved(
                    render_ctx,
                    item.chart,
                    executor,
                    render_ctx.variables,
                    width,
                    resolved_style,
                    style_contexts[id(resolved_style)],
                )
                if resolved_spark_bar is None:
                    return _unresolved_height(item.chart, width, resolved_style)
                # spark_bar validates its data at RENDER time, not resolve time
                # (whether x holds numbers is a property of the rows, which the
                # resolver never inspects), so _require_resolved's catch above
                # cannot see it. Degrade the same way it does: record the
                # per-chart failure and fall back to the unresolved slot height,
                # letting the main pass paint an error card. Without this a
                # single bad spark_bar escapes to renderer.py's board-level
                # handler and takes down every chart on the board.
                try:
                    _chart_svg, _, actual_height, _ = _render_chart_to_svg(
                        resolved_spark_bar,
                        executor,
                        variables,
                        width,
                        height=None,
                        resolved_style=resolved_style,
                    )
                except DbtChartsError as exc:
                    _log.warning(
                        "Chart %r failed to render during sizing: %s",
                        item.chart.id,
                        exc,
                    )
                    render_ctx.resolve_errors[item.chart.id] = ChartResolveFailure(
                        diagnostic=stamp_chart_diagnostic(
                            exc, item.chart.id, item.chart.source_path
                        ),
                        identity=ChartIdentity.from_normalized(item.chart),
                    )
                    return _unresolved_height(item.chart, width, resolved_style)
                return actual_height + _vertical_inset(resolved_spark_bar)

            # Render-first Vega sizing.
            # Vega charts now render at full item width with card_pad as internal
            # Vega padding, so the returned SVG height already includes the inset.
            # Do NOT add 2*card_pad here — it would double-count the padding.
            if (
                render_ctx.executor is not None
                and item.chart.type not in NON_ASPECT_RATIO_TYPES
            ):
                render_inner_width = width
                # A chart that failed to resolve falls through to the
                # static-estimate branch below, which sizes the slot its error
                # tile will be drawn into. _require_resolved has already
                # recorded the diagnostic.
                resolved_chart = _require_resolved(
                    render_ctx,
                    item.chart,
                    executor,
                    render_ctx.variables,
                    render_inner_width,
                    resolved_style,
                    style_contexts[id(resolved_style)],
                )
                if resolved_chart is not None:
                    # The same cached ResolvedChart the render call below
                    # reuses, so its baked layout_padding is the single source
                    # of truth for this placement's chart-local padding (no
                    # separate cascade re-run).
                    chart_padding = resolved_chart.layout_padding
                    # static_estimate approximates Vega output height: marks + Vega padding
                    # (additive: card_pad + chart-local top/bottom).
                    static_estimate = (
                        get_chart_content_height(
                            item.chart,
                            width=render_inner_width,
                            resolved_style=resolved_style,
                        )
                        + 2 * card_pad
                        + chart_padding.top
                        + chart_padding.bottom
                    )
                    # Use item.height when it has been pre-set (future: cols alignment
                    # pre-pass). In the normal sizing flow item.height is 0 here because
                    # _calculate_rows_dimensions fires the provider in its first pass before
                    # the second pass sets item.height.  _fix_slot_heights_in_tree runs after
                    # calculate_layout_items to re-render at the final slot height when the
                    # two values diverge (e.g. a 600px wrapper around an 800px estimate).
                    slot_height = item.height if item.height > 0 else static_estimate

                    # Use cached result if already rendered at this width. Pie/arc
                    # charts have a fixed natural height (see the undershoot note
                    # below) — return the cached value as-is. Other chart types
                    # still floor to static_estimate, matching the non-cached path.
                    if (
                        item.chart.id,
                        render_inner_width,
                    ) in render_ctx.natural_heights:
                        cached_height = render_ctx.natural_heights[
                            (item.chart.id, render_inner_width)
                        ]
                        if item.chart.type in _FIXED_ASPECT_PIE_TYPES:
                            return cached_height
                        return max(cached_height, static_estimate)

                    try:
                        chart_svg, actual_width, actual_height, probe_title_offset = (
                            _render_chart_to_svg(
                                resolved_chart,
                                render_ctx.executor,
                                render_ctx.variables,
                                render_inner_width,
                                height=slot_height,
                                resolved_style=resolved_style,
                                padding=additive_padding(card_pad, chart_padding),
                            )
                        )
                    except (ExecutionError, ChartDataError) as exc:
                        # Migrate-or-fail: only ExecutionError (query failed) and
                        # ChartDataError (vega-lite spec rejected) are expected
                        # data-side failures we estimate around. KeyError/ValueError/
                        # OSError/RuntimeError/LookupError are bug-class exceptions
                        # — let them surface loudly, do not silently render an
                        # aspect-ratio fallback that masks the bug.
                        _log.warning(
                            "Render-first sizing failed for chart %r "
                            "(will use aspect-ratio estimate): %s",
                            item.chart.id,
                            exc,
                        )
                    else:
                        if item.chart.type in _FIXED_ASPECT_PIE_TYPES:
                            # Pie/arc charts have a fixed natural height — direct-label
                            # aspect-ratio, or an arc-attached-table composition (e.g.
                            # "right" placement composes a short/wide SVG well below
                            # the generic aspect-ratio static_estimate). Only clamp a
                            # tiny rounding overshoot; trust a real undershoot as-is so
                            # the layout slot matches the actual composed content
                            # instead of reserving dead space below it.
                            if 0.0 <= actual_height - static_estimate <= 2.0:
                                actual_height = static_estimate
                        else:
                            # Clamp: tiny overshoot (≤2px above static_estimate) →
                            # static_estimate. Two-pass height correction (endpoint-label
                            # hconcat) can return actual_height = static_estimate + ε due
                            # to fractional-pixel rounding. Without the clamp,
                            # natural_heights differs by ε between endpoint_labels ON and
                            # OFF, causing row_height drift. Other undershoots are floored
                            # to static_estimate (aligns Vega chart heights within rows).
                            if actual_height - static_estimate <= 2.0:
                                actual_height = static_estimate

                        # autosize:pad (set by attach_support_table) makes the outer
                        # SVG wider than render_inner_width by the y-axis label +
                        # padding + legend overhead. Measure the first render's
                        # actual outer width, compute the overhead, and re-render
                        # with spec.width pre-shrunk so the second render's outer
                        # SVG fits the allocated slot.
                        # Pass height=static_estimate (not actual_height) to the
                        # re-render: under autosize:pad, spec.height is the *inner*
                        # plot rect, so Vega adds strip/axis on top — using
                        # actual_height as spec.height would inflate the output by
                        # ~strip_height on every re-render.
                        # Store the shrunk width in support_table_corrected_widths so
                        # _align_cols_heights can reuse it instead of item.width,
                        # preventing the overhead from being re-applied on alignment.
                        if _support_table_of(item.chart) is not None:
                            overhead = actual_width - render_inner_width
                            if overhead > 0:
                                would_be_width = render_inner_width - overhead
                                # The width floor is the column block's own
                                # mechanism (plot_width_floor.py: "reserves
                                # pixel width beside a horizontal bar's
                                # plot") — a top/bottom row strip reserves no
                                # width at all, so this measured overhead is
                                # ordinary axis-label/legend chrome any chart
                                # carries, support_table or not. Gate the
                                # raise on the same category-axis-vertical
                                # condition apply_chart_support_table_post_pass
                                # uses to pick the column path, so a narrow
                                # card with a long legend and a strip that
                                # never competed for width can't trip a floor
                                # meant for a block that isn't there.
                                category_axis_vertical = (
                                    isinstance(resolved_chart, ResolvedBarChart)
                                    and resolved_chart.orientation == "horizontal"
                                )
                                floor_px = (
                                    render_inner_width
                                    * get_chart_rendering().support_table.plot_width_floor_ratio
                                )
                                if category_axis_vertical and would_be_width < floor_px:
                                    # Record and degrade like the spark_bar
                                    # render-time failure above: an uncaught
                                    # raise here escapes this chart's own
                                    # provider call and is caught at board
                                    # level, blanking every sibling chart and
                                    # dropping every other diagnostic. This
                                    # chart alone gets an error card instead.
                                    width_floor_exc = RenderError.from_code(
                                        ERR_INPUT_INVALID,
                                        message=(
                                            f"chart {item.chart.id!r}: attaching "
                                            "support_table leaves the plot an "
                                            f"estimated {would_be_width:.0f}px wide "
                                            f"on a {render_inner_width:.0f}px card, "
                                            f"below the {floor_px:.0f}px floor it "
                                            "needs to stay readable. A plot this "
                                            "narrow is a missing chart, not a "
                                            "squeezed one, and would paint values "
                                            "beside nothing. Drop a support_table "
                                            "column, widen the card, or move the "
                                            "block to `position: right`."
                                        ),
                                    )
                                    render_ctx.resolve_errors[item.chart.id] = (
                                        ChartResolveFailure(
                                            diagnostic=stamp_chart_diagnostic(
                                                width_floor_exc,
                                                item.chart.id,
                                                item.chart.source_path,
                                            ),
                                            identity=ChartIdentity.from_normalized(
                                                item.chart
                                            ),
                                        )
                                    )
                                    return _unresolved_height(
                                        item.chart, width, resolved_style
                                    )
                                shrunk_width = max(would_be_width, 1.0)
                                # Resolve at the corrected spec width so title
                                # typography matches the emitted chart.
                                canonical_key = item.chart.id, id(resolved_style)
                                render_ctx.canonical_resolved.pop(canonical_key, None)
                                corrected_resolved = _require_resolved(
                                    render_ctx,
                                    item.chart,
                                    executor,
                                    render_ctx.variables,
                                    shrunk_width,
                                    resolved_style,
                                    style_contexts[id(resolved_style)],
                                )
                                # Only reachable if resolution ever becomes
                                # width-conditional: the same chart resolved at
                                # the un-shrunk width moments ago. Size the slot
                                # for the error tile and drop the first render
                                # rather than cache an SVG for a chart now
                                # recorded as failed.
                                if corrected_resolved is None:
                                    return _unresolved_height(
                                        item.chart, width, resolved_style
                                    )
                                try:
                                    chart_svg, _, correction_height, _ = (
                                        _render_chart_to_svg(
                                            corrected_resolved,
                                            render_ctx.executor,
                                            render_ctx.variables,
                                            shrunk_width,
                                            height=slot_height,
                                            resolved_style=resolved_style,
                                            padding=additive_padding(
                                                card_pad, chart_padding
                                            ),
                                        )
                                    )
                                except (ExecutionError, ChartDataError) as exc:
                                    # Render-stage only: the resolve above is
                                    # guarded by its own None check.
                                    _log.warning(
                                        "Width-correction re-render failed for chart %r "
                                        "(keeping first render): %s",
                                        item.chart.id,
                                        exc,
                                    )
                                else:
                                    actual_height = max(
                                        correction_height, static_estimate
                                    )
                                    original_key = resolved_chart_variant_key(
                                        item.chart.id,
                                        render_inner_width,
                                        resolved_style,
                                    )
                                    render_ctx.resolved_variants[original_key] = (
                                        corrected_resolved
                                    )
                                    render_ctx.support_table_corrected_widths[
                                        item.chart.id
                                    ] = shrunk_width

                        # Calibrate title.offset for titled top support_table charts.
                        # probe_title_offset (from the initial render's spec) is the
                        # value set by apply_chart_support_table_post_pass using the
                        # real series_count. Re-render with a corrected offset so
                        # VL's baseline gap is cancelled. Runs after width correction
                        # so the SVG being measured has the correct slot width.
                        effective_width = render_ctx.support_table_corrected_widths.get(  # type-state: silent_fallback — render_inner_width is the correct default when no width correction was done for this chart
                            item.chart.id, render_inner_width
                        )
                        if probe_title_offset is not None:
                            title_offset_override = (
                                _support_table_title_corrected_offset(
                                    chart_svg, probe_title_offset
                                )
                            )
                            if title_offset_override is not None:
                                effective_resolved = _require_resolved(
                                    render_ctx,
                                    item.chart,
                                    executor,
                                    render_ctx.variables,
                                    effective_width,
                                    resolved_style,
                                    style_contexts[id(resolved_style)],
                                )
                                if effective_resolved is not None:
                                    try:
                                        chart_svg, _, calibrated_height, _ = (
                                            _render_chart_to_svg(
                                                effective_resolved,
                                                executor,
                                                render_ctx.variables,
                                                effective_width,
                                                height=slot_height,
                                                resolved_style=resolved_style,
                                                padding=additive_padding(
                                                    card_pad, chart_padding
                                                ),
                                                title_offset_override=title_offset_override,
                                            )
                                        )
                                    except (ExecutionError, ChartDataError) as exc:
                                        _log.warning(
                                            "Title-calibration re-render failed for "
                                            "chart %r (keeping uncalibrated render): %s",
                                            item.chart.id,
                                            exc,
                                        )
                                    else:
                                        actual_height = max(
                                            calibrated_height, static_estimate
                                        )

                        render_ctx.render_cache[
                            (item.chart.id, render_inner_width, actual_height)
                        ] = (chart_svg, actual_height)
                        render_ctx.natural_heights[
                            (item.chart.id, render_inner_width)
                        ] = actual_height
                        return actual_height

            # Fallback: static aspect-ratio estimate (marks + Vega padding).
            # Resolve any chart that didn't go through the render-first path
            # (e.g. kpi, which is in NON_ASPECT_RATIO_TYPES) so it lands in
            # pre_resolved for _build_resolved_layout.
            resolved_fallback = _require_resolved(
                render_ctx,
                item.chart,
                executor,
                render_ctx.variables,
                width,
                resolved_style,
                style_contexts[id(resolved_style)],
            )
            if resolved_fallback is None:
                return _unresolved_height(item.chart, width, resolved_style)
            return get_chart_content_height(
                item.chart,
                width=width,
                resolved_style=resolved_style,
            ) + _vertical_inset(resolved_fallback)

        if item.type == "board" and item.board:
            data_aware = get_item_content_height(
                item,
                card_gap,
                gap,
                width,
                variable_values,
                height_provider=provider,
                resolved_style=resolved_style,
            )
            return max(data_aware, item.height)

        # Fallback for any other item types
        return get_item_content_height(
            item,
            card_gap,
            gap,
            width,
            variable_values,
            height_provider=provider,
            resolved_style=resolved_style,
        )

    return provider


def _col_chart_body_target(
    items: list[LayoutItem], render_ctx: SizingRenderCtx
) -> float:
    """Max natural chart body height across all items in a cols row.

    For direct chart items uses the natural height from natural_heights.
    For board items the logic depends on the board's inner layout:
      - All-Vega rows board (every sub-item is a Vega chart): contribute
        item.height (= sum of sub-chart heights + gaps, set by the sizing pass).
        An adjacent direct chart must fill the same cell height.
      - Any other board layout (mixed content, cols sub-layout, single chart):
        contribute max(sub-chart natural heights) — the non-chart overhead must
        not inflate adjacent chart bodies.
    Non-aspect-ratio types (table, kpi, spark_bar) are excluded.
    Returns 0.0 if no Vega charts are found.
    """
    max_h = 0.0
    for item in items:
        if item.chart is not None and item.chart.type not in NON_ASPECT_RATIO_TYPES:
            h = render_ctx.natural_heights.get((item.chart.id, item.width))
            if h is not None:
                max_h = max(max_h, h)
        elif item.board is not None:
            sub_items = item.board.layout.items
            is_all_vega_rows = (
                item.board.layout.type == "rows"
                and len(sub_items) > 1
                and all(
                    s.chart is not None and s.chart.type not in NON_ASPECT_RATIO_TYPES
                    for s in sub_items
                )
            )
            if is_all_vega_rows and item.height > 0:
                # board item.height = sum(sub-chart heights) + inter-item gaps,
                # computed by the sizing pass. Use it as-is so the adjacent
                # direct chart fills the same cell.
                max_h = max(max_h, item.height)
            else:
                for sub in sub_items:
                    if (
                        sub.chart is not None
                        and sub.chart.type not in NON_ASPECT_RATIO_TYPES
                    ):
                        h = render_ctx.natural_heights.get((sub.chart.id, sub.width))
                        if h is not None:
                            max_h = max(max_h, h)
    return max_h


def _effective_alignment_target_for_chart(
    chart: Chart,
    width: float,
    target_height: float,
    render_ctx: SizingRenderCtx,
) -> float:
    """Return the per-chart alignment target, capping pie/arc growth."""
    natural_h = render_ctx.natural_heights.get((chart.id, width))
    if (
        chart.type in _FIXED_ASPECT_PIE_TYPES
        and natural_h is not None
        and target_height > natural_h
    ):
        return natural_h
    return target_height


def _align_board_charts(
    board_item: LayoutItem, target_height: float, render_ctx: SizingRenderCtx
) -> None:
    """Expand Vega charts inside a board item toward target_height.

    The board bounds the expansion: charts can grow at most to board.layout.content_height
    minus the space taken by non-chart siblings (rows layout only — stacked items share
    vertical space). For cols layouts, items are side-by-side so non-chart siblings do
    not reduce the Vega chart's available height.

    Updates each chart's item.height so the render layer uses the correct cache key,
    then delegates the actual re-render to _align_cols_heights.
    """
    assert board_item.board is not None, (
        "_align_board_charts requires a board LayoutItem"
    )
    board = board_item.board
    vega_count = sum(
        1
        for sub in board.layout.items
        if sub.chart is not None and sub.chart.type not in NON_ASPECT_RATIO_TYPES
    )
    # cols layout: all side-by-side charts need equalization.
    # non-cols with exactly 1 Vega chart (e.g. chart + text caption): safe to expand.
    # non-cols with multiple Vega charts: stacked rows — equalizing would overflow the board.
    if board.layout.type != "cols" and vega_count != 1:
        return
    content_h: float = board.layout.content_height
    if content_h <= 0:
        return

    if board.layout.type == "cols":
        # cols: items are side-by-side — non-chart siblings don't consume Vega height.
        board_chart_max = content_h
    else:
        # rows with 1 Vega chart: stacked non-chart items (captions, KPIs) do consume height.
        non_chart_h = sum(
            sub.height
            for sub in board.layout.items
            if sub.chart is None or sub.chart.type in NON_ASPECT_RATIO_TYPES
        )
        board_chart_max = max(content_h - non_chart_h, 0.0)
    effective = min(target_height, board_chart_max)

    for sub in board.layout.items:
        if sub.chart is not None and sub.chart.type not in NON_ASPECT_RATIO_TYPES:
            sub.height = _effective_alignment_target_for_chart(
                sub.chart,
                sub.width,
                effective,
                render_ctx,
            )

    _align_cols_heights(board.layout.items, effective, render_ctx)


def _correct_support_table_height(
    render_ctx: SizingRenderCtx,
    item: LayoutItem,
    chart_id: str,
    slot_width: float,
    target: float,
    chart_svg: str,
    actual_height: float,
    card_pad: float,
    chart_padding: PaddingStyle,
    executor: Executor,
    caught: tuple[type[Exception], ...],
    log_msg: str,
    title_offset_override: float | None = None,
) -> tuple[str, float]:
    """Correct autosize:pad height overhead and/or title.offset for a support_table chart.

    A no-op when the chart has no support_table, no height correction is needed,
    and no title calibration is required. Otherwise re-renders once with the
    corrected height and/or corrected title.offset.

    Shared by ``_align_cols_heights`` and ``_fix_slot_heights_in_tree``.
    ``title_offset_override`` is the calibrated title.offset from
    ``_support_table_title_corrected_offset``; None means skip title calibration.
    """
    assert item.chart is not None, "_correct_support_table_height requires a chart item"
    if _support_table_of(item.chart) is None:
        return chart_svg, actual_height
    overhead = actual_height - target
    needs_height_correction = overhead > 0
    needs_title_calibration = title_offset_override is not None
    if not needs_height_correction and not needs_title_calibration:
        return chart_svg, actual_height

    corrected_height = (
        max(target - overhead, 1.0) if needs_height_correction else target
    )
    resolved = _require_resolved(
        render_ctx,
        item.chart,
        executor,
        render_ctx.variables,
        slot_width,
        render_ctx.resolved_style,
        render_ctx.chart_style_context,
    )
    if resolved is None:
        return chart_svg, actual_height
    try:
        corrected_svg, _, corrected_actual, _ = _render_chart_to_svg(
            resolved,
            executor,
            render_ctx.variables,
            slot_width,
            height=corrected_height,
            resolved_style=render_ctx.resolved_style,
            padding=additive_padding(card_pad, chart_padding),
            title_offset_override=title_offset_override,
        )
    except caught as exc:
        _log.warning(log_msg, chart_id, exc)
        return chart_svg, actual_height
    # When title calibration is active, the smaller offset also shrinks the SVG
    # (the offset change is part of autosize:pad chrome). A second pass corrects
    # the undershoot. Pure height correction has constant overhead, so the first
    # correction render already lands on target -- no second pass needed there.
    if needs_title_calibration and abs(corrected_actual - target) > 1.0:
        final_h = max(corrected_height + (target - corrected_actual), 1.0)
        try:
            corrected_svg, _, corrected_actual, _ = _render_chart_to_svg(
                resolved,
                executor,
                render_ctx.variables,
                slot_width,
                height=final_h,
                resolved_style=render_ctx.resolved_style,
                padding=additive_padding(card_pad, chart_padding),
                title_offset_override=title_offset_override,
            )
        except caught as exc:
            _log.warning(log_msg, chart_id, exc)
    return corrected_svg, corrected_actual


def _align_cols_heights(
    items: list[LayoutItem], target_height: float, render_ctx: SizingRenderCtx
) -> None:
    """Re-render shorter Vega cols items at target_height; update their cache entries.

    Handles both direct chart items and board items (delegating to
    _align_board_charts for the latter).  Non-Vega items (table, KPI) are not
    re-rendered — their container gets the max height but their content stays
    at its natural height.
    """
    card_pad = float(render_ctx.resolved_style.frame.card_padding)

    if render_ctx.executor is None:
        return

    executor = render_ctx.executor

    for item in items:
        if item.board is not None:
            _align_board_charts(item, target_height, render_ctx)
            continue
        if item.chart is None or item.chart.type in NON_ASPECT_RATIO_TYPES:
            continue
        chart_id = item.chart.id
        natural_h = render_ctx.natural_heights.get((chart_id, item.width))
        effective_target = _effective_alignment_target_for_chart(
            item.chart,
            item.width,
            target_height,
            render_ctx,
        )
        if natural_h is None or abs(natural_h - effective_target) < 1.0:
            continue

        # Vega-family: render at item width (or the corrected shrunk width for
        # support_table charts). autosize:pad (activated by attach_support_table) makes
        # the outer SVG wider than spec.width by a constant overhead; using item.width
        # as spec.width would re-apply that overhead and overflow the allocated slot.
        slot_width = render_ctx.support_table_corrected_widths.get(
            chart_id, item.width
        )  # type-state: silent_fallback — a chart with no support_table has no width correction to look up; item.width is the real slot, not a guess
        # A chart that failed to resolve has no alignment to do — it will be
        # drawn as an error tile at the height already assigned to its slot.
        resolved_chart = _require_resolved(
            render_ctx,
            item.chart,
            executor,
            render_ctx.variables,
            slot_width,
            render_ctx.resolved_style,
            render_ctx.chart_style_context,
        )
        if resolved_chart is None:
            continue
        # Per-family padding, baked onto the resolved chart at construction time.
        chart_padding = resolved_chart.layout_padding
        try:
            chart_svg, _, actual_height, probe_title_offset = _render_chart_to_svg(
                resolved_chart,
                executor,
                render_ctx.variables,
                slot_width,
                height=effective_target,
                resolved_style=render_ctx.resolved_style,
                padding=additive_padding(card_pad, chart_padding),
            )
        except ChartDataError as exc:
            # Migrate-or-fail: only ChartDataError (vega-lite rejected the spec)
            # is an expected per-chart render failure we keep going through.
            # Other exception types are bug-class — let them surface loudly.
            _log.warning(
                "Cols alignment re-render failed for chart %r; "
                "keeping original height: %s",
                chart_id,
                exc,
            )
            continue

        # Calibrate title.offset using probe_title_offset from the render spec
        # (set by apply_chart_support_table_post_pass with the real series_count).
        title_offset_override = (
            _support_table_title_corrected_offset(chart_svg, probe_title_offset)
            if probe_title_offset is not None
            else None
        )

        # Correct height overhead and/or title.offset in a single combined re-render.
        chart_svg, actual_height = _correct_support_table_height(
            render_ctx,
            item,
            chart_id,
            slot_width,
            effective_target,
            chart_svg,
            actual_height,
            card_pad,
            chart_padding,
            executor,
            (ChartDataError,),
            "Height-correction re-render failed for chart %r "
            "(keeping first alignment render): %s",
            title_offset_override,
        )

        render_ctx.render_cache[(chart_id, item.width, effective_target)] = (
            chart_svg,
            actual_height,
        )


def _align_grid_rows(items: list[LayoutItem], render_ctx: SizingRenderCtx) -> None:
    """Align Vega chart heights within each row of a grid layout.

    Groups items by (row, height). Items with the same row and row_span share
    the same height and form one visual row band to align.
    """
    row_groups: dict[tuple[int, float], list[LayoutItem]] = {}
    for item in items:
        key = (item.row or 0, item.height)
        if key not in row_groups:
            row_groups[key] = []
        row_groups[key].append(item)
    for (_, row_height), row_items in row_groups.items():
        if len(row_items) > 1:
            _align_cols_heights(row_items, row_height, render_ctx)


def _fix_slot_heights_in_tree(layout: Layout, render_ctx: SizingRenderCtx) -> None:
    """Re-render Vega charts whose slot height differs from the cached render height.

    Called after calculate_layout_items has set all item.height values.
    When a layout wrapper declares an explicit height (e.g. height: 600 on a
    rows wrapper), the sizing provider may have rendered at the aspect-ratio
    estimate (because item.height was 0 at provider call time). If the layout
    algorithm then scaled the item.height down to the slot, the cache entry
    (chart_id, width, estimate) won't match the renderer's lookup key
    (chart_id, width, item.height). This pass re-renders at item.height and
    updates the cache so the render pass hits without a fresh vl-convert call.

    Only fires when item.height differs from the natural height by more than 1px
    (avoids redundant re-renders for sub-pixel rounding differences).
    """
    if render_ctx.executor is None:
        return

    card_pad = float(render_ctx.resolved_style.frame.card_padding)

    for item in active_layout_items(layout, render_ctx.variables):
        if item.board is not None:
            nested_ctx = _nested_render_ctx(render_ctx, item.board)
            _fix_slot_heights_in_tree(item.board.layout, nested_ctx)
            continue

        if item.chart is None or item.chart.type in NON_ASPECT_RATIO_TYPES:
            continue
        if item.height <= 0:
            continue

        chart_id = item.chart.id
        natural_h = render_ctx.natural_heights.get((chart_id, item.width))
        if natural_h is None or abs(natural_h - item.height) < 1.0:
            continue

        # Slot height differs from the rendered height — re-render at item.height.
        slot_width = render_ctx.support_table_corrected_widths.get(
            chart_id, item.width
        )  # type-state: silent_fallback — a chart with no support_table has no width correction to look up; item.width is the real slot, not a guess
        # A chart that failed to resolve has nothing to re-render — its slot
        # already holds the height its error tile will be drawn at.
        resolved_chart = _require_resolved(
            render_ctx,
            item.chart,
            render_ctx.executor,
            render_ctx.variables,
            slot_width,
            render_ctx.resolved_style,
            render_ctx.chart_style_context,
        )
        if resolved_chart is None:
            continue
        # Per-family padding, baked onto the resolved chart at construction time.
        chart_padding = resolved_chart.layout_padding
        try:
            chart_svg, _, actual_height, probe_title_offset = _render_chart_to_svg(
                resolved_chart,
                render_ctx.executor,
                render_ctx.variables,
                slot_width,
                height=item.height,
                resolved_style=render_ctx.resolved_style,
                padding=additive_padding(card_pad, chart_padding),
            )
        except (ExecutionError, ChartDataError) as exc:
            _log.warning(
                "Slot-height re-render failed for chart %r "
                "(keeping aspect-ratio render): %s",
                chart_id,
                exc,
            )
            continue

        # Calibrate title.offset using probe_title_offset from the render spec
        # (set by apply_chart_support_table_post_pass with the real series_count).
        title_offset_override = (
            _support_table_title_corrected_offset(chart_svg, probe_title_offset)
            if probe_title_offset is not None
            else None
        )

        # Correct height overhead and/or title.offset in a single combined re-render.
        chart_svg, actual_height = _correct_support_table_height(
            render_ctx,
            item,
            chart_id,
            slot_width,
            item.height,
            chart_svg,
            actual_height,
            card_pad,
            chart_padding,
            render_ctx.executor,
            (ExecutionError, ChartDataError),
            "Height-correction re-render failed for chart %r "
            "(keeping slot-height render): %s",
            title_offset_override,
        )

        render_ctx.render_cache[(chart_id, item.width, item.height)] = (
            chart_svg,
            actual_height,
        )
        render_ctx.natural_heights[(chart_id, item.width)] = actual_height


def _ensure_resolved_variants_in_tree(
    layout: Layout,
    render_ctx: SizingRenderCtx,
    executor: Executor,
) -> None:
    """Resolve every final layout placement at its assigned slot width.

    Every layout item ends this pass with either a resolved variant or an entry
    in `render_ctx.resolve_errors` — the two branches `_resolve_layout_item`
    chooses between. Nothing to do with the return value here: a failure has
    already recorded itself, and the item becomes an error tile.
    """
    for item in active_layout_items(layout, render_ctx.variables):
        if item.board is not None:
            nested_ctx = _nested_render_ctx(render_ctx, item.board)
            _ensure_resolved_variants_in_tree(item.board.layout, nested_ctx, executor)
        elif item.chart is not None:
            _require_resolved(
                render_ctx,
                item.chart,
                executor,
                render_ctx.variables,
                item.width,
                render_ctx.resolved_style,
                render_ctx.chart_style_context,
            )


def _align_all_cols_in_tree(layout: Layout, render_ctx: SizingRenderCtx) -> None:
    """Walk the layout tree depth-first and align cols/grid heights where needed."""
    if render_ctx.render_cache and layout.items:
        if layout.type == "cols":
            chart_target = _col_chart_body_target(layout.items, render_ctx)
            if chart_target > 0:
                for item in layout.items:
                    if (
                        item.chart is not None
                        and item.chart.type not in NON_ASPECT_RATIO_TYPES
                    ):
                        item.height = _effective_alignment_target_for_chart(
                            item.chart,
                            item.width,
                            chart_target,
                            render_ctx,
                        )
                _align_cols_heights(layout.items, chart_target, render_ctx)
        elif layout.type == "grid":
            _align_grid_rows(layout.items, render_ctx)

    # Nested layouts are always modeled as nested boards in the Board tree;
    # board-only recursion is correct and complete.
    for item in active_layout_items(layout, render_ctx.variables):
        if item.board is not None and item.board.layout.items:
            nested_ctx = _nested_render_ctx(render_ctx, item.board)
            _align_all_cols_in_tree(item.board.layout, nested_ctx)


def _snapshot_authored_slot_heights(
    layout: Layout,
    inherited_authored: bool,
    out: dict[str, float],
    variable_values: VariableValues,
) -> None:
    """Record chart_id -> item.height for every chart under an authored ancestor.

    Must run after ``calculate_layout_items`` (and ``_fix_slot_heights_in_tree``)
    have assigned real slot heights, but before ``_align_all_cols_in_tree`` — the
    cols-alignment step re-expands a `cols:`-wrapped chart's ``item.height`` to
    its natural/unconstrained size to keep siblings visually aligned, discarding
    the authored cap this snapshot exists to preserve.

    Reads ``item.height`` directly rather than re-deriving a budget from
    ``item.layout_height`` (the raw authored string): the sizing pass has
    already done that arithmetic correctly for every layout shape, including
    splitting a `rows:` wrapper's height across its children and resolving a
    percentage-authored height against real available space. Re-deriving it a
    second time here would just grow a second cascade that can drift from the
    first — which is exactly how round 2 of this detector went wrong for
    `rows:` wrappers with more than one child.

    ``inherited_authored`` tracks only whether *some* ancestor (or the item
    itself) declared a literal ``layout_height`` — the gate for "was this
    chart's height ever a deliberate authoring choice" — separately from the
    pixel value, which always comes from ``item.height``.
    """
    for item in active_layout_items(layout, variable_values):
        authored = bool(item.layout_height) or inherited_authored
        if item.chart is not None and authored and item.height > 0:
            out[item.chart.id] = item.height
        if item.board is not None:
            _snapshot_authored_slot_heights(
                item.board.layout, authored, out, variable_values
            )


def calculate_data_aware_layout(
    board: Board,
    executor: Executor,
    variables: dict[str, Any] | None = None,
    render_first: bool = True,
    *,
    pre_resolved: dict[str, ResolvedChart] = {},  # noqa: B006 — read-only sentinel
    resolved_variants: ResolvedChartVariants | EllipsisType = ...,
    resolve_errors: dict[str, ChartResolveFailure] | EllipsisType = ...,
    authored_slot_heights: dict[str, float] | EllipsisType = ...,
) -> tuple[Board, RenderCache]:
    """Calculate layout dimensions using actual query data for table sizing.

    Uses the executor to get actual row counts for table charts and renders
    Vega-Lite charts to get true heights.

    Must be called after query results are cached on the executor.

    Args:
        board: Board with layout structure
        executor: Executor with cached query results
        variables: Variable values for query execution
        render_first: When True, Vega-Lite charts are rendered during sizing to
            get actual heights (render-first sizing). Set False for non-SVG
            formats (yaml/json/text) to avoid unnecessary chart rendering.
        pre_resolved: Already-resolved charts (chart_id → ResolvedChart).
            When provided, the sizing pass renders these directly, avoiding a
            second resolution per chart. Always pass from build_resolved_board.
        resolve_errors: When given, populated in place with chart_id -> the
            failure recorded for every chart whose resolution raised. Charts
            listed here have no entry in ``resolved_variants``.
        authored_slot_heights: When given, populated in place with
            chart_id -> the real px slot height assigned to every chart under
            an authored ancestor, captured before cols-alignment can overwrite
            it. See ``_snapshot_authored_slot_heights``.

    Returns:
        (Board with calculated dimensions, render_cache mapping chart_id
        to (svg_string, actual_height) for Vega charts rendered during sizing)
    """
    frame = board.resolved_style.frame

    container_width = board_container_width(board)
    content_width = max(container_width - 2 * float(frame.margin), 0.0)
    min_height = float(frame.min_height)
    card_padding = float(frame.card_padding)

    card_gap = float(frame.card_gap) if board.card_gap else 0.0
    gap = get_board_gap(board)

    # Create render context; executor=None disables render-first Vega sizing.
    # variables=None means "no variable overrides" — normalize to {} here so
    # downstream code (SizingRenderCtx, height provider) always works with a dict.
    effective_vars = variables if variables is not None else {}

    # What the sizing pass measures against. The charts already render at the
    # committed values; a title interpolating one, and a variable control
    # displaying one, have to be measured at them too, or this pass reserves
    # space for a board the render never draws. Layered over the defaults
    # because a request names only the variables it changed. This also
    # covers the tabs layout, which resolves its active tab against this same
    # merged state (e.g. a URL param) — sizing must measure the same tab the
    # renderer emits. Merge here rather than trust the caller to pre-merge:
    # renderer.py's merged_variables already is this superset, so the merge
    # is a no-op for production, but a caller that passes only its own
    # overrides (e.g. `{}` for "no overrides") still gets the declared
    # defaults for everything else this pass measures.
    variable_values = {**board.variable_defaults, **effective_vars}
    render_ctx = SizingRenderCtx(
        resolved_style=board.resolved_style,
        chart_style_context=board.chart_style_context,
        executor=executor if render_first else None,
        variables=effective_vars,
        pre_resolved=pre_resolved,
        resolved_variants=(resolved_variants if resolved_variants is not ... else {}),
        resolve_errors=(resolve_errors if resolve_errors is not ... else {}),
    )

    # Build a data-aware height provider
    height_provider = _make_data_aware_height_provider(
        render_ctx,
        executor,
        effective_vars,
        card_padding=card_padding,
        style_contexts=_style_contexts_by_resolved_style_id(board),
    )

    # Calculate layout height using compile sizing algorithms + data-aware provider
    container_height = calculate_layout_height(
        board.layout,
        card_gap,
        gap,
        min_height,
        available_width=content_width,
        variable_values=variable_values,
        height_provider=height_provider,
        resolved_style=board.resolved_style,
    )

    if board.title:
        title_measure_width = max(content_width - 2 * card_padding, 0.0)
        board_title_height = get_title_height(
            board.title,
            title_measure_width,
            variable_values,
            level=board.level,
            resolved_style=board.resolved_style,
        )
        container_height += board_title_height + gap + card_gap

    board.layout.width = container_width
    board.layout.height = container_height
    board.layout.content_width = content_width
    board.layout.content_height = container_height

    # Assign dimensions using compile sizing + data-aware provider
    calculate_layout_items(
        board.layout,
        content_width,
        container_height,
        card_gap,
        gap,
        variable_values,
        height_provider,
        resolved_style=board.resolved_style,
    )

    # Slot-height fix: re-render charts whose cached SVG was sized by the
    # aspect-ratio estimate but whose assigned slot height differs.  This handles
    # layout wrappers with an explicit height (rows: [- height: 600, rows: [chart]])
    # where the sizing provider ran before item.height was set.
    if render_ctx.render_cache:
        _fix_slot_heights_in_tree(board.layout, render_ctx)

    # Capture the real, per-chart assigned slot height before cols-alignment
    # can overwrite it — see _snapshot_authored_slot_heights.
    _snapshot_authored_slot_heights(
        board.layout,
        False,
        authored_slot_heights if authored_slot_heights is not ... else {},
        variable_values,
    )

    # Cols alignment: walk tree, re-render shorter Vega items at aligned height
    if render_ctx.render_cache:
        _align_all_cols_in_tree(board.layout, render_ctx)

    _ensure_resolved_variants_in_tree(board.layout, render_ctx, executor)

    return board, render_ctx.render_cache
