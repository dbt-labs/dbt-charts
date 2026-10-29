"""Public chart rendering entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from dbt_charts.core.execute.executor import utc_now

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.normalized import SVG_LAYOUT_PADDED_TYPES
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.models.primitives import PieMode
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics import (
    ERR_RESOLVED_PIE_DATA_MISMATCH,
    ERR_RESOLVED_PIE_WIDTH_MISMATCH,
)
from dbt_charts.core.render.chart.artifacts import ChartRenderData, RenderArtifact
from dbt_charts.core.render.chart.serialization import build_dbt_charts_json
from dbt_charts.core.render.converters.chart import render_chart_artifact
from dbt_charts.core.render.errors import RenderError
from dbt_charts.core.render.pie_layout import sized_pie_mode


def render_resolved_chart(
    resolved: ResolvedChart,
    render_data: ChartRenderData,
    resolved_style: ResolvedStyle,
    *,
    width: float | None = None,
    height: float | None = None,
    is_placeholder: bool = False,
    datasets: dict[str | None, ChartRenderData] | None = None,
    padding: dict[str, int | float] | None = None,
    title_shift: TitleShift = NO_TITLE_SHIFT,
    measuring: bool = False,
    authored_height: float | None = None,
    pie_mode: PieMode | None = None,
) -> RenderArtifact:
    """Render an already-resolved chart to a RenderArtifact.

    Takes a data-resolved ResolvedChart (no re-resolve).  Two callers:
    (a) render_chart() — resolves and delegates here;
    (b) render_chart_item() — passes the tree's data-resolved chart straight in.

    Every family renders in the Rust engine and yields an ``svg`` artifact.
    ``title_shift`` moves the engine-drawn titles. ``measuring`` marks a sizing probe, whose height
    is the box the layout sizes the slot from; ``authored_height`` is the
    literal slot height an ancestor authored.

    A pie renders at the width its data-aware layout was resolved for;
    omission reuses it and a conflicting explicit width is rejected. Other
    families fall back to the theme-family width for callers without a slot.

    ``pie_mode`` is a pie's mode as the size stage decided it. Omitted (a pie no
    sizing pass placed), it is decided here, once, in the pie's own box.
    """
    if isinstance(resolved, ResolvedPieChart):
        if width is not None and width != resolved.resolution_width:
            raise RenderError.from_code(
                ERR_RESOLVED_PIE_WIDTH_MISMATCH,
                chart_id=resolved.id,
                resolved_width=resolved.resolution_width,
                render_width=width,
            )
        # Labels, label ink and the attached table's rows were frozen against
        # the rows the pie resolved with.
        if (
            pie_presentation_fingerprint(render_data)
            != resolved.presentation_fingerprint
        ):
            raise RenderError.from_code(
                ERR_RESOLVED_PIE_DATA_MISMATCH, chart_id=resolved.id
            )
        resolved_width = resolved.resolution_width
        if pie_mode is None:
            pie_mode = sized_pie_mode(resolved, render_data, resolved_style)
    else:
        from dbt_charts.core.compile.resolve import default_chart_width

        resolved_width = (
            width
            if width is not None
            else default_chart_width(resolved.chart_type, resolved_style.chart_defaults)
        )

    from dbt_charts.core.render.chart.session import BoardRenderSession

    session = BoardRenderSession(resolved_style)
    # The chart's own rows are always in the map; `datasets` carries a layered
    # chart's per-layer queries.
    full_datasets: dict[str | None, ChartRenderData] = (
        dict(datasets) if datasets else {}
    )
    if resolved.query_name not in full_datasets:
        full_datasets[resolved.query_name] = render_data
    svg = session.render_svg_family(
        resolved,
        render_data,
        width=resolved_width,
        height=height,
        is_placeholder=is_placeholder,
        # The same gate rendering.py shrinks by: only the padded families were
        # rendered at the inner size, so only they have an inset to grow over.
        inset=padding if resolved.chart_type in SVG_LAYOUT_PADDED_TYPES else None,
        padding=padding,
        title_shift=title_shift,
        datasets=full_datasets,
        measuring=measuring,
        authored_height=authored_height,
        pie_mode=pie_mode,
    )
    return RenderArtifact(kind="svg", payload=svg)


def render_chart(
    chart: Chart,
    resolved_style: ResolvedStyle,
    chart_style_context: ChartStyleContext,
    data: list[dict[str, Any]],
    format: str = "json",
    width: float | None = None,
    height: float | None = None,
    is_placeholder: bool = False,
    datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    padding: dict[str, Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> str:
    """Render a chart to JSON, SVG, PNG, or PDF.

    ``clock`` is the render time annotation clock anchors read.
    """
    if format == "json":
        artifact: RenderArtifact = RenderArtifact(
            kind="json",
            payload=build_dbt_charts_json(chart, data, width=width, height=height),
        )
        return render_chart_artifact(artifact, format)

    render_data: ChartRenderData = data
    # A custom template names its own columns; generic placeholder rows would
    # fail its column params, so it draws a blank chart's empty rows instead.
    if is_placeholder and not data and chart.type != "custom":
        from dbt_charts.core.render.placeholder import generate_placeholder_data

        render_data = generate_placeholder_data(chart.type, chart)

    from dbt_charts.core.compile.resolve import preferred_chart_width, resolve

    # This is the entry point's one width resolution: an omitted width falls
    # back to the chart's authored width. Every downstream call (resolve,
    # render_resolved_chart, render_chart_artifact) gets the concrete result.
    resolved_width = (
        width
        if width is not None
        else preferred_chart_width(chart, chart_style_context)
    )
    resolved = resolve(
        chart,
        render_data,
        chart_style_context=chart_style_context,
        width=resolved_width,
        datasets=(
            ...
            if datasets is None
            else {name: rows for name, rows in datasets.items() if name is not None}
        ),
        display_text="raw",
        rendered_at=clock(),
    )
    artifact = render_resolved_chart(
        resolved,
        render_data,
        resolved_style,
        width=resolved_width,
        height=height,
        is_placeholder=is_placeholder,
        datasets=datasets or None,
        padding=padding,
    )
    return render_chart_artifact(artifact, format)
