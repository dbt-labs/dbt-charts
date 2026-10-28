"""dbt_charts renderer module.

Stage: RENDER
Purpose: Render compiled boards to various output formats.

Entry Points:
    - render(board, executor, format, variables, **options) -> str | bytes
    - render_chart(chart, data, **options) -> str

This is the main rendering orchestration module. It:
1. Takes a Board from compile stage
2. Delegates SVG rendering to boards.py
3. Delegates format conversion to converters/
4. Produces output in requested format

Dependencies:
    - dbt_charts.compile (for Board, Chart)
    - dbt_charts.execute (for Executor)
    - .boards (for SVG rendering)
    - .charts (for chart rendering)
    - .converters (for format conversion)
"""

import contextlib
import datetime
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

from dbt_charts.core.compile.config import (
    get_rendering_config,
    resolve_max_workers,
)
from dbt_charts.core.compile.models.board.normalized import (
    Board,
    Layout,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.board.resolved import (
    ResolvedBoard,
    ResolvedLayout,
)
from dbt_charts.core.compile.models.chart.normalized import NON_ASPECT_RATIO_TYPES
from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.compile.template.variables import (
    check_option_values,
    daterange_preset_ids,
    normalize_render_values,
    parse_variable_json_strings,
    variable_value_is_absent,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.suppression import partition as _partition_warnings
from dbt_charts.core.execute.collect import (
    collect_all_query_names,
    collect_layout_chart_query_names,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.parallel import execute_queries_parallel
from dbt_charts.core.render.board_to_dict import NO_ROW_CAP
from dbt_charts.core.render.board_variables import (
    board_clock,
    board_variables,
    current_board_today,
    daterange_presets,
)
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.chart.annotation_nothing_drawn import (
    NothingDrawn,
    collect_annotation_nothing_drawn,
)
from dbt_charts.core.render.chart.axis_label_collision import (
    AxisLabelCollision,
    collect_axis_label_collisions,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    collect_endpoint_label_gap_overflows,
)
from dbt_charts.core.render.chart.geometry_warnings import (
    GeometryWarnings,
    collect_geometry_warnings,
)
from dbt_charts.core.render.chart.pie_total_overflow import (
    PieTotalOverflow,
    collect_pie_total_overflows,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    PlotWidthShareWarning,
    collect_plot_width_share_warnings,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    SeriesLabelTruncation,
    collect_series_label_truncations,
)
from dbt_charts.core.render.chart.spec_builders import additive_padding
from dbt_charts.core.render.chart.table_align import (
    collect_table_align_no_visible_effects,
    collect_table_numeric_align_lefts,
)
from dbt_charts.core.render.chart.table_overflow import (
    TableCramping,
    TableOverflow,
    collect_table_crampings,
    collect_table_overflows,
)
from dbt_charts.core.render.chart.table_page_squeeze import (
    TablePageSqueeze,
    collect_table_page_squeezes,
)
from dbt_charts.core.render.chart.table_static_pagination import (
    StaticPagination,
    collect_static_paginations,
)
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
    collect_text_truncations,
)
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    collect_x_domain_paint_orders,
)
from dbt_charts.core.render.contrast_warning import (
    ContrastRecord,
    collect_contrast_warnings,
)
from dbt_charts.core.render.controls import interactive_controls
from dbt_charts.core.render.converters import to_html, to_pdf, to_png
from dbt_charts.core.render.errors import (
    FormatError,
    MissingRequiredVariablesError,
    MissingVariable,
    RenderError,
)
from dbt_charts.core.render.font_selection import collect_painted_italic_families
from dbt_charts.core.render.layout_sizing import RenderCache, build_chart_datasets
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.restated_auto import (
    auto_restated_variable_warnings,
    auto_restated_warnings,
)
from dbt_charts.core.render.sizing import resolve_active_tab_index
from dbt_charts.core.render.svg_utils import strip_pagination_chrome
from dbt_charts.core.render.tab_scope import scope_tabs
from dbt_charts.core.render.thumbnail import cartoonize
from dbt_charts.core.render.variables_resolve import resolve_option_values
from dbt_charts.core.render.warnings import (
    WarningContext,
    registry as _warnings_registry,
    run_all,
)
from dbt_charts.core.text.format_d3 import regional_scope
from dbt_charts.core.utils import Rows

_logger = logging.getLogger(__name__)

# Formats whose payload is a layout-tree walk instead of the drawn svg — every
# format still draws the board once (below) so the measurement-warning family
# and ERR-CHART-PAINTED-NO-MARKS are detected the same way regardless of format.
_DATA_FORMATS = frozenset({"json", "text", "text-data", "yaml", "data"})
# Formats whose payload prints every tab, not just the one a tabs layout shows.
_EVERY_TAB_FORMATS = _DATA_FORMATS | {"terminal"}


class DataFormatRenderer(Protocol):
    """Signature every data-bearing format renderer shares.

    Narrower than the implementations, deliberately: this dispatch always
    supplies a collector and a cap, so neither is optional here.
    """

    def __call__(
        self,
        board: Board,
        executor: Executor,
        variables: VariableValues,
        error_collector: list[Diagnostic],
        max_rows_per_query: int,
    ) -> str: ...


def _data_format_renderer(format: str) -> DataFormatRenderer:
    """Resolve a data-bearing format (other than ``text``) to its renderer.

    ``text`` is dispatched separately by its caller — ``render_board_text``
    alone accepts ``include_data``, which would break this shared Protocol's
    single signature for every other data-bearing format.

    Imported here rather than at module scope so the serializer modules stay
    off the import path of an SVG-only render.
    """
    from dbt_charts.core.render.data_format import render_board_data
    from dbt_charts.core.render.json_format import render_board_json
    from dbt_charts.core.render.yaml_format import render_board_yaml

    return {
        "json": render_board_json,
        "yaml": render_board_yaml,
        "data": render_board_data,
    }[format]


def _collect_active_layout_charts(
    board: ResolvedBoard, variables: VariableValues
) -> dict[str, tuple["ResolvedChart", float, float, TitleShift]]:
    """Map chart_id -> (the chart instance, its laid-out width, height and title
    shift) for
    the active-tab subtree.

    Reads each ``ResolvedLayoutItem`` directly rather than looking a chart id
    up in ``board.charts`` — the catalog holds one entry per id, but a shared
    chart id can carry several distinct ``ResolvedChart`` instances in the
    layout tree, each resolved at its own item's width
    (``_resolve_layout_item`` keys its variant cache by
    ``(chart_id, width, style)``). Reading the item directly makes
    ``resolution_width == width`` hold by construction for whatever is
    returned here — there is no catalog entry that can belong to a
    different placement than the one being judged.

    A tabs layout descends into only the active tab's item — like
    ``active_layout_items`` — instead of every tab. Zero-width items (an
    inactive tab or a collapsed ``details:`` chart, never laid out) are
    excluded outright; a chart placed twice at different real widths within
    the active subtree keeps its narrowest placement — that is where
    crowding shows.
    """
    charts: dict[str, tuple[ResolvedChart, float, float, TitleShift]] = {}

    def _walk(layout: ResolvedLayout) -> None:
        items = layout.items
        if layout.type == "tabs" and items:
            active = resolve_active_tab_index(
                len(items),
                layout.tab_variable,
                list(layout.tab_slugs),
                layout.default_tab,
                variables,
            )
            items = (items[active],)
        for item in items:
            if item.chart is not None and item.width:
                existing = charts.get(item.chart.id)
                if existing is None or item.width < existing[1]:
                    charts[item.chart.id] = (
                        item.chart,
                        item.width,
                        item.height,
                        item.title_shift,
                    )
            if item.board is not None:
                _walk(item.board.layout)

    _walk(board.layout)
    return charts


def _tab_variables(layout: Layout) -> set[str]:
    """Every tab variable in ``layout`` and the boards nested in it."""
    names = {layout.tab_variable} if layout.tab_variable else set()
    for item in layout.items:
        if item.board is not None:
            names |= _tab_variables(item.board.layout)
    return names


def _collect_render_warnings(
    board: ResolvedBoard,
    authored_chart_heights: dict[str, float],
    executor: Executor,
    variables: VariableValues,
    table_overflows: dict[str, TableOverflow],
    table_crampings: dict[str, TableCramping],
    table_numeric_align_lefts: dict[str, list[str]],
    table_align_no_visible_effects: dict[str, list[str]],
    text_truncations: dict[str, list[TextTruncation]],
    static_paginations: dict[str, StaticPagination],
    table_page_squeezes: dict[str, TablePageSqueeze],
    endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow],
    x_domain_paint_orders: dict[str, XDomainPaintOrder],
    annotation_nothing_drawn: NothingDrawn,
    plot_width_share_warnings: dict[str, PlotWidthShareWarning],
    pie_total_overflows: dict[str, PieTotalOverflow],
    contrast_warnings: list[ContrastRecord],
    geometry_warnings: GeometryWarnings,
    series_label_truncations: dict[str, list[SeriesLabelTruncation]],
    axis_label_collisions: dict[str, AxisLabelCollision],
    source_board: Board,
    restated_check: bool,
) -> list[Diagnostic]:
    """Build WarningContext from cached query results and run all detectors.

    Called after queries have already executed (results are in the executor
    cache), so every query run from here is a cache hit.

    This is warning diagnostics only, not the board render path — but a
    *geometry* detector must judge a chart against the width it actually
    renders at, so each chart's real layout width (from the already-computed
    ``board.layout`` tree) is passed through rather than falling back to the
    unrelated theme-family default; a *data* detector runs off the query
    result alone and does not care whether the chart has a width at all. See
    ``render/warnings/registry.py``'s module docstring for the split.

    ``authored_chart_heights`` is the snapshot ``build_resolved_board`` took of
    each chart's real assigned slot height, for charts under an authored
    ancestor, captured before cols-alignment could overwrite it — see
    ``layout_sizing._snapshot_authored_slot_heights``.
    """
    # Fast path: skip all spec/query work when no detectors are registered.
    # Avoids double-building vega specs on every SVG render for zero benefit.
    if not _warnings_registry.DETECTORS:
        return []

    # Restrict to charts the layout actually renders — same set that
    # execute_queries_parallel pre-executed.
    # Base-chart queries only: this walk never sees a layer's own `query:`, so
    # it is not a usable gate for one (see the layer_results loop below).
    pre_executed_query_names = collect_layout_chart_query_names(board)

    # A tabs layout's inactive-tab charts reach here only when the format walks
    # every tab (a drawn format empties them, `scope_tabs`). Then they are
    # data-resolved (ResolvedBoard.charts must agree with what record_board
    # records) but never laid out — the
    # render-first vl-convert measurement the active tab gets is exactly the
    # cost this skips, so these items (and a collapsed `details:` chart, sized
    # the same unmeasured way) keep width=height=0.0.
    #
    # That absence of a real width only invalidates GEOMETRY detectors —
    # truncation, band width, panel width — which judge
    # something that was never painted at any width. It says nothing about
    # DATA detectors (WARN-QUERY-RETURNED-ZERO-ROWS, formatter/currency
    # nudges, pie share/segment checks, …): the query still executed and
    # a genuine defect in its result is real regardless of whether the chart
    # is on-screen right now. See registry.py's module docstring for the
    # detector-kind split this gate exists to preserve.
    active_charts = _collect_active_layout_charts(board, variables)
    card_padding = float(board.style.frame.card_padding)

    chart_results: dict[str, Rows] = {}
    for chart_id, chart in board.charts.items():
        if chart.query_name not in pre_executed_query_names:
            continue
        # Unconditional on width: chart_results feeds data detectors, which
        # must run whether or not this chart is currently on-screen.
        # Omit failed charts so detectors can distinguish failure from genuine
        # zero rows: absent from chart_results = failed execute.
        with contextlib.suppress(Exception):  # noqa: BLE001 — query failure already recorded elsewhere
            if chart.query_name:
                # Re-use cached execute_query result; no re-execution.
                chart_results[chart_id] = executor.execute_query(
                    chart.query_name, variables
                )

    # chart id → layer query name → rows, for typed overlay layers whose own
    # `query:` differs from the base chart's (the shape the deterministic
    # migrator emits: one query per layer). chart_results holds only the base
    # query's rows, so a detector comparing series across layers has nowhere
    # else to read them. Every execute_query here is a cache hit — the
    # pre-execution pass walks `collect_all_query_names`, which descends into
    # layers, so every layout-reachable layer query has already run.
    layer_results: dict[str, dict[str, Rows]] = {}
    for chart_id in chart_results:
        chart = board.charts[chart_id]
        if not isinstance(chart, LayeredResolvedChart):
            continue
        for layer in chart.layers:
            # A layer with no authored override bakes query_name to the base
            # chart's own (see _resolve_one_layer) — those rows are already in
            # chart_results, so only a genuinely distinct query lands here.
            query_name = layer.query_name
            if query_name is None or query_name == chart.query_name:
                continue
            with contextlib.suppress(Exception):  # noqa: BLE001 — query failure already recorded elsewhere
                layer_results.setdefault(chart_id, {})[query_name] = (
                    executor.execute_query(query_name, variables)
                )

    # chart id → truncation record, for every chart whose query was cut by
    # execution.max_rows/max_result_bytes this render.
    query_truncations = executor.truncations()
    chart_query_names = {
        chart.query_name for chart in board.charts.values() if chart.query_name
    }
    chart_truncations = {
        chart_id: query_truncations[chart.query_name]
        for chart_id, chart in board.charts.items()
        if chart.query_name in query_truncations
    }
    # Truncations for queries not owned by any chart (e.g. upstream queries
    # demand-executed by cache-ref composition — only the composed query is
    # charted, not its upstreams). Surfaced as board-level warnings so the
    # author knows the data they see may be incomplete even though no chart
    # id maps to the affected query.
    unattributed_truncations = {
        name: info
        for name, info in query_truncations.items()
        if name not in chart_query_names
    }

    # A warning judged against an ancestor's authored height needs that height,
    # which the sizing and painted renders do not carry: those charts render once
    # more, with it, and that render replaces the chart's record from the earlier ones.
    rerendered: list[str] = []
    with (
        collect_geometry_warnings() as rerendered_geometry,
        collect_series_label_truncations() as rerendered_series_labels,
        collect_axis_label_collisions() as rerendered_axis_collisions,
    ):
        for chart_id, authored_height in authored_chart_heights.items():
            if chart_id not in chart_results or chart_id not in active_charts:
                continue
            chart, layout_width, layout_height, title_shift = active_charts[chart_id]
            if chart.chart_type in NON_ASPECT_RATIO_TYPES:
                continue
            # A render failure must not block the render: the main pass reports it.
            with contextlib.suppress(Exception):  # noqa: BLE001 — render failure is reported by the main pass
                render_resolved_chart(
                    chart,
                    chart_results[chart_id],
                    board.style,
                    width=layout_width,
                    height=layout_height,
                    padding=additive_padding(card_padding, chart.layout_padding),
                    datasets=build_chart_datasets(chart, executor, variables),
                    title_shift=title_shift,
                    authored_height=authored_height,
                )
                rerendered.append(chart_id)
    for chart_id in rerendered:
        geometry_warnings.pop(chart_id, None)
        series_label_truncations.pop(chart_id, None)
        axis_label_collisions.pop(chart_id, None)
    geometry_warnings.update(rerendered_geometry)
    series_label_truncations.update(rerendered_series_labels)
    axis_label_collisions.update(rerendered_axis_collisions)

    ctx = WarningContext(
        board_spec=board,
        chart_results=chart_results,
        layer_results=layer_results,
        geometry_warnings=geometry_warnings,
        table_overflows=table_overflows,
        table_crampings=table_crampings,
        table_numeric_align_lefts=table_numeric_align_lefts,
        table_align_no_visible_effects=table_align_no_visible_effects,
        static_paginations=static_paginations,
        table_page_squeezes=table_page_squeezes,
        authored_chart_heights=authored_chart_heights,
        layout_charts={
            chart_id: chart for chart_id, (chart, *_) in active_charts.items()
        },
        series_label_truncations=series_label_truncations,
        axis_label_collisions=axis_label_collisions,
        text_truncations=text_truncations,
        endpoint_label_gap_overflows=endpoint_label_gap_overflows,
        x_domain_paint_orders=x_domain_paint_orders,
        annotation_nothing_drawn=annotation_nothing_drawn,
        chart_truncations=chart_truncations,
        unattributed_truncations=unattributed_truncations,
        plot_width_share_warnings=plot_width_share_warnings,
        pie_total_overflows=pie_total_overflows,
        contrast_warnings=contrast_warnings,
    )
    warnings = run_all(ctx)
    if restated_check:
        warnings += auto_restated_warnings(
            source_board,
            {chart_id: width for chart_id, (_c, width, *_) in active_charts.items()},
            executor,
            variables,
        )
        warnings += auto_restated_variable_warnings(
            source_board, executor, variables, ""
        )
    return warnings


def _render_variables(
    board: Board,
    parsed_variables: VariableValues,
    builtin_variables: VariableValues | None,
    today: datetime.date,
) -> VariableValues:
    """The variable values a render runs every query and template with."""
    variable_registry = (
        board.variable_registry  # type-state: silent_fallback — a board without variables has an empty registry
        or {}
    )

    # Merge variables: start with None for all vars, then defaults, then user values
    all_variables: VariableValues = dict.fromkeys(variable_registry)
    all_variables.update(board.variable_defaults)  # Pre-computed by normalizer
    merged_variables = {**all_variables, **parsed_variables}

    # Inject caller-supplied built-in variables (e.g. dir-navigation context
    # computed by render_dashboard).  Builtins sit UNDER user-declared
    # variables so merged_variables can shadow any name.
    if builtin_variables is not None:
        merged_variables = {**builtin_variables, **merged_variables}

    # A multiselect is a list on every path. The seed above is `None` for any
    # variable without a default, and markdown/title Jinja reads this dict
    # directly — so without this, `{{ v | join(', ') }}` on an unset multiselect
    # raises and the whole board renders as an error. Queries get the same
    # narrowing from coerce_variable_values; this is the render half.
    return normalize_render_values(merged_variables, variable_registry, today)


def _prefetch_query_names(board: Board, variables: VariableValues) -> frozenset[str]:
    """Queries of the hidden tabs whose cache policy asks to be prefetched.

    ``prefetch`` is resolved per query at compile time, so a query is warmed
    only when its own policy has caching on and ``prefetch`` set.
    """
    hidden = collect_all_query_names(board) - collect_all_query_names(
        scope_tabs(board, variables)
    )
    return frozenset(name for name in hidden if board.queries[name].cache.prefetch)


def prefetch_tab_queries(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    max_workers: int | None = None,
    builtin_variables: VariableValues | None = None,
) -> dict[str, Exception | None]:
    """Run the queries of the hidden tabs that a drawn render left cold.

    A drawn render executes only the tabs it shows; this runs the queries of the
    rest whose cache policy sets ``prefetch: true``, so the click that opens one
    finds its rows in the result cache. Pass the variables and builtins the render
    got — the queries must render to the same SQL to land on the same cache
    keys. Cached queries are skipped, misses run in parallel through
    ``execute_queries_parallel`` with the executor's own per-query guards, and
    a failure is returned in the per-query outcome rather than raised: the
    click re-runs that query and reports it.
    """
    merged = _render_variables(
        board,
        parse_variable_json_strings(variables),
        builtin_variables,
        board.regional.today(executor.rendered_at),
    )
    misses = {
        name
        for name in _prefetch_query_names(board, merged)
        if not executor.is_cached(name, merged)
    }
    outcomes = execute_queries_parallel(
        executor, misses, merged, resolve_max_workers(max_workers)
    )
    for name, exc in outcomes.items():
        if exc is not None:
            _logger.warning("prefetch of query %r failed: %s", name, exc)
    return outcomes


def render(
    board: Board,
    executor: Executor,
    format: str = "svg",
    variables: VariableValues | None = None,
    ignore_codes: set[str] | None = None,
    builtin_variables: VariableValues | None = None,
    max_workers: int | None = None,
    warnings_ignore: frozenset[str] = frozenset(),
    max_rows_per_query: int = NO_ROW_CAP,
    strict_variables: bool = True,
    restated_check: bool = False,
    **options: Any,
) -> RenderResult:
    overrides = variables or {}  # type-state: silent_fallback — None: no overrides
    registry = board.variable_registry or {}  # type-state: silent_fallback — none
    committed = {**board.variable_defaults, **overrides}
    presets = daterange_preset_ids(committed, registry)
    with (
        regional_scope(
            board.regional.format_locale,
            board.regional.time_locale,
            board.regional.week_start,
            board.regional.zone,
            language=board.regional.resolved_language,
        ),
        daterange_presets(presets),
        board_clock(board.regional.today(executor.rendered_at)),
    ):
        return _render(
            board,
            executor,
            format,
            variables,
            ignore_codes,
            builtin_variables,
            max_workers,
            warnings_ignore,
            max_rows_per_query,
            strict_variables,
            restated_check,
            **options,
        )


def _render(
    board: Board,
    executor: Executor,
    format: str,
    variables: VariableValues | None,
    ignore_codes: set[str] | None,
    builtin_variables: VariableValues | None,
    max_workers: int | None,
    warnings_ignore: frozenset[str],
    max_rows_per_query: int,
    strict_variables: bool,
    restated_check: bool,
    **options: Any,  # type-state: explicit_any — render()'s own pass-through options
) -> RenderResult:
    """Render a compiled board.

    Stage: RENDER (Main Entry Point)

    This is the main rendering function. It walks the layout structure,
    renders each chart (triggering lazy query execution), and produces
    output in the requested format.

    Args:
        board: Compiled board to render
        executor: Executor for query execution
        format: Output format (svg, html, png, pdf, thumbnail, terminal, json, text, text-data, yaml, data)
        variables: Variable values for queries
        ignore_codes: Caller-supplied set of warning codes to suppress (CLI seam).
        builtin_variables: Pre-computed built-in variables (e.g. dir-navigation
            context from ``lazy_dir_context``) injected UNDER user variables so
            user-declared variables take precedence.  Computed by callers that
            have a real board file location (render_dashboard); omit for in-memory
            boards or inspect renders.
        warnings_ignore: Project-level warning codes to suppress. Callers resolve
            these before calling render() — typically via ``ProjectSession.warnings_ignore``
            or get_project_warnings_ignore(). Defaults to frozenset() (no suppression).
        restated_check: Re-render each chart without its authored keys to find
            ones that change nothing (WARN-RESTATED-*). Roughly doubles render
            time, so only authoring surfaces ask for it.
        max_rows_per_query: Cap on rows embedded per chart in the data-bearing
            formats (json/text/yaml); each truncation is declared in the output
            (``rows_truncated`` record / comment header). Defaults to
            ``NO_ROW_CAP`` (embed all rows). Other formats ignore it.
        **options: Format-specific options
            - background: Background color
            - scale: Scale factor (for png)
            - grid: Show grid overlay (for debugging)
            - controls: Host can re-run queries, so ship the control runtime
            - standalone: Output will be opened with no host, so carry the font
              bytes inline instead of naming /static/fonts/ (html and svg only)

    Returns:
        RenderResult with:
            - output: rendered content (str or bytes)
            - chart_errors: per-chart runtime failures (board still rendered)
            - board_error: post-validation fatal (None when render succeeded)
            - warnings: active (non-suppressed) warnings
            - suppressed_warnings: warnings dropped by any ignore layer

    Raises:
        RenderError: If a board-level invariant is violated before rendering starts
        FormatError: If format is unknown
    """
    # Compile warns on orphans; render hard-fails the empty-layout-with-charts
    # case so the dashboard never silently renders with nothing visible.
    if board.charts and not board.layout.items:
        from dbt_charts.core.diagnostics import ERR_NO_LAYOUT

        chart_list = ", ".join(sorted(board.charts.keys()))
        raise RenderError.from_code(ERR_NO_LAYOUT, charts=chart_list)

    # Trust the normalizer - use pre-computed variable_defaults
    variable_registry = board.variable_registry or {}
    parsed_variables = parse_variable_json_strings(variables or {})
    merged_variables = _render_variables(
        board, parsed_variables, builtin_variables, current_board_today()
    )

    # Board-level precondition: required variables must have a value before any query runs.
    if variable_registry:
        missing = [
            MissingVariable(
                key=key,
                label=var.label,
                notes=var.notes,
                input_type=var.input,
            )
            for key, var in variable_registry.items()
            if var.required is True
            and variable_value_is_absent(merged_variables.get(key))
        ]
        if missing:
            raise MissingRequiredVariablesError(missing)

    # Resolve max_workers: explicit param → DCT_MAX_WORKERS env var → config default.
    resolved_max_workers = resolve_max_workers(max_workers)

    # Pre-render query execution — authoritative, not a fallback.
    # Queries run BEFORE calculate_data_aware_layout so the sizing pass can
    # use cached results (render-first sizing reads query data from the executor
    # cache without re-executing).
    # Cache-hit pre-pass: queries already in cache are resolved synchronously
    # so they never occupy a worker thread or wait behind slow warehouse misses.
    # Only misses go to the ThreadPoolExecutor.
    # Errors are stored on the executor (executor._query_errors) so that
    # execute_chart() during the render walk raises the stored error
    # instead of re-executing.  The render walk never retries a failed query.
    #
    # A drawn format shows one tab per tabs layout, so it executes, resolves and
    # checks only those (`scope_tabs`); the board is rebound so nothing below
    # can reach a hidden tab's query. The formats that print every tab keep the
    # whole board. The width still hugs the whole board, so it never depends on
    # which tab is open.
    prefetch_query_names: frozenset[str] = frozenset()
    whole_board = board
    if format not in _EVERY_TAB_FORMATS:
        try:
            prefetch_query_names = _prefetch_query_names(board, merged_variables)
            board = scope_tabs(board, merged_variables)
        except DbtChartsError as e:
            return RenderResult(
                output=None,
                board_error=e.to_diagnostic(),
            )
    query_names = collect_all_query_names(board)
    query_started = time.perf_counter()

    # Partition: resolve hits synchronously, submit only misses to pool.
    # The probe runs twice for a hit (once here, once inside execute_query) but the
    # probe cost — dict lookup + optional duckdb sub-ms read — is negligible vs
    # the worker-slot wait it avoids on a saturated pool with slow warehouse queries.
    cache_miss_names: set[str] = set()
    for name in query_names:
        if executor.is_cached(name, merged_variables):
            executor.execute_query(name, merged_variables)
        else:
            cache_miss_names.add(name)

    execute_queries_parallel(
        executor, cache_miss_names, merged_variables, resolved_max_workers
    )
    query_ms = (time.perf_counter() - query_started) * 1000
    # Option queries have run for the board's own controls, so their rows cost
    # nothing more to check a query-backed select against. A tab
    # variable's options are its tabs: an unknown tab is ERR-TAB-UNKNOWN, raised
    # by the tabs layout with the slugs.
    if strict_variables:
        tab_variables = _tab_variables(whole_board.layout)
        check_option_values(
            {
                name: var
                for name, var in variable_registry.items()
                if name not in tab_variables
            },
            parsed_variables,
            lambda name, var: resolve_option_values(
                name, var, executor, merged_variables
            ),
        )

    # board.resolved_style is trusted here. Compile populated it; if a caller
    # mutated board.theme they must have gone through Board.set_theme(), which
    # re-cascades synchronously. Direct writes to board.theme are not supported.

    # Set auto-link context BEFORE calculate_data_aware_layout because the
    # render-first sizing pass resolves charts (layout_sizing._require_resolved),
    # which needs the context active to synthesize links.  The sizing-pass SVG
    # is cached and reused in the main render pass, so the context must be set here.
    from dbt_charts.core.render.board_links import (
        set_link_context as _set_link_context,  # noqa: PLC0415
    )
    from dbt_charts.core.render.chart.automatic_link import (  # noqa: PLC0415
        set_filter_variables_context as _set_filter_variables_context,
    )

    _link_context = options.get("link_context")
    _set_link_context(_link_context)
    _filter_var_names = (
        frozenset(variable_registry) if variable_registry else frozenset()
    )
    _set_filter_variables_context(_filter_var_names)

    # Per-chart error collector: single append site in render_chart_item.
    # All formats share this collector so callers get chart_errors regardless of format.
    error_collector: list[Diagnostic] = []
    authored_chart_heights: dict[str, float] = {}
    _sizing_truncations: dict[str, list[TextTruncation]] = {}
    _sizing_endpoint_label_gap_overflows: dict[
        str, EndpointLabelGapOverflow | None
    ] = {}
    _sizing_plot_width_share_warnings: dict[str, PlotWidthShareWarning] = {}
    _sizing_pie_total_overflows: dict[str, PieTotalOverflow] = {}

    # Every painted render of a chart records into these; the narrowest placement's set
    # is kept and sizing probes record none (see `claim_placement`).
    render_sinks = contextlib.ExitStack()
    geometry_warnings = render_sinks.enter_context(collect_geometry_warnings())
    series_label_truncations = render_sinks.enter_context(
        collect_series_label_truncations()
    )
    axis_label_collisions = render_sinks.enter_context(collect_axis_label_collisions())

    def _reset_contexts() -> None:
        _set_link_context(None)
        _set_filter_variables_context(frozenset())
        render_sinks.close()

    # Narrow first try: only build_resolved_board(). Failures here mean no render
    # work has started, so there is nothing to detect warnings against — return
    # empty lists directly rather than routing through _finalize_warnings.
    # A chart's own resolve failure no longer reaches this except at all: the
    # sizing pass records it per-chart and the tile renders as an error. Only a
    # sizing-pass failure that is not chart-scoped would land here.
    # Calculate layout with data awareness — table heights use actual row counts,
    # and Vega-Lite charts are rendered to get true heights (render-first sizing).
    # render_first=True unconditionally: a data-bearing format's own
    # layout-tree walk never computes real heights, so it needs the same ones
    # every other format gets from this pass.
    # Single-resolution pass: each chart is resolved once and the sizing pass
    # reuses that resolution rather than resolving a second time.
    from dbt_charts.core.render.board_resolve import build_resolved_board

    render_cache: RenderCache
    # Open the sizing-pass sinks before build_resolved_board so that chart
    # title truncations and callout overflow
    # (height cap) detected during render-first sizing are captured. Callout
    # is cached after the sizing pass; KPI/table/spark_bar re-render in the
    # main pass and are caught by the inner sink below.
    # The x-domain verdict is recorded by the VL emitter, which for a Vega
    # family runs *only* here — the main pass serves those charts from the
    # render cache — so this is the sink that has to catch it.
    # Endpoint-label gap overflow is recorded by the same emitter call, for
    # the same reason: a chart-root `height:` makes the sizing pass's render
    # fully authoritative (no clamping — see sizing.py's
    # get_chart_content_height), so the main pass reuses its cached SVG
    # instead of re-rendering, and a sink opened only around the main pass
    # never sees the recording.
    # Row-level `height:` and chart-root `height:` differ in exactly how many
    # times the chart renders, not just which pass records: row-level height
    # produces a main-pass cache miss (a genuine re-render, one recascade
    # call, landing in the main-pass sink below), while chart-root height's
    # sizing-pass render is also retried at more than one candidate height
    # before the main pass ever runs (aspect-ratio estimate, slot-height fix,
    # cols-alignment) — each retry recascades again, and only the *last* one
    # is the render that ships. `record_endpoint_label_gap_overflow` records
    # every recascade (including `fit`, as None) so the sink always reflects
    # only the most recent one — see endpoint_label_overflow.py.
    with (
        collect_text_truncations() as _sizing_truncations,
        collect_x_domain_paint_orders() as _sizing_x_domain_paint_orders,
        collect_annotation_nothing_drawn() as _sizing_annotation_nothing_drawn,
        collect_endpoint_label_gap_overflows() as _sizing_endpoint_label_gap_overflows,
        collect_plot_width_share_warnings() as _sizing_plot_width_share_warnings,
        collect_pie_total_overflows() as _sizing_pie_total_overflows,
        board_variables(merged_variables),
    ):
        try:
            resolved_board, render_cache = build_resolved_board(
                board,
                executor,
                merged_variables,
                render_first=True,
                authored_slot_heights=authored_chart_heights,
                hug_board=whole_board,
            )
        except DbtChartsError as e:
            _reset_contexts()
            return RenderResult(
                query_ms=query_ms,
                prefetch_query_names=prefetch_query_names,
                output=None,
                chart_errors=error_collector,
                payload_errors=error_collector,
                board_error=e.to_diagnostic(),
                warnings=[],
                suppressed_warnings=[],
            )
        except Exception as e:  # noqa: BLE001
            from dbt_charts.core.diagnostics import ERR_INTERNAL

            _reset_contexts()
            wrapped = RenderError.from_code(ERR_INTERNAL, message=str(e))
            return RenderResult(
                query_ms=query_ms,
                prefetch_query_names=prefetch_query_names,
                output=None,
                chart_errors=error_collector,
                payload_errors=error_collector,
                board_error=wrapped.to_diagnostic(),
                warnings=[],
                suppressed_warnings=[],
            )

    # resolved_board is now a plain ResolvedBoard for the rest of this function.

    # Run warning detectors now that queries are cached, partitioning into
    # active vs suppressed via the union of three ignore layers. finalize is
    # a closure, recomputed with the populated capture once the draw below
    # runs; the error paths above pass an empty capture instead, since no
    # draw happened there.
    _per_chart_codes: dict[str, set[str]] = {
        chart_id: set(chart.warnings_ignore)
        for chart_id, chart in board.charts.items()
        if chart.warnings_ignore
    }

    def _finalize_warnings(
        table_overflows: dict[str, TableOverflow],
        table_crampings: dict[str, TableCramping],
        table_numeric_align_lefts: dict[str, list[str]],
        table_align_no_visible_effects: dict[str, list[str]],
        text_truncations: dict[str, list[TextTruncation]],
        static_paginations: dict[str, StaticPagination],
        table_page_squeezes: dict[str, TablePageSqueeze],
        endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow],
        x_domain_paint_orders: dict[str, XDomainPaintOrder],
        annotation_nothing_drawn: NothingDrawn,
        plot_width_share_warnings: dict[str, PlotWidthShareWarning],
        pie_total_overflows: dict[str, PieTotalOverflow],
        contrast_warnings: list[ContrastRecord],
    ) -> tuple[list[Diagnostic], list[Diagnostic]]:
        all_warnings = _collect_render_warnings(
            resolved_board,
            authored_chart_heights,
            executor,
            merged_variables,
            table_overflows,
            table_crampings,
            table_numeric_align_lefts,
            table_align_no_visible_effects,
            text_truncations,
            static_paginations,
            table_page_squeezes,
            endpoint_label_gap_overflows,
            x_domain_paint_orders,
            annotation_nothing_drawn,
            plot_width_share_warnings,
            pie_total_overflows,
            contrast_warnings,
            geometry_warnings,
            series_label_truncations,
            axis_label_collisions,
            board,
            restated_check,
        )
        return _partition_warnings(
            all_warnings,
            cli_codes=ignore_codes or set(),
            project_codes=set(warnings_ignore),
            per_chart_codes=_per_chart_codes,
        )

    # Resolve SVG canvas background: API override wins, otherwise use the
    # cascaded board background (theme default or authored override, already merged).
    override = options.get("background")
    if override is not None:
        background = None if override == "transparent" else override
    else:
        resolved_bg = resolved_board.style.background
        background = None if resolved_bg == "transparent" else resolved_bg

    # Second try: draw the board once. resolved_board is guaranteed bound
    # here, so _finalize_warnings can run on error paths too. A data format's
    # own payload comes from a separate, lightweight layout-tree walk below
    # (`_data_format_renderer` — resolves each chart's metadata and embeds its
    # raw query rows, no spec, no drawing), and this pass's svg_content is
    # discarded for those formats — only the capture sinks and error_collector
    # populated below are kept.
    #
    # A board-level failure here does NOT return immediately: a data format's
    # walk below is independent of the draw (it never touches svg_content), so
    # it may still produce a real payload even though the draw that would
    # have populated warnings/error_collector failed. board_error is recorded
    # and the walk still runs; only the SVG-family formats (which have no
    # other source of output) return early on it, further down.
    svg_content: str | None = None
    board_error: Diagnostic | None = None
    render_warnings: list[Diagnostic] = []
    suppressed_warnings: list[Diagnostic] = []
    # Declared before the try: the except arm records board_error and falls
    # through, so these are read below even when the `with` never bound them.
    _table_overflows: dict[str, TableOverflow] = {}
    _table_crampings: dict[str, TableCramping] = {}
    _table_numeric_align_lefts: dict[str, list[str]] = {}
    _table_align_no_visible_effects: dict[str, list[str]] = {}
    _text_truncations: dict[str, list[TextTruncation]] = {}
    _static_paginations: dict[str, StaticPagination] = {}
    _table_page_squeezes: dict[str, TablePageSqueeze] = {}
    _endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow | None] = {}
    _x_domain_paint_orders: dict[str, XDomainPaintOrder] = {}
    _annotation_nothing_drawn: NothingDrawn = {}
    _plot_width_share_warnings: dict[str, PlotWidthShareWarning] = {}
    _pie_total_overflows: dict[str, PieTotalOverflow] = {}
    _contrast_warnings: list[ContrastRecord] = []
    try:
        # Render the canonical SVG once; output formats only wrap or convert it.
        # The capture sinks record each table's real post-cascade column overflow,
        # all text truncations, each table's static-export paging, and any
        # endpoint-label rail that couldn't fit its intended gap, so warnings
        # can be finalized against what actually rendered.
        grid_enabled = options.get("grid", False)
        margins_enabled = options.get("margins", False)
        # Interactivity is opt-in and only meaningful on a host that can re-run
        # the queries behind a control (`dct serve`, Cloud, Playground). The
        # controls are drawn either way — that is what keeps an export and a
        # live board the same picture — so what this switches on is the payload
        # a runtime needs and an artifact cannot use.
        # An artifact nobody serves has to carry its own fonts: `/static/fonts/…`
        # is root-relative and resolves only while a server is running, so a file
        # on disk, in a mailbox or in a bucket paints in fallback type at wrap
        # points measured for a font it isn't using. `standalone` is the caller
        # saying the output will be opened without a host — `dct render` writing
        # files sets it; a live host does not. `svg` is included because it is what
        # `dct render` writes by default, and a `.svg` opened from disk needs its
        # fonts exactly as much as an HTML page does. The visual goldens render
        # through `format="svg"` too but never set the flag, so they stay on URLs
        # and the golden tree carries no payloads.
        #
        # png/pdf are excluded whatever the flag says: they go through resvg, which
        # binds fonts from the registered font directory and never reads @font-face,
        # so base64 in the SVG it rasterizes would be bytes nothing reads.
        embed_fonts = format in ("html", "svg") and bool(options.get("standalone"))
        with (
            collect_table_overflows() as _table_overflows,
            collect_table_crampings() as _table_crampings,
            collect_table_numeric_align_lefts() as _table_numeric_align_lefts,
            collect_table_align_no_visible_effects() as _table_align_no_visible_effects,
            collect_text_truncations() as _text_truncations,
            collect_static_paginations() as _static_paginations,
            collect_table_page_squeezes() as _table_page_squeezes,
            collect_endpoint_label_gap_overflows() as _endpoint_label_gap_overflows,
            collect_x_domain_paint_orders() as _x_domain_paint_orders,
            collect_annotation_nothing_drawn() as _annotation_nothing_drawn,
            collect_plot_width_share_warnings() as _plot_width_share_warnings,
            collect_pie_total_overflows() as _pie_total_overflows,
            collect_contrast_warnings() as _contrast_warnings,
            collect_painted_italic_families(),
            interactive_controls(options.get("controls", False)),
        ):
            # No board_variables() scope here: render_board_svg opens its own
            # (boards.py), which also covers dct artifact render's replay
            # path (board_replay.py) -- both call render_board_svg directly,
            # so scoping there instead of here is what covers both callers,
            # not just this one.
            svg_content = render_board_svg(
                resolved_board,
                executor,
                merged_variables,
                background,
                grid_enabled,
                render_cache=render_cache,
                margins=margins_enabled,
                error_collector=error_collector,
                embed_fonts=embed_fonts,
            )
    except DbtChartsError as e:
        # Board-level fatal: a render-domain code escaped chart isolation
        # (e.g. ERR_NO_LAYOUT, MissingRequiredVariablesError). Surface as
        # board_error so callers see the structured code unwrapped. Do not
        # return here — a data format's walk below is independent of this
        # failed draw and may still produce a payload.
        board_error = e.to_diagnostic()
        # Render failed before a table could rasterize: finalize with an empty
        # capture so detector warnings still surface alongside the board error.
        render_warnings, suppressed_warnings = _finalize_warnings(
            {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, []
        )
    except Exception as e:  # noqa: BLE001
        from dbt_charts.core.diagnostics import ERR_INTERNAL

        board_error = RenderError.from_code(
            ERR_INTERNAL, message=str(e)
        ).to_diagnostic()
        render_warnings, suppressed_warnings = _finalize_warnings(
            {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, []
        )
    finally:
        _reset_contexts()

    if board_error is None:
        # The SVG render is done — finalize warnings against the real table overflow
        # and text truncations captured across both passes.
        # The render pass (_text_truncations) wins per chart-id when both passes
        # record the same chart (e.g. spark_bar). All SVG-family outputs below derive
        # from this same svg_content, so they share this capture.
        # Endpoint-label overflow entries are `None`-able (a chart's most recent
        # recascade fit): the merge lets a main-pass verdict — fit or not — fully
        # replace a sizing-pass one for a chart the main pass actually touched,
        # then None entries (nothing overflowed, or a stale sizing-pass trial
        # that a later fit cleared) are dropped before the detector ever sees
        # this dict, since WarningContext expects only genuine overflows.
        _merged_endpoint_label_gap_overflows = {
            **_sizing_endpoint_label_gap_overflows,
            **_endpoint_label_gap_overflows,
        }
        render_warnings, suppressed_warnings = _finalize_warnings(
            _table_overflows,
            _table_crampings,
            _table_numeric_align_lefts,
            _table_align_no_visible_effects,
            {**_sizing_truncations, **_text_truncations},
            # A data format ships no static file: its pagination is stated by
            # the payload itself (the text format's row/page line).
            {} if format in _DATA_FORMATS else _static_paginations,
            _table_page_squeezes,
            {
                chart_id: overflow
                for chart_id, overflow in _merged_endpoint_label_gap_overflows.items()
                if overflow is not None
            },
            {**_sizing_x_domain_paint_orders, **_x_domain_paint_orders},
            {**_sizing_annotation_nothing_drawn, **_annotation_nothing_drawn},
            {**_sizing_plot_width_share_warnings, **_plot_width_share_warnings},
            {**_sizing_pie_total_overflows, **_pie_total_overflows},
            _contrast_warnings,
        )

    if format in _DATA_FORMATS:
        # svg_content above ran purely to populate the capture sinks and
        # error_collector; the actual payload is this separate, lightweight
        # walk of the normalized layout tree, which fails per chart into its
        # own list (also embedded inline as `_error` in the payload, by
        # board_to_dict). The two streams are merged by chart rather than
        # concatenated: they enter the executor by different doors — the draw
        # through `execute_query` (keyed by query name), the walk through
        # `execute_chart` (keyed by chart id, and resolving runtime inputs on
        # the way) — so the same broken chart normally fails in both and
        # concatenating would report it twice. A chart that fails only in the
        # walk still has to reach `chart_errors`: it is what `board.py` reads
        # to call a render "partial", so dropping it would return status "ok"
        # for a payload that carries an error inline.
        #
        # The walk runs even when the draw above failed board-level
        # (board_error is set): it takes no input from the draw, so a draw
        # failure doesn't imply a walk failure. An exception escaping the
        # walk itself (e.g. a payload holding a value its serializer can't
        # handle) is caught here rather than left to propagate — a draw's
        # board_error, if one is already set, takes priority over a fresh one
        # from the walk, since the draw's failure is the more fundamental one.
        _data_format_errors: list[Diagnostic] = []
        try:
            # 'text' and 'text-data' both render through render_board_text,
            # called directly rather than through the shared
            # DataFormatRenderer Protocol dispatch (which json/yaml/data's
            # renderers use) since only render_board_text accepts
            # include_data — 'text-data' is exactly 'text' with that True.
            if format in ("text", "text-data"):
                from dbt_charts.core.render.text_format import render_board_text

                output = render_board_text(
                    board,
                    executor,
                    merged_variables,
                    error_collector=_data_format_errors,
                    max_rows_per_query=max_rows_per_query,
                    include_data=(format == "text-data"),
                    static_paginations=_static_paginations,
                )
            else:
                output = _data_format_renderer(format)(
                    board,
                    executor,
                    merged_variables,
                    error_collector=_data_format_errors,
                    max_rows_per_query=max_rows_per_query,
                )
        except DbtChartsError as e:
            output = None
            if board_error is None:
                board_error = e.to_diagnostic()
        except Exception as e:  # noqa: BLE001
            from dbt_charts.core.diagnostics import ERR_INTERNAL

            output = None
            if board_error is None:
                board_error = RenderError.from_code(
                    ERR_INTERNAL, message=str(e)
                ).to_diagnostic()
        if format in ("text", "text-data") and output is not None:
            # Warnings live in render_warnings (computed above, outside this
            # format-specific walk) — appended here rather than threaded into
            # render_board_text so every _DATA_FORMATS renderer keeps the same
            # DataFormatRenderer signature.
            from dbt_charts.core.render.text_format import render_warnings_section

            output += render_warnings_section(render_warnings)
        # Keyed on (fields["chart_id"], code) — chart_id is the identity
        # channel both streams stamp (chart_diagnostics.stamp_chart_diagnostic;
        # `Diagnostic.chart` is not set on either side of this merge), but a
        # chart id is unique only within one board, not across a nested-board
        # tree (data_format.py), so two *different* charts sharing an id
        # would collide on chart_id alone.
        _drawn_errors = {(d.fields.get("chart_id"), d.code) for d in error_collector}
        error_collector.extend(
            d
            for d in _data_format_errors
            if (d.fields.get("chart_id"), d.code) not in _drawn_errors
        )
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=output,
            chart_errors=error_collector,
            payload_errors=_data_format_errors,
            board_error=board_error,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    if board_error is not None:
        # No fallback payload for the SVG-family formats (svg/html/png/pdf) —
        # the payload IS the drawing this pass failed to produce. terminal is
        # grouped here too even though its own payload is actually an
        # independent walk, same shape as a data format's.
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=None,
            chart_errors=error_collector,
            payload_errors=error_collector,
            board_error=board_error,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )
    if svg_content is None:
        raise AssertionError("board_error is None only when the draw succeeded")

    # Convert to requested format. svg/html/png/pdf: the payload IS the
    # drawing (svg_content), so payload_errors always equals chart_errors.
    # terminal is the exception — see the board_error branch above.
    if format == "svg":
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=svg_content,
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "html":
        # 'background' is an SVG canvas override callers pass through
        # options, not a to_html() option — to_html() reads the HTML page
        # background separately, off the rendered SVG's own
        # data-dbt-page-background attribute, unaffected by this override.
        html_options = {k: v for k, v in options.items() if k != "background"}
        html_output = to_html(
            svg_content,
            **html_options,
        )
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=html_output,
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "png":
        png_scale = options.get("scale")
        if png_scale is None:
            png_scale = get_rendering_config().png.scale
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=to_png(strip_pagination_chrome(svg_content), png_scale),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "pdf":
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=to_pdf(strip_pagination_chrome(svg_content)),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "thumbnail":
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=cartoonize(strip_pagination_chrome(svg_content)),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "terminal":
        return RenderResult(
            query_ms=query_ms,
            prefetch_query_names=prefetch_query_names,
            output=_to_terminal(
                board, executor, merged_variables, resolved_board.style, **options
            ),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    else:
        from dbt_charts.core.diagnostics import ERR_FORMAT_UNSUPPORTED

        raise FormatError.from_code(ERR_FORMAT_UNSUPPORTED, format=format)


def _to_terminal(
    board: Board,
    executor: Executor,
    variables: VariableValues,
    resolved_style: "ResolvedStyle",
    **options: Any,
) -> str:
    """Render board to terminal output.

    Args:
        board: Board to render
        executor: Executor for query execution
        variables: Variable values for queries
        resolved_style: Board-scoped resolved style (from resolved_board.style).
        **options: Terminal-specific options
            - width: Terminal width in characters
            - height: Terminal height in characters
            - colors: Whether to use ANSI colors (default: True)

    Returns:
        Terminal-formatted string
    """
    from dbt_charts.core.render.terminal import render_board_terminal

    return render_board_terminal(
        board,
        executor,
        variables=variables,
        width=options.get("width"),
        height=options.get("height"),
        colors=options.get("colors", True),
        resolved_style=resolved_style,
        **{k: v for k, v in options.items() if k not in ("width", "height", "colors")},
    )


__all__ = [
    "render",
]
