"""MirrorAxisFeature — draw the y-scale on BOTH edges (``style.axis_y.mirror``).

Appends a transparent ghost overlay layer carrying the opposite-edge y-axis and
sets ``resolve.axis.y = "independent"`` so Vega-Lite emits a second axis object off
the single shared y-scale (identical ticks on both sides). Registered last in
DEFAULT_FEATURES so it runs after every other feature (endpoint labels, click,
legend) has shaped the layer stack, and can refuse the endpoint-label combination.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._base import (
    _CartesianResolvedChartFields,
)
from dbt_charts.core.compile.models.chart.resolved.heatmap import (
    ResolvedHeatmapChart,
)
from dbt_charts.core.compile.models.chart.resolved.scatter import ResolvedScatterChart
from dbt_charts.core.compile.models.style.resolved._cartesian import (
    _CartesianResolvedStyle,
)
from dbt_charts.core.compile.models.style.theme.axis import AxisMirrorStyle
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_fields
from dbt_charts.core.compile.resolve.chart.tick_values import numeric_domain_bounds
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_MIRROR_ENDPOINT_LABELS,
    ERR_MIRROR_LAYERS,
    ERR_MIRROR_MULTI_SERIES,
    ERR_MULTIPLES_ENDPOINT_LABELS,
)
from dbt_charts.core.render.chart.emitters._measured_label_padding import (
    estimated_quantitative_tick_labels,
    numeric_values,
    quantitative_tick_labels,
)
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.render.chart.type_inference import (
    HEATMAP_FORMAT_REMEDY,
    gate_label_format,
)
from dbt_charts.core.render.chart.vl_field_maps import compose_axis_label_expr
from dbt_charts.core.text.predefined_formats import PREDEFINED_NATIVE_NAMES
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    cap_padding_to_label_limit,
    measured_label_padding,
)


def _primary_y_orient(spec: ChartSpec) -> str:
    """The concrete edge (left/right) the main y-axis already sits on."""
    enc = spec.encoding.get("y")
    if isinstance(enc, dict) and isinstance(enc.get("axis"), dict):
        orient = enc["axis"].get("orient")
        if orient in ("left", "right"):
            return orient
    return "left"


def _mirror_on(chart: ResolvedChart) -> bool:
    """True when the resolved y-axis style requests mirroring.

    The baked y-axis style lives on the cartesian resolved-style base
    (``chart.style.axis_y``); narrowing to ``_CartesianResolvedStyle`` guarantees
    the field exists (radial/kpi/table styles don't carry axes).
    """
    style = chart.style
    return isinstance(style, _CartesianResolvedStyle) and bool(style.axis_y.mirror)


class MirrorAxisFeature:
    """``style.axis_y.mirror: true`` → y-axis on both edges."""

    def applies_to(self, chart: ResolvedChart) -> bool:
        return _mirror_on(chart)

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: dict[str | None, list[dict[str, Any]]],
    ) -> ChartSpec:
        assert isinstance(chart, _CartesianResolvedChartFields)
        # The mirror binds to the SPEC's own emitted y encoding — the one fact
        # that decides whether there is anything to reflect. The resolved
        # chart's `y` is the wrong proxy: a histogram resolves y=None yet
        # emits a count y encoding (must mirror). With no SHARED y encoding,
        # the resolved model says WHY it is missing, and the cases are not
        # the same (`layers:` is refused above, before the encoding is read
        # at all): a multi-measure
        # heatmap folds its list y into per-measure sublayers (the
        # multi-series refusal, naming the fields the author wrote); a chart
        # with no y anywhere is inert, not an error (the design panel
        # produces that shape by clearing y). Checked before the
        # endpoint-label refusal so a y-less chart renders instead of
        # raising a conflict about an axis it doesn't have. Reusing the
        # emitted encoding also means the ghost binds the SAME
        # field/type/scale (quantitative and categorical y alike); only the
        # axis edge differs. `layers` is structurally absent on
        # ResolvedHeatmapChart, hence the getattr.
        # Checked before the encoding shape, not through it: `layers:` moves the
        # measure encoding onto the sublayers on BOTH orientations, but a
        # horizontal base still leaves its category on the outer `y`. Reading
        # "no outer y" as the proxy therefore stopped refusing the horizontal
        # case the moment the overlay began hoisting the category channel, and
        # silently mirrored the category axis while the measure axis the layers
        # live on got nothing.
        if getattr(
            chart, "layers", ()
        ):  # type-state: silent_fallback — layers is structurally absent on ResolvedHeatmapChart; absent means "no overlays", not missing data
            raise ChartDataError.from_code(ERR_MIRROR_LAYERS, chart_id=chart.id)
        main_y = spec.encoding.get("y")
        if not isinstance(main_y, dict):
            if isinstance(chart.y, list):
                raise ChartDataError.from_code(
                    ERR_MIRROR_MULTI_SERIES, chart_id=chart.id, y=list(chart.y)
                )
            return spec
        # Endpoint labels compose the chart into a pane and claim the opposite edge
        # for the label rail — mirror can't also place an axis there. This is the
        # only place that knows BOTH facts at once (the composed spec's rail, and
        # whether multiples authored the collision), so it decides which field to
        # name rather than guessing earlier: a `multiples:` chart is named for
        # `multiples:` (its shared-scale mirror default may be the engine's own
        # decision, but multiples: is what the author actually wrote and the
        # field they can act on); anything else names axis_y.mirror, since that
        # is the only remaining way mirror could be truthy here (author- or
        # theme-set — _apply_multiples_mirror never turns it on when
        # multiples is None).
        assert isinstance(chart.style, _CartesianResolvedStyle)
        # Mirror reflects a single shared y-scale — no meaning for multi-series.
        # Check this before endpoint_label_layout: the multi-series conflict is
        # more fundamental and multi-y + endpoint_labels may both be active.
        if wide_fields := wide_measure_fields(chart):
            raise ChartDataError.from_code(
                ERR_MIRROR_MULTI_SERIES, chart_id=chart.id, y=list(wide_fields)
            )
        if spec.endpoint_label_layout is not None:
            code = (
                ERR_MULTIPLES_ENDPOINT_LABELS
                if chart.multiples is not None
                else ERR_MIRROR_ENDPOINT_LABELS
            )
            raise ChartDataError.from_code(code, chart_id=chart.id)
        primary = _primary_y_orient(spec)
        opposite = "left" if primary == "right" else "right"
        # Match the primary axis's tick format on the opposite edge; suppress its
        # title and grid so the mirrored edge doesn't duplicate them. Use an
        # explicit null (not pop) — absent title lets VL fall back to
        # encoding.y.title, which paints the measure label on the ghost edge.
        primary_axis = main_y.get("axis")
        ghost_axis = dict(primary_axis) if isinstance(primary_axis, dict) else {}
        ghost_axis.update(orient=opposite, grid=False)
        ghost_axis["title"] = None
        # Per-edge relabel: mirror: {format/expr: ...} overrides only the ghost
        # axis's label presentation. Ticks stay pinned to the single shared
        # scale (resolve.axis.y = independent below) — only the label changes.
        # Applied BEFORE the align-invasion check below so a mirror.format
        # override is what gets measured, not the (possibly stale) primary format.
        mirror = chart.style.axis_y.mirror
        if isinstance(mirror, AxisMirrorStyle):
            if mirror.format is not None:
                # mirror.format is authored-only (no theme default) and never
                # passes through axis_to_vl — this is the one place that
                # would otherwise paint $NaN over a categorical shared scale
                # with no diagnostic, the same failure gate_label_format
                # already refuses on the primary axis.
                #
                # Read both keys rather than indexing: a histogram emits a
                # `{aggregate: "count"}` y with no `field` at all (see the
                # y=None note above). That axis is quantitative, which the
                # gate no-ops on anyway, so the miss is "nothing to gate" —
                # not a suppressed diagnostic.
                y_field = main_y.get("field")
                y_vl_type = main_y.get("type")
                if isinstance(y_field, str) and isinstance(y_vl_type, str):
                    # `orientation: horizontal` flips the VL channels, never
                    # the authored ones, so a
                    # bar's measure stays style.axis_y in both orientations;
                    # a scatter dot plot is the one family whose measure is
                    # axis_x, its y being the category; a heatmap has no
                    # measure axis at all — the value is on color. Naming the
                    # wrong one routes the author into this same error code.
                    move_it_to = (
                        HEATMAP_FORMAT_REMEDY
                        if isinstance(chart, ResolvedHeatmapChart)
                        else (
                            "Remove axis_y.mirror.format; to format the "
                            "measure instead, author it on "
                            + (
                                "style.axis_x.labels.format."
                                if isinstance(chart, ResolvedScatterChart)
                                else "style.axis_y.labels.format."
                            )
                        )
                    )
                    gate_label_format(
                        mirror.format,
                        y_field,
                        chart_rows(chart, datasets).all_rows(),
                        y_vl_type,
                        setting="axis_y.mirror.format",
                        # Temporal takes the gate's own text: it names a time
                        # spec / style.time_format, which is right on every
                        # family, where this channel-swap advice would assert
                        # the scale is categorical when it is a date.
                        remedy=(
                            "axis_y.mirror.format relabels the shared y-scale "
                            "on the opposite edge, and that scale is "
                            "categorical here — it can't carry a number "
                            f"format. {move_it_to}"
                        )
                        if y_vl_type in ("nominal", "ordinal")
                        else None,
                    )
                ghost_axis["format"] = mirror.format
                # The primary may carry a labelExpr (style.axis_y.labels.expr),
                # which VL prefers over format — drop the inherited copy so the
                # explicit per-edge format actually takes effect.
                ghost_axis.pop("labelExpr", None)
            if mirror.expr is not None:
                ghost_axis["labelExpr"] = mirror.expr
        # The ghost inherited the primary's labelExpr verbatim above
        # (dict(primary_axis)). When that labelExpr is the engine's own
        # ruler composition, it was built for the PRIMARY's own resolved
        # anchoring -- and the ghost's own anchoring, on the opposite
        # orient, is not always the same (an authored labels.align can make
        # either edge either anchoring). build_resolved_axis already bakes
        # the ghost's own mechanism for this exact case
        # (ResolvedAxisStyle.mirror_ruler), through the same tabular-font
        # guard the primary's ruler went through -- read it rather than
        # re-deriving the ghost's anchoring here and rebuilding a ruler for
        # it at render. The rebuild goes through compose_axis_label_expr --
        # the same numeral+case+values-filter chain, in the same order, that
        # a primary axis's own build calls. Composing the numeral producer
        # alone here would drop the wrappers the primary carries, so the two
        # edges of one axis would render different label text.
        axis_y = chart.style.axis_y
        if axis_y.mirror_ruler is not None:
            ghost_axis["labelExpr"] = compose_axis_label_expr(
                {}, axis_y.mirror_ruler, axis_y
            )["labelExpr"]
        # An authored label.align that is safe on the primary edge invades the
        # plot on the opposite-orient ghost (and vice versa) — no explicit
        # align is safe on both edges at once. Same own-side invasion
        # vl_field_maps.py's measure_axis_to_vl guards against; reuse its
        # measured-labelPadding fix here rather than duplicating the reject.
        # A labelExpr override (mirror.expr) makes the rendered string an
        # arbitrary Vega expression dbt charts can't measure, so that path
        # always falls back to rejecting. A font.case guard IS needed here,
        # same as measure_axis_to_vl's: inject_axis_label_case runs on the
        # PRIMARY axis, and the ghost inherits its labelExpr wholesale via
        # dict(primary_axis) above — see authored_unmeasurable below.
        ghost_align = ghost_axis.get("labelAlign")
        if ghost_align in (opposite, "center"):
            ghost_format = ghost_axis.get("format")
            ghost_si_format: str | None = None
            ghost_scientific_format: str | None = None
            # Whether the ghost's rendered string is something dbt charts can
            # introspect and re-measure, decided from resolved fields rather
            # than sniffing "labelExpr" out of the VL dict (the ghost may
            # *inherit* a labelExpr from the primary that isn't authored at
            # all -- it's the engine's own ruler composition, copied via
            # ``dict(primary_axis)`` above; mistaking that for an authored,
            # unmeasurable override is the mirror + compacting-ladder bug).
            # An explicit ``mirror.expr`` is always authored and unmeasurable.
            # ``mirror.format`` replaces the ghost's paint with a literal
            # spec (ruler doesn't apply -- it was baked for the primary's own
            # format, not this override). Otherwise the ghost paints exactly
            # what the primary paints: unmeasurable only if the primary's own
            # ``label.expr`` is authored; if not, ``axis_y.mirror_ruler``
            # (None or set) already answers whether the ghost's own paint is
            # the literal format or the ruler composition -- the mechanism
            # that actually reaches this edge, not the primary's.
            mirror_expr_override = (
                isinstance(mirror, AxisMirrorStyle) and mirror.expr is not None
            )
            mirror_format_override = (
                isinstance(mirror, AxisMirrorStyle) and mirror.format is not None
            )
            if mirror_expr_override:
                authored_unmeasurable = True
                ghost_ruler = None
            elif mirror_format_override:
                authored_unmeasurable = False
                ghost_ruler = None
            else:
                # inject_axis_label_case runs on the PRIMARY axis (e.g.
                # line.py, after measure_axis_to_vl), and the ghost is
                # dict(primary_axis) -- so it inherits an upper(...)/
                # lower(...)-wrapped labelExpr too, not just an authored
                # label.expr. Both make the ghost's paint un-introspectable:
                # quantitative_tick_labels has no case-folding of its own, so
                # measuring un-cased strings while Vega paints upper(...)
                # under-measures the gutter by the case width delta.
                authored_unmeasurable = axis_y.labels.expr is not None or (
                    axis_y.labels.font.case in ("upper", "lower")
                )
                ghost_ruler = axis_y.mirror_ruler
                # tick_label (ruler's non-compacting sibling) never reaches
                # the emitted `format` -- inject_axis_numeral_expr composes
                # it into labelExpr instead, exactly like ruler, so the
                # ghost's inherited `format` still holds the untouched
                # label.format. Read the resolved field directly here too,
                # mirroring ghost_ruler just above, so the gutter measures
                # the plain-digit text that actually paints, not the SI
                # spec Vega no longer uses for this axis's ticks. Unlike
                # ruler, tick_label has no per-edge mirror variant -- it is
                # the same primary-authored spec on both edges. si_format/
                # scientific_format (set only for a ladder-less axis) mirror
                # the exact per-tick guard inject_axis_numeral_expr composes
                # -- see estimated_quantitative_tick_labels's own docstring.
                if axis_y.tick_label is not None:
                    ghost_format = axis_y.tick_label.format
                    ghost_si_format = axis_y.tick_label.si_format
                    ghost_scientific_format = axis_y.tick_label.scientific_format
            # A PREDEFINED_NATIVE format name (e.g. "percent_number") bypasses
            # d3 entirely -- quantitative_tick_labels/
            # estimated_quantitative_tick_labels call d3_format() directly,
            # which raises on a bare name like this. Same guard as
            # vl_field_maps.py's measure_axis_to_vl.
            if ghost_format is not None and ghost_format in PREDEFINED_NATIVE_NAMES:
                ghost_format = None
            labels: list[str] = []
            if (
                ghost_align == opposite
                and not authored_unmeasurable
                and ghost_format is not None
            ):
                if axis_y.tick_values:
                    labels = quantitative_tick_labels(
                        tuple(axis_y.tick_values),
                        ghost_format,
                        ruler=ghost_ruler,
                        # mirror.format has its own precision -- the primary's
                        # decimal_pad_table was built for the primary's spec, so
                        # passing it here would silently mis-pad the ghost labels.
                        tick_label=None
                        if mirror_format_override
                        else axis_y.tick_label,
                    )
                elif isinstance(chart.y, str):
                    _ay_cont_m = (
                        axis_y.scale.continuous if axis_y.scale is not None else None
                    )
                    domain = _ay_cont_m.domain if _ay_cont_m is not None else None
                    bounds = numeric_domain_bounds(domain)
                    # When the axis has an explicit domain (e.g. normalize-stack
                    # bakes [0.0, 1.0]), measure from the domain ONLY — raw data
                    # values outside that range can't produce ticks there.
                    if bounds is not None:
                        values = list(bounds)
                    else:
                        # `mirror` can only reach a faceted chart when
                        # `multiples.scale == "shared"` — `_apply_multiples_mirror`
                        # raises ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR for
                        # `scale: independent`, so the union across all panels IS
                        # what every panel's shared scale shows here, matching
                        # what the primary axis itself measures from
                        # (vl_field_maps.py).
                        values = numeric_values(
                            chart_rows(chart, datasets).all_rows(), (chart.y,)
                        )
                    labels = estimated_quantitative_tick_labels(
                        values,
                        ghost_format,
                        si_format=ghost_si_format,
                        scientific_format=ghost_scientific_format,
                    )
            if labels:
                padding = measured_label_padding(
                    labels, axis_y.labels.font.family, axis_y.labels.font.size
                )
                label_limit = (
                    axis_y.labels.max_width
                    if axis_y.labels.max_width is not None
                    else DEFAULT_VL_LABEL_LIMIT
                )
                ghost_axis["labelPadding"] = cap_padding_to_label_limit(
                    padding, label_limit
                )
            else:
                raise ChartDataError(
                    f"axis_y.labels.align: {ghost_align!r} cannot be "
                    "combined with axis_y.mirror — the alignment that keeps "
                    f"labels outside the plot on the {primary} edge draws the "
                    f"mirrored {opposite}-edge labels back across the axis "
                    "into the plot, and dbt charts has no baked tick content to "
                    "measure a safe labelPadding from. Omit labels.align; each "
                    "edge then uses Vega-Lite's safe per-orient default.",
                    chart_id=chart.id,
                )
        spec.layers.append(
            ChartSpec(
                mark="rule",
                mark_props={"opacity": 0, "tooltip": False},
                encoding={"y": {**main_y, "axis": ghost_axis}},
            )
        )
        # Preserve any other resolve keys (e.g. scale) and set axis.y independent.
        spec.resolve = {**spec.resolve, "axis": {"y": "independent"}}
        return spec
