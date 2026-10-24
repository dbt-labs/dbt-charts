"""Public chart rendering entrypoints."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path  # noqa: TID251 — DCT_TRACE_VL debug spec dump
from typing import TYPE_CHECKING, Any

from dbt_charts.core.execute.executor import utc_now
from dbt_charts.core.render.chart._types import VLDict

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

_log = logging.getLogger(__name__)


def _trace_vl_spec(chart_id: str, spec: VLDict) -> None:
    """Write *spec* to DCT_TRACE_VL/<chart_id>.json when the env var is set.

    Set DCT_TRACE_VL to any directory path to enable.  The directory is created
    on first use.
    """
    trace_dir = os.getenv("DCT_TRACE_VL")  # noqa: TID251 — DCT_TRACE_VL debug knob
    if not trace_dir:
        return
    # Map the chart id to a safe basename so a id containing path separators or
    # ".." can't write outside the trace directory.
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", chart_id).strip(".") or "chart"
    out = Path(trace_dir) / f"{safe_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
    _log.debug("VL spec trace → %s", out)


from dbt_charts.core.compile.models.board.normalized import (
    NO_TITLE_SHIFT,
    TitleShift,
)
from dbt_charts.core.compile.models.chart.normalized import SVG_LAYOUT_PADDED_TYPES
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _SharedResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.resolve.chart.label_data import (
    pie_presentation_fingerprint,
)
from dbt_charts.core.diagnostics import (
    ERR_RESOLVED_PIE_DATA_MISMATCH,
    ERR_RESOLVED_PIE_WIDTH_MISMATCH,
)
from dbt_charts.core.render.chart.artifacts import ChartRenderData, RenderArtifact
from dbt_charts.core.render.chart.serialization import build_dbt_charts_json
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.title_overflow import apply_title_overflow_to_spec
from dbt_charts.core.render.converters.chart import render_chart_artifact
from dbt_charts.core.render.errors import RenderError

_TOP_LEGEND_ORIENTS = frozenset({"top", "top-left", "top-right"})


def _legend_has_nothing_to_draw(node: VLDict, legend: VLDict) -> bool:
    """Whether a legend over an empty dataset has no title and no entries.

    A legend's entries come from the data unless the author fixed a domain or
    ``values``; it is titleless when its encoding writes ``title: null`` (an
    absent ``title`` falls back to the field name).
    """
    scale = node.get("scale")
    has_domain = isinstance(scale, dict) and scale.get("domain") is not None
    titleless = "title" in node and not node["title"]
    return titleless and not (has_domain or legend.get("values"))


def drop_empty_legends(node: VLDict) -> None:
    """Drop the legends Vega cannot lay out from a spec over no rows.

    Vega places a legend with no entries at ``-Number.MAX_VALUE`` unless a title
    gives it a box: a titleless one paints the chart at an infinite offset. A
    legend on the top edge moves its whole view to ``translate(…, Infinity)``
    even with a title. A legend with a title on any other edge keeps a sane box
    and is left alone.
    """
    legend = node.get("legend")
    if isinstance(legend, dict) and (
        legend.get("orient") in _TOP_LEGEND_ORIENTS
        or _legend_has_nothing_to_draw(node, legend)
    ):
        node["legend"] = None
    for child in node.values():
        for part in child if isinstance(child, list) else [child]:
            if isinstance(part, dict):
                drop_empty_legends(part)


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
) -> RenderArtifact:
    """Render an already-resolved chart to a RenderArtifact.

    Takes a data-resolved ResolvedChart (no re-resolve).  Two callers:
    (a) render_chart() — resolves and delegates here;
    (b) render_chart_item() — passes the tree's data-resolved chart straight in.

    Every family but the geo maps renders in the Rust engine and yields an
    ``svg`` artifact; a geo map yields the ``vega_spec`` artifact the caller
    converts. ``title_shift`` moves the engine-drawn titles; a Vega-Lite spec is
    left for the caller to shift with ``shift_artifact_title``, after any
    title-offset calibration. ``measuring`` marks a sizing probe, whose height
    is the box the layout sizes the slot from; ``authored_height`` is the
    literal slot height an ancestor authored.

    A pie renders at the width its data-aware layout was resolved for;
    omission reuses it and a conflicting explicit width is rejected. Other
    families fall back to the theme-family width for callers without a slot.
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
    else:
        from dbt_charts.core.compile.resolve import default_chart_width

        resolved_width = (
            width
            if width is not None
            else default_chart_width(resolved.chart_type, resolved_style.chart_defaults)
        )

    from dbt_charts.core.render.chart.session import BoardRenderSession

    session = BoardRenderSession.create(resolved_style)
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
    )
    if svg is not None:
        return RenderArtifact(kind="svg", payload=svg)
    # render_svg_family only returns None for the geo maps, which are
    # _SharedResolvedChartFields subclasses — title_style is present below.
    assert isinstance(resolved, _SharedResolvedChartFields)

    box = RenderBox(
        width=resolved_width,
        height=height
        if height is not None
        else resolved_style.chart_defaults.view.continuous_height,
    )
    chart_spec = session.emit_chart(resolved, box, full_datasets)
    vl = session.finalize_vl(chart_spec)
    if not any(full_datasets.values()):
        drop_empty_legends(vl)
    if resolved.link_open != "page":
        # vl_convert writes the anchors; _fix_chart_click_hrefs stamps them.
        vl["$df_link_open"] = resolved.link_open
    if padding is not None:
        vl["padding"] = padding
    if resolved_width > 0:
        vl["width"] = resolved_width
    if height is not None and height > 0:
        vl["height"] = height
    apply_title_overflow_to_spec(vl, resolved.title_style, chart_id=resolved.id)
    _trace_vl_spec(resolved.id, vl)
    return RenderArtifact(kind="vega_spec", payload=vl)


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
        return render_chart_artifact(
            artifact,
            format,
            resolved_style,
            width=width,
            height=height,
            is_placeholder=is_placeholder,
            chart_id=chart.id,
        )

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
    return render_chart_artifact(
        artifact,
        format,
        resolved_style,
        width=resolved_width,
        height=height,
        chart_id=resolved.id,
        is_placeholder=is_placeholder,
    )
