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
    VariableValues,
)
from dbt_charts.core.compile.models.board.resolved import (
    ResolvedBoard,
    ResolvedLayout,
)
from dbt_charts.core.compile.models.chart.normalized import NON_ASPECT_RATIO_TYPES
from dbt_charts.core.compile.models.chart.resolved._layer import LayeredResolvedChart
from dbt_charts.core.compile.template.variables import (
    normalize_multiselect_values,
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
from dbt_charts.core.render.board_variables import board_variables
from dbt_charts.core.render.boards import render_board_svg
from dbt_charts.core.render.chart.axis_label_collision import (
    collect_axis_label_collisions,
)
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
    collect_endpoint_label_gap_overflows,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    PlotWidthShareWarning,
    collect_plot_width_share_warnings,
)
from dbt_charts.core.render.chart.series_label_truncation import (
    collect_series_label_truncations,
)
from dbt_charts.core.render.chart.table import strip_pagination_chrome
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
    StaticPaginationCap,
    collect_static_pagination_caps,
)
from dbt_charts.core.render.chart.text_truncation import (
    TextTruncation,
    collect_text_truncations,
)
from dbt_charts.core.render.chart.x_domain_paint_order import (
    XDomainPaintOrder,
    collect_x_domain_paint_orders,
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
from dbt_charts.core.render.layout_sizing import RenderCache
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.sizing import resolve_active_tab_index
from dbt_charts.core.render.warnings import (
    WarningContext,
    registry as _warnings_registry,
    run_all,
)
from dbt_charts.core.utils import Rows

# Formats whose payload is a layout-tree walk instead of the drawn svg — every
# format still draws the board once (below) so the measurement-warning family
# and ERR-CHART-PAINTED-NO-MARKS are detected the same way regardless of format.
_DATA_FORMATS = frozenset({"json", "text", "text-data", "yaml", "data"})


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
) -> dict[str, tuple["ResolvedChart", float, float]]:
    """Map chart_id -> (the chart instance, its laid-out width and height) for
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
    charts: dict[str, tuple[ResolvedChart, float, float]] = {}

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
                    charts[item.chart.id] = (item.chart, item.width, item.height)
            if item.board is not None:
                _walk(item.board.layout)

    _walk(board.layout)
    return charts


def _collect_render_warnings(
    board: ResolvedBoard,
    authored_chart_heights: dict[str, float],
    executor: Executor,
    variables: VariableValues,
    table_overflows: dict[str, TableOverflow],
    table_crampings: dict[str, TableCramping],
    text_truncations: dict[str, list[TextTruncation]],
    static_pagination_caps: dict[str, StaticPaginationCap],
    table_page_squeezes: dict[str, TablePageSqueeze],
    endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow],
    x_domain_paint_orders: dict[str, XDomainPaintOrder],
    plot_width_share_warnings: dict[str, PlotWidthShareWarning],
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

    # A tabs layout's inactive-tab charts are data-resolved (ResolvedBoard.charts
    # must agree with what record_board records) but never laid out — the
    # render-first vl-convert measurement the active tab gets is exactly the
    # cost this skips, so these items (and a collapsed `details:` chart, sized
    # the same unmeasured way) keep width=height=0.0.
    #
    # That absence of a real width only invalidates GEOMETRY detectors —
    # truncation, category counts, band width, panel width — which judge
    # something that was never painted at any width. It says nothing about
    # DATA detectors (WARN-QUERY-RETURNED-ZERO-ROWS, formatter/currency
    # nudges, pie share/segment checks, …): the query still executed and
    # a genuine defect in its result is real regardless of whether the chart
    # is on-screen right now. See registry.py's module docstring for the
    # detector-kind split this gate exists to preserve.
    active_charts = _collect_active_layout_charts(board, variables)

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

    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

    vega_specs: dict[str, dict[str, Any]] = {}
    # Open a fresh sink so VL chart spec-generation here (for the detection
    # pass) captures axis_title / chart_title truncations. This is distinct
    # from the sizing-pass sink (``text_truncations`` param) — both are needed:
    # sizing-pass covers the SVG render path (specs cached, not regenerated),
    # and this detection-pass sink covers the json/data render path (sizing
    # pass skips VL rendering). The two are merged below.
    with (
        collect_series_label_truncations() as series_label_truncations,
        collect_text_truncations() as _detection_truncations,
        collect_plot_width_share_warnings() as _detection_plot_width_share_warnings,
        collect_axis_label_collisions() as axis_label_collisions,
    ):
        for chart_id, (chart, layout_width, _layout_height) in active_charts.items():
            if chart.query_name not in pre_executed_query_names:
                continue
            # Non-VL families (kpi, table, spark_bar, callout) never produce a
            # vega_spec artifact — skip them to avoid an extra render pass.
            if chart.chart_type in NON_ASPECT_RATIO_TYPES:
                continue
            data = chart_results.get(chart_id, [])
            # Spec generation failure must not block the render — omit from vega_specs.
            with contextlib.suppress(Exception):  # noqa: BLE001 — spec failure must not block render
                artifact = render_resolved_chart(
                    chart,
                    data,
                    board.style,
                    width=layout_width,
                )
                if artifact.kind == "vega_spec" and isinstance(artifact.payload, dict):
                    vega_specs[chart_id] = artifact.payload

    # Merge sizing-pass truncations with detection-pass truncations per
    # (surface, authored_field), detection winning on a collision — the two
    # passes can render the same chart at slightly different widths, so a
    # whole-list overwrite could drop a record the other pass didn't produce.
    merged_truncations = dict(text_truncations)
    for chart_id, records in _detection_truncations.items():
        existing = {
            (r.surface, r.authored_field): r
            for r in merged_truncations.setdefault(chart_id, [])
        }
        for r in records:
            existing[(r.surface, r.authored_field)] = r
        merged_truncations[chart_id] = list(existing.values())

    ctx = WarningContext(
        board_spec=board,
        chart_results=chart_results,
        layer_results=layer_results,
        vega_specs=vega_specs,
        table_overflows=table_overflows,
        table_crampings=table_crampings,
        static_pagination_caps=static_pagination_caps,
        table_page_squeezes=table_page_squeezes,
        authored_chart_heights=authored_chart_heights,
        layout_chart_heights={
            chart_id: height for chart_id, (_c, _w, height) in active_charts.items()
        },
        layout_charts={
            chart_id: chart for chart_id, (chart, _w, _h) in active_charts.items()
        },
        series_label_truncations=series_label_truncations,
        axis_label_collisions=axis_label_collisions,
        text_truncations=merged_truncations,
        endpoint_label_gap_overflows=endpoint_label_gap_overflows,
        x_domain_paint_orders=x_domain_paint_orders,
        chart_truncations=chart_truncations,
        unattributed_truncations=unattributed_truncations,
        plot_width_share_warnings={
            **plot_width_share_warnings,
            **_detection_plot_width_share_warnings,
        },
    )
    return run_all(ctx)


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
    **options: Any,
) -> RenderResult:
    """Render a compiled board.

    Stage: RENDER (Main Entry Point)

    This is the main rendering function. It walks the layout structure,
    renders each chart (triggering lazy query execution), and produces
    output in the requested format.

    Args:
        board: Compiled board to render
        executor: Executor for query execution
        format: Output format (svg, html, png, pdf, terminal, json, text, text-data, yaml, data)
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

    # Merge variables: start with None for all vars, then defaults, then user values
    all_variables: dict[str, Any] = dict.fromkeys(variable_registry)
    all_variables.update(board.variable_defaults)  # Pre-computed by normalizer
    # Parse JSON strings in variables (from URL parameters) and merge
    parsed_variables = parse_variable_json_strings(variables or {})
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
    merged_variables = normalize_multiselect_values(merged_variables, variable_registry)

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
    query_names = collect_all_query_names(board)

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
    from dbt_charts.core.render.chart.auto_link import (  # noqa: PLC0415
        set_auto_link_context as _set_auto_link_context,
        set_filter_variables_context as _set_filter_variables_context,
    )

    _link_context = options.get("link_context")
    _set_link_context(_link_context)
    _set_auto_link_context(board.auto_link)
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

    def _reset_contexts() -> None:
        _set_link_context(None)
        _set_auto_link_context(False)
        _set_filter_variables_context(frozenset())

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
    # Open the sizing-pass sinks before build_resolved_board so that VL chart
    # title truncations (apply_title_overflow_to_spec) and callout overflow
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
        collect_endpoint_label_gap_overflows() as _sizing_endpoint_label_gap_overflows,
        collect_plot_width_share_warnings() as _sizing_plot_width_share_warnings,
        board_variables(merged_variables),
    ):
        try:
            resolved_board, render_cache = build_resolved_board(
                board,
                executor,
                merged_variables,
                render_first=True,
                authored_slot_heights=authored_chart_heights,
            )
        except DbtChartsError as e:
            _reset_contexts()
            return RenderResult(
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
        text_truncations: dict[str, list[TextTruncation]],
        static_pagination_caps: dict[str, StaticPaginationCap],
        table_page_squeezes: dict[str, TablePageSqueeze],
        endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow],
        x_domain_paint_orders: dict[str, XDomainPaintOrder],
        plot_width_share_warnings: dict[str, PlotWidthShareWarning],
    ) -> tuple[list[Diagnostic], list[Diagnostic]]:
        all_warnings = _collect_render_warnings(
            resolved_board,
            authored_chart_heights,
            executor,
            merged_variables,
            table_overflows,
            table_crampings,
            text_truncations,
            static_pagination_caps,
            table_page_squeezes,
            endpoint_label_gap_overflows,
            x_domain_paint_orders,
            plot_width_share_warnings,
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
    # Declared here rather than inside the try below so they stay defined for
    # the `if board_error is None:` read further down on every path — mypy's
    # possibly-undefined check can't otherwise prove they survive a `with`
    # target that a chained context manager's own __enter__ could in theory
    # raise before assigning.
    _table_overflows: dict[str, TableOverflow] = {}
    _table_crampings: dict[str, TableCramping] = {}
    _text_truncations: dict[str, list[TextTruncation]] = {}
    _static_pagination_caps: dict[str, StaticPaginationCap] = {}
    _table_page_squeezes: dict[str, TablePageSqueeze] = {}
    _endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow | None] = {}
    _x_domain_paint_orders: dict[str, XDomainPaintOrder] = {}
    _plot_width_share_warnings: dict[str, PlotWidthShareWarning] = {}
    try:
        # Render the canonical SVG once; output formats only wrap or convert it.
        # The capture sinks record each table's real post-cascade column overflow,
        # all text truncations, the static-export page cap, and any endpoint-label
        # rail that couldn't fit its intended gap, so warnings can be finalized
        # against what actually rendered.
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
            collect_text_truncations() as _text_truncations,
            collect_static_pagination_caps() as _static_pagination_caps,
            collect_table_page_squeezes() as _table_page_squeezes,
            collect_endpoint_label_gap_overflows() as _endpoint_label_gap_overflows,
            collect_x_domain_paint_orders() as _x_domain_paint_orders,
            collect_plot_width_share_warnings() as _plot_width_share_warnings,
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
            {}, {}, {}, {}, {}, {}, {}, {}
        )
    except Exception as e:  # noqa: BLE001
        from dbt_charts.core.diagnostics import ERR_INTERNAL

        board_error = RenderError.from_code(
            ERR_INTERNAL, message=str(e)
        ).to_diagnostic()
        render_warnings, suppressed_warnings = _finalize_warnings(
            {}, {}, {}, {}, {}, {}, {}, {}
        )
    finally:
        _reset_contexts()

    if board_error is None:
        # The SVG render is done — finalize warnings against the real table overflow,
        # text truncations, and static-pagination cap captured across both passes.
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
            {**_sizing_truncations, **_text_truncations},
            _static_pagination_caps,
            _table_page_squeezes,
            {
                chart_id: overflow
                for chart_id, overflow in _merged_endpoint_label_gap_overflows.items()
                if overflow is not None
            },
            {**_sizing_x_domain_paint_orders, **_x_domain_paint_orders},
            {**_sizing_plot_width_share_warnings, **_plot_width_share_warnings},
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
            output=to_png(strip_pagination_chrome(svg_content), png_scale),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "pdf":
        return RenderResult(
            output=to_pdf(strip_pagination_chrome(svg_content)),
            chart_errors=error_collector,
            payload_errors=error_collector,
            warnings=render_warnings,
            suppressed_warnings=suppressed_warnings,
        )

    elif format == "terminal":
        return RenderResult(
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
