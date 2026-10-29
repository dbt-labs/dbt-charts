"""Pydantic models for render-time warnings.

Diagnostic (the wire shape for a single warning) lives in
``dbt_charts.core.diagnostics.diagnostic`` — below render — so compile-time
authoring warnings can construct it without depending on render.

WarningContext carries everything detectors need:
  - board_spec: the compiled Board (typed — detectors use attribute access).
  - chart_results: chart id → list of row dicts (query output).
  - layer_results: chart id → layer query name → list of row dicts, for
    typed overlay layers that carry their own `query:`. Sparse — only
    charts with a layer query distinct from the base chart's appear.
  - table_overflows: chart id → the table's slot overflow captured at render.
  - table_crampings: chart id → the table's width cramping (columns under
    their demand, headers wrapped) captured at render. Sparse.
  - authored_chart_heights: chart id → the nearest ancestor's literal
    authored layout height, in px.
  - text_truncations: chart id → list of TextTruncation records captured
    by renderers when any user-visible text is clipped or cut with an
    ellipsis. Sparse — only charts with truncated text appear.
  - chart_truncations: chart id → TruncationInfo record for a query result
    truncated by execution.max_rows/max_result_bytes. Sparse — only charts
    whose query was actually truncated this render appear.

table_overflows is likewise SPARSE: only tables that overflowed their slot
appear, and only on SVG-family renders (non-SVG formats never rasterize a
table, so it is empty for them).

static_paginations is likewise SPARSE: only tables a static export paginated
appear, and only on SVG-family renders (the text format reads its own copy).

table_page_squeezes is likewise SPARSE: only tables whose layout slot fit
fewer rows than their resolved page size appear, and only on SVG-family
renders. A table paginating because its data is genuinely longer than its
page is absent — the slot and the page agreed.

authored_chart_heights is likewise SPARSE: a chart with no authored height
anywhere in its ancestor chain is OMITTED, not present as 0 or None. The
value is a snapshot of the chart's real, assigned px slot height, taken by
build_resolved_board (via layout_sizing._snapshot_authored_slot_heights)
immediately after the sizing pass assigns it but before cols-alignment can
re-expand it — for a `cols:`-wrapped chart, that alignment step re-expands
the resolved height to the chart's natural/unconstrained size afterward to
keep siblings visually aligned, discarding the authored cap. Because the
value is read off the sizing pass's own output (not re-derived), it already
reflects a `rows:` wrapper splitting its height across children and a
percentage-authored height resolved against real available space.

text_truncations is SPARSE: only charts where any user-visible text was cut
with an ellipsis (or silently clipped) appear. The warning pass re-renders
all charts (including non-VL families) with the sink open, so table, KPI,
callout, and spark_bar truncations are captured alongside VL axis titles.

contrast_warnings is a flat list, not chart-keyed like the other captures
above: a low-contrast pair comes from a board-level text block, which has
no chart id at all — each ContrastRecord carries its own optional
chart/path instead of relying on a dict key. Populated the same way as
text_truncations — see render/contrast_warning.py's
collect_contrast_warnings().
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedChart,
    ResolvedLineChart,
)
from dbt_charts.core.compile.models.primitives import PieMode
from dbt_charts.core.diagnostics.registry import WarningCode
from dbt_charts.core.execute.executor import TruncationInfo
from dbt_charts.core.render.chart.annotation_nothing_drawn import NothingDrawn
from dbt_charts.core.render.chart.axis_label_collision import AxisLabelCollision
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
)
from dbt_charts.core.render.chart.geometry_warnings import (
    EngineParams,
    GeometryWarnings,
)
from dbt_charts.core.render.chart.pie_total_overflow import PieTotalOverflow
from dbt_charts.core.render.chart.plot_width_floor_record import (
    PlotWidthShareWarning,
)
from dbt_charts.core.render.chart.series_label_truncation import SeriesLabelTruncation
from dbt_charts.core.render.chart.table_overflow import (
    TableCramping,
    TableOverflow,
)
from dbt_charts.core.render.chart.table_page_squeeze import TablePageSqueeze
from dbt_charts.core.render.chart.table_static_pagination import StaticPagination
from dbt_charts.core.render.chart.text_truncation import TextTruncation
from dbt_charts.core.render.chart.x_domain_paint_order import XDomainPaintOrder
from dbt_charts.core.render.contrast_warning import ContrastRecord
from dbt_charts.core.utils import Rows


class WarningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_spec: ResolvedBoard
    # chart id → list of row dicts from the executed query
    chart_results: dict[str, Rows]
    # chart id → layer query name → rows, for typed overlay layers whose own
    # `query:` differs from the base chart's. Sparse — a chart with no such
    # layer is absent, and a layer sharing the base query is served by
    # chart_results (its rows are the same result set).
    layer_results: dict[str, dict[str, Rows]] = {}
    # chart id → warning code → the params of each warning the engine raised
    # about the chart's laid-out geometry (sparse — see chart/geometry_warnings.py).
    geometry_warnings: GeometryWarnings = {}
    # chart id → the table's slot overflow at render time (tables only; sparse —
    # captured from the real SVG render, so empty for non-SVG formats).
    table_overflows: dict[str, TableOverflow] = {}
    # chart id → the table's slot cramping at render time (tables only; sparse —
    # only tables the renderer had to degrade appear, and only on SVG renders).
    table_crampings: dict[str, TableCramping] = {}
    # chart id → authored-left-aligned numeric columns whose rendered values
    # are not all the same width (tables only; sparse — see
    # render/chart/table_align.py's module docstring for the two-outcome split
    # with table_align_no_visible_effects).
    table_numeric_align_lefts: dict[str, list[str]] = {}
    # chart id → authored-left-aligned numeric columns with no slack beyond
    # their content demand, where left and decimal alignment paint identically
    # (tables only; sparse).
    table_align_no_visible_effects: dict[str, list[str]] = {}
    # chart id → how the table paginated in a static export (tables only; sparse
    # — only a paginated table appears, and only on SVG-family renders).
    static_paginations: dict[str, StaticPagination] = {}
    # chart id → the page the table's slot cut down at render time (tables
    # only; sparse — only a table whose slot fit fewer rows than its page holds
    # appears, and only on SVG-family renders).
    table_page_squeezes: dict[str, TablePageSqueeze] = {}
    # chart id → the endpoint-label rail's gap overflow at render time
    # (right_pane layout only; sparse — captured from the real SVG render's
    # probe, so only charts whose intended gap didn't fit appear).
    endpoint_label_gap_overflows: dict[str, EndpointLabelGapOverflow] = {}
    # chart id → the shared categorical x domain a layer widened that the base
    # stated no order to absorb, recorded by rendered_x_domain at the point it
    # declined to place them (sparse — only charts left in paint order appear).
    x_domain_paint_orders: dict[str, XDomainPaintOrder] = {}
    # chart id → annotation index → why the engine drew nothing for it (sparse).
    annotation_nothing_drawn: NothingDrawn = {}
    # chart id → nearest ancestor's literal authored layout height, in px
    # (sparse — see module docstring for why this isn't derived from
    # board_spec.layout's resolved .height).
    authored_chart_heights: dict[str, float] = {}
    # chart id → the resolved chart at its narrowest active layout placement.
    # Unlike board_spec.charts (catalog), these entries are picked directly from
    # the layout tree so style/geometry fields match the slot being judged.
    layout_charts: dict[str, ResolvedChart] = {}
    # chart id → a pie's mode as the size stage decided it for the same
    # placement as ``layout_charts``; absent for every chart that is not a pie.
    pie_modes: dict[str, PieMode] = {}
    # chart id → the sized mode of every active placement of a pie (``pie_modes``
    # keeps only the narrowest placement's).
    pie_placement_modes: dict[str, tuple[PieMode, ...]] = {}
    # chart id → the series labels the endpoint-label rail cap cut, captured at
    # the single measure site (render/chart/features/endpoint_labels.py). Sparse —
    # only charts whose rail was actually degraded appear.
    series_label_truncations: dict[str, list[SeriesLabelTruncation]] = {}
    # chart id → the x-axis label collision the render captured when every
    # enabled overlap strategy (skip/tilt) still left labels overlapping.
    # Sparse — only line/area/scatter charts (see axis_label_collision.py's
    # module docstring for the bar/heatmap scoping) whose axis genuinely
    # never fit appear.
    axis_label_collisions: dict[str, AxisLabelCollision] = {}
    # chart id → list of text truncations captured at render time across all
    # surfaces (axis titles included). Sparse — only charts with truncated
    # text appear.
    text_truncations: dict[str, list[TextTruncation]] = {}
    # chart id → the support_table column block's width-share fact at
    # render time (bar horizontal-category-axis charts only; sparse —
    # only a chart whose column block already claims most of the
    # pre-axis-chrome footprint appears).
    plot_width_share_warnings: dict[str, PlotWidthShareWarning] = {}
    # chart id → the donut whose center total the engine measured wider than
    # the hole it drew. Sparse.
    pie_total_overflows: dict[str, PieTotalOverflow] = {}
    # chart id → the query-result truncation record (execution.max_rows /
    # max_result_bytes) for that chart's query, from Executor.truncations().
    # Sparse — only charts whose query was actually truncated appear.
    chart_truncations: dict[str, TruncationInfo] = {}
    # query name → truncation record for queries that were truncated but whose
    # query_name does not map to any chart (e.g. upstream queries demand-executed
    # by a cache-ref composition — only the composed query is charted, not its
    # upstreams). Sparse — only present when such orphan truncations exist.
    unattributed_truncations: dict[str, TruncationInfo] = {}
    # Every low-contrast ink/background pair the render pass captured. Flat,
    # not chart-keyed — see the module docstring above.
    contrast_warnings: list[ContrastRecord] = []


# Labels quoted in full before a message falls back to a count. Enough to
# recognize which values are the problem without pasting a whole column in.
_MAX_LISTED = 3


def listed_phrase(items: list[str]) -> str:
    """Quote up to ``_MAX_LISTED`` items, then count the remainder."""
    quoted = [repr(item) for item in items]
    if len(quoted) <= _MAX_LISTED:
        return ", ".join(quoted)
    remaining = len(quoted) - _MAX_LISTED
    return f"{', '.join(quoted[:_MAX_LISTED])} and {remaining} more"


@dataclass(frozen=True)
class ChartSeries:
    """The authored key/field a series-count diagnostic should name so its
    message points at YAML the author actually wrote: ``color`` for a
    color-encoded chart, ``y`` for a wide chart whose series ARE its measure
    list.
    """

    authored_key: str
    authored_field: str


def chart_series(chart: ResolvedChart) -> ChartSeries | None:
    """The authored key/field a chart's series come from, or None when its
    color channel makes none.

    Two authoring shapes produce series: a ``color:`` column, or a wide
    ``y: [a, b]`` measure list (resolve injects a synthetic color channel for
    it, so it takes the ``wide_measures`` branch, not the presence of a color
    field).

    ``mode`` is the gate, not the presence of a ``data_field``: a color channel can
    carry a field without splitting the chart into series. ``conditional_formatting``
    lowers its rules onto the color channel with ``data_field`` set to the *measure*
    (mode ``conditional``), and a continuous ramp does the same (mode ``gradient``);
    both paint one mark per row of a single series. Counting either would report one
    "series" per distinct measure value on a chart that draws one.
    """
    color = chart.resolved_channels.get("color")
    if color is None or color.mode != "series" or not color.data_field:
        return None
    color_field = color.data_field

    if (
        isinstance(chart, (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart))
        and chart.wide_measures
    ):
        if chart.color is None:
            return ChartSeries("y", "y")
        return ChartSeries("color", chart.color)

    return ChartSeries("color", color_field)


def engine_warnings(
    ctx: WarningContext, code: WarningCode
) -> Iterator[tuple[str, EngineParams]]:
    """Every ``(chart_id, params)`` the engine raised for ``code`` in this render."""
    for chart_id, by_code in ctx.geometry_warnings.items():
        if code.code in by_code:
            for params in by_code[code.code]:
                yield chart_id, params
