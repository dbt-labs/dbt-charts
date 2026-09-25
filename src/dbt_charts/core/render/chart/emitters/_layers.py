"""Shared per-mark layer emitters used by family emitters and overlay layer rendering."""

from __future__ import annotations

import json
from typing import Literal

from dbt_charts.core.compile.models.style.resolved._marks import (
    ResolvedAreaLineStyle,
    ResolvedAreaMarkStyle,
    ResolvedLineMarkStyle,
)
from dbt_charts.core.compile.models.style.theme import PointMarkStyle
from dbt_charts.core.compile.models.style.theme.marks import BarMarkStyle
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.spec import ChartSpec
from dbt_charts.core.render.chart.step_band import BAND_STEP_INTERPOLATE
from dbt_charts.core.render.chart.vl_field_maps import (
    _dasharray_to_vl,
    _emit_point_mark,
    bar_corner_props,
    bar_data_signs,
    bar_mark_to_vl,
    bar_span_signs,
    continuous_bar_size_prop,
    line_mark_to_vl,
    scatter_mark_to_vl,
)
from dbt_charts.core.render.chart.x_domain import vl_sort_op
from dbt_charts.core.utils import x_domain_order

# Vega's ``autosize: fit`` sizes the plot to fit everything the scenegraph draws,
# so a mark spilling past the plot rect pushes the plot box inward — and on line
# and area charts most of that spill is ink nobody can see: the invisible hover
# hit-target disc (opacity 0, sized by ``HOVER_TARGET_SIZE`` below) and the
# background-colored halo. Paying layout for it left the grid ~9px inside the
# card padding the title sits on. Spread this into the mark props of a layer
# whose paint nobody can see: a clip cuts a stroke in half wherever it runs
# along the plot boundary (a series
# resting on a zero floor, the baseline rule) and takes the radius off a marker
# sitting on the domain edge, so the data line, the stacked band's perimeter, the
# baseline rule and every point overlay keep their reservation instead. Two
# knock-on effects this buys with: the hit-target's own outer half stops taking
# pointer events at the first and last datum, and the point halo below stays
# unclipped despite being invisible — clipping it would let the line show through
# the outer half of a marker on the domain edge.
CLIP_TO_PLOT: VLDict = {"clip": True}

# Radius of the invisible hover-target disc `_hover_target_point` draws at
# every line/area datum: r = sqrt(size)/2 (Vega's circle symbol — NOT
# sqrt(size/pi)). Ceiling: discs paint in data order and SVG hit-testing picks
# the topmost shape, so a radius past half the smallest on-screen x-step lets
# a later datum's disc steal its neighbor's hit region. Reuses the ~18px
# empirical "points still read as discrete, not a caterpillar" spacing floor
# that `chart_rendering.point.min_px_per_point` (default_config.yml) is
# itself calibrated against, since no finer-grained signal is available here:
# ceiling = 18/2 = 9.0px, so size <= 4 * 9.0**2 = 324 —
# test_hover_target_size_stays_under_x_step_ceiling pins this.
HOVER_TARGET_SIZE: float = 320.0


def pin_categorical_domain_order(cat_enc: VLDict, data: list[VLDict]) -> None:
    """Pin ``cat_enc``'s scale domain explicitly, in place.

    Splitting a mark's data across VL sub-layers (the positive/negative sign
    split below) makes Vega-Lite re-derive the shared categorical domain from
    each sub-layer's own *filtered* data independently — for an unauthored
    sort this silently reorders every category, not just the one that moved
    layers, and for a field-based ``sort`` (e.g. bar's own value-descending
    default) each sub-layer's sort aggregate runs over an incomplete subset,
    which Vega-Lite cannot reconcile into one coherent order and falls back
    to alphabetical. Pinning the domain explicitly, computed once from the
    full (unfiltered) dataset via ``x_domain_order`` — the same
    per-category sort-aggregate ``x_domain.py``'s ``rendered_x_domain`` (used
    by ``emitters/_overlay.py``) builds its own union on top of — makes the
    split invisible to the axis either way.

    ``cat_enc["sort"]`` reaching a categorical channel is always either absent
    or ``chart_sort_to_vl``'s own ``{"field", "order"}`` shape, optionally with
    the ``op`` a dimension axis or a bar pins (``emitters/_cartesian.py``) —
    never a bare VL-native sort array, which the authored surface has no way
    to produce. ``vl_sort_op`` reads that pin so the domain computed here folds
    each category the same way Vega-Lite will.
    """
    if cat_enc.get("type") not in ("nominal", "ordinal"):
        return
    cat_field = cat_enc.get("field")
    if cat_field is None:
        return
    sort = cat_enc.get("sort")
    sort_field = sort["field"] if isinstance(sort, dict) else ""
    descending = isinstance(sort, dict) and sort.get("order") == "descending"
    ordered = x_domain_order(
        data, cat_field, sort_field, descending, op=vl_sort_op(sort)
    )
    if not ordered:
        return
    existing_scale = cat_enc.get("scale")
    scale: VLDict = dict(existing_scale) if isinstance(existing_scale, dict) else {}
    scale.setdefault("domain", ordered)
    cat_enc["scale"] = scale


def _sublayer_encoding(
    tooltip: list[VLDict],
    literal_color: str | None = None,
    *,
    paint_channels: tuple[str, ...],
    series_encoding: VLDict,
) -> VLDict:
    """Build child encoding without replacing a parent series-grouping channel."""
    encoding: VLDict = {"tooltip": tooltip} if tooltip else {}
    if literal_color is not None:
        for channel in paint_channels:
            encoding[channel] = {"value": literal_color}
        if "field" in series_encoding:
            encoding["detail"] = {
                key: series_encoding[key]
                for key in ("field", "type", "timeUnit", "bin", "aggregate")
                if key in series_encoding
            }
    return encoding


def _point_paint_channel(mark_props: VLDict) -> str:
    """Return the paint channel controlled by point color."""
    return "fill" if mark_props.get("filled") is True else "stroke"


def _hover_target_point(
    tooltip: list[VLDict],
    single_series_color: str,
    has_color_encoding: bool,
    pin_child_colors: bool,
    announce: bool,
) -> ChartSpec:
    """Invisible large-target point overlay so hover/tooltip work on thin marks.

    Shared by the line and area paths — a thin stroke and a thin fill need the
    same oversized hit target.

    The overlay is invisible today, but it still needs the chart's ink: with no
    fill Vega-Lite stamps its own default on the mark, which is the wrong color
    the moment this layer gains any opacity. Mirrors ``_area_point_sublayers``.

    ``announce=False`` when a visible sibling point already carries each datum's
    description — a second labeled mark makes a screen reader read every value
    twice. The overlay still takes the pointer either way: the hover runtime
    resolves an unlabeled hit inside a mark group to the nearest labeled mark.
    When no sibling point is drawn the overlay must stay labeled, since the
    runtime only hovers labeled marks.
    """
    mark_props: VLDict = {
        **CLIP_TO_PLOT,
        "filled": True,
        "size": HOVER_TARGET_SIZE,
        "opacity": 0,
        "tooltip": True,
    }
    if not announce:
        mark_props["aria"] = False
    if not has_color_encoding:
        mark_props["fill"] = single_series_color
    return ChartSpec(
        mark="point",
        mark_props=mark_props,
        encoding=_sublayer_encoding(
            tooltip,
            (
                single_series_color
                if pin_child_colors and not has_color_encoding
                else None
            ),
            paint_channels=("fill",),
            series_encoding={},
        ),
    )


# ── line ──────────────────────────────────────────────────────────────────────


def _cap_join(cap: str | None, join: str | None) -> VLDict:
    """``strokeCap``/``strokeJoin`` props with any None left out.

    ``null`` is not in Vega-Lite's strokeCap/strokeJoin enum, and the spec is a
    shipped artifact under ``--format json``, so an authored ``cap: null``
    must omit the key rather than write it -- on a halo as on its fg line.
    """
    return {
        k: v for k, v in (("strokeCap", cap), ("strokeJoin", join)) if v is not None
    }


def _line_halo_sublayers(
    stroke_width: float,
    halo_multiplier: float,
    halo_color: str,
    fg_interp: str,
    fg_cap: str | None,
    fg_join: str | None,
    point_size: float | None,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    series_encoding: VLDict,
) -> list[ChartSpec]:
    """Build halo sub-layers (stroke halo + optional point halo). Empty when either is zero.

    The halo is a wider knockout stroke drawn *behind* the foreground line, so
    it must take the fg's own cap and join: a round halo behind a butt-capped
    line pokes out past the line's ends at the full halo width.
    """
    if stroke_width == 0 or halo_multiplier == 0:
        return []
    halo_width = stroke_width * halo_multiplier
    layers: list[ChartSpec] = [
        ChartSpec(
            mark="line",
            mark_props={
                **CLIP_TO_PLOT,
                "strokeWidth": halo_width,
                **_cap_join(fg_cap, fg_join),
                # Halo must trace the same curve as the fg line.
                "interpolate": fg_interp,
                "stroke": halo_color,
                "opacity": 1,
                "strokeOpacity": 1,
                "tooltip": False,
                "aria": False,
            },
            encoding=_sublayer_encoding(
                tooltip,
                halo_color if pin_child_colors else None,
                paint_channels=("stroke",),
                series_encoding=series_encoding,
            ),
        )
    ]
    if point_size is not None:
        layers.append(
            ChartSpec(
                mark="point",
                mark_props={
                    "filled": True,
                    "fill": halo_color,
                    "stroke": halo_color,
                    "size": point_size * halo_multiplier,
                    "opacity": 1,
                    "fillOpacity": 1,
                    "strokeOpacity": 1,
                    "tooltip": False,
                    "aria": False,
                },
                encoding=_sublayer_encoding(
                    tooltip,
                    halo_color if pin_child_colors else None,
                    paint_channels=("fill", "stroke"),
                    series_encoding={},
                ),
            )
        )
    return layers


def _line_fg_sublayers(
    stroke_width: float,
    fg_props: VLDict,
    has_color_encoding: bool,
    single_series_color: str,
    point_mark: PointMarkStyle,
    point_size: float | None,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    inherit_parent_color: bool,
    series_encoding: VLDict,
) -> list[ChartSpec]:
    """Build foreground line + optional point sub-layers. Empty when stroke_width is zero.

    For multi-series (has_color_encoding), omits the static stroke so the
    parent encoding.color drives the per-series color — matching V1.
    """
    if stroke_width == 0:
        return []
    has_explicit_line_color = "stroke" in fg_props
    fg_mark_props: VLDict = {**fg_props, "tooltip": True, "aria": False}
    # Only apply single_series_color when no authored stroke.color is present
    # (fg_props carries it from line_mark_to_vl) and no color encoding owns color.
    if not has_color_encoding and "stroke" not in fg_mark_props:
        fg_mark_props["stroke"] = single_series_color
    layers: list[ChartSpec] = [
        ChartSpec(
            mark="line",
            mark_props=fg_mark_props,
            encoding=_sublayer_encoding(
                tooltip,
                (
                    fg_mark_props["stroke"]
                    if pin_child_colors
                    and (
                        has_explicit_line_color
                        or (not has_color_encoding and not inherit_parent_color)
                    )
                    else None
                ),
                paint_channels=("stroke",),
                series_encoding=series_encoding,
            ),
        )
    ]
    if point_size is not None:
        fg_point_props: VLDict = {
            **_emit_point_mark(point_mark),
            "opacity": 1,
            "fillOpacity": 1,
            "strokeOpacity": 1,
            "tooltip": True,
        }
        has_explicit_point_color = "color" in fg_point_props
        if not has_color_encoding and "color" not in fg_point_props:
            fg_point_props["color"] = single_series_color
        layers.append(
            ChartSpec(
                mark="point",
                mark_props=fg_point_props,
                encoding=_sublayer_encoding(
                    tooltip,
                    (
                        fg_point_props["color"]
                        if pin_child_colors
                        and (
                            has_explicit_point_color
                            or (not has_color_encoding and not inherit_parent_color)
                        )
                        else None
                    ),
                    paint_channels=(_point_paint_channel(fg_point_props),),
                    series_encoding={},
                ),
            )
        )
    return layers


def emit_line_layer(
    line_mark: ResolvedLineMarkStyle,
    point_mark: PointMarkStyle,
    halo_color: str,
    single_series_color: str,
    has_color_encoding: bool,
    series_encoding: VLDict,
    tooltip: list[VLDict],
    suppress_halo: bool = False,
    band_step: bool = False,
    pin_child_colors: bool = False,
    inherit_parent_color: bool = False,
) -> list[ChartSpec]:
    """Assemble halo, foreground, and hover sub-layers for one line series.

    Shared by LineEmitter (base series) and overlay layer rendering (chart.layers).
    Returns the ordered VL sub-specs: halo stroke/point, fg stroke/point, invisible
    hover target.

    ``suppress_halo`` (default False, used only by chart.layers overlay
    rendering): the halo is an opaque knockout mask painted first, meant to
    give a single series a clean edge against the page background or a
    same-chart overlap. A layer overlaid on a DIFFERENT base series must not
    paint that mask — it would opaquely block the base series from showing
    through, defeating the overlay's whole "compare against the base" purpose
    (and making a translucent fg color visually indistinguishable from solid,
    since translucent-over-opaque-white looks the same as a paler solid).

    ``band_step`` (default False): the caller has already row-doubled the data
    for a band-aware ``step`` curve (see ``step_band.apply_step_band``) — force
    the VL interpolate to ``step-after`` so the stroke lands on the band
    boundary the doubled rows expect, overriding whatever the curve's plain
    passthrough interpolate would otherwise be. Also forces ``order: False``
    on every line sub-layer (halo included): Vega-Lite sorts line vertices by
    x, and the band-doubled edges tie in float x at some widths, so without
    this a tied pair can transpose and draw a zero-width spike instead of
    the plateau — see ``step_band.py``'s module docstring.

    Band mode is the only place ``connect`` means anything, and it is only
    known here (it needs the resolved VL x-type), so it is also where the
    disconnected plateaus' line cap is selected: ``marks.line.disconnected_cap``
    replaces ``stroke.cap`` when those plateaus are drawn as separate paths.
    Both values come from the theme — this picks between them, it does not
    supply either.
    """
    fg_props = line_mark_to_vl(line_mark)
    if band_step:
        fg_props["interpolate"] = BAND_STEP_INTERPOLATE
        if line_mark.connect is False and line_mark.disconnected_cap is not None:
            fg_props["strokeCap"] = line_mark.disconnected_cap
    stroke_width = line_mark.stroke.width
    halo_multiplier = 0.0 if suppress_halo else line_mark.halo_multiplier
    point_size: float | None = (
        point_mark.size if point_mark.size is not None and point_mark.size > 0 else None
    )
    fg_interp: str = fg_props.get("interpolate", "linear")
    layers = _line_halo_sublayers(
        stroke_width,
        halo_multiplier,
        halo_color,
        fg_interp,
        fg_props.get("strokeCap"),
        fg_props.get("strokeJoin"),
        point_size,
        tooltip,
        pin_child_colors,
        series_encoding,
    )
    fg_layers = _line_fg_sublayers(
        stroke_width,
        fg_props,
        has_color_encoding,
        single_series_color,
        point_mark,
        point_size,
        tooltip,
        pin_child_colors,
        inherit_parent_color,
        series_encoding,
    )
    layers += fg_layers
    if band_step:
        # order:False must travel with BAND_STEP_INTERPOLATE on every line
        # sub-layer (halo included) — see step_band.py's module docstring.
        for sub in layers:
            if sub.mark == "line":
                sub.mark_props["order"] = False
    layers.append(
        _hover_target_point(
            tooltip,
            single_series_color,
            has_color_encoding,
            pin_child_colors,
            announce=not any(sub.mark == "point" for sub in fg_layers),
        )
    )
    return layers


# ── bar ───────────────────────────────────────────────────────────────────────


def emit_bar_layer(
    bar_mark: BarMarkStyle,
    orientation: Literal["vertical", "horizontal"],
    has_color_encoding: bool,
    single_series_color: str,
    radius: float | None,
    encoding: VLDict,
    data: list[VLDict],
    measure_field: str | None,
    start_field: str | None,
    config: VLDict,
    transforms: list[VLDict],
    x_is_banded: bool,
    cat_field: str | None,
) -> ChartSpec:
    """Assemble mark_props and emit one bar series as a ChartSpec.

    Shared by BarEmitter (base series) and overlay layer rendering (chart.layers).
    Handles single-sign corner rounding and mixed-sign layer split internally;
    callers pass radius=None only when no corner radius is configured at all.

    ``x_is_banded`` and ``cat_field`` describe the category channel this bar's
    OWN encoding resolves to (the base's own x, or an overlay layer's own
    authored x) — not necessarily the outer chart's. ``x_is_banded=False``
    routes width/height through ``continuous_bar_size_prop`` instead of
    ``bar_mark_to_vl``'s band-fraction shorthand; see both docstrings.

    ``start_field`` is the bar's ``y_start`` column, bound to the span channel
    (``y2``/``x2``) already in ``encoding``. A span's sign is its direction,
    end minus start, so rises and falls round their own tip.
    """
    if measure_field is None:
        has_pos, has_neg = True, False
    elif start_field is None:
        has_pos, has_neg = bar_data_signs(data, measure_field)
    else:
        has_pos, has_neg = bar_span_signs(data, measure_field, start_field)
    mark_props = {**bar_mark_to_vl(bar_mark, orientation, x_is_banded), "tooltip": True}
    if not x_is_banded and bar_mark.size is None:
        mark_props.update(
            continuous_bar_size_prop(bar_mark, cat_field, data, orientation)
        )
    if radius is not None:
        corner = bar_corner_props(radius, orientation, has_pos, has_neg)
        if corner is not None:
            mark_props.update(corner)
    if not has_color_encoding:
        mark_props["fill"] = single_series_color
    measure_ch = "y" if orientation == "vertical" else "x"
    # Mixed-sign data with a corner radius: split into two sign-filtered layers so
    # each can carry the correct tip-corner radius.  extra_transforms (e.g. stack
    # z-order) are appended after the sign filter in each layer.
    if (
        radius is not None
        and measure_field is not None
        and measure_ch in encoding
        and has_pos
        and has_neg
    ):
        # Keep measure_ch on the outer (shared) encoding too — VL's layer spec
        # form lets a per-layer encoding key win over the shared one, so the
        # sub-layers' own measure_ch entries below still take effect. Features
        # that read spec.encoding on the outer ChartSpec (e.g. MirrorAxisFeature)
        # depend on the outer spec carrying its own channel encodings.
        measure_enc = encoding[measure_ch]
        # Sub-layers need the measure channel's field/type/scale for their own
        # marks, but NOT its axis: VL redraws a layer's own encoding.<ch>.axis
        # independently of the shared outer one, so carrying the same axis def
        # on both sub-layers *and* the outer encoding paints it three times
        # (once per sub-layer, once shared) instead of once. Only the outer
        # encoding keeps the axis.
        sublayer_measure_enc = {k: v for k, v in measure_enc.items() if k != "axis"}
        sublayer_enc: VLDict = {measure_ch: sublayer_measure_enc}
        span_ch = f"{measure_ch}2"
        if span_ch in encoding:
            sublayer_enc[span_ch] = encoding[span_ch]
        base = "0" if start_field is None else f"datum['{start_field}']"
        # Preserve original category order — see pin_categorical_domain_order's
        # docstring for why splitting a mark across sub-layers needs this.
        cat_ch = "y" if measure_ch == "x" else "x"
        cat_enc = encoding.get(cat_ch)
        if isinstance(cat_enc, dict):
            pin_categorical_domain_order(cat_enc, data)
        pos_corner = bar_corner_props(radius, orientation, True, False)
        neg_corner = bar_corner_props(radius, orientation, False, True)
        assert pos_corner is not None and neg_corner is not None  # both single-sign
        pos_layer = ChartSpec(
            mark="bar",
            mark_props={**mark_props, **pos_corner},
            encoding=dict(sublayer_enc),
            transforms=[
                {"filter": f"datum['{measure_field}'] >= {base}"},
                *transforms,
            ],
        )
        neg_layer = ChartSpec(
            mark="bar",
            mark_props={**mark_props, **neg_corner},
            encoding=dict(sublayer_enc),
            transforms=[
                {"filter": f"datum['{measure_field}'] < {base}"},
                *transforms,
            ],
        )
        return ChartSpec(
            mark="layered",
            encoding=encoding,
            layers=[pos_layer, neg_layer],
            config=config,
        )
    return ChartSpec(
        mark="bar",
        mark_props=mark_props,
        encoding=encoding,
        config=config,
        transforms=transforms,
    )


# ── area ──────────────────────────────────────────────────────────────────────


def _area_style_props(
    area_mark: ResolvedAreaMarkStyle,
    line_mark: ResolvedAreaLineStyle,
    background: str,
    single_series_fill: str,
) -> tuple[float, bool, float, str | None, float, VLDict, str, str]:
    """Extract mark geometry from a resolved area mark + its top-edge line mark.

    Returns (area_opacity, backdrop, halo_multiplier, area_interp,
    stroke_width, fg_line_props, halo_color, single_series_color).
    """
    area_opacity: float = area_mark.opacity
    backdrop: bool = area_mark.backdrop
    area_interp: str | None = area_mark.curve
    # Top-edge fg line stroke width/color/cap/join/dasharray + halo come from
    # line_mark (authored via charts.area.marks.line.*) — Vega-Lite itself
    # compiles an area's edge line as a genuine separate line mark, so its
    # geometry belongs on the "line" identity, not the area fill mark.
    # cap/join come off whichever stroke resolve handed us: marks.line.stroke
    # for the overlap recipe, or marks.area.stacked.stroke for a stacked chart
    # — resolve REPLACES the stroke wholesale there (resolve/chart/area.py),
    # so this is not always the theme's marks.line.stroke. (Assuming it was is
    # what let the stacked perimeter lose its cap once already.) Either way an
    # area edge is one continuous silhouette (area has no ``connect``), so it
    # never takes the disconnected-step cap that emit_line_layer selects.
    halo_multiplier: float = line_mark.halo_multiplier
    stroke_width: float = line_mark.stroke.width
    fg_line_props: VLDict = {}
    if line_mark.stroke.color is not None:
        fg_line_props["stroke"] = line_mark.stroke.color
    # Guarded like every other property here: `null` is not in Vega-Lite's
    # strokeCap/strokeJoin enum, and the spec is a shipped artifact under
    # `--format json`.
    if line_mark.stroke.cap is not None:
        fg_line_props["strokeCap"] = line_mark.stroke.cap
    if line_mark.stroke.join is not None:
        fg_line_props["strokeJoin"] = line_mark.stroke.join
    if line_mark.stroke.dasharray is not None:
        fg_line_props["strokeDash"] = _dasharray_to_vl(line_mark.stroke.dasharray)
    fg_line_props["strokeWidth"] = stroke_width
    return (
        area_opacity,
        backdrop,
        halo_multiplier,
        area_interp,
        stroke_width,
        fg_line_props,
        background,
        single_series_fill,
    )


def _area_halo_layers(
    backdrop: bool,
    stroke_width: float,
    halo_multiplier: float,
    halo_color: str,
    fg_cap: str | None,
    fg_join: str | None,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    series_encoding: VLDict,
) -> list[ChartSpec]:
    """Build the fill backdrop (+ optional edge-line halo) sub-layers.

    The backdrop is an opaque background-colored fill behind the fg fill —
    it gives a translucent fill a clean, consistent floor regardless of what
    would otherwise show through (grid lines, an overlapping series). Gated
    ONLY on ``backdrop`` (``marks.area.backdrop`` — its own on/off switch): a
    fill has no "width" to scale, so unlike a stroke it's a plain boolean,
    and it's INDEPENDENT of the edge line's ``halo_multiplier`` — that knob
    must have no effect on whether this backdrop renders.

    The halo LINE is a knockout stroke behind the fg edge stroke so crossings
    between series read cleanly — it has nothing to knock out when the edge
    itself has no stroke, so it's gated on both ``stroke_width`` and
    ``halo_multiplier``. Setting ``stroke.width: 0`` (or `halo_multiplier: 0`)
    must remove only the edge's own halo line, never the fill backdrop.
    """
    layers: list[ChartSpec] = []
    if backdrop:
        layers.append(
            ChartSpec(
                mark="area",
                mark_props={
                    **CLIP_TO_PLOT,
                    "fill": halo_color,
                    "opacity": 1,
                    "fillOpacity": 1,
                    "strokeOpacity": 0,
                    "tooltip": False,
                    "aria": False,
                },
                encoding=_sublayer_encoding(
                    tooltip,
                    halo_color if pin_child_colors else None,
                    paint_channels=("fill",),
                    series_encoding=series_encoding,
                ),
            )
        )
    if stroke_width != 0 and halo_multiplier != 0:
        halo_width = stroke_width * halo_multiplier
        layers.append(
            ChartSpec(
                mark="line",
                mark_props={
                    **CLIP_TO_PLOT,
                    "stroke": halo_color,
                    "strokeWidth": halo_width,
                    **_cap_join(fg_cap, fg_join),
                    "tooltip": False,
                    "aria": False,
                },
                encoding=_sublayer_encoding(
                    tooltip,
                    halo_color if pin_child_colors else None,
                    paint_channels=("stroke",),
                    series_encoding=series_encoding,
                ),
            )
        )
    return layers


def _area_point_sublayers(
    point_mark: PointMarkStyle,
    has_color_encoding: bool,
    single_series_color: str,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    inherit_parent_color: bool,
) -> list[ChartSpec]:
    """Build the visible point-overlay sub-layer at each plotted value.

    Mirrors VL's native ``mark: {type: area, point: true}`` (which compiles
    to a genuine separate ``symbol`` mark) and the line chart's own fg point
    handling. Empty when point_mark.size is unset/0 — points are opt-in,
    invisible by default (matches the line-chart precedent).
    """
    if point_mark.size is None or point_mark.size <= 0:
        return []
    point_props: VLDict = {
        **_emit_point_mark(point_mark),
        "opacity": 1,
        "fillOpacity": 1,
        "strokeOpacity": 1,
        "tooltip": True,
    }
    has_explicit_point_color = "color" in point_props
    if not has_color_encoding and "color" not in point_props:
        point_props["color"] = single_series_color
    return [
        ChartSpec(
            mark="point",
            mark_props=point_props,
            encoding=_sublayer_encoding(
                tooltip,
                (
                    point_props["color"]
                    if pin_child_colors
                    and (
                        has_explicit_point_color
                        or (not has_color_encoding and not inherit_parent_color)
                    )
                    else None
                ),
                paint_channels=(_point_paint_channel(point_props),),
                series_encoding={},
            ),
        )
    ]


def _area_fg_layers(
    area_opacity: float,
    stroke_width: float,
    fg_line_props: VLDict,
    has_color_encoding: bool,
    single_series_color: str,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    inherit_parent_color: bool,
    series_encoding: VLDict,
) -> list[ChartSpec]:
    """Build foreground area, optional fg line, and hover sub-layers.

    The fg area always renders.  The fg line is omitted when stroke_width is
    zero.  Single-series color is applied when no color encoding is present.
    """
    layers: list[ChartSpec] = []
    fg_area_props: VLDict = {
        "opacity": 1,
        "tooltip": True,
        "aria": False,
        "fillOpacity": area_opacity,
        "strokeOpacity": 0,
    }
    if not has_color_encoding:
        fg_area_props["fill"] = single_series_color
    layers.append(
        ChartSpec(
            mark="area",
            mark_props=fg_area_props,
            encoding=_sublayer_encoding(
                tooltip,
                (
                    single_series_color
                    if pin_child_colors
                    and not has_color_encoding
                    and not inherit_parent_color
                    else None
                ),
                paint_channels=("fill",),
                series_encoding=series_encoding,
            ),
        )
    )
    if stroke_width != 0:
        has_explicit_line_color = "stroke" in fg_line_props
        fg_line_mark_props: VLDict = {
            **fg_line_props,
            "aria": False,
            "tooltip": True,
        }
        # Only apply single_series_color when no authored stroke.color is present
        # (fg_line_props carries it when authored) and no color encoding owns color.
        if not has_color_encoding and "stroke" not in fg_line_mark_props:
            fg_line_mark_props["stroke"] = single_series_color
        layers.append(
            ChartSpec(
                mark="line",
                mark_props=fg_line_mark_props,
                encoding=_sublayer_encoding(
                    tooltip,
                    (
                        fg_line_mark_props["stroke"]
                        if pin_child_colors
                        and (
                            has_explicit_line_color
                            or (not has_color_encoding and not inherit_parent_color)
                        )
                        else None
                    ),
                    paint_channels=("stroke",),
                    series_encoding=series_encoding,
                ),
            )
        )
    layers.append(
        _hover_target_point(
            tooltip,
            single_series_color,
            has_color_encoding,
            pin_child_colors,
            announce=True,
        )
    )
    return layers


def _stacked_area_props(fg_line_props: VLDict, area_opacity: float) -> VLDict:
    """Base mark properties shared by every stacked/streamgraph area layer.

    Both the single-series path (``_area_stacked_layer``) and the multi-metric
    path (``_emit_multi_metric_area``) use the same core dict so a future change
    to the stacked mark shape lands in one place.
    """
    return {
        **fg_line_props,
        "opacity": 1,
        "tooltip": True,
        "aria": False,
        "fillOpacity": area_opacity,
    }


def _area_stacked_layer(
    area_opacity: float,
    fg_line_props: VLDict,
    has_color_encoding: bool,
    single_series_color: str,
    tooltip: list[VLDict],
    pin_child_colors: bool,
    inherit_parent_color: bool,
    series_encoding: VLDict,
) -> ChartSpec:
    """Build the stacked/streamgraph area layer: solid fill, native perimeter stroke.

    Stacked bands don't cross, so there's no halo undercoat and no separate
    top-edge line — the area mark's own stroke (unsuppressed here, unlike the
    overlap recipe's ``strokeOpacity: 0``) covers the full perimeter (top,
    bottom, sides), which is the background-color separator between adjacent
    bands (mirrors the bar.border knockout idiom). ``fg_line_props`` already
    carries the merged stacked stroke color/width — the theme's stacked
    recipe always supplies a stroke, so no single-series stroke fallback is
    needed here (unlike the overlap recipe's fg line).
    """
    area_props = _stacked_area_props(fg_line_props, area_opacity)
    if not has_color_encoding:
        area_props["fill"] = single_series_color
    return ChartSpec(
        mark="area",
        mark_props=area_props,
        encoding=_sublayer_encoding(
            tooltip,
            (
                single_series_color
                if pin_child_colors
                and not has_color_encoding
                and not inherit_parent_color
                else None
            ),
            paint_channels=("fill",),
            series_encoding=series_encoding,
        ),
    )


def sparse_band_transforms(
    x_field: str, measure_field: str, groupby: list[str]
) -> list[VLDict]:
    """Give a band with too few vertices something to sweep to.

    An area mark interpolates *between* vertices, so a series contributing a
    single point to an otherwise dense chart has no segment and paints nothing —
    while the stack still reserves its height, leaving a wedge-shaped hole. Two
    points far apart fail the other way, sweeping one band across every column
    between them. Filling each missing (x, series) pair with zero fixes both: the
    lone point becomes a wedge, and the distant pair becomes two.

    ``impute`` only fills key/group pairs that have no row at all, so an explicit
    NULL — a row that exists — would survive it untouched and still not draw. The
    filter ahead of it drops those rows first, so a null and an absent row reach
    the same wedge.

    Only a stacked chart should ask for these. A stack is a sum, so it already
    places every band above a missing series as though that series contributed
    zero — painting the zero makes an existing commitment visible. An overlap
    chart sums nothing and has made no such commitment, and filling its gaps turns
    a series sampled at irregular intervals into a sawtooth.

    ``groupby`` is stated rather than left to Vega-Lite: its compiler folds every
    encoded nominal channel into a line/area impute's groupby, and a
    per-row-distinct channel there explodes it to one group per row and fragments
    the path (see ``translate.py::_apply_structured_tooltip``). It must name every
    channel Vega-Lite will group the mark by, not just the series — an imputed row
    missing one of them reads as a further distinct group and paints a phantom
    zero-height band alongside the real ones.
    """
    return [
        {"filter": f"isValid(datum[{json.dumps(measure_field)}])"},
        {
            "impute": measure_field,
            "key": x_field,
            "groupby": groupby,
            "value": 0,
        },
    ]


def emit_area_layer(
    area_mark: ResolvedAreaMarkStyle,
    line_mark: ResolvedAreaLineStyle,
    point_mark: PointMarkStyle,
    background: str,
    single_series_fill: str,
    has_color_encoding: bool,
    series_encoding: VLDict,
    tooltip: list[VLDict],
    is_stacked: bool,
    band_transforms: list[VLDict],
    suppress_halo: bool = False,
    band_step: bool = False,
    pin_child_colors: bool = False,
    inherit_parent_color: bool = False,
) -> list[ChartSpec]:
    """Assemble sub-layers for an area chart.

    Shared by AreaEmitter (base series) and overlay layer rendering (chart.layers).
    Overlap (``is_stacked=False``): halo + foreground fill + top-edge line +
    optional point overlay + hover sub-layers. Stacked/streamgraph
    (``is_stacked=True``): a single solid-fill layer with its own perimeter
    stroke, plus the hover overlay — no halo, no separate line layer, no
    points (stacked bands don't have a meaningful per-point marker). Fill
    geometry comes from ``area_mark``; the top-edge line's stroke/halo comes
    from ``line_mark``; the optional point overlay comes from ``point_mark``
    (size 0 = no points, matching the line-chart precedent) — all already
    selected at resolve time based on the chart's stack mode.

    Carries the curve interpolation onto every area/line sub-layer so the fill
    and its top-edge stroke agree.  The hover point is left untouched.

    ``suppress_halo`` (default False, used only by chart.layers overlay
    rendering): the halo/backdrop are an opaque knockout mask painted first,
    meant to give a single series a clean edge against the page background or
    a same-chart overlap. A layer overlaid on a DIFFERENT base series must
    not paint that mask — it would opaquely block the base series from
    showing through, defeating the overlay's whole "compare against the
    base" purpose (and making a translucent fg color visually
    indistinguishable from solid, since translucent-over-opaque-white looks
    the same as a paler solid). Forces both the fill backdrop and the edge
    line's halo off.

    ``band_step`` (default False): the caller has already row-doubled the data
    for a band-aware ``step`` curve (see ``step_band.apply_step_band``) — force
    the VL interpolate to ``step-after`` on every sub-layer, overriding
    whatever the curve's plain passthrough interpolate would otherwise be.
    Also forces ``order: False`` on every area/line sub-layer (halo
    included): Vega-Lite sorts vertices by x, and the band-doubled edges tie
    in float x at some widths, so without this a tied pair can transpose and
    draw a zero-width spike instead of the plateau — see ``step_band.py``'s
    module docstring.
    """
    (
        area_opacity,
        backdrop,
        halo_multiplier,
        area_interp,
        stroke_width,
        fg_line_props,
        halo_color,
        single_series_color,
    ) = _area_style_props(area_mark, line_mark, background, single_series_fill)
    if suppress_halo:
        backdrop = False
        halo_multiplier = 0.0
    if band_step:
        area_interp = BAND_STEP_INTERPOLATE
    if is_stacked:
        layers = [
            _area_stacked_layer(
                area_opacity,
                fg_line_props,
                has_color_encoding,
                single_series_color,
                tooltip,
                pin_child_colors,
                inherit_parent_color,
                series_encoding,
            ),
            _hover_target_point(
                tooltip,
                single_series_color,
                has_color_encoding,
                pin_child_colors,
                announce=True,
            ),
        ]
    else:
        layers = _area_halo_layers(
            backdrop,
            stroke_width,
            halo_multiplier,
            halo_color,
            fg_line_props.get("strokeCap"),
            fg_line_props.get("strokeJoin"),
            tooltip,
            pin_child_colors,
            series_encoding,
        )
        layers += _area_fg_layers(
            area_opacity,
            stroke_width,
            fg_line_props,
            has_color_encoding,
            single_series_color,
            tooltip,
            pin_child_colors,
            inherit_parent_color,
            series_encoding,
        )
        layers += _area_point_sublayers(
            point_mark,
            has_color_encoding,
            single_series_color,
            tooltip,
            pin_child_colors,
            inherit_parent_color,
        )
    if area_interp is not None:
        for sub in layers:
            if sub.mark in ("area", "line"):
                sub.mark_props["interpolate"] = area_interp
                if band_step:
                    # order:False must travel with BAND_STEP_INTERPOLATE on every
                    # area/line sub-layer (halo included) — see step_band.py's
                    # module docstring for why a synthetic order ENCODING was
                    # tried instead and rejected.
                    sub.mark_props["order"] = False
    if band_transforms:
        # Fill/edge only: the hover overlay must not gain tooltip targets at
        # columns the series never reported.
        for sub in layers:
            if sub.mark in ("area", "line"):
                sub.transforms = band_transforms + sub.transforms
    return layers


# ── scatter ───────────────────────────────────────────────────────────────────


def emit_scatter_layer(
    point_mark: PointMarkStyle,
    has_color_encoding: bool,
    single_series_fill: str,
) -> VLDict:
    """Build mark_props for a scatter series.

    Shared by ScatterEmitter (base series) and overlay layer rendering (chart.layers).
    When no color encoding is present, applies single_series_fill directly so
    the mark is painted with the chart's single-series palette slot.
    """
    mark_props: VLDict = {"tooltip": True}
    if not has_color_encoding:
        mark_props["fill"] = single_series_fill
    mark_props.update(scatter_mark_to_vl(point_mark))
    return mark_props
