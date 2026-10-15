"""BarHoverBandFeature -- baseline-anchored invisible hover band for bar charts.

Appended as a sub-layer (invisible ``mark="bar"``, ``opacity=0``) after the emitter
so near-zero bars have a wider hover hit-target without any visible change. Two
distinct paths, both gated in ``BarHoverBandFeature.apply``:

Two separate engine constants: ``HOVER_BAND_TRIGGER_FRACTION`` (does this bar/
total need help at all -- default 0.02, deliberately small so the band never
fires on a bar that's already plainly visible) and ``HOVER_BAND_EXTEND_FRACTION``
(how big the band is once it does fire -- default 0.05). Kept distinct so
tightening "when does this fire" doesn't also shrink the hit target once it
does.

- **Non-stacked** (``_append_bar_hover_area``): gated twice. First, a cheap
  Python-side precheck over the chart's own rows: if no row's value is under
  ``HOVER_BAND_TRIGGER_FRACTION`` of the largest, the layer is never declared
  at all -- an unconditional layer would still add an invisible zero-extent
  path per row even when it never extends, which is markup with no hover
  value and shows up as noise in every visual golden diff. Once declared,
  each row is gated again, exactly: anchored at ``scale('ch', 0)`` -- the
  zero baseline -- extending by ``HOVER_BAND_EXTEND_FRACTION`` of the plot's
  pixel extent toward the datum's sign, but ONLY when the bar's own rendered
  extent (read directly off the real bar's scale via
  ``scale(ch, datum[field])``, not the Python-side estimate) is already
  smaller than ``HOVER_BAND_TRIGGER_FRACTION`` of the plot. A bar at or above
  that trigger gets no extended hit area at all -- there's nothing to gain by
  widening an already-adequate hit target.
- **Stacked** (``_append_stacked_total_hover_band``, ``chart.stack == "zero"``
  only): a stacked segment's true position depends on Vega-Lite's own cumulative
  stack transform, which this design deliberately never reimplements (see below)
  -- so instead of a per-row band, at most ONE shared band is added per x-category
  whose stacked TOTAL renders near-invisible (under ``HOVER_BAND_TRIGGER_FRACTION``
  of the domain max), carried by one real row for that x. Per-segment precision is
  unnecessary: ``chart_interactivity.js``'s x-unified tooltip already shows every
  series at a given x once ANY mark there is hovered.

The band has no data, aria override, or sub-layer transform of its own -- it
inherits ``spec.data`` and the shared structured-tooltip description like any
ordinary sub-layer, so it gets its own correct ``aria-label`` and self-identifies
directly on hover, with no proximity-based fallback involved. Both alternatives
were tried and rejected: private per-layer data gets its description forced to
``None`` by ``translate.py::_apply_structured_tooltip``'s "private data means
incompatible row shape" guard; a sub-layer ``transform`` -- even on this LAST
layer, even unrelated to the real bar's own transforms -- makes vl-convert
discard an authored ``encoding.<channel>.sort`` (the documented hazard in
``features/value_labels.py:450-456``; it generalizes to any sub-layer transform,
not just one sharing a layer with the sort-dependent encoding).

Without a transform to exclude null-measure rows, a null measure instead
collapses the band to zero extent via the same sign ternary that already
branches per row. The row's category still reaches the shared x-scale domain
through the band's ordinary field-encoded categorical channel -- exactly as the
real bar layer's own field-encoded x already does independently of the band, via
this repo's ``config.mark.invalid: "break-paths-show-domains"`` fix (see
``test_bar_null_bucket_axis.py``) -- so there is nothing extra for the band to
contribute or diverge on.

``tooltip: True`` in ``band_mark_props`` is load-bearing, not decorative: without
it, a chart with a legend (``LegendToggleFeature``'s ``dct_legend`` param) marks
the band's layer non-interactive (``translate.py`` skips opacity-0 layers when
stamping that param), which Vega compiles straight to ``pointer-events: none`` --
the band would then never receive a mouse event at all on any legend-bearing bar
chart.

Exclusions (all gated in ``applies_to``):
- Histograms: their VL pipeline differs from bar charts.
- Multi-metric (wide) bars (``y: [...]``): no single scalar measure channel.
- Combo-overlay bars (``chart.layers`` set): the overlay pipeline wraps the spec
  into its own layered structure and must remain the sole authority on layers.
- Faceted (``chart.multiples is not None``): ``height``/``width`` in the band
  expression resolve to root-scope Vega signals; child views use
  ``child_height``/``child_width`` instead, so the band would have zero extent.
- Stacked ``normalize``/``center`` (``chart.stack`` neither ``None``/``"none"``
  nor ``"zero"``): not handled by either path above. ``normalize`` always fills
  the full plot height (nothing is ever near-invisible there); ``center``'s
  baseline floats per-x rather than sitting at a fixed zero, which the
  total-tininess math above doesn't account for. A per-ROW band on either would
  repeat the original stacked bug: the band's measure channel would be a fixed
  baseline-relative expression, never stacked, so every series at one x would
  emit an identical unstacked rect and whichever paints last wins every hit
  test -- wrong for hover (lost per-segment tooltip highlight) and wrong for an
  authored ``link:`` (click navigates to the wrong segment's URL).

Assumes 0 is within the measure axis's domain -- true by default, but an
authored non-zero-inclusive ``axis_y.scale.continuous.domain`` can violate it;
Vega then simply clamps the band to the plot floor rather than erroring.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.chart.resolved._layer import effective_color_field
from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
from dbt_charts.core.compile.resolve.chart.tick_values import (
    ChartValue,
    stacked_bar_totals,
)
from dbt_charts.core.constants.bar import (
    HOVER_BAND_EXTEND_FRACTION,
    HOVER_BAND_TRIGGER_FRACTION,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.artifacts import ChartRenderData
from dbt_charts.core.render.chart.feature import chart_rows
from dbt_charts.core.render.chart.spec import ChartSpec, RenderBox
from dbt_charts.core.utils import coerce_numeric_cell

# VL named style applied to the hover band mark.  Used by tests to identify
# band sub-layers in the VL spec dict (``la["mark"].get("style") == _HOVER_BAND_STYLE``).
# Also useful for VL config hooks (e.g. you can set ``dct-hover-band`` in the
# VL config to apply mark defaults to the band). Not ``dbt-`` — that prefix is
# reserved for the CSS/SVG-facing surface (``dbt-box-outer`` etc.); VL style
# names do NOT appear as CSS classes in rendered SVG, so this isn't that
# surface — the band is instead recognized at the SVG level by
# ``opacity="0"`` on its rect paths (mark_extents.py).
_HOVER_BAND_STYLE = "dct-hover-band"

# Matches ChartFeature.apply's datasets param.
_Datasets = dict[str | None, list[VLDict]]


def _signed_extent_expr(
    measure_ch: Literal["x", "y"], measure_field: str, band: str, baseline_expr: str
) -> str:
    """The sign-branching pixel extent for one row's band, before any outer gate."""
    if measure_ch == "y":
        # Vertical: positive bars sit ABOVE the baseline (lower y in screen coords);
        # the band extends upward (smaller y) -> subtract band from baseline.
        return f"{baseline_expr} + (datum['{measure_field}'] >= 0 ? -{band} : {band})"
    # Horizontal: positive bars sit RIGHT of the baseline (higher x in screen
    # coords); the band extends rightward -> add band to baseline.
    return f"{baseline_expr} + (datum['{measure_field}'] >= 0 ? {band} : -{band})"


def _band_mark_props(spec: ChartSpec) -> VLDict:
    """Mark props for a new hover band layer, mirroring the visible bar's thickness.

    Mirrors ``width``/``height``/``size`` from the visible bar layer (a
    literal pixel value, a band-fraction dict, or a continuous-scale
    ``{"expr": ...}``) so the band matches the real bar's own footprint.
    Without these the band can span the gutter (no "width"/"height" -> full
    slot) or be narrower than the bar (no "size" on grouped bars), both
    causing a wrong-neighbor hit. ``tooltip: True`` is load-bearing, not
    decorative: without it, a legend-bearing chart's ``dct_legend`` param
    stamping skips opacity-0 layers, which Vega then compiles to
    ``pointer-events: none`` on the band.
    """
    band_mark_props: VLDict = {
        "opacity": 0,
        "style": _HOVER_BAND_STYLE,
        "tooltip": True,
    }
    visible_mark_props = (
        spec.layers[0].mark_props
        if (spec.mark == "layered" and spec.layers)
        else spec.mark_props
    )
    for key in ("width", "height", "size"):
        val = visible_mark_props.get(key)
        if val is not None:
            band_mark_props[key] = val
    return band_mark_props


def _promote_and_append_layer(spec: ChartSpec, hover_layer: ChartSpec) -> None:
    """Append ``hover_layer`` to ``spec``, promoting a flat bar spec to layered first.

    When ``spec.mark == "bar"`` (flat spec), promotes to ``"layered"`` by
    wrapping the existing bar in sub-layer 0 (moving ``mark_props`` only --
    ``transforms`` stay on the outer spec so outer-encoding references like
    ``encoding.order`` on a ``__df_series_order`` calculate remain reachable).
    When already ``"layered"`` (mixed-sign corner-rounding split), appends the
    band as the final sub-layer.
    """
    if spec.mark == "bar":
        existing_layer = ChartSpec(
            mark="bar",
            mark_props=spec.mark_props,
            encoding={},
        )
        spec.mark = "layered"
        spec.mark_props = {}
        # Preserve overlay layers (text labels from ValueLabelFeature, etc.) that
        # ran before us in the pipeline; hover band goes after bar and existing
        # overlays.  spec.transforms stays on the outer spec -- do NOT move to
        # existing_layer.  Transforms added by the emitter (e.g. __df_series_order
        # calculate) are referenced by encoding.order on the OUTER encoding; moving
        # them to a sub-layer would orphan them from their reference.
        existing_overlays = list(spec.layers)
        spec.layers = [existing_layer, *existing_overlays, hover_layer]
    else:
        # Already layered (mixed-sign corner-rounding split); append.
        spec.layers.append(hover_layer)


def _append_bar_hover_area(
    spec: ChartSpec,
    measure_ch: Literal["x", "y"],
    measure_field: str,
    data: ChartRenderData,
) -> None:
    """Append an invisible baseline-anchored hover band to ``spec`` in place.

    No-op when the complementary (categorical) axis channel is absent from
    ``spec.encoding`` -- the bar has no categorical anchor to hover against.
    Also no-op when no row in ``data`` is tiny relative to the chart's own
    approximate value range -- adding a layer that would never extend for any
    row is markup with no hover value (an invisible zero-extent path per row,
    still present in the rendered SVG even though no pixel moves), so the
    layer itself must not be declared at all in that case, not merely
    collapsed. This precheck only decides WHETHER to declare the layer; it
    uses a plain per-row max, not the exact resolved Vega domain (headroom,
    "nice" rounding), so it's an approximation -- fine for a yes/no gate, see
    the per-row expression below for the exact geometry.

    Once the layer IS declared, each row's own extent is gated exactly:
    only extends for a row whose own rendered extent is already smaller than
    the band would be -- there's nothing to gain by adding a hit area no
    wider than the bar's own already-hoverable shape. Read directly off the
    real bar's own scale via ``scale(ch, datum[field])`` (the same
    scale/position VL uses to draw it), not a separately-computed Python-side
    domain estimate -- exact by construction, no "nice"-domain/headroom
    mismatch to approximate.
    """
    cat_ch: Literal["x", "y"] = "x" if measure_ch == "y" else "y"
    if cat_ch not in spec.encoding:
        return

    values = [
        abs(v)
        for row in data
        if (v := coerce_numeric_cell(row.get(measure_field))) is not None
    ]
    if not values:
        return
    domain_max = max(values)
    if domain_max <= 0 or not any(
        v < domain_max * HOVER_BAND_TRIGGER_FRACTION for v in values
    ):
        return

    band = (
        f"height * {HOVER_BAND_EXTEND_FRACTION}"
        if measure_ch == "y"
        else f"width * {HOVER_BAND_EXTEND_FRACTION}"
    )
    trigger_px = (
        f"height * {HOVER_BAND_TRIGGER_FRACTION}"
        if measure_ch == "y"
        else f"width * {HOVER_BAND_TRIGGER_FRACTION}"
    )
    baseline_expr = f"scale('{measure_ch}', 0)"
    signed_extent = _signed_extent_expr(measure_ch, measure_field, band, baseline_expr)
    own_extent_expr = (
        f"abs(scale('{measure_ch}', datum['{measure_field}']) - {baseline_expr})"
    )
    # A null measure, or a bar whose own rendered extent is already at/above
    # the trigger threshold, collapses to zero extent (both edges at the
    # baseline) -- neither via a transform, see module docstring for why one
    # isn't an option here.
    extent_expr = (
        f"isValid(datum['{measure_field}']) && ({own_extent_expr} < {trigger_px}) "
        f"? ({signed_extent}) : {baseline_expr}"
    )

    hover_layer = ChartSpec(
        mark="bar",
        mark_props=_band_mark_props(spec),
        encoding={
            measure_ch: {"value": {"expr": baseline_expr}},
            f"{measure_ch}2": {"value": {"expr": extent_expr}},
        },
    )
    _promote_and_append_layer(spec, hover_layer)


def _append_stacked_total_hover_band(
    spec: ChartSpec,
    chart: ResolvedBarChart,
    measure_ch: Literal["x", "y"],
    measure_field: str,
    datasets: _Datasets,
) -> None:
    """Append one shared invisible hover band per near-invisible stacked total.

    Per-segment precision is unnecessary for a stacked bar: chart_interactivity.js's
    x-unified tooltip already shows every series at a given x once ANY mark there is
    hovered (see ``collectMatchingMarks``). The only real gap is a column whose
    TOTAL renders too short to hover at all. Giving every series its own band (the
    non-stacked design) would reintroduce the wrong-neighbor hit-test/link bug
    stacked exclusion was added to avoid -- multiple bands at the same tiny x would
    overlap, and whichever painted last would win every hit test. So this emits at
    most ONE band per tiny x, carried by one arbitrarily-but-deterministically
    chosen REAL row for that x (the first one encountered in row order). Because the
    carrier is a genuine existing row, not synthetic, it inherits ``spec.data`` and
    the shared structured-tooltip description exactly like the non-stacked band --
    no private data, no aria override, no new failure mode to guard against.
    """
    cat_ch: Literal["x", "y"] = "x" if measure_ch == "y" else "y"
    if cat_ch not in spec.encoding:
        return
    if not isinstance(chart.x, str):
        return
    series_field = effective_color_field(chart)
    if series_field is None:
        return
    x_field = chart.x

    # applies_to() gates on chart.multiples is None, so this always sees the
    # N=1 (single-panel) dataset — .all_rows() is the whole flat row set.
    data = chart_rows(chart, datasets).all_rows()
    totals = stacked_bar_totals(data, x_field, measure_field)
    if not totals:
        return
    domain_max = chart.stacked_domain_max or max(totals.values())
    if domain_max <= 0:
        return

    carriers: dict[ChartValue, ChartValue] = {}
    for row in data:
        x = row.get(x_field)
        if x is None or x in carriers:
            continue
        total = totals.get(x)
        if total is None or total / domain_max >= HOVER_BAND_TRIGGER_FRACTION:
            continue
        series = row.get(series_field)
        if series is None:
            continue
        carriers[x] = series
    if not carriers:
        return

    band = (
        f"height * {HOVER_BAND_EXTEND_FRACTION}"
        if measure_ch == "y"
        else f"width * {HOVER_BAND_EXTEND_FRACTION}"
    )
    baseline_expr = f"scale('{measure_ch}', 0)"
    signed_extent = _signed_extent_expr(measure_ch, measure_field, band, baseline_expr)
    carrier_test = " || ".join(
        f"(datum['{x_field}'] === '{x}' && datum['{series_field}'] === '{series}')"
        for x, series in carriers.items()
    )
    extent_expr = f"({carrier_test}) ? ({signed_extent}) : {baseline_expr}"

    hover_layer = ChartSpec(
        mark="bar",
        mark_props=_band_mark_props(spec),
        encoding={
            measure_ch: {"value": {"expr": baseline_expr}},
            f"{measure_ch}2": {"value": {"expr": extent_expr}},
        },
    )
    _promote_and_append_layer(spec, hover_layer)


@dataclass
class BarHoverBandFeature:
    """Appends an invisible baseline-anchored hover band to eligible bar charts.

    See the module docstring for the full contract and exclusion rationale.
    """

    def applies_to(self, chart: ResolvedChart) -> bool:
        return (
            isinstance(chart, ResolvedBarChart)
            and chart.chart_type != "histogram"
            and isinstance(chart.y, str)
            and not chart.layers
            and chart.multiples is None
            and chart.stack in (None, "none", "zero")
        )

    def apply(
        self,
        spec: ChartSpec,
        chart: ResolvedChart,
        box: RenderBox,
        datasets: _Datasets,
    ) -> ChartSpec:
        assert isinstance(chart, ResolvedBarChart)
        assert isinstance(chart.y, str)
        measure_field = chart.y
        measure_ch: Literal["x", "y"] = (
            "x" if chart.orientation == "horizontal" else "y"
        )
        if chart.stack == "zero":
            _append_stacked_total_hover_band(
                spec, chart, measure_ch, measure_field, datasets
            )
        else:
            _append_bar_hover_area(
                spec,
                measure_ch,
                measure_field,
                chart_rows(chart, datasets).all_rows(),
            )
        return spec


def hover_band_layers(layers: Iterator[VLDict]) -> list[VLDict]:
    """Return sub-layers in ``layers`` that carry the hover band.

    Identifies by ``mark.style == _HOVER_BAND_STYLE`` -- set only by this module,
    never collides with authored mark styles.
    """
    return [
        la
        for la in layers
        if isinstance(la.get("mark"), dict)
        and la["mark"].get("style") == _HOVER_BAND_STYLE
    ]
