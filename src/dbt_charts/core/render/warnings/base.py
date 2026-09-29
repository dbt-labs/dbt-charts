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
  - vega_specs: chart id → Vega-Lite spec dict.
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

vega_specs is SPARSE: KPI, text, and markdown charts do not compile to
Vega-Lite, so their ids are OMITTED from this dict (not present as None).
Detectors that key into vega_specs must guard with:
    if chart_id not in ctx.vega_specs:
        continue

table_overflows is likewise SPARSE: only tables that overflowed their slot
appear, and only on SVG-family renders (non-SVG formats never rasterize a
table, so it is empty for them).

static_pagination_caps is likewise SPARSE: only tables whose static export
hit the pre-rendered-page cap appear, and only on SVG-family renders (a
static export is the only surface that pre-renders pages at all — non-SVG
formats and interactive hosts never populate it).

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
from typing import Any

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.compile.models.board.resolved import ResolvedBoard
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedChart,
    ResolvedLineChart,
)
from dbt_charts.core.execute.executor import TruncationInfo
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.axis_label_collision import AxisLabelCollision
from dbt_charts.core.render.chart.endpoint_label_overflow import (
    EndpointLabelGapOverflow,
)
from dbt_charts.core.render.chart.plot_width_floor_record import (
    PlotWidthShareWarning,
)
from dbt_charts.core.render.chart.series_label_truncation import SeriesLabelTruncation
from dbt_charts.core.render.chart.table_overflow import (
    TableCramping,
    TableOverflow,
)
from dbt_charts.core.render.chart.table_page_squeeze import TablePageSqueeze
from dbt_charts.core.render.chart.table_static_pagination import StaticPaginationCap
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
    # chart id → Vega-Lite spec dict (sparse — non-vega charts are omitted)
    vega_specs: dict[str, dict[str, Any]]
    # chart id → the table's slot overflow at render time (tables only; sparse —
    # captured from the real SVG render, so empty for non-SVG formats).
    table_overflows: dict[str, TableOverflow] = {}
    # chart id → the table's slot cramping at render time (tables only; sparse —
    # only tables the renderer had to degrade appear, and only on SVG renders).
    table_crampings: dict[str, TableCramping] = {}
    # chart id → the table's static-export page cap at render time (tables
    # only; sparse — only a static export that hit the cap populates this).
    static_pagination_caps: dict[str, StaticPaginationCap] = {}
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
    # chart id → nearest ancestor's literal authored layout height, in px
    # (sparse — see module docstring for why this isn't derived from
    # board_spec.layout's resolved .height).
    authored_chart_heights: dict[str, float] = {}
    # chart id → resolved height from the active-layout item's ResolvedLayoutItem.height.
    # Same key set as layout_charts by construction (collapsed/inactive zero-width
    # items are excluded from both).
    layout_chart_heights: dict[str, float] = {}
    # chart id → the resolved chart at its narrowest active layout placement.
    # Unlike board_spec.charts (catalog), these entries are picked directly from
    # the layout tree so style/geometry fields match the slot being judged.
    layout_charts: dict[str, ResolvedChart] = {}
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


def encoding_channel_type(vega_spec: dict[str, object], channel: str) -> str:
    """Vega-Lite encoding ``type`` for a channel ("x", "color", …).

    Returns "" when the spec declares no such channel or no type — Vega specs
    are generated and these keys are not guaranteed present; absence means "no
    categorical encoding here", which detectors treat as "do not fire".
    """
    encoding = vega_spec.get("encoding")
    if not isinstance(encoding, dict):
        return ""
    channel_def = encoding.get(channel)
    if not isinstance(channel_def, dict):
        return ""
    channel_type = channel_def.get("type")
    return channel_type if isinstance(channel_type, str) else ""


# Vega-Lite composition operators whose children are INDEPENDENT views: a child
# does not inherit the parent's `encoding`. Our own wrappers never hoist
# `encoding` onto a composition root anyway (see each `hoist_keys` in
# render/chart/translate.py), so this is belt-and-braces — but inheriting here
# would invent channels on a view that never declared them.
#
# `concat` (the general grid form) is listed for completeness: no emitter
# produces it today, but `TopLevelUnitSpec`/`TopLevelCompositeSpec`
# (compile/models/vega_lite/contracts.py) both permit it, so a spec carrying
# one should walk rather than silently yield nothing.
_INDEPENDENT_VIEW_CONTAINERS = ("hconcat", "vconcat", "concat")


def iter_mark_units(vega_spec: VLDict) -> Iterator[tuple[VLDict, VLDict]]:
    """Yield ``(unit, effective_encoding)`` for every leaf mark unit in a spec.

    A detector that reads only the top-level ``encoding`` sees nothing on a
    real bar spec: the emitter wraps every bar — layered *and* flat — in a
    ``layer`` array, hoisting only the shared position channel to the root and
    leaving each mark's own channels (``color``, ``xOffset``, the measure) on
    its layer. An endpoint-label rail wraps the whole thing again in an
    ``hconcat``/``vconcat``. Walking the composition operators is what makes
    such a detector fire at all.

    Every wrapper an emitter can produce is descended (production sites in
    ``render/chart/translate.py`` unless noted):

    * ``layer`` — rule layers, nested sub-layers, overlay layers, geo layers,
      and the support-table attachment (``render/chart/support_table_attachment.py``).
    * ``facet`` (child under ``spec``) — small multiples.
    * ``hconcat`` — the endpoint-label right pane.
    * ``vconcat`` — the endpoint-label top rail.
    * ``concat`` — never emitted today, walked because the VL contracts permit it.

    ``repeat`` is deliberately NOT descended: no emitter produces it, and its
    inner spec addresses columns through ``{"repeat": ...}`` templating rather
    than real field names, so a detector reading `field`/`type` off it would be
    reading a template, not data. Such a spec yields its own root, which
    carries no mark and is therefore ignored by every caller.
    """
    yield from _walk_mark_units(vega_spec, {})


def _walk_mark_units(
    vega_spec: VLDict, inherited_encoding: VLDict
) -> Iterator[tuple[VLDict, VLDict]]:
    """Recursive half of ``iter_mark_units``.

    ``inherited_encoding`` is required rather than defaulted: the empty dict
    belongs to the public entry point above, and defaulting it here would make
    "no parent" and "parent with no encoding" the same unwritten assumption.
    """
    own = vega_spec.get("encoding")
    encoding: VLDict = {
        **inherited_encoding,
        **(own if isinstance(own, dict) else {}),
    }
    # Facet: the operator form is `{facet: ..., spec: ...}`, which VL gives no
    # top-level `encoding` of its own, so the panel starts clean. (The other
    # facet form — a unit spec with row/column channels — has no `spec` key and
    # falls through to the leaf yield below, correctly.)
    if "facet" in vega_spec:
        panel = vega_spec.get("spec")
        if isinstance(panel, dict):
            yield from _walk_mark_units(panel, {})
        return
    for container in _INDEPENDENT_VIEW_CONTAINERS:
        children = vega_spec.get(container)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    yield from _walk_mark_units(child, {})
            return
    # Layer children DO inherit: VL resolves a layered view's channels against
    # the shared top-level encoding, which is exactly where the emitter hoists
    # a bar's `x` while leaving `xOffset` on the layer.
    layers = vega_spec.get("layer")
    if isinstance(layers, list):
        for child in layers:
            if isinstance(child, dict):
                yield from _walk_mark_units(child, encoding)
        return
    yield vega_spec, encoding


def unit_mark_type(unit: VLDict) -> str:
    """The mark type of one leaf unit — VL accepts a bare string or a dict.

    Returns "" when the unit declares no mark, which callers treat the same
    way as a mark they do not care about.
    """
    mark = unit.get("mark")
    if isinstance(mark, str):
        return mark
    if isinstance(mark, dict):
        mark_type = mark.get("type")
        return mark_type if isinstance(mark_type, str) else ""
    return ""


def facet_channel_is_independent(
    vega_spec: dict[str, object],  # type-state: object_annotation — generated VL dict
    channel: str,
) -> bool:
    """True when the emitted facet-root ``resolve.scale.<channel>`` is
    ``"independent"`` — the signal ``facet_bound_position_channels``
    (``render/chart/emitters/_cartesian.py``) leaves on a faceted spec when a
    panel's own rows carry a proper subset of a position channel's domain
    and its scale resolves independently per panel. A detector computing a
    per-band/per-slot pixel width from the chart's whole, unpartitioned row
    count must check this first: once the channel is independent, each
    panel's domain is genuinely narrowed to ITS OWN subset — which can be
    any size, not always one value — so a detector must read the WIDEST
    panel's own count (``widest_panel_distinct_count``,
    ``emitters/_cartesian.py``), not assume a flat 1 and not fall back to
    the union `rows` would give.

    Absence at any level (no ``resolve``, no ``scale``, or a ``scale`` dict
    without this channel) means "not independent" — a vega spec is generated
    and these keys are only present when something actually resolved
    independently, matching ``encoding_channel_type``'s own absence
    convention above.
    """
    resolve = vega_spec.get("resolve")
    if not isinstance(resolve, dict):
        return False
    scale = resolve.get("scale")
    if not isinstance(scale, dict):
        return False
    return scale.get(channel) == "independent"
