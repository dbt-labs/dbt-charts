"""Chart and layout-item rendering entrypoints.

This module owns:
- executing a chart query
- resolving the chart into render-ready semantics
- dispatching to chart vs nested-board rendering

UI chrome such as menus is handled outside core rendering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    Board,
    TitleShift,
    VariableValues,
)
from dbt_charts.core.compile.models.board.resolved import (
    ChartIdentity,
    ChartResolveFailure,
    ResolvedBoard,
    ResolvedLayoutItem,
)
from dbt_charts.core.compile.models.chart.normalized import (
    PAINTS_MARKS,
    SVG_LAYOUT_PADDED_TYPES,
)
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic, display_message
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.registry import REGISTRY
from dbt_charts.core.execute.chart_data_provider import ChartDataProvider
from dbt_charts.core.render.chart.spec_builders import additive_padding
from dbt_charts.core.render.chart_diagnostics import stamp_chart_diagnostic
from dbt_charts.core.render.layout_sizing import RenderCache

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedCalloutStyle
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.render.conditions import evaluate_visible
from dbt_charts.core.render.svg_utils import (
    attr_name,
    authored_attrs,
    border_dash_attrs,
    escape_attr,
    extract_svg_dimensions,
    extract_svg_inner_content,
    padded_authoring_content,
    px,
    selection_boxes,
)

_ZERO_PADDING: dict[str, float] = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}

__all__ = [
    "render_layout_item",
    "render_chart_item",
]


def _uses_svg_family_layout_padding(chart_type: str) -> bool:
    """Return whether the chart should be inset by layout card padding."""
    return chart_type in SVG_LAYOUT_PADDED_TYPES


def _ensure_resolved_board(board: Board | ResolvedBoard) -> ResolvedBoard:
    """Return the board as a ResolvedBoard, resolving it if it's a Board."""
    if isinstance(board, ResolvedBoard):
        return board
    from dbt_charts.core.render.board_resolve import build_resolved_nested_board_static

    return build_resolved_nested_board_static(board)


def render_layout_item(
    item: ResolvedLayoutItem,
    executor: ChartDataProvider,
    variables: VariableValues,
    card_gap: float,
    available_width: float,
    available_height: float = 300.0,
    gap: float = 0.0,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    source_path: str = "",
    error_collector: list[Diagnostic] | None = None,
    painted_canvas: str | None,
) -> tuple[str, float]:
    """Render a single layout item (chart, nested board, or details section).

    If the item has details metadata, renders a collapsible section:
    - Summary bar (always rendered, clickable)
    - Content (only rendered when the details variable is true)

    Args:
        item: Layout item to render
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Width in pixels for the item
        available_height: Height in pixels for the item
        resolved_style: Board-scoped ResolvedStyle for effective chart style.

    Returns:
        (svg_string, actual_height) — actual_height is the true rendered height.
    """
    # Evaluate visible expression before doing any work — hidden items cost nothing.
    if not evaluate_visible(item.visible, variables, executor):
        return "", 0.0

    # Use calculated dimensions if available, otherwise use passed dimensions
    width = item.width if item.width > 0 else available_width
    height = item.height if item.height > 0 else available_height

    # Details (collapsible section): render summary bar + conditionally render content
    if item.details_variable and item.details_summary:
        from dbt_charts.core.render.layouts import (
            is_details_expanded,
            render_details_summary,
        )

        details_config = resolved_style.layout.details
        summary_height = float(details_config.summary_height)

        expanded = is_details_expanded(item, variables)
        summary_svg = render_details_summary(
            item,
            variables,
            width,
            expanded=expanded,
            resolved_style=resolved_style,  # keyword-only, required
        )

        if expanded and item.board:
            from dbt_charts.core.render.boards import render_nested_board
            from dbt_charts.core.render.sizing import details_chrome_height

            content_height = height - details_chrome_height(
                item, gap=gap, card_gap=card_gap, resolved_style=resolved_style
            )
            content_svg, board_actual_height = render_nested_board(
                _ensure_resolved_board(item.board),
                executor,
                variables,
                width,
                content_height,
                card_gap,
                source_path=source_path,
                render_cache=render_cache,
                error_collector=error_collector,
                painted_canvas=painted_canvas,
            )
            actual_height = float(
                px(summary_height + details_config.content_y_offset)
                + board_actual_height
            )
            section_border = (
                f'<rect x="0" y="0" width="{escape_attr(width)}" height="{escape_attr(actual_height)}" '
                f'fill="none" stroke="{escape_attr(resolved_style.border.color)}" stroke-width="{escape_attr(details_config.border.width)}"'
                f"{border_dash_attrs(details_config.border)} "
                f'rx="{escape_attr(details_config.border.radius)}"/>'
            )
            rendered = (
                f"{section_border}"
                f"{summary_svg}"
                f'<g transform="translate(0, {px(summary_height + details_config.content_y_offset)})">'
                f"{content_svg}</g>"
            )
        else:
            rendered = summary_svg
            actual_height = summary_height
    else:
        rendered = ""
        actual_height = height
        if item.chart_error is not None:
            rendered, actual_height = _render_chart_resolve_failure(
                item.chart_error,
                width,
                height,
                error_collector,
                resolved_style=resolved_style,
            )
        elif item.type == "chart" and item.chart:
            # Per-family padding: baked into layout_padding at construction time
            # by every family resolver — the single source of truth.
            card_padding = float(resolved_style.frame.card_padding)
            chart_padding = item.chart.layout_padding
            chart_svg, chart_actual_height = render_chart_item(
                item.chart,
                executor,
                variables,
                width,
                height,
                resolved_style=resolved_style,
                render_cache=render_cache,
                padding=additive_padding(card_padding, chart_padding),
                error_collector=error_collector,
                title_shift=item.title_shift,
            )
            if chart_svg:
                rendered = chart_svg
                actual_height = chart_actual_height
        elif item.type == "board" and item.board:
            # Import here to avoid circular imports
            from dbt_charts.core.render.boards import render_nested_board

            rendered, actual_height = render_nested_board(
                _ensure_resolved_board(item.board),
                executor,
                variables,
                width,
                available_height=height,
                card_gap=card_gap,
                source_path=source_path,
                render_cache=render_cache,
                error_collector=error_collector,
                painted_canvas=painted_canvas,
                title_shift=item.title_shift,
            )

    if rendered and item.notes:
        return (
            f'<g class="dbt-layout-item" data-layout-notes="{escape_attr(item.notes)}">'
            f"{rendered}</g>",
            actual_height,
        )
    return rendered, actual_height


def _render_callout_block(
    exc: DbtChartsError,
    chart: ResolvedChart,
    available_width: float,
    available_height: float,
    error_collector: list[Diagnostic] | None,
    *,
    resolved_style: ResolvedStyle,
) -> tuple[str, float]:
    """Build an inline error SVG block and optionally collect the Diagnostic.

    Single call site for materializing a *render-stage* chart error as SVG —
    the streaming task can wrap this as a yield point without hunting across
    multiple files. The Diagnostic's identity fields are stamped by
    ``stamp_chart_diagnostic``, shared with the resolve-stage and data-format
    paths.
    """
    # An extra stamp beyond the shared shape, and it has to happen before
    # to_diagnostic(): Cloud's render_error_source reads fields["source_path"]
    # to place the editor cursor, and the Diagnostic copies fields rather than
    # aliasing them, so a stamp added afterwards would never reach it.
    exc.fields["source_path"] = chart.source_path
    diagnostic = stamp_chart_diagnostic(exc, chart.id, chart.source_path)

    if error_collector is not None:
        error_collector.append(diagnostic)

    return _wrap_rendered_chart_svg(
        ChartIdentity.from_resolved(chart),
        _build_error_callout_svg(
            diagnostic,
            available_width,
            available_height,
            resolved_style.chart_defaults.callout_error,
        ),
        outer_width=available_width,
        resolved_title=chart.id,
        extra_classes=("dbt-chart-callout",),
        is_error_fallback=True,
        inset=None,
        # The placard draws at the full available size — no padding is baked
        # into it, so there is nothing for its box to sit outside of.
        ink_inset=_ZERO_PADDING,
    )


def _build_error_callout_svg(
    diagnostic: Diagnostic,
    width: float,
    height: float,
    callout_style: ResolvedCalloutStyle,
) -> str:
    """The error placard on its own — no chart, no wrapper, just the card."""
    from dbt_charts.core.render.chart.callout import render_callout_svg

    return render_callout_svg(
        message=display_message(diagnostic),
        width=width,
        height=height,
        title=_callout_error_title(diagnostic.code),
        callout_style=callout_style,
        code=diagnostic.code,
        hint=diagnostic.hint,
        doc_url=REGISTRY.get(diagnostic.code).doc_url,
        markdown=False,  # runtime error text: identifiers/sql/tracebacks are not markdown
    )


def _render_chart_resolve_failure(
    failure: ChartResolveFailure,
    available_width: float,
    available_height: float,
    error_collector: list[Diagnostic] | None,
    *,
    resolved_style: ResolvedStyle,
) -> tuple[str, float]:
    """Draw the tile for a chart that never resolved.

    The same placard and the same wrapper the render-stage path draws, built
    from the identity captured when resolution failed — a host cannot tell
    which stage failed by looking at the DOM, and should not have to.
    """
    if error_collector is not None:
        error_collector.append(failure.diagnostic)

    return _wrap_rendered_chart_svg(
        failure.identity,
        _build_error_callout_svg(
            failure.diagnostic,
            available_width,
            available_height,
            resolved_style.chart_defaults.callout_error,
        ),
        outer_width=available_width,
        resolved_title=failure.identity.id,
        extra_classes=("dbt-chart-callout",),
        is_error_fallback=True,
        inset=None,
        # The placard draws at the full available size — no padding is baked
        # into it, so there is nothing for its box to sit outside of.
        ink_inset=_ZERO_PADDING,
    )


def _callout_error_title(code: str) -> str:
    """The placard title is the registered diagnostic code's title — never a
    hand-rolled generic label. REGISTRY.get raises on an unregistered code,
    which cannot happen here: `code` came from exc.to_diagnostic(), which
    already required exc.code to be a registered ErrorCode.
    """
    return REGISTRY.get(code).title


def render_chart_item(
    chart: ResolvedChart,
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float = 300.0,
    *,
    resolved_style: ResolvedStyle,
    render_cache: RenderCache,
    padding: dict[str, float] | None = None,
    error_collector: list[Diagnostic] | None = None,
    title_shift: TitleShift = NO_TITLE_SHIFT,
) -> tuple[str, float]:
    """Render a chart item with explicit dimensions.

    Args:
        chart: ResolvedChart to render.
        executor: ChartDataProvider for query execution
        variables: Variable values for queries
        available_width: Width in pixels for the chart — the padded (outer)
            box; also what the tagged authoring group's own size becomes.
        available_height: Height in pixels for the chart
        resolved_style: Board-scoped ResolvedStyle for effective chart style.
        render_cache: Cache of pre-rendered SVGs shared with the sizing pass.
        padding: 4-sided padding dict (left/right/top/bottom). Vega-family forwards
            it to the spec, rendering at the outer size with ink pre-inset;
            SVG-family renders at the inner size and wraps in a translate so the
            inset content sits inside the padded box the tagged group carries.
        error_collector: When provided, per-chart Diagnostics are appended here.

    Returns:
        (svg_string, actual_height) — actual_height is the true rendered height.
    """
    from dbt_charts.core.diagnostics.execution import ExecutionError
    from dbt_charts.core.render.errors import RenderError

    chart_type = chart.chart_type
    is_svg_family = _uses_svg_family_layout_padding(chart_type)
    try:
        slot_width = available_width
        slot_height = available_height
        # SVG-family: shrink the leaf render by the 4-sided padding so the wrap
        # below positions the inset content inside the slot. The same value
        # travels on as `inset` and is what tells the wrapper to translate the
        # content back in — one decision, made once, so the two halves cannot
        # drift apart.
        inset: dict[str, float] | None = None
        if is_svg_family and padding is not None:
            inset = padding
            slot_width = max(
                available_width - float(padding["left"]) - float(padding["right"]),
                0.0,
            )
            slot_height = max(
                available_height - float(padding["top"]) - float(padding["bottom"]),
                0.0,
            )

        # Object title sizing reads the outer card width (inset + padding sides)
        # so chart and table at the same card position pick the same H slot.
        # SVG-family leaf renderers derive that locally from ``board_style``;
        # Vega-family already receives the outer width as ``width``.
        svg, actual_height = _render_chart_item_inner(
            chart,
            executor,
            variables,
            slot_width,
            slot_height,
            resolved_style=resolved_style,
            render_cache=render_cache,
            padding=padding,
            outer_width=available_width,
            inset=inset,
            title_shift=title_shift,
        )
        return svg, actual_height
    except (RenderError, ExecutionError, DbtChartsError) as e:
        # DbtChartsError is the shared base for RenderError, ChartDataError, and
        # compile-stage errors (e.g. JinjaError) that escape into render (e.g.
        # jinja in a chart title evaluated at render time); the KPI multirow
        # path (ChartDataError) is preserved via this catch.
        return _render_callout_block(
            e,
            chart,
            available_width,
            available_height,
            error_collector,
            resolved_style=resolved_style,
        )
    except Exception as e:  # noqa: BLE001
        wrapped = RenderError.from_code(ERR_INTERNAL, message=str(e))
        return _render_callout_block(
            wrapped,
            chart,
            available_width,
            available_height,
            error_collector,
            resolved_style=resolved_style,
        )


def _render_chart_item_inner(
    chart: ResolvedChart,
    executor: ChartDataProvider,
    variables: VariableValues,
    available_width: float,
    available_height: float,
    *,
    resolved_style: ResolvedStyle | None = None,
    render_cache: RenderCache,
    padding: dict[str, float] | None = None,
    outer_width: float,
    inset: dict[str, float] | None,
    title_shift: TitleShift,
) -> tuple[str, float]:
    """Internal chart rendering — raises on error.

    SVG-family charts use an explicit height; Vega-family auto-sizes vertically.
    Cache is only used for Vega-family.
    """
    # resolved_style comes from ResolvedBoard in the real render pipeline
    # (always non-None); the None defaults exist for test convenience only. Synthesize
    # here so render_chart_to_svg never receives None.
    if resolved_style is None:
        from dbt_charts.core.compile.resolve.style.board import resolve_style as _rs

        resolved_style = _rs(get_theme_style())

    from dbt_charts.core.compile.template.jinja import resolve_jinja_template
    from dbt_charts.core.render.chart.spec_builders import shift_artifact_title
    from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
    from dbt_charts.core.render.converters.chart import render_chart_artifact
    from dbt_charts.core.render.layout_sizing import build_chart_datasets

    chart_type = chart.chart_type
    is_svg_family = chart_type in SVG_LAYOUT_PADDED_TYPES
    paints_marks = chart_type in PAINTS_MARKS
    cache_key = (chart.id, available_width, available_height, title_shift)
    if not is_svg_family and cache_key in render_cache:
        chart_svg, _ = render_cache[cache_key]
        # The no-marks guard below needs the row count even on a cache hit.
        # Re-fetching here is a memo hit, never a re-execution: the executor's
        # per-render memo is unconditional (`--no-cache` disables only the
        # persistent store — see Executor.execute_query), so these are the
        # same rows the chart was resolved against.
        v2_data = (
            build_chart_datasets(chart, executor, variables)[chart.query_name]
            if paints_marks
            else []
        )
    else:
        datasets = build_chart_datasets(chart, executor, variables)
        v2_data = datasets[chart.query_name]
        artifact = render_resolved_chart(
            chart,
            v2_data,
            resolved_style,
            width=available_width,
            height=available_height,
            is_placeholder=False,
            datasets=datasets,
            padding=padding,
            title_shift=title_shift,
        )
        from dbt_charts.core.render.layout_sizing import (
            _support_table_of,
            _support_table_title_corrected_offset,
        )

        def draw() -> str:
            return render_chart_artifact(
                artifact,
                "svg",
                resolved_style,
                width=available_width,
                height=available_height,
                is_placeholder=False,
                chart_id=chart.id,
            )

        # Calibrate title.offset for titled top support_table charts so both the
        # sizing pass (dct render) and artifact replay (dct artifact render) apply
        # the same correction. The sizing pass caches the calibrated SVG and this
        # path is only reached on a cache miss (empty render_cache in artifact replay).
        # The row's title shift goes on after the calibration, which measures the
        # unshifted title.
        if (
            _support_table_of(chart) is not None
            and artifact.kind == "vega_spec"
            and isinstance(artifact.payload, dict)
        ):
            chart_svg = draw()
            spec = artifact.payload
            spec_target = spec["hconcat"][0] if "hconcat" in spec else spec
            redraw = not title_shift.is_zero
            if isinstance(spec_target, dict):
                title_block = spec_target.get("title")
                if isinstance(title_block, dict):
                    raw_offset = title_block.get("offset")
                    if isinstance(raw_offset, (int, float)):
                        corrected_offset = _support_table_title_corrected_offset(
                            chart_svg, float(raw_offset)
                        )
                        if corrected_offset is not None:
                            title_block["offset"] = corrected_offset
                            redraw = True
            if redraw:
                shift_artifact_title(artifact, title_shift)
                chart_svg = draw()
        else:
            shift_artifact_title(artifact, title_shift)
            chart_svg = draw()

    if paints_marks and v2_data:
        from dbt_charts.core.diagnostics.codes_render import ERR_CHART_PAINTED_NO_MARKS
        from dbt_charts.core.render.chart.mark_extents import (
            all_marks_degenerate,
            chart_has_nonzero_measure,
        )
        from dbt_charts.core.render.errors import RenderError

        # All-zero measure data is a faithful blank render — the engine painted
        # exactly what the data said — not a defect. Only fire when the rows
        # carried a measure value that should have painted.
        if chart_has_nonzero_measure(chart, v2_data) and all_marks_degenerate(
            chart_svg
        ):
            raise RenderError.from_code(
                ERR_CHART_PAINTED_NO_MARKS,
                chart_id=chart.id,
                row_count=len(v2_data),
            )

    # Resolve chart title for data attributes (Jinja only — no full resolve needed).
    # KPI charts carry their authored text in ``label``; every other chart type
    # uses ``title``. The wire-level ``data-chart-title`` attribute stays under
    # one name regardless (it is an accessibility/identification handle, not
    # tied to the YAML field name).
    chart_title_source = chart.label if chart.chart_type == "kpi" else chart.title
    chart_title = (
        resolve_jinja_template(chart_title_source, variables, strict=False)
        if chart_title_source
        else ""
    ) or chart.id

    return _wrap_rendered_chart_svg(
        ChartIdentity.from_resolved(chart),
        chart_svg,
        outer_width=outer_width,
        resolved_title=chart_title,
        ink_inset=(
            padding
            if padding is not None and _ink_carries_padding(chart)
            else _ZERO_PADDING
        ),
        inset=inset,
    )


def _ink_carries_padding(chart: ResolvedChart) -> bool:
    """True when the chart's whole SVG is one Vega render at the outer size, so
    its ink already sits inset by the padding its spec carried.

    False for a ``callout``, whose card ``render_svg_family`` returns full-bleed
    at 0,0 with the padding never applied, and false for a pie that composed a
    companion table: the wheel above carries the padding, but the table is flush
    with the composite's bottom edge, so the composite as a whole carries none.
    Claiming an inset over either one puts the block's pointer target on its own
    ink.
    """
    # From the family module, not the package: `resolved/__init__` re-exports
    # the `ResolvedChart` union, not its individual members.
    from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart

    if isinstance(chart, ResolvedPieChart) and chart.attached_table is not None:
        return False
    return chart.chart_type in PAINTS_MARKS


def _wrap_rendered_chart_svg(
    identity: ChartIdentity,
    chart_svg: str,
    outer_width: float,
    inset: dict[str, float] | None,
    ink_inset: dict[str, float],
    resolved_title: str | None = None,
    extra_classes: tuple[str, ...] = (),
    is_error_fallback: bool = False,
) -> tuple[str, float]:
    dims = extract_svg_dimensions(chart_svg)
    # Internal SVG-family renderers embed directly in the chart wrapper <g>;
    # extract their inner content to avoid a redundant nested <svg> viewport.
    # is_error_fallback marks the ChartDataError path, where the payload is a
    # render_callout_svg card on top of any chart.type (including Vega).
    # Vega/Vega-Lite output otherwise remains as a standalone nested <svg>.
    chart_type = identity.chart_type
    is_internal_svg = chart_type not in PAINTS_MARKS or is_error_fallback
    if is_internal_svg:
        chart_content = extract_svg_inner_content(chart_svg)
    else:
        chart_content = chart_svg

    # SVG-family (table/kpi/spark_bar) content was rendered at the padding-
    # shrunk inner size; wrap it in a translate so it sits inset inside the
    # padded outer box the tag now carries. A card rect the family paints
    # (svg_utils.card_box) already reaches back out over the same inset, so it
    # fills the outer box. Vega families already render at the outer size with
    # padding baked into their own spec — no wrap, and the outer box is the
    # measured size (Vega auto-sizes height).
    #
    # `inset` is the caller's own answer to "did I shrink this?" — present means
    # yes, and carries by how much. Shrinking and wrapping are two halves of one
    # decision, so they travel as one value: a separate boolean beside the dict
    # would let the two disagree.
    if inset is not None:
        # Width comes from the slot, never from `dims.width`: a table widened by
        # column overflow (table.py) or a spark_bar clamped up to its min width
        # renders past what it was given, and a box drawn from that reaches into
        # the neighboring slot. Height is a genuine render-time fact — these
        # families size to their content — so it does come from `dims`.
        inner_width = outer_width - inset["left"] - inset["right"]
        boundary = padded_authoring_content(
            chart_content, inner_width, dims.height, inset
        )
        actual_height = dims.height + px(inset["top"]) + px(inset["bottom"])
    else:
        # `ink_inset` is how far this content's own ink already sits inside the
        # outer box — the inner box has to say the same, or the pointer target
        # is the whole cell and the gutter between two blocks belongs to
        # whichever chart abuts it. Only the caller can answer it: a Vega
        # family bakes the padding into its spec and renders at the outer size,
        # while a hand-drawn card paints full-bleed at 0,0.
        boundary = selection_boxes(outer_width, dims.height, ink_inset) + chart_content
        actual_height = dims.height

    var_deps = identity.variable_dependencies
    notes = identity.notes

    var_attrs = (
        " ".join(f'data-var-{attr_name(v)}="true"' for v in sorted(var_deps))
        if var_deps
        else ""
    )

    chart_title = (resolved_title or identity.id) or identity.id
    notes_text = (
        notes
        or ""  # type-state: silent_fallback — no notes authored; empty string is the sentinel the data-chart-notes truthy-gate below reads as "omit the attribute"
    )
    authored_path = identity.source_path

    classes = ["dbt-chart", *extra_classes]
    if chart_type == "callout" and "dbt-chart-callout" not in classes:
        classes.append("dbt-chart-callout")
    class_attr = " ".join(classes)
    attrs_parts = [
        f'class="{escape_attr(class_attr)}"',
        f'id="chart-{escape_attr(identity.id)}"',
        f'data-chart-id="{escape_attr(identity.id)}"',
        f'data-chart-type="{escape_attr(chart_type)}"',
        f'data-chart-title="{escape_attr(chart_title)}"',
        f'data-chart-width="{escape_attr(outer_width)}"',
        f'data-chart-height="{escape_attr(actual_height)}"',
        f'aria-label="{escape_attr(chart_title)}"',
    ]
    # `defined_in_other_file` means the path names a ChartRef string, not a
    # definition — compile keeps the coordinates (a diagnostic anchors there),
    # but emitting a handle would give the host something to click that
    # repoints the reference rather than styling anything, so the block
    # carries no handle for it.
    if authored_path and not identity.defined_in_other_file:
        attrs_parts.append(authored_attrs(authored_path, "chart").strip())
    if notes_text:
        attrs_parts.append(f'data-chart-notes="{escape_attr(notes_text)}"')
    if var_attrs:
        attrs_parts.append(var_attrs)
    # A JS selection hook, not a visual property (stripped in normalize_svg
    # alongside data-dbt-series / data-dbt-value-label): chart_interactivity.js's
    # hover-emphasis reads this off the wrapper instead of sniffing the
    # rendered legend for a gradient node, which misses a hidden legend and a
    # faceted chart's legend-outside-every-panel shape alike. Absence means
    # safe to recede, so it is only ever emitted, never emitted as "false".
    if identity.magnitude_colored:
        attrs_parts.append('data-dbt-magnitude-colored="true"')
    if identity.canvas is not None and not is_error_fallback:
        attrs_parts.append(f'data-dbt-chart-canvas="{escape_attr(identity.canvas)}"')

    attrs_str = " " + " ".join(attrs_parts)
    return f"<g{attrs_str}>{boundary}</g>", actual_height
