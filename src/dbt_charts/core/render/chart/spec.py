"""Render-local intermediate chart representation.

``ChartSpec`` is produced by a family emitter and mutated by the ``ChartFeature``
pipeline before being assembled to Vega-Lite by ``assemble_final_vl()``
(see ``translate.py``).  Emitters write Vega-Lite encoding and mark names
directly — ChartSpec is a VL-shaped builder intermediate, not a dbt charts-native
vocabulary.  The two structural dispatch sentinels (``"layered"`` and
``"geoshape"``) are not VL marks; they drive composition shape detection in the
assembler.

This is deliberately a mutable ``dataclass``, not a frozen Pydantic model — features
accumulate mutations (overlay layers, config overrides) before the spec is sealed for
assembly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from dbt_charts.core.utils import CellValue

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
    from dbt_charts.core.compile.models.style.theme import TitleStyle


@dataclass(frozen=True)
class RenderBox:
    """Render-time layout slot geometry for one chart emission.

    Both dimensions are the extent the chart will actually render at. When the
    caller has no explicit height (``height: null`` — "let Vega auto-size
    vertically"), it resolves the height Vega-Lite itself will use from the
    theme before constructing the box, so consumers never have to model the
    absent case or invent a fallback.
    """

    width: float
    height: float
    # The per-column-panel width a faceted chart WOULD use if no position
    # channel narrowed independently — i.e. `width` before any
    # `facet_extra_axis_width_px()` reservation is subtracted. `None` for a
    # non-faceted chart (nothing to narrow) or a caller that built this box
    # without running the real facet geometry (e.g. a test harness's fixed
    # box). `FacetFeature` (`features/facet.py`) invents no stand-in when it
    # is None — it passes the value straight through.
    #
    # None is PERMISSIVE, not restrictive: `facet_bound_position_channels`
    # starts from the full candidate set and the affordability gate only ever
    # `discard`s from it, so a None here makes that discard unreachable and
    # narrowing proceeds unchecked — `resolve.scale.y = independent` gets
    # stamped with no width reserved for the axis it forces. A box built
    # without the real facet geometry is therefore the permissive case; that
    # is tolerable only because the sole `src/` construction site always
    # supplies it.
    #
    # Do NOT "helpfully" default this to `width` either: `width` has already
    # had the reservation subtracted, so re-checking against it double-charges
    # the axis and declines narrowing that was in fact affordable.
    # Populated by `_render_vl_artifact`
    # (`vega_lite.py`) so the affordability check in
    # `facet_bound_position_channels` (`emitters/_cartesian.py`) has the same
    # number both there and in the pre-spec width budget it must agree with.
    facet_unnarrowed_panel_width: float | None = None
    # How many facet panels each dimension was divided across to get
    # ``width`` / ``height`` — 1 when nothing was divided. Chrome outside the
    # plot (title block, view padding) is a whole-chart cost paid once, so a
    # panel owes only its share of it; ``axis_title_budget`` divides by these.
    # ``panel_rows`` is 1 even on a row-faceted chart when no height was
    # authored: VL then falls back to ``config.view.continuousHeight``, which
    # is already a per-unit height, so nothing was divided.
    panel_rows: int = 1
    panel_cols: int = 1


@dataclass
class EndpointLabelData:
    """Pre-computed endpoint label positions set by ``EndpointLabelFeature``.

    ``positions`` holds one ``(series_name, position_value)`` tuple per series.
    For ``"right_pane"`` layout ``position_value`` is the y-coordinate of the
    series' last data point; for ``"top_rail"`` layout it is the x-midpoint of
    the series' segment in the top categorical row.

    For ``"right_pane"``, ``positions`` carries the RAW, un-cascaded anchor
    values — the greedy-nudge pass is deferred until the real plot geometry is
    known (``render/converters/chart.py``'s post-probe re-cascade, which calls
    ``features/endpoint_labels.recascade_endpoint_labels``), because the
    pixel<->data conversion the cascade needs depends on the rendered plot
    height and y-scale, neither of which exists until vl-convert has actually
    laid the chart out. ``label_gap_px``/``y_domain_min``/``y_domain_max``
    carry what that later pass needs; they are unused (left at their defaults)
    on ``"top_rail"``, which has no cascade at all. ``positions`` for
    ``"top_rail"`` is genuinely final — no later pass revisits it.
    """

    series_field: str
    value_alias: str  # column alias for the position field ("__y" or "__x")
    positions: list[tuple[str, float]]  # (series_name, position_value)
    color_domain: list[str]  # ordered series names
    color_range: list[str]  # palette entries in same order
    # Sourced from the resolved EndpointLabelsConfig — no in-code default. translate.py
    # can't reach the resolved style directly (it takes only ChartSpec), so the value
    # must be threaded through here by the feature that has style access.
    label_offset: float  # gap between main pane and label pane (pixels)
    height: float  # top_rail pane height (pixels)
    # "right_pane" only (0.0 = unset on "top_rail"): the intended pixel gap
    # between adjacent labels, and the raw data-value bounds the post-probe
    # re-cascade clamps into. See the class docstring.
    label_gap_px: float = 0.0
    y_domain_min: float = 0.0
    y_domain_max: float = 0.0
    # Dark-companion ink for label text.  When non-empty, translate.py uses this
    # for the label pane color scale instead of color_range so label text carries
    # readable contrast against the background (darker ink) rather than the bright
    # mark color.  Empty list → fall back to color_range.
    # Populated by callers that pre-bake dark companion stops at resolve time
    # (analogous to ResolvedPieChart.dark_companion_stops) — compile.palette is
    # banned from render/chart/ per the import-boundary test.
    dark_companion_range: list[str] = field(default_factory=list)
    # Label pane pixel width measured from series names.  0 = unset (no explicit
    # pane width — VL auto-sizes, which causes the overshoot corrector to compress
    # the main pane).  Set by EndpointLabelFeature, already capped there to
    # chart_rendering.endpoint_labels.max_width_fraction of the chart's width.
    label_pane_width: float = 0.0
    # VL text-mark font props {"fontSize", "font", "fontWeight", "fontStyle"}
    # for the label pane.
    # Empty dict → no explicit font (VL uses global config defaults).
    # Populated by EndpointLabelFeature from chart.style.series_label.font_*.
    label_mark_font_props: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartSpec:
    """Mutable render-local intermediate produced by an emitter and mutated by features.

    Attributes:
        mark: VL mark name (``"bar"``, ``"line"``, ``"arc"``, ``"rect"``,
            ``"circle"``, ``"rule"``, …) OR a structural sentinel
            (``"layered"`` / ``"geoshape"``) that drives composition shape
            detection in ``assemble_final_vl``.  Emitters write VL names
            directly — no dbt charts-native mark vocabulary.
        encoding: Channel name → encoding config (VL keys).
        layers: Overlay sub-specs appended by features (zero-baseline rule layers,
            etc.).  Empty for single-layer charts before features run.
        config: Vega-Lite ``config`` block passthrough.  Only VL ``config``
            object keys belong here — no hint-bag entries.
        mark_props: Extra mark-level VL properties merged into the mark object
            (e.g. ``{"innerRadius": 90}`` for donut charts).  When non-empty,
            ``assemble_final_vl`` emits ``{"mark": {"type": <vl_mark>, ...}}``
            instead of a bare mark string.
        projection: Map projection name; emitted as ``{"projection": {"type": ...}}``
            by ``assemble_final_vl`` for geoshape/circle families.
        data_name: Named dataset for secondary data sources (layered sub-layers).
        endpoint_label_layout: Composition mode set by ``EndpointLabelFeature``.
            ``"right_pane"`` → hconcat with a right-side text-mark pane (line/area).
            ``"top_rail"`` → vconcat with a top text-mark rail (horizontal stacked bar).
            ``None`` → no endpoint label composition.
        endpoint_label_data: Pre-computed label positions and color data.
            Required when ``endpoint_label_layout`` is not ``None``.
        href_link: VL calculate expression string set by ``ClickInteractivityFeature``.
            ``assemble_final_vl`` injects ``{"calculate": href_link, "as": "__df_href__"}``
            into transforms and wires an href encoding.  ``None`` → no href.
        tooltip_description: Per-datum expression string set by
            ``StructuredTooltipFeature`` (chart-axes LUT-driven header/series/
            value/total string). ``assemble_final_vl`` wires it to VL's native
            ``description`` channel as a ``{"value": {"expr": ...}}`` def (NOT
            a ``{"field": ...}`` reference bound through a ``calculate``
            transform — the latter corrupts stacked area/line paths; see
            ``translate.py::_apply_structured_tooltip``). ``description``
            fully replaces the mark's aria-label (no merge with
            channel-derived content — see ``emitters/_tooltip.py``).  ``None`` →
            no structured tooltip (family outside the walking-skeleton scope,
            or a chart.layers overlay), matching the ``None``-is-unset idiom of
            the sibling ``href_link`` field.
        geo_data: Geo data source block ``{"url": ..., "format": {...}}`` for
            geoshape/map families.  Set by ``GeoshapeEmitter`` on the outer spec
            (no-data path) or on each layer (choropleth layered path).
            ``assemble_final_vl`` emits it as the VL ``data`` key.
        transforms: VL ``transform`` list. Set by ``GeoshapeEmitter`` on the choropleth
            overlay layer (lookup join); by a wide (``y: [a, b]``) bar/area/line/scatter
            emitter for its VL ``fold`` transform.  Empty list → no transform key emitted.
        resolve: VL ``resolve`` block for layered specs (e.g. scale independence
            across layers).  Set by pie emitter as ``{"scale": {"color": "independent"}}``.
            Empty dict → no resolve key emitted.
    """

    mark: str
    encoding: dict[str, Any] = field(default_factory=dict)
    layers: list[ChartSpec] = field(default_factory=list)
    # Overlay sub-specs composed BEFORE the main mark, so they render beneath it.
    # `layers` cannot express this: the translator always emits the main mark at
    # layer[0], which is right for a fill family (a bar would hide a rule drawn
    # under it) and wrong for a sparse one — a threshold rule drawn over a
    # scatter bisects its points, the one on the threshold most of all. Line and
    # area need no underlay because their strokes already live in `layers`.
    # Only scatter ever populates this — the invariant every positional index
    # into `layers` above relies on: nothing that reaches scatter is counted
    # by `layers[0]`/`layers[-1]`/etc, because scatter's rule lives here instead.
    underlays: list[ChartSpec] = field(default_factory=list)
    # True only on a flat text-mark ChartSpec that IS one mark's own printed
    # value (features/value_labels.py's per-segment/line/point/scatter label,
    # an overlay layer's own label via emitters/_overlay.py's reuse of
    # text_layer_spec(), or features/zero_value_label.py's direct "0" label
    # for a genuine-zero bar row) -- never on a nested "layered" sub-spec, a
    # stack's aggregate total label, or any of pie.py's own text layers
    # (center total, outside labels), which never set this flag. vega_lite.py
    # reads it off the assembled spec to stamp `$df_value_label_layers`, the
    # join key chart_interactivity.js's recedableMarks() twin search gates on.
    value_label: bool = False
    config: dict[str, Any] = field(default_factory=dict)
    mark_props: dict[str, Any] = field(default_factory=dict)
    # Simple string name ("mercator") or full projection dict ({"type": "conic...", "center": [...]}).
    projection: str | dict[str, Any] | None = None
    data_name: str | None = None
    endpoint_label_layout: Literal["right_pane", "top_rail"] | None = None
    endpoint_label_data: EndpointLabelData | None = None
    href_link: str | None = None
    tooltip_description: str | None = None
    title: str | None = None
    subtitle: str | None = None
    title_font: ResolvedFontStyle | None = None
    # The base series' legend label on a layered cartesian chart: the y title
    # as plain text, before display wrapping. `encoding.y.title` is not usable
    # for this — it becomes a list[str] once wrapped, and VL's color.datum and
    # scale domain take primitives.
    base_series_label: str | None = None
    background: str | None = None
    title_style: TitleStyle | None = None
    geo_data: dict[str, Any] | None = None
    transforms: list[dict[str, Any]] = field(default_factory=list)
    # Row data for non-geo VL families; set centrally by BoardRenderSession.emit_chart.
    # assemble_final_vl emits it as {"data": {"values": [...]}} in _base_spec.
    data: list[dict[str, Any]] | None = None
    # VL resolve block (e.g. scale independence across layers); emitted by
    # _translate_layered when non-empty.  Values are VL resolution strings
    # ("independent", "shared") so the inner dict is str→str.
    resolve: dict[str, dict[str, str]] = field(default_factory=dict)
    # Named datasets for per-layer-query layered charts: query_name → rows.
    # assemble_final_vl emits these as the top-level VL ``"datasets"`` block.
    # None for single-query charts.
    datasets: dict[str, list[dict[str, Any]]] | None = None
    # Encoding channels that belong ONLY on the main mark layer (not shared/outer)
    # when the spec is promoted to a layered VL spec. Used by _translate_standard
    # to place these channels on the first layer dict without exposing them to
    # overlay layers (e.g. scatter bubble size must not reach the text label layer).
    main_layer_encoding: dict[str, Any] = field(default_factory=dict)
    # Pixel height of the x-axis label block at the angle the emitter's own
    # tilt resolution picked (``AxisLabelLayout.label_block_height``).
    # ``vega_lite.py`` hands it to the support_table post-pass, which reserves
    # the gap a ``position: bottom`` strip leaves below the plot and cannot
    # otherwise see a render-time tilt. None when the emitter resolved no
    # x-axis labels (no x field, a non-cartesian family).
    x_label_block_height: float | None = None
    # Small-multiples faceting set by FacetFeature. facet_row / facet_column are
    # the partition fields — either or both may be set (at least one when
    # faceting): row-only stacks vertically, column-only is a horizontal strip,
    # both a grid. facet_scale defaults "shared"; "independent" emits a
    # facet-root resolve.scale. assemble_final_vl wraps the unit in a VL facet
    # operator when either field is set. Both None → no faceting.
    facet_row: str | None = None
    facet_column: str | None = None
    facet_scale: Literal["shared", "independent"] = "shared"
    # Which VL positional channel carries the measure: "y", or "x" on a
    # horizontal bar, whose axes are flipped. Written by FacetFeature and read by
    # the facet wrap, where "independent" must free the measure scale and not the
    # category one — so like its facet_* neighbors it only says anything about a
    # faceted spec, and keeps the default on every other one. Heatmap is the one
    # faceted family whose measure is neither positional channel (it rides color)
    # — out of this field's vocabulary, and its independent-scale resolution is
    # unaddressed.
    measure_channel: Literal["x", "y"] = "y"
    # VL position channels ("x"/"y") whose per-panel domain is a proper
    # subset of the channel's whole domain — decided from the data, not from
    # a field-name match, so it covers a panel field that differs from the
    # axis field (region panels over a product axis, where a panel sells
    # only some products). VL's facet default shares a channel's scale
    # domain across every panel, so without forcing these independent a
    # panel reserves band/axis space for values it never draws. Narrowing
    # costs a real per-panel axis, so it applies only where that width is
    # affordable — see `facet_bound_position_channels`. Color scales are
    # deliberately never added here — cross-panel color identity stays
    # shared; only positional band/axis space narrows.
    facet_independent_channels: frozenset[Literal["x", "y"]] = frozenset()
    # Query order of facet_row's / facet_column's distinct values (first
    # encounter in the query rows), read via ChartDataset.column_values().
    # A VL facet field def has no "preserve source order" sort mode (unlike
    # a position channel's `sort: null`), so the row-order domain must be
    # pinned as an explicit values array or VL falls back to alphabetical.
    # None only for a non-faceted spec (no facet_row/facet_column set). See
    # FacetFeature.apply() for the one known gap this order can miss.
    facet_row_order: tuple[CellValue, ...] | None = None
    facet_column_order: tuple[CellValue, ...] | None = None
