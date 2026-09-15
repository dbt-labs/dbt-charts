"""Bar and histogram chart resolvers."""

from __future__ import annotations

from typing import Literal

from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.format import resolve_label_format
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.authored._support_table import (
    ChartSupportTablePerSeries,
)
from dbt_charts.core.compile.models.chart.normalized import (
    BarChart,
)
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedBarChart,
)
from dbt_charts.core.compile.models.style.authored import EndpointLabelsConfig
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedBarStyle
from dbt_charts.core.compile.models.style.theme import (
    AxisXStyle,
    AxisYStyle,
    BarLabelsStyle,
)
from dbt_charts.core.compile.resolve.chart._axes import (
    _author_asked_for_endpoint_labels,
    _authored_legend,
    _bake_ay_orient,
    _bake_ay_position_left,
    _edge_or_none,
    _endpoint_labels_off_for_layers,
    _endpoint_labels_off_for_multiples,
    _reject_dual_axis_layered_endpoint_labels,
    cartesian_color_domain_values,
    cartesian_series_naming,
    cartesian_top_legend_entries,
    estimate_cartesian_plot_height,
    estimate_left_axis_reserve_px,
    legend_wrap_marginal_height_px,
    legend_wrap_required_height_px,
)
from dbt_charts.core.compile.resolve.chart._channels import (
    _bar_orientation,
    _classify_to_channel_type,
)
from dbt_charts.core.compile.resolve.chart._chart_rows import (
    ChartDataset,
    ChartRows,
    LayerDatasets,
    PanelRows,
    fold_panels,
    partition,
    restamp,
)
from dbt_charts.core.compile.resolve.chart._domain import (
    _authored_axis_y_ticks_count,
    _axis_headroom,
    _bake_normalize_domain,
    _CartesianTickResolution,
    _first_non_numeric_y,
    _numeric_y_values,
    _resolve_cartesian_ticks,
    _resolve_stacked_bar_ticks,
    _shared_y_values,
)
from dbt_charts.core.compile.resolve.chart._kwargs import (
    AutomaticLinkCandidate,
    ChartTextVariables,
    _base_kwargs,
    _cartesian_kwargs,
    _title_font,
)
from dbt_charts.core.compile.resolve.chart._layers import (
    _check_layers_y_domain,
    _resolve_layer_list,
)
from dbt_charts.core.compile.resolve.chart._marks import (
    _label_format_fallback,
    _measure_tooltip_format,
)
from dbt_charts.core.compile.resolve.chart._palette import (
    _effective_palette,
    _effective_requested_alias_palette,
    _effective_single_series_fill,
    _resolved_series_label,
)
from dbt_charts.core.compile.resolve.chart._plan import (
    build_cartesian_axes,
    plan_cartesian,
    quantitative_channel_values,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
    bake_wide_measures_kwargs,
    humanize_wide_series_name,
    raw_wide_series_names,
    resolve_wide_measure_channels,
    unfold_wide_rows,
    wide_measure_labels_for,
)
from dbt_charts.core.compile.resolve.chart.plot_height_floor import (
    estimate_plot_height,
    plot_height_floor_px,
)
from dbt_charts.core.compile.resolve.chart.tick_values import (
    apply_headroom,
    numeric_domain_bounds,
    stacked_totals_max,
)
from dbt_charts.core.compile.resolve.style.chart_context import (
    build_chart_style_context,
)
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_BAR_LOG_SCALE_NOT_SUPPORTED,
    ERR_BAR_Y_NOT_NUMERIC,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.text.format_d3 import is_d3_si_spec
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    bar_sort_op,
    cap_padding_to_label_limit,
    cumulative_stack_midpoints,
    layered_endpoint_rail_fires,
    layered_endpoint_rail_shape,
    measured_label_padding,
    numeric_column_values,
    x_domain_order,
)

__all__ = [
    "_resolve_bar",
    "_resolve_histogram",
]


def _bar_endpoint_labels_for_stack(
    endpoint_labels: EndpointLabelsConfig,
    normalized: BarChart,
    data: ChartRows,
    stack_mode: str,
    horizontal: bool,
    author_opted_in: bool,
    width: float,
    ax_merged: AxisXStyle,
    ay_merged: AxisYStyle,
    stack_order: str | None,
    font_family: str,
    font_size: float,
) -> EndpointLabelsConfig:
    """Direct labeling is the default; the disqualifiers below turn it back off.

    Every disqualifier here steers the *default* away from a shape it would
    render badly or not at all. An author who wrote
    ``style.endpoint_labels.visible: true`` on this chart has said they want
    the rail on that shape, so ``author_opted_in`` returns the cascaded value
    untouched: grouped bars label at their bar tops as they always have, and
    the shapes render refuses (a negative measure, a sorted or center-stacked
    horizontal rail, ``multiples:``) raise there with a message naming the
    conflict. Quietly dropping the rail instead would be neither.
    """
    if author_opted_in:
        return endpoint_labels
    if normalized.support_table is not None and any(
        isinstance(entry, ChartSupportTablePerSeries) and not entry.by_measure
        for entry in normalized.support_table.entries
    ):
        # A per-series support table already prints one row per series, labeled
        # in that series' own ink — the rail would name them a second time,
        # and it costs the plot the height and the axis side it needs.
        return endpoint_labels.model_copy(update={"visible": False})
    if stack_mode == "none":
        # Grouped: every series' last bar rises from the same baseline to a
        # similar height, so the collision cascade squashes the labels
        # together. A legend is the honest treatment.
        return endpoint_labels.model_copy(update={"visible": False})
    endpoint_labels = _endpoint_labels_off_for_multiples(
        endpoint_labels, normalized, author_opted_in
    )
    if not endpoint_labels.visible:
        return endpoint_labels
    y_fields = (
        (normalized.y,)
        if isinstance(normalized.y, str)
        else tuple(normalized.y)
        if isinstance(normalized.y, list)
        else ()
    )
    # A negative (zero-crossing) measure breaks the cumulative-midpoint
    # computation; the render layer raises rather than mislabel. Stack mode
    # zero/normalize/center all reach that raise, so this check runs
    # regardless of which one resolved_stack picked.
    if any(v < 0 for v in _numeric_y_values(data, y_fields)):
        return endpoint_labels.model_copy(update={"visible": False})
    # author_opted_in is False by construction here — the opt-in returned above.
    endpoint_labels = _endpoint_labels_off_for_layers(
        endpoint_labels, normalized, author_opted_in
    )
    if not endpoint_labels.visible:
        return endpoint_labels
    if normalized.sort is not None and not numeric_column_values(
        data, normalized.sort.by
    ):
        # Conservative rather than necessary: `bar_sort_op` pins `min`, which
        # Vega compares natively on strings and dates, so this refuses a shape
        # the engine could order.
        return endpoint_labels.model_copy(update={"visible": False})
    if horizontal and stack_mode == "center":
        # The horizontal rail anchors on the cumulative (0..Σ) axis, which the
        # diverging center-stack domain doesn't have.
        return endpoint_labels.model_copy(update={"visible": False})
    if not _any_column_stacks_more_than_one_series(normalized, data, y_fields):
        return endpoint_labels.model_copy(update={"visible": False})
    if horizontal and not _every_series_reaches_the_anchor_row(
        normalized, data, y_fields
    ):
        return endpoint_labels.model_copy(update={"visible": False})
    if horizontal and _horizontal_rail_labels_would_collide(
        normalized,
        data,
        y_fields,
        stack_mode,
        width,
        ax_merged,
        ay_merged,
        stack_order,
        font_family,
        font_size,
    ):
        return endpoint_labels.model_copy(update={"visible": False})
    return endpoint_labels


def _every_series_reaches_the_anchor_row(
    normalized: BarChart,
    data: ChartRows,
    y_fields: tuple[str, ...],
) -> bool:
    """True when the horizontal rail's anchor row carries a segment for every series.

    The vertical rail can seat a series that is absent from its anchor column on
    the zero-height seam between its neighbors, because the label cascade then
    pushes it clear. The horizontal rail has no such resolver: a series absent
    from the anchor row anchors on a zero-width seam, sitting exactly at a
    neighbor's segment edge — a fragile position a later data refresh can
    turn into an overprint even when today's snapshot happens to have room.
    This is disqualified unconditionally, independent of what
    ``_horizontal_rail_labels_would_collide`` measures for the current render:
    that check only ever sees the one snapshot in front of it, not the shape
    of the data going forward.

    Steering the default back to a legend is the honest treatment. An explicit
    ``endpoint_labels.visible: true`` still reaches the seam math, which is
    correct and tested — it is only unsafe to *choose* unprompted.
    """
    color = normalized.color
    x = normalized.x
    if not isinstance(color, str) or not isinstance(x, str):
        return True
    sort = normalized.sort
    sort_by = sort.by if sort else ""
    # Only reached past _bar_endpoint_labels_for_stack's stack_mode == "none"
    # return, so the mark stacks. The measure mirrors what the emitter pins
    # against (``emitters/bar.py``): a wide chart's is a synthetic fold field,
    # so it never reads as a sort by the measure.
    domain = x_domain_order(
        data,
        x,
        sort_by,
        bool(sort and sort.order == "desc"),
        op=bar_sort_op(
            sort_by,
            normalized.y if isinstance(normalized.y, str) else None,
            stacked=True,
        ),
    )
    if not domain:
        return True
    anchor_row = domain[0]
    if isinstance(normalized.y, list):
        # Wide + dimension: the series are the composites, and a measure
        # null at the anchor row is exactly the missing segment this
        # guards. RAW composites, matching unfold_wide_rows's own
        # (unhumanized) WIDE_LABEL_FIELD stamp -- a humanized name would
        # never match `drawn_at_anchor` here.
        every_series = set(raw_wide_series_names(y_fields, color, data))
        drawn_at_anchor = {
            str(row[WIDE_LABEL_FIELD])
            for row in unfold_wide_rows(data, y_fields, color)
            if row.get(x) == anchor_row
        }
        return drawn_at_anchor == every_series
    every_series = set()
    drawn_at_anchor = set()
    for row in data:
        series = row.get(color)
        if series is None:
            continue
        every_series.add(str(series))
        if row.get(x) == anchor_row and any(
            row.get(field) is not None for field in y_fields
        ):
            drawn_at_anchor.add(str(series))
    return drawn_at_anchor == every_series


def _stacked_measure_domain_span(
    ay: AxisYStyle,
    rows: PanelRows,
    x_field: str,
    y_fields: list[str],
) -> tuple[float, float] | None:
    """The stacked measure axis's rendered ``(lo, hi)`` span — authored domain wins.

    Mirrors the emitter (``emitters/bar.py`` gates every ``domainMax`` on
    ``authored_measure_domain(ay) is None`` and puts an authored domain
    straight onto the scale): an author-pinned
    ``style.axis_y.scale.continuous.domain`` replaces the stacked-total
    derivation outright, never blends with it. Falls through to ``(0.0,
    stacked total)`` (headroom-applied) otherwise.

    Used only by the horizontal rail's collision check, which needs the real
    ``(lo, hi)`` span its pixel math divides by. Deliberately NOT used for
    ``_resolve_bar``'s own ``stacked_domain_max`` bake — that field feeds
    render-time hover-band sizing on every stacked bar, vertical included,
    and widening it to read an authored domain would change that unrelated
    behavior; the bake keeps its original stacked-total-only derivation.

    ``None`` for a degenerate span: a non-positive authored width (``hi <=
    lo`` — e.g. ``domain: [0, 0]``) or a non-positive/absent stacked total
    (an all-null or all-zero top column). Either way, nothing renders a real
    span to divide by.
    """
    if ay.scale is not None and ay.scale.continuous is not None:
        authored = numeric_domain_bounds(ay.scale.continuous.domain)
        if authored is not None:
            lo, hi = min(authored), max(authored)
            return (lo, hi) if hi > lo else None
    raw_max = stacked_totals_max(rows, x_field, y_fields)
    if raw_max is None or raw_max <= 0:
        return None
    return 0.0, apply_headroom(raw_max, _axis_headroom(ay))


def _categorical_axis_gutter_px(
    ax: AxisXStyle,
    data: ChartRows,
    x_field: str,
) -> float:
    """Real width the horizontal bar's left categorical-label gutter eats.

    The rail and chart panes share the x scale, but its own plot span is
    narrower than the card's outer width by however much the left-hand
    category axis (VL y for a horizontal bar) spends on its own tick
    labels — long category names visibly eat into the room left for the
    rail (and the labels the rail measures against). ``0.0`` when the axis
    hides its labels entirely (``ax.labels.visible is False``) — no gutter
    is drawn for it.

    Composes the same two functions the categorical axis's own gutter emit
    (``emitters/bar.py``'s ``ax_vl["labelPadding"]`` line) calls:
    ``measured_label_padding`` for the widest label's real text width, then
    ``cap_padding_to_label_limit`` so a name Vega-Lite would ellipsize past
    ``labelLimit`` (``ax.labels.max_width``, else Vega-Lite's own
    ``DEFAULT_VL_LABEL_LIMIT``) doesn't keep growing the estimate for text
    that never actually renders. Reusing the render-side derivation rather
    than a second, hand-rolled one means this can never drift from the
    gutter Vega-Lite actually draws.
    """
    if ax.labels.visible is False:
        return 0.0
    font = ax.labels.font
    if font.family is None or font.size is None:
        return 0.0
    labels = sorted({str(row[x_field]) for row in data if row.get(x_field) is not None})
    if not labels:
        return 0.0
    padding = measured_label_padding(labels, font.family, font.size)
    label_limit = (
        ax.labels.max_width
        if ax.labels.max_width is not None
        else DEFAULT_VL_LABEL_LIMIT
    )
    return cap_padding_to_label_limit(padding, label_limit)


def _horizontal_rail_labels_would_collide(
    normalized: BarChart,
    data: ChartRows,
    y_fields: tuple[str, ...],
    stack_mode: str,
    width: float,
    ax_merged: AxisXStyle,
    ay_merged: AxisYStyle,
    stack_order: str | None,
    font_family: str,
    font_size: float,
) -> bool:
    """True when the top rail's own label text would overlap a neighbor.

    Recomputes the exact positions the rail renders at
    (``cumulative_stack_midpoints`` — top-row cumulative segment midpoints,
    un-nudged), converts them to the same pixel scale the chart's own x-axis
    renders on (``_stacked_measure_domain_span`` for ``stack: zero``; the
    pinned unit span for ``stack: normalize``), scaled against the plot's
    real usable width — the card width less the left categorical-label
    gutter (``_categorical_axis_gutter_px``), since the rail and chart panes
    share that x scale and long category names visibly shrink it — and
    measures each series name with the real font the rail paints with
    (``font_measure``; ``translate.py``'s ``_wrap_vconcat_label_rail``
    stamps ``series_label``'s font props onto the rail mark the same way the
    vertical rail's own pane does, so this is the font that actually
    renders). Adjacent pairs, sorted by pixel x, collide when their half
    label-widths plus a gap exceed the pixel distance between them — the
    same adjacent-pair model ``resolve_axis_x_overlap`` uses for axis
    labels, applied to these real, non-evenly-spaced positions instead of an
    even tick band.

    Covers both series-color shapes that actually draw a rail: an authored
    ``color:`` and wide measures (``y: [a, b, ...]``, with or without a
    ``color:`` dimension). ``resolve_wide_measure_channels`` injects a
    synthetic series-color channel for the wide-measure shape, and
    ``EndpointLabelFeature`` folds those rows into the same long form
    (``unfold_wide_rows``) before naming its series — the same fold is used
    below, so this check and the render can never disagree on where a wide
    measure's series come from.

    Real measurement rather than a heuristic: a short-name, many-series rail
    can be perfectly legible, and a two-series rail with very long names can
    still collide — series count alone predicts neither.
    """
    x = normalized.x
    if not isinstance(x, str) or not y_fields:
        return False
    color = normalized.color
    # `series_names` stays RAW -- the identity `cumulative_stack_midpoints`
    # groups real rows by. `label_of_series` is the separate, display-only
    # text this function measures with (the rail's actual painted text).
    if isinstance(color, str) and not isinstance(normalized.y, list):
        series_field, y_field, measure_data = color, y_fields[0], data
        series_names = sorted(
            {str(row[color]) for row in data if row.get(color) is not None}
        )
        label_of_series = {s: s for s in series_names}
    elif isinstance(normalized.y, list):
        series_field, y_field = WIDE_LABEL_FIELD, WIDE_VALUE_FIELD
        measure_data = unfold_wide_rows(data, y_fields, color)
        wide_labels = wide_measure_labels_for(y_fields)
        series_names = raw_wide_series_names(y_fields, color, data)
        label_of_series = {
            raw: humanize_wide_series_name(raw, color, wide_labels)
            for raw in series_names
        }
    else:
        return False
    if stack_mode == "normalize":
        domain_lo, domain_hi = 0.0, 1.0
    else:
        # data is the whole chart's rows, not a per-panel slice — small
        # multiples already disqualified the rail earlier in
        # _bar_endpoint_labels_for_stack, so partition(None, ...) always
        # yields the single trivial panel. Routed through partition() rather
        # than constructing PanelRows directly: only _chart_rows.py may do
        # that (test_panel_rows_construction_boundary.py).
        panel_rows = partition(None, data).panels[0].rows
        domain_span = _stacked_measure_domain_span(
            ay_merged, panel_rows, x, list(y_fields)
        )
        if domain_span is None:
            # A degenerate (zero-width, all-null, or all-zero) span has no
            # real room to anchor on — every label piles on the same pixel.
            # That is the collision this check exists to catch, not a reason
            # to wave the rail through.
            return True
        domain_lo, domain_hi = domain_span
    sort = normalized.sort
    positions = cumulative_stack_midpoints(
        measure_data,
        x,
        y_field,
        series_field,
        series_names,
        sort.by if sort else "",
        bool(sort and sort.order == "desc"),
        stack_mode=stack_mode,
        stack_order=stack_order,
    )
    measurer = get_font_measurer(font_family)
    gap = get_chart_rendering().bar.top_rail_label_gap_spaces * measurer.measure(
        " ", font_size
    )
    usable_width = max(width - _categorical_axis_gutter_px(ax_merged, data, x), 0.0)
    domain_span_width = domain_hi - domain_lo
    pixel = sorted(
        (
            (mid - domain_lo) / domain_span_width * usable_width,
            measurer.measure(label_of_series[series], font_size),
        )
        for series, mid in positions
    )
    return any(
        (width_a + width_b) / 2 + gap > x_b - x_a
        # Adjacent-pair walk: pixel[1:] is intentionally one shorter.
        for (x_a, width_a), (x_b, width_b) in zip(pixel, pixel[1:], strict=False)
    )


def _any_column_stacks_more_than_one_series(
    normalized: BarChart,
    data: ChartRows,
    y_fields: tuple[str, ...],
) -> bool:
    """True when at least one column carries segments from two or more series.

    The rail names the segments *within* a stack. When ``color:`` merely
    re-labels ``x:`` — one series per column, the shape a category-colored bar
    takes — nothing stacks, so there are no segments to name and the rail
    degenerates into a badly-laid-out legend. Hand those back to a legend.

    A series missing from the anchor column does NOT disqualify the rail: it
    anchors on its zero-height stack seam instead (see ``_stacked_midpoints``
    in render/chart/features/endpoint_labels.py), so every series stays named.

    Drawing nothing at all — a single series, or an all-null measure — answers
    this question on its own terms: no column stacks, so the count comparison
    below is False without a special case.
    """
    color = normalized.color
    x = normalized.x
    if not isinstance(color, str) or not isinstance(x, str):
        return True
    drawn: set[tuple[str, str]] = set()
    if isinstance(normalized.y, list):
        # Wide + dimension: each measure is its own segment within a column.
        for row in unfold_wide_rows(data, y_fields, color):
            drawn.add((str(row.get(x)), str(row[WIDE_LABEL_FIELD])))
    else:
        for row in data:
            series = row.get(color)
            if series is None or not any(
                row.get(field) is not None for field in y_fields
            ):
                continue
            drawn.add((str(row.get(x)), str(series)))
    columns = {column for column, _ in drawn}
    return len(drawn) > len(columns)


def _distinct_series_count(dataset: ChartDataset, color: str) -> int:
    """Distinct non-null-value count for the legend's color field.

    ``column_values`` reads a partition field straight off the baked axis
    (no row scan needed) and falls back to scanning rows for a non-partition
    field — column-wise/union, correct whether the chart is faceted or not.
    The ``is not None`` filter applies on both paths: ``partition()`` bakes a
    real SQL NULL as its own legitimate panel value (a ``(None,)`` key), so a
    color field that is itself the partition field can surface ``None``
    through the axis path too, same as an ordinary row scan would.
    """
    return len([v for v in dataset.column_values(color) if v is not None])


def _stack_legend_should_yield(
    resolved_stack: str,
    orientation: Literal["vertical", "horizontal"] | None,
    distinct_series: int,
    symbol_limit: int | None,
    plot_height: float,
    legend_is_top: bool,
    legend_columns: int,
) -> bool:
    """True when a stacked bar's legend would squeeze its own segments to zero height.

    Vega-Lite's ``autosize:fit`` divides a card's total height between the
    legend and the plot; past some entry count the legend's demand leaves the
    plot nothing to draw into (ERR-CHART-PAINTED-NO-MARKS). The marks always
    win that contest. Scoped narrowly to the one shape that actually collapses
    this way: a genuine stack (``stack: none`` subdivides width, not height,
    and never hits this collapse), rendered vertically (a horizontal stack's
    plot height is governed by its category count, not its series count — it
    never collapses this way), whose legend has actually moved to a top,
    multi-column layout (``legend_is_top``; a side legend costs width, not
    plot height, and never competes with the plot for it). The height math
    (``legend_wrap_required_height_px`` -- the legend's *total* footprint at
    collapse, correct here because nothing else is subtracted alongside it)
    divides entries across ``legend_columns`` — the renderer never lays out
    one row per entry once the legend goes top/compact.
    """
    if (
        resolved_stack == "none"
        or distinct_series == 0
        or orientation == "horizontal"
        or not legend_is_top
    ):
        return False
    required = legend_wrap_required_height_px(
        distinct_series, symbol_limit, legend_columns
    )
    return required > plot_height


def _resolve_bar(
    normalized: BarChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    datasets: LayerDatasets,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedBarChart:
    data = dataset.all_rows()
    multiples_scale = (
        normalized.multiples.scale if normalized.multiples is not None else "shared"
    )
    x_ch_type = _classify_to_channel_type(normalized.x, data, is_dimension=True)
    # Bar semantics fix x=category, y=measure regardless of orientation (see
    # the _bake_cartesian_axes docstring above) — the axis cascade hardcodes y
    # to "quantitative" and downstream stack-totals math assumes numeric y. A
    # categorical y silently bakes a NaN axis with zero marks; refuse instead.
    _bar_bad_y = _first_non_numeric_y(normalized.y, data)
    if _bar_bad_y is not None:
        _bar_y_hint = None
        if x_ch_type == "quantitative":
            _bar_y_hint = (
                f"Column {normalized.x!r} looks numeric — did you mean to swap x and y?"
            )
        raise CompilationError.from_code(
            ERR_BAR_Y_NOT_NUMERIC,
            chart_id=normalized.id,
            y_field=_bar_bad_y,
            hint=_bar_y_hint,
        )
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "bar",
        x_ch_type,
        "quantitative",
        normalized.multiples,
        normalized.y,
        has_quantitative_axis=True,
    )
    bar = chart_local_style_context.bar
    # Computed here (rather than at their original, later call sites) because
    # the plot-height floor check below needs both before axes are built:
    # the legend the classifier judges must be the legend that actually
    # renders — chart_style_context.legend (board+chart-level style.legend)
    # merged with bar.legend (family-scoped style.bar.legend), the same merge
    # _base_kwargs performs internally. Neither depends on axis/tick
    # resolution, so hoisting them is a pure reordering. Reused below by the
    # fit-rule call, `_stack_legend_should_yield`, and the floor check --
    # one estimate for all three, never recomputed.
    merged_legend = merge_onto_base(chart_style_context.legend, bar.legend)
    plot_height_estimate = estimate_cartesian_plot_height(normalized, bar, width)
    # Flat chart.stack overrides style.bar.stack; fall through to the merged bar
    # style (which already applied style.bar.stack → board theme cascade) so that
    # per-chart style overrides are respected even when chart.stack is None.
    resolved_stack = normalized.stack if normalized.stack is not None else bar.stack
    is_stacked = resolved_stack not in (None, "none")
    primary = plan.primary
    channels = plan.channels
    channels, wide_measure_series = resolve_wide_measure_channels(
        normalized, channels, "bar", has_layers=bool(normalized.layers)
    )
    ax_merged, ay_merged = plan.ax_merged, plan.ay_merged
    # Orientation depends on ax_merged.time_unit (a pure cascade fact, not
    # data-derived — see _bar_orientation), so it's computable right after
    # the merge, before the axes are built. Computed once, here, and reused
    # both for the categorical axis's edge below and the chart's own
    # resolved `orientation` field at the bottom of this function.
    orientation = _bar_orientation(
        normalized, bar, data, ax_merged.time_unit is not None
    )
    # The layered-single-series rail (EndpointLabelFeature._apply_layered_single_series)
    # only ever fires on the vertical right_pane path — a horizontal bar's rail is the
    # color-series top_rail only (see applies_to()'s horizontal branch, which never
    # reaches the layered fallback), so a horizontal layered bar with no color must
    # not claim the rail here either, or its legend gets suppressed with nothing to
    # replace it.
    endpoint_label_has_layers = (
        orientation != "horizontal"
        and layered_endpoint_rail_fires(
            layered_endpoint_rail_shape(normalized.x, normalized.y),
            [layer.color is None for layer in normalized.layers],
        )
    )
    # A horizontal bar's color-series rail (EndpointLabelFeature.applies_to()'s
    # horizontal branch) only ever fires on a genuinely stacked chart — a
    # grouped (stack: none) horizontal bar never gets a rail even with a
    # color series and endpoint_labels.visible explicitly authored, so
    # letting suppression fire off the color-series term there would retire
    # the legend with nothing to replace it.
    _horizontal_is_grouped = orientation == "horizontal" and resolved_stack in (
        None,
        "none",
    )
    _horizontal_series_rail_fires = not _horizontal_is_grouped
    # Hoisted above the ResolvedBarStyle construction below (its only other
    # use) so the horizontal collision disqualifier can measure the same
    # font the rail actually paints with, without resolving it twice.
    series_label = _resolved_series_label(chart_style_context, primary, width)
    # Resolved after orientation: the disqualifier chain below is specific to
    # the horizontal rail. `merged_legend` was hoisted above (with
    # `plot_height_estimate`) for the floor check; `_base_kwargs` further
    # down still takes the unmerged `bar.legend` patch (as every other family
    # resolver does) and repeats the same merge internally.
    # Bar wants a top legend whenever it isn't stacked -- its own long-
    # standing default, unconditional, even with an overlay layer to name: a
    # stacked bar's segments read better against a side legend, and stacking
    # is orthogonal to whether a layer is present. The color channel's
    # real, distinct values are already known from the executed dataset --
    # not guessed -- via the same accessor _distinct_series_count above reads.
    # A gradient color channel's entries still get built here (this tuple
    # is also wants_top_legend_shape's "this shape has entries to name"
    # signal, which must survive a gradient), but cartesian_series_naming
    # ignores them for the two rungs that measure rows -- once, for every
    # family that calls it (see that function's own docstring), not here.
    color_domain_values = cartesian_color_domain_values(dataset, normalized.color)
    top_legend_series = (
        cartesian_top_legend_entries(
            normalized.y if isinstance(normalized.y, str) else None,
            normalized.y_label,
            normalized.layers,
            color_domain_values=color_domain_values,
            has_color=normalized.color is not None,
        )
        if not is_stacked
        else None
    )
    # A horizontal bar puts its dimension field on Vega-Lite's y-channel --
    # the axis a top legend's row actually loses width to (see
    # estimate_left_axis_reserve_px). Every other orientation keeps that
    # axis's own quantitative measure on the right (_bake_ay_position_left),
    # so only the horizontal case has real dimension content to measure.
    dimension_values = (
        cartesian_color_domain_values(dataset, normalized.x)
        if orientation == "horizontal"
        else None
    )
    left_axis_reserve_px = estimate_left_axis_reserve_px(
        dimension_values, merged_legend
    )
    # Only the axis title on the horizontal rail costs the plot height; the
    # other one is rotated and costs width. `orientation` picks which
    # channel lands on that rail -- a horizontal bar swaps them
    # (_emit_horizontal maps ay to VL x). Feeds both rung 2's floor check
    # below and this function's own floor measurement further down -- the
    # same fact, computed once.
    _axis_title_costs_height = (
        ay_merged if orientation == "horizontal" else ax_merged
    ).title.visible is not False
    naming = cartesian_series_naming(
        normalized,
        channels,
        _authored_legend(primary),
        merged_legend,
        width,
        _bar_endpoint_labels_for_stack(
            bar.endpoint_labels,
            normalized,
            data,
            resolved_stack,
            orientation == "horizontal",
            _author_asked_for_endpoint_labels(normalized.style),
            width,
            ax_merged,
            ay_merged,
            bar.stack_order,
            series_label.font_family,
            series_label.font_size,
        ),
        endpoint_label_has_layers=endpoint_label_has_layers,
        has_layers=bool(normalized.layers),
        rail_eligible_for_suppression=_horizontal_series_rail_fires,
        suppress_wide_measure_series=wide_measure_series,
        multiples_wide_measure_series=wide_measure_series,
        top_legend_series=top_legend_series,
        plot_height_estimate=plot_height_estimate,
        left_axis_reserve_px=left_axis_reserve_px,
        card_padding_px=chart_style_context.card_padding,
        subtitle_present=bool(normalized.subtitle),
        axis_title_costs_height=_axis_title_costs_height,
    )
    # The classifier's series count, not the legend's entry count -- see
    # `_floor_entry_count` below for the difference and why they are
    # separate. A wide bar (`y: [m01..m25]`) folds its measures into a color
    # channel at render (fold_wide_measures, emitters/_wide.py) and gets one
    # series per measure, crossed with the color column's values when it
    # authors one. A plain (non-list) y contributes no color-cardinality
    # series here; it can still draw a legend off its layers, which is the
    # floor's business below, not the collapse model's.
    if isinstance(normalized.y, list):
        legend_entry_count = len(normalized.y) * (
            _distinct_series_count(dataset, normalized.color)
            if normalized.color is not None
            else 1
        )
    elif normalized.color is not None:
        legend_entry_count = _distinct_series_count(dataset, normalized.color)
    else:
        legend_entry_count = 0
    legend_is_top = naming.top_legend == "compact"
    stack_legend_yield = _stack_legend_should_yield(
        resolved_stack,
        orientation,
        legend_entry_count,
        merged_legend.symbol_limit,
        plot_height_estimate,
        legend_is_top,
        merged_legend.compact_columns,
    )
    # Any legend Vega actually draws above the plot -- `row` as well as
    # `compact`. `legend_is_top` above stays compact-only because
    # `_stack_legend_should_yield` is calibrated on that narrower meaning.
    _legend_renders_on_top = (
        naming.top_legend in ("compact", "row")
        and not naming.suppress_legend
        and not stack_legend_yield
    )
    # The legend's own height counts against the floor but is never a
    # candidate to give way -- see plot_height_floor.py's module docstring:
    # for bar, the legend is typically the chart's only series-naming
    # mechanism, and hiding it with nothing to replace it violates the
    # series-naming invariant (tests/visual/test_series_naming_invariant.py).
    #
    # The floor's own entry count, deliberately NOT `legend_entry_count`:
    # that one feeds `_stack_legend_should_yield`, whose verdict reaches
    # `suppress_legend` on the resolved chart. A layered bar draws a real
    # legend (base plus one entry per overlay, sharing a color scale) that
    # `legend_entry_count` does not see, but widening that variable moved a
    # rendered decision -- it un-short-circuited the yield classifier on a
    # shape its stacked-segment collapse model was never calibrated for, and
    # suppressed the legend on a short layered card. The layer term belongs
    # to the legend's *height*, not to the collapse model, so it lives here.
    #
    # Vega draws at most `symbol_limit` symbols, so a 60-series legend is 20
    # symbols tall, not 60 -- the sibling _stack_legend_should_yield clamps
    # the same way. Unclamped, a high-cardinality legend charges rows that
    # never render and accuses a plot that is not starved.
    # A color-less overlay joins the base's color scale under its own
    # label, adding exactly one entry (_overlay.py appends it
    # unconditionally) -- measured domain ["APAC", "EMEA", "NA", "target"]
    # for 3 regions and one line layer. A layer that authors its own
    # `color:` instead contributes only the values not already in the
    # domain, which can be none of them: a per-region target line draws the
    # same 4 entries as the bars alone. Charging it +1 anyway bills a row
    # the legend does not draw and accuses a plot that is not starved, so
    # only color-less layers are counted here. The remainder -- a colored
    # layer introducing genuinely new values -- is undercharged, which
    # keeps this a miss rather than a false positive, the direction this
    # check errs in everywhere else.
    _unlabeled_layers = sum(1 for layer in normalized.layers if layer.color is None)
    if legend_entry_count:
        _floor_entry_count = legend_entry_count + _unlabeled_layers
    elif normalized.layers:
        _floor_entry_count = 1 + _unlabeled_layers
    else:
        _floor_entry_count = 0
    _legend_entries_for_floor = (
        min(_floor_entry_count, merged_legend.symbol_limit)
        if merged_legend.symbol_limit
        else _floor_entry_count
    )
    # Both top layouts cost height, so both are charged. `compact` (the tiny
    # tier) stacks entries into columns, so its height tracks the row count --
    # charged via the shared legend_wrap_marginal_height_px, the same
    # calculation the fallback ladder's own rung-2 check
    # (legend_wrap_fits_height_budget) uses, so the two can never disagree
    # about what a wrapped legend costs. Deliberately NOT
    # legend_wrap_required_height_px: that one is the legend's *total*
    # footprint at collapse (fixed chrome included), and this charge feeds
    # estimate_plot_height below, which already subtracts its own fixed
    # chrome (irreducible_height_px / axis_titles_height_px) -- summing both
    # fixed terms would double-count non-legend chrome a second time.
    # `row` flows entries horizontally in one row of flat height instead --
    # measured 31px at 2, 5, 16 and 25 series, on 400px and 640px cards
    # alike. A grouped bar (bar's default with `color:`) emits `row` at
    # every width, so charging only `compact` left every default grouped
    # bar above the tiny tier billing 0px for a strip that really takes
    # ~31px.
    # No entries, no legend: a bar with no `color:` (and no wide-measure fold)
    # has nothing to name, and Vega emits no legend element for it even
    # though the layout policy still reads "row".
    if not _legend_renders_on_top or _legend_entries_for_floor == 0:
        legend_height_for_floor = 0.0
    elif naming.top_legend == "compact":
        legend_height_for_floor = legend_wrap_marginal_height_px(
            _legend_entries_for_floor, None, merged_legend.compact_columns
        )
    else:
        legend_height_for_floor = (
            get_chart_rendering().bar.plot_height_floor_row_legend_total_px
        )
    # A measurement, not a mutation -- see plot_height_floor.py's module
    # docstring for why chrome is never removed automatically. Surfaced to
    # the author as WARN-PLOT-HEIGHT-BELOW-MINIMUM
    # (render/warnings/plot_height_below_minimum.py) via the stored fields
    # below, not acted on here. Measured on the calibration chart at 300px:
    # hiding the x title moved the plot 44 -> 65px tall at unchanged width,
    # hiding the y title moved it 216 -> 238px wide at unchanged height --
    # `_axis_title_costs_height` above (hoisted for rung 2's own floor
    # check) is that same fact.
    #
    # The card the renderer actually draws: the content height above plus
    # card padding on both sides, matching render/sizing.py's
    # get_item_content_height. The floor is a fraction of the card's own
    # height, so it has to be a fraction of that card and not of the
    # padding-less content box -- measuring the latter set the floor 2 *
    # card_padding too low and left genuinely starved plots unreported.
    floor_card_height = plot_height_estimate + 2 * chart_style_context.card_padding
    estimated_plot_height = estimate_plot_height(
        floor_card_height,
        card_padding_px=chart_style_context.card_padding,
        legend_height_px=legend_height_for_floor,
        subtitle_present=bool(normalized.subtitle),
        axis_title_costs_height=_axis_title_costs_height,
    )
    plot_starved = estimated_plot_height < plot_height_floor_px(floor_card_height)
    endpoint_labels = naming.endpoint_labels
    _reject_dual_axis_layered_endpoint_labels(
        normalized.id,
        normalized.layers,
        endpoint_labels.visible and endpoint_label_has_layers,
    )
    if orientation == "horizontal":
        # For horizontal bar, ay (the semantic measure axis) maps to VL x,
        # which has no left/right orient — _emit_horizontal pops it entirely.
        # ay.position's only remaining role is supplying the swapped
        # categorical axis's (VL y) orient in emitters/bar.py. The deleted
        # categorical_orient field always defaulted "left" there, statically —
        # the horizontal rail sits above the plot (top-row), never left/right,
        # so it never competes for this axis's side the way the vertical
        # endpoint-label rail does. Preserve the static default via
        # _bake_ay_position_left rather than _bake_ay_orient's
        # endpoint-label-aware auto-resolution.
        ay_merged = _bake_ay_position_left(ay_merged)
    else:
        ay_merged = _bake_ay_orient(
            ay_merged,
            channels,
            endpoint_labels.visible,
            wide_measure_series=wide_measure_series,
            has_layers=endpoint_label_has_layers,
        )
    # Bar's categorical axis (axis_x) only has a left/right edge when the
    # chart is horizontal — it then renders on VL's y channel at ay.position
    # (see emitters/bar.py's own-orient swap; categorical_orient was deleted
    # 2026-08 — position now serves this role too). A vertical bar's x stays
    # bottom-orient — no edge.
    x_edge = _edge_or_none(ay_merged.position) if orientation == "horizontal" else None
    _ay_cont_log = ay_merged.scale.continuous if ay_merged.scale is not None else None
    if _ay_cont_log is not None and _ay_cont_log.type == "log":
        raise CompilationError.from_code(
            ERR_BAR_LOG_SCALE_NOT_SUPPORTED,
            chart_id=normalized.id,
        )
    _ay_cont_zero = ay_merged.scale.continuous if ay_merged.scale is not None else None
    ay_scale_zero = _ay_cont_zero.zero if _ay_cont_zero is not None else None
    # Non-stacked bars anchor at zero by default unless scale.zero is explicitly False.
    # Stacked bars only anchor when scale.zero is explicitly True.
    bar_zero = (not is_stacked and ay_scale_zero is not False) or ay_scale_zero is True
    y_field = normalized.y if isinstance(normalized.y, str) else None
    y_fields = (
        [y_field]
        if y_field
        else list(normalized.y)
        if isinstance(normalized.y, list)
        else []
    )
    x_field = normalized.x if isinstance(normalized.x, str) else None
    bar_domain_max: float | None = None
    bar_domain_min: float | None = None
    authored_y_domain = _ay_cont_zero.domain if _ay_cont_zero is not None else None
    if resolved_stack == "normalize" or not y_fields:
        # normalize-stack domain is always [0,1]; no y_field means no ticks.
        tick_values: tuple[float, ...] = ()
        if resolved_stack == "normalize":
            ay_merged = _bake_normalize_domain(ay_merged)
    elif is_stacked and x_field:
        tick_values = _resolve_stacked_bar_ticks(
            ay_merged, dataset, y_fields, x_field, multiples_scale
        )
        # stacked domainMax baked separately below; domain_min stays None
    else:
        bar_y_values = (
            _shared_y_values(data, y_field, normalized, datasets)
            if y_field
            else _numeric_y_values(data, tuple(y_fields))
        )
        _bar_ticks = _resolve_cartesian_ticks(
            normalized.id,
            ay_merged,
            bar_y_values,
            zero_anchor=bar_zero,
            authored_ticks_count=_authored_axis_y_ticks_count(normalized.style),
            scale=multiples_scale,
        )
        tick_values = _bar_ticks.ticks
        bar_domain_max, bar_domain_min = _bar_ticks.domain_max, _bar_ticks.domain_min
    # Bake stacked_domain_max at resolve: emitter reads the pre-baked value.
    # Only set for regular zero-stacked bars; not normalize, not grouped, not
    # single-series. Headroom applies to the stacked TOTAL max, same as the
    # plain data max. Unlike the non-stacked path (which never emitted a
    # domainMax before headroom existed), stacked bars always pinned the
    # exact stacked total — VL's nice-rounding would otherwise add
    # accidental top margin — so a headroom of 0 keeps the flush-exact-total
    # pin rather than dropping it. A non-positive total (all-negative stacks)
    # is skipped: domainMax 0 on a zero-anchored scale is a degenerate [0, 0]
    # domain; VL auto-fits instead. Deliberately does NOT read an authored
    # axis_y domain here — that stays scoped to the horizontal rail's own
    # collision check (_stacked_measure_domain_span), which calls it
    # directly; widening this bake too would change hover-band rendering
    # (render/chart/features/bar_hover_band.py) on every stacked bar,
    # vertical included, well outside anything this task touches.
    stacked_domain_max: float | None = None
    if is_stacked and resolved_stack != "normalize" and y_fields and x_field:
        # x_field can itself be the multiples field — partition() strips a
        # panel's own partition column from its rows, so restamp it back
        # before grouping, or every row's x_field reads as absent and the
        # stacked total silently folds to None (nothing to sum).
        _raw_stacked_max = fold_panels(
            restamp(dataset, x_field),
            multiples_scale,
            lambda rows: stacked_totals_max(rows, x_field, y_fields),
        )
        if _raw_stacked_max is not None and _raw_stacked_max > 0:
            stacked_domain_max = apply_headroom(
                _raw_stacked_max, _axis_headroom(ay_merged)
            )
    # A horizontal bar's measure axis (this "ay") renders on VL's x channel
    # and forms no column — see build_resolved_axis's column_forming
    # docstring. Every other bar orientation keeps the default (vertical
    # ruler, column-forming).
    tooltip_format_values = quantitative_channel_values(data, normalized.y)
    ay, style_tail = build_cartesian_axes(
        normalized.id,
        chart_style_context,
        ax_merged,
        ay_merged,
        ax_band_position=plan.ax_band_position,
        ay_band_position=plan.ay_band_position,
        ax_edge=x_edge,
        ay_format_authored=plan.ay_format_authored,
        ay_format_is_alias=plan.ay_format_is_alias,
        ticks=_CartesianTickResolution(tick_values, bar_domain_max, bar_domain_min),
        column_forming=orientation != "horizontal",
        measure_tooltip_format=_measure_tooltip_format(
            normalized, primary, chart_style_context, values=tooltip_format_values
        ),
        tooltip_format_values=tooltip_format_values,
        ax_is_quantitative=x_ch_type == "quantitative",
        # Bar semantics fix y=measure regardless of orientation (see the
        # _bake_cartesian_axes docstring above), so the published channel-type
        # fact is unconditionally True -- unlike column_forming above, which
        # tracks the render geometry orientation swaps instead.
        ay_is_quantitative=True,
        # The digit-alignment gate still needs the old geometry-based
        # question (does this axis render as the column-forming measure
        # axis) since a horizontal bar's measure axis renders on VL's x
        # channel, not as a right-edge column.
        ay_quantitative_for_alignment=orientation != "horizontal",
        zero_anchor=bar_zero,
        # Bar always zero-anchors and pins axis_x.ticks.visible: false
        # explicitly -- this axis's baked domain_min is never read for the
        # tick-stub decision regardless (see the histogram/heatmap comment
        # on the sibling ticks=... empty-ladder calls).
        endpoint_rail_may_discard_domain=False,
    )
    axis_is_house = (
        ay.labels.format is not None
        and is_d3_si_spec(ay.labels.format)
        and (not plan.ay_format_authored or plan.ay_format_is_alias)
    )
    _tf = _title_font(normalized, chart_local_style_context, width)
    bkw = _base_kwargs(
        normalized,
        chart_style_context,
        channels,
        bar.legend,
        _effective_palette(chart_style_context, primary),
        requested_alias_palette=_effective_requested_alias_palette(
            chart_style_context, primary
        ),
        automatic_link_candidate=automatic_link_candidate,
        layout_padding=bar.padding,
        suppress_legend=naming.suppress_legend or stack_legend_yield,
        top_legend=naming.top_legend,
        force_legend_visible=naming.force_legend_visible,
        legend_position_overridden_by_width=naming.legend_position_overridden_by_width,
    )
    resolved_layers = _resolve_layer_list(
        normalized.layers,
        chart_style_context,
        "bar",
        bar,
        normalized.query_name,
        0.0,
        0.0,
    )
    if authored_y_domain is not None:
        _check_layers_y_domain(normalized.id, resolved_layers, authored_y_domain)
    # Total label: fall back to axis format when none authored; use resolve_label_format
    # so both branches share the alias-gate register decision.
    raw_total = bar.marks.bar.total_label
    if raw_total.format is None and ay.labels.format is not None:
        resolved_total = raw_total.model_copy(update={"format": ay.labels.format})
        total_label_is_house = axis_is_house
    elif raw_total.format is not None:
        resolved_total_fmt, total_label_is_house = resolve_label_format(
            raw_total.format, chart_style_context.formats
        )
        resolved_total = raw_total.model_copy(update={"format": resolved_total_fmt})
    else:
        total_label_is_house = False
        resolved_total = raw_total
    resolved_labels, label_is_house = _label_format_fallback(
        bar.marks.bar.labels,
        ay.labels.format,
        axis_is_house,
        chart_style_context.formats,
    )
    bar_mark = bar.marks.bar.model_copy(
        update={"labels": resolved_labels, "total_label": resolved_total}
    )
    # support_table.position resolution needs the same orientation facts
    # already baked above: axis_y_orient falls back to "right" when unset,
    # mirroring the fallback apply_chart_support_table_post_pass used to
    # apply at render before this bake closed that reach-back.
    if ay_merged.position == "left" or ay_merged.position == "right":
        _ay_orient_for_table: Literal["left", "right"] = ay_merged.position
    else:
        _ay_orient_for_table = "right"
    _ck = _cartesian_kwargs(
        normalized,
        chart_local_style_context,
        variables,
        data,
        "bar",
        panel_axes=dataset.axes,
        category_axis_vertical=orientation == "horizontal",
        axis_y_orient=_ay_orient_for_table,
    )
    _ck, wide_measures = bake_wide_measures_kwargs(normalized.y, _ck)
    return ResolvedBarChart(
        **bkw,
        **_ck,
        wide_measures=wide_measures,
        chart_type="bar",
        stack=resolved_stack,
        stacked_domain_max=stacked_domain_max,
        orientation=orientation,
        style=ResolvedBarStyle(
            series_label=series_label,
            stack_order=bar.stack_order,
            mark=bar_mark,
            overlap=bar.overlap,
            plot_height_below_floor=plot_starved,
            estimated_plot_height_px=estimated_plot_height,
            estimated_card_height_px=floor_card_height,
            single_series_fill=_effective_single_series_fill(
                chart_style_context,
                primary,
                rhythm_slot=normalized.rhythm_slot,
                has_layers=bool(normalized.layers),
            ),
            endpoint_labels=endpoint_labels,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            title_font=_tf,
            label_is_house=label_is_house,
            label_font_size=_effective_label_font_size(
                bar_mark.labels, chart_style_context
            ),
            total_label_is_house=total_label_is_house,
            **style_tail,
        ),
        layers=resolved_layers,
    )


def _effective_label_font_size(
    labels: BarLabelsStyle, chart_style_context: ChartStyleContext
) -> float:
    """Font size the value-label text will actually paint at, in px.

    An unset ``labels.font.size`` is not a gap — the text mark falls through to
    the board's VL text config (``compile/vega_lite/mapping.py``), so that is
    the honest second source. Required post-cascade, like series_label's font
    fields in ``_palette.py``: every theme resolves the text-mark size, and the
    inherit cascade refills an explicitly nulled one, so a None here is a
    broken cascade to name rather than a state for render to tiptoe around.
    """
    if labels.font is not None and labels.font.size is not None:
        return labels.font.size
    size = chart_style_context.marks.text.font.size
    if size is None:
        raise ValueError("marks.text.font.size must be non-None after cascade")
    return size


def _resolve_histogram(
    normalized: BarChart,
    dataset: ChartDataset,
    chart_style_context: ChartStyleContext,
    width: float,
    automatic_link_candidate: AutomaticLinkCandidate,
    variables: ChartTextVariables,
) -> ResolvedBarChart:
    """Resolve a histogram chart through the histogram theme (not bar theme).

    Histogram uses plain bin:True (VL computes extents); resolve bakes mark +
    axes from chart_style_context.histogram.* so histogram.axis_y.title.visible and
    histogram.marks.bar.border apply correctly.
    """
    data = dataset.all_rows()
    x_ch_type = _classify_to_channel_type(normalized.x, data, is_dimension=True)
    chart_local_style_context = build_chart_style_context(
        chart_style_context, normalized
    )
    plan = plan_cartesian(
        normalized,
        data,
        chart_style_context,
        "histogram",
        x_ch_type,
        "quantitative",
        None,
        None,
        has_quantitative_axis=True,
    )
    primary = plan.primary
    hist = merge_onto_base(chart_style_context.histogram, primary)
    channels = plan.channels
    ax_merged, ay_merged = plan.ax_merged, plan.ay_merged
    ay_merged = _bake_ay_position_left(ay_merged)
    # Histogram's x is always a bottom-orient bin axis — no left/right edge.
    # Neither axis carries tick_values on a histogram (VL computes bins
    # client-side). ay_format_authored=True below does not describe a real
    # authoring (histogram has no cascade to read one from), yet it is not
    # inert: empty tick_values is also the ladder-less sub-unit guard's own
    # entry condition (build_resolved_axis's `elif`, axis_cascade.py), and
    # True is what keeps that branch from firing here — histogram's y is a
    # VL-computed row count, not real user data, so nothing chose it to
    # receive that guard. The label gate below uses the REAL
    # ay_format_authored from plan_cartesian so the narrative/native decision
    # matches bar's behavior for the same format provenance.
    ay, style_tail = build_cartesian_axes(
        normalized.id,
        chart_style_context,
        ax_merged,
        ay_merged,
        ax_band_position=plan.ax_band_position,
        ay_band_position=plan.ay_band_position,
        ax_edge=None,
        ay_format_authored=True,
        ay_format_is_alias=False,
        ticks=_CartesianTickResolution((), None, None),
        column_forming=True,
        measure_tooltip_format=None,
        # Histogram's y is a VL-computed row count, not a real column --
        # nothing to vote the sub-$1 floor on.
        tooltip_format_values=(),
        # Histogram's binned x column is always numeric; its y (row count)
        # is always the measure, regardless of the x column's own type.
        ax_is_quantitative=x_ch_type == "quantitative",
        ay_is_quantitative=True,
        # Inert: ticks is always the empty _CartesianTickResolution above, so
        # _y_gridline_caps_bottom returns before this bool is ever read.
        zero_anchor=True,
        endpoint_rail_may_discard_domain=False,
    )
    hist_axis_is_house = (
        ay.labels.format is not None
        and is_d3_si_spec(ay.labels.format)
        and (not plan.ay_format_authored or plan.ay_format_is_alias)
    )
    # histogram reuses ResolvedBarStyle; these bar-only fields are unread by
    # the histogram emit path (no stack, no endpoint labels, no grouped x offset).
    _tf = _title_font(normalized, chart_local_style_context, width)
    hist_labels, hist_label_is_house = _label_format_fallback(
        hist.marks.bar.labels,
        ay.labels.format,
        hist_axis_is_house,
        chart_style_context.formats,
    )
    hist_mark = hist.marks.bar.model_copy(update={"labels": hist_labels})
    return ResolvedBarChart(
        **_base_kwargs(
            normalized,
            chart_style_context,
            channels,
            hist.legend,
            _effective_palette(chart_style_context, primary),
            requested_alias_palette=_effective_requested_alias_palette(
                chart_style_context, primary
            ),
            automatic_link_candidate=automatic_link_candidate,
            layout_padding=hist.padding,
        ),
        **{
            **_cartesian_kwargs(
                normalized,
                chart_local_style_context,
                variables,
                data,
                "histogram",
                panel_axes=dataset.axes,
            ),
            # Histogram uses aggregate-count on y; authored `y: [a, b]` is legal
            # at the normalize boundary but meaningless here. Strip the list so the
            # narrowed resolved y field (str | None) doesn't fail validation.
            "y": None if isinstance(normalized.y, list) else normalized.y,
        },
        chart_type="histogram",
        stack="none",
        stacked_domain_max=None,
        orientation="vertical",
        style=ResolvedBarStyle(
            series_label=_resolved_series_label(chart_style_context, primary, width),
            stack_order=None,
            mark=hist_mark,
            overlap=None,
            single_series_fill=_effective_single_series_fill(
                chart_style_context,
                primary,
                rhythm_slot=normalized.rhythm_slot,
                has_layers=bool(normalized.layers),
            ),
            # A histogram bins x and aggregates y to a count, so there is no
            # per-row value for a rail to anchor to — it would place labels
            # against a measure the chart never plots. Bar's slot supplies the
            # rest of the style; this one field is off for the family.
            endpoint_labels=chart_style_context.bar.endpoint_labels.model_copy(
                update={"visible": False}
            ),
            label_is_house=hist_label_is_house,
            # Read, but it can never produce a threshold: a histogram emits
            # `{"aggregate": "count", ...}` with no scale key, so _measure_span
            # finds no span and the fit test stands down on the next line.
            label_font_size=_effective_label_font_size(
                hist_mark.labels, chart_style_context
            ),
            total_label_is_house=False,
            label_usable_ratio=chart_style_context.label_usable_ratio,
            title_font=_tf,
            **style_tail,
        ),
    )
