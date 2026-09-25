"""Shared overlay rendering for cartesian chart.layers.

When a bar/line/area/scatter chart carries authored ``layers``, the base chart
emits its base spec as usual; then this module wraps both into an outer
``mark="layered"`` spec with the overlay sublayers appended in authored order
(paint order: base first / bottom, each overlay on top).

Dual-axis, mixed-mark legend symbols, and step-band xOffset resolution are
handled here.
"""

from __future__ import annotations

import json as _json
import math
from typing import Any, Literal

from dbt_charts.core.compile.models.chart.resolved._layer import (
    ResolvedAreaLayer,
    ResolvedBarLayer,
    ResolvedLayer,
    ResolvedLineLayer,
    ResolvedScatterLayer,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedLegendStyle
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.resolve.chart.tick_values import numeric_domain_bounds
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_LAYER_AXIS_POSITION_ORIENTATION,
    ERR_LAYER_STEP_ORIENTATION,
)
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.emitters._cartesian import (
    authored_measure_domain,
    build_zero_rule_if_applicable,
    canonicalize_cartesian_x_data,
    distinct_series_values,
    layer_encoding_owner,
    nest_zero_rule,
    non_bar_zero_rule_should_fire,
    spatial_color_scale,
    values_straddle_zero,
    x_encoding_is_banded,
)
from dbt_charts.core.render.chart.emitters._channels import (
    apply_color_legend,
    apply_legend_entry_order,
    layer_label_and_aliases,
)
from dbt_charts.core.render.chart.emitters._layers import (
    CLIP_TO_PLOT,
    emit_area_layer,
    emit_bar_layer,
    emit_line_layer,
    emit_scatter_layer,
)
from dbt_charts.core.render.chart.emitters._tooltip import (
    TooltipField,
    build_structured_tooltip_expr,
    header_tooltip_field,
    series_row_promoted,
    span_tooltip_rows,
)
from dbt_charts.core.render.chart.features.value_labels import (
    BandLabelAnchor,
    _build_bar_text_layer,
    _build_point_text_layer,
    _layer_label_slots,
    band_label_anchor,
    build_line_text_layers,
    labels_draw_text,
    text_layer_spec,
)
from dbt_charts.core.render.chart.spec import ChartSpec
from dbt_charts.core.render.chart.step_band import (
    BAND_STEP_CURVE,
    apply_step_band,
    is_band_step,
)
from dbt_charts.core.render.chart.time_unit_detect import (
    calendar_bucket_key,
    ordinal_axis_values,
)
from dbt_charts.core.render.chart.type_inference import (
    infer_vega_type_from_data,
    is_date_like_string,
)
from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl, bar_mark_radius
from dbt_charts.core.render.chart.x_domain import rendered_x_domain
from dbt_charts.core.render.utils import (
    normalize_data_types,
    ordered_distinct_values,
)
from dbt_charts.core.text.case import default_axis_title

# VL scale types where the domain is an ordered list of discrete categories
# (as opposed to a continuous [min, max] range). Union-domain reconciliation
# below is scoped to this case only — a continuous (temporal/quantitative)
# shared x scale already gets a correct min/max union natively from Vega-Lite
# across sub-layers, ordering never matters, so no explicit reconciliation
# is needed there.
_CATEGORICAL_X_TYPES = ("nominal", "ordinal")

# A data row: same shape as VLDict (str-keyed, untyped values) — aliased under
# its own name so row-of-query-results reads distinctly from a VL fragment.
_Row = VLDict

# Combo tooltip unification (StructuredTooltipFeature) covers bar/line/area
# bases only -- matches applies_to()'s own gate in
# features/structured_tooltip.py. A scatter base has no natural x "header"
# identity for the x-unified bubble (scatter's own role model carries no
# header row at all; x/y are two peer VALUE rows instead), so a scatter base
# stays on the plain VL auto-tooltip. Overlay layers atop a scatter base get
# no structured description either -- keeping both sides on the SAME (old,
# unmodified) behavior, rather than a half-migrated state where only the
# overlay is role-marked and can never unify with its own base.
_STRUCTURED_TOOLTIP_BASE_FAMILIES = frozenset({"bar", "line", "area"})


def _base_domain_is_date_shaped(base_data: list[_Row], base_field: str) -> bool:
    """True when every distinct value of the base's own x column is date-like.

    Drives the one deliberate exception in _resolve_layer_x_encoding: a
    layer classifying "temporal" standalone is only union-compatible with a
    categorical base when the base's OWN labels are also dates (just
    density-gated to ordinal/nominal by the bar/line x-encoding builder) —
    never with a base whose categories are plain non-date labels.
    """
    values = ordered_distinct_values(base_data, base_field)
    return bool(values) and all(
        isinstance(v, str) and is_date_like_string(v) for v in values
    )


def _resolve_layer_x_encoding(
    x_field: str,
    rows: list[_Row],
    base_x_enc: VLDict,
    base_domain_is_date_shaped: bool,
    chart_id: str,
) -> VLDict:
    """Build a layer's own x encoding, its type resolved against the base's
    x type at construction time — not classified standalone and patched
    afterward.

    A layer authoring its own x column is classified independently via
    infer_vega_type_from_data. Sharing a scale with a categorical
    (nominal/ordinal) base works whenever the layer ALSO classifies as
    categorical (nominal or ordinal) — both are discrete-label types, and
    the two independent per-field classifications routinely disagree on
    which one (e.g. a bucket-gated date base reads "ordinal" while a plain-
    label goal/target layer reads "nominal") without the underlying data
    being incompatible. The one further exception is a layer classifying
    "temporal" (well-formed dates) against a base whose own domain is ALSO
    date-shaped (see _base_domain_is_date_shaped) — that's the same
    date-shaped labels on both sides, just density-gated differently. Either
    way the layer's type is pinned to the base's before ANY downstream
    consumer (step-band curve detection, the union domain) ever sees the
    standalone classification. A layer resolving to a genuinely incompatible
    type — quantitative, or non-date-shaped temporal against a non-date-
    shaped base — raises; this never silently accepts a mismatch.

    A quantitative base additionally rejects a layer resolving to anything
    other than "quantitative" — sharing a numeric scale with a string field
    gives Vega-Lite no pixel positions at all (NaN), and sharing it with a
    temporal field forces the base's plain-number tick format onto the
    layer's epoch-ms values (garbled labels, not a correct union) — neither
    is the "continuous shared x scale gets a correct min/max union natively"
    case the categorical-base check exists to make an exception for. This
    only runs when the layer's classification is data-backed (rows present
    and the field is actually in them): infer_vega_type_from_data returns
    "nominal" as an absence sentinel for empty data, which is not a real
    classification and must not be treated as a genuine string-vs-number
    mismatch — an ordinary empty-query overlay layer is unremarkable, not an
    error.

    base_x_enc carries no "type" key (or a temporal one) when the base x
    isn't nominal/ordinal/quantitative at all — no validation runs there,
    returning the layer's own standalone classification unchanged. That
    temporal-base gap is a separate, out-of-scope follow-up. Callers pass {}
    (never None) when the base has no x encoding.
    """
    layer_type = infer_vega_type_from_data(rows, x_field)
    base_type = base_x_enc.get("type")
    if (
        base_type == "quantitative"
        and rows
        and x_field in rows[0]
        and layer_type != "quantitative"
    ):
        raise ChartDataError(
            f"chart '{chart_id}': layer x '{x_field}' resolved to a "
            f"{layer_type} scale, but the base x '{base_x_enc.get('field')}' is "
            f"{base_type} — a layer sharing the x axis with a quantitative "
            "base must itself resolve to a quantitative type.",
            chart_id=chart_id,
        )
    if base_type not in _CATEGORICAL_X_TYPES:
        return {"field": x_field, "type": layer_type}
    if layer_type in _CATEGORICAL_X_TYPES:
        return {"field": x_field, "type": base_type}
    if layer_type == "temporal" and base_domain_is_date_shaped:
        return {"field": x_field, "type": base_type}
    raise ChartDataError(
        f"chart '{chart_id}': layer x '{x_field}' resolved to a "
        f"{layer_type} scale, but the base x '{base_x_enc.get('field')}' is "
        f"{base_type} — layers sharing the x axis with the base must "
        "resolve to the same discrete-label domain.",
        chart_id=chart_id,
    )


def _reconcile_x_domain(
    x_enc: VLDict,
    base_data: list[_Row],
    layer_x_columns: list[tuple[str, list[_Row]]],
    chart_id: str,
) -> None:
    """Pin an explicit ordered-union domain on a layered chart's shared
    categorical x scale.

    Unconditional on purpose. Vega-Lite's native sort-by-field does resolve
    across a *plain* layer array, but reverts the shared categorical scale to
    alphabetical the moment anything forks the dataflow feeding it: a
    ``transform:`` pipeline on the shared data (a ``color:`` channel's own
    series-order ``calculate``, a label sublayer's house-register
    ``calculate``, a support_table cell layer) or a base mark split into
    sign-filtered sub-layers (``pin_categorical_domain_order``,
    ``emitters/_layers.py``). None of those are legible here, and none of them
    show up in ``layer_x_columns`` — a layer authoring neither its own ``x:``
    nor its own ``query:`` contributes nothing to it while still forking the
    dataflow — so an empty ``layer_x_columns`` is no evidence the domain is
    safe.

    ``rendered_x_domain`` decides the pinned order, including how an authored
    sort on the base x encoding and a layer-only category interact (see its own
    docstring); this function only writes its result. That result also has to
    overwrite the narrower domain the base may already have pinned from its own
    rows alone (``pin_categorical_domain_order``, ``pin_sorted_domain``), or
    a layer-only category is silently dropped from the shared scale.

    No-op when the base x scale isn't categorical (nominal/ordinal). Every
    layer's own x type has already been resolved against the base's (see
    _resolve_layer_x_encoding) by the time this runs — this function only
    unions and orders values, it never classifies or raises.
    """
    if x_enc.get("type") not in _CATEGORICAL_X_TYPES:
        return
    if not isinstance(x_enc.get("field"), str):
        return
    x_enc.setdefault("scale", {})["domain"] = rendered_x_domain(
        x_enc, base_data, layer_x_columns, chart_id=chart_id
    )


def _resolve_layer_rows(
    layer: ResolvedLayer,
    base_x_field: str | None,
    axis_x: ResolvedAxisStyle,
    base_x_authored_temporal: bool,
    datasets: dict[str | None, list[_Row]] | None,
    base_query_name: str | None,
) -> tuple[str | None, list[_Row] | None]:
    """Return this layer's effective x field and its OWN rows (None → shares base's).

    A layer's rows genuinely diverge from the base's only when its resolved
    query name differs from the base's own — an unauthored layer's query_name
    defaults to ``base_query_name`` (see ``render_cartesian_overlay``'s
    docstring), so ``layer.query_name is not None`` is true for nearly every
    layer and is NOT a divergence signal. A non-diverging layer reads the
    base's own ``data`` (already gap-filled/bucket-normalized by the family
    emitter), never the raw ``datasets`` lookup — using ``datasets`` there
    would read pre-normalization rows and mismatch what the base renders.

    Own-query rows are canonicalized through the same
    ``canonicalize_cartesian_x_data`` the base ran, so every layer shares one
    x domain in the same JS-``Date``-parseable string form rather than
    splitting onto two (which halves the marks across the axis, or — for a
    datetime split — buckets the overlay off the base outside UTC).
    ``skip_bucket_collapse`` inherits the base's OWN verdict
    (``base_x_authored_temporal``) rather than re-deriving it from ``axis_x``.
    The authored ``time_unit`` only carries over when the layer shares the
    base's own field; a layer with its own DIFFERENT x column gets None
    (auto-detect), since the base's authored grain was never chosen for it.

    Shared by ``render_cartesian_overlay``'s own loop and by
    ``overlay_x_domain_values`` so the x domain the axis labels is derived
    from exactly the rows the layers render.
    """
    effective_x_field = layer.x if layer.x is not None else base_x_field
    layer_diverges = layer.query_name != base_query_name
    own_data: list[_Row] | None = (
        datasets.get(layer.query_name)
        if layer_diverges and datasets is not None
        else None
    )
    if own_data is not None and effective_x_field is not None:
        layer_authored_time_unit = (
            axis_x.time_unit if effective_x_field == base_x_field else None
        )
        own_data, _ = canonicalize_cartesian_x_data(
            own_data,
            effective_x_field,
            layer_authored_time_unit,
            skip_bucket_collapse=base_x_authored_temporal,
        )
    return effective_x_field, own_data


def _orderable_axis_values(rows: list[_Row], field: str) -> list[Any] | None:
    """``ordinal_axis_values``, or None when the column has no orderable domain.

    Query results are not guaranteed homogeneous, and ``ordinal_axis_values``
    sorts internally — a column mixing ints and strings raises ``'<' not
    supported between instances of 'str' and 'int'`` inside it, before any
    caller can inspect the values. A column with no total order simply has no
    tick domain to derive, which is a fact about the data rather than a
    failure: the axis keeps deriving its own ticks exactly as it did before
    this function existed. Catching is the only way to learn it here.
    """
    try:
        return ordinal_axis_values(rows, field)
    except TypeError:  # unsortable or unhashable column — no total order
        return None


def _band_identity(value: Any) -> Any:
    """The band ``value`` occupies — its instant, else the value itself.

    Non-calendar columns have no parse to key on and are already one band per
    distinct value, so they key on themselves.
    """
    key = calendar_bucket_key(value)
    return value if key is None else key


def _same_x_vocabulary(base_values: list[Any], layer_values: list[Any]) -> bool:
    """True when two x columns hold the same KIND of value, not just the same name.

    Two gates. Types must match, or the merged list cannot even be sorted
    (``'<' not supported between 'str' and 'int'``). Then calendar-membership
    must match **in both directions**: a plain-label column of strings passes
    the type gate but is not a bucket of the base's calendar grain, and merging
    either way round makes grain detection raise. Asking only "is the BASE a
    calendar column" let a base this module failed to recognize fall through to
    the bare type comparison and admit a plain-label layer.

    Membership is decided by the same parser the downstream consumers use, not
    by a date-shaped pattern: a pattern disagrees with the parser in both
    directions — accepting the unparseable half-year forms (``2024-H1``) and
    rejecting parseable timezone-aware ones — and either disagreement admits a
    value that raises later.

    The two sides must be wholly calendar or wholly not. ``all(base) ==
    all(layer)`` is not equivalent: a base carrying a few unparseable strays is
    not "all calendar", so it matches a plain-label layer on both-are-false.
    ``any`` fails the other way, admitting a layer that mixes one real date
    into labels.
    """
    if {type(v) for v in layer_values} != {type(v) for v in base_values}:
        return False
    base_calendar = [calendar_bucket_key(v) is not None for v in base_values]
    layer_calendar = [calendar_bucket_key(v) is not None for v in layer_values]
    if all(base_calendar) and all(layer_calendar):
        return True
    return not any(base_calendar) and not any(layer_calendar)


def overlay_uses_band_step(layers: tuple[ResolvedLayer, ...]) -> bool:
    """True when any overlay layer draws the band-aware ``step`` curve.

    Such a layer shares the base's x scale and doubles its rows onto each
    band's two edges, so it depends on adjacent band edges being the same
    float (``step_band.py``). Anything that perturbs the band scale — even
    sub-pixel — breaks that pairing, so the base emitter must know before it
    builds the scale, not after the overlay wrap.
    """
    for layer in layers:
        # A y-less layer is skipped entirely by render_cartesian_overlay, so
        # it doubles nothing and has no shared edge to protect — exempting on
        # it would silently reinstate the cull this fix exists to remove.
        if layer.y is None:
            continue
        if isinstance(layer, ResolvedLineLayer):
            if layer.line_mark.curve == BAND_STEP_CURVE:
                return True
        elif isinstance(layer, ResolvedAreaLayer) and (
            layer.area_mark.curve == BAND_STEP_CURVE
        ):
            return True
    return False


def overlay_x_domain_values(
    layers: tuple[ResolvedLayer, ...],
    data: list[_Row],
    base_x_field: str | None,
    axis_x: ResolvedAxisStyle,
    base_x_authored_temporal: bool,
    datasets: dict[str | None, list[_Row]] | None,
    base_query_name: str | None,
) -> list[Any] | None:
    """Sorted distinct x values across the base series and every overlay layer.

    Vega-Lite unions the sub-layer domains of a shared x scale, so an overlay
    running past the base (a forward goal ramp against actuals, say) grows the
    band scale beyond the base's own rows. The axis tick values and the label
    overlap measurement must both be derived from THAT domain — deriving
    either from the base's rows alone leaves the extra bands unlabeled and
    measures crowding against a band count that isn't the one being rendered.

    A layer contributes only when its column speaks the base's own vocabulary
    — same field name AND same value shape. Sharing a field *name* is not
    enough: a layer's own query can return a column of the same name holding
    something else entirely (plain labels against monthly bars, integers
    against string SKUs). Merging those would corrupt the tick ladder, and
    would also fail outright — mixed types are unsortable, and plain labels
    make calendar-grain detection raise against a remedy pointing at the axis
    style rather than at the layer's query. Those layers keep exactly their
    pre-existing treatment: ``_reconcile_x_domain``'s categorical union still
    puts their categories on the shared scale, and the base's own rows still
    govern the calendar ticks.

    Returns None when no x values exist, matching ``ordinal_axis_values``.
    """
    if base_x_field is None:
        return None
    # Runs for every layered chart — including ones whose x never resolves to
    # a bucketed calendar grain, where nothing read these values before. So
    # adding a layer to a working chart must never be what breaks it.
    base_values = _orderable_axis_values(data, base_x_field)
    if base_values is None:
        return None
    # Keyed by the band each value denotes, not by its spelling. A layer whose
    # own query returns datetimes isoformats to "2023-01-01T00:00:00+00:00"
    # where the base spells the same band "2023-01-01" — one band, two strings.
    # Counting both measures a scale wider than the one rendering, which
    # rotates labels that fit flat. The base is inserted first so its spelling
    # wins wherever the two disagree.
    by_band: dict[Any, Any] = {}
    for value in base_values:
        by_band.setdefault(_band_identity(value), value)
    for layer in layers:
        if layer.y is None:
            continue
        x_field, own_data = _resolve_layer_rows(
            layer,
            base_x_field,
            axis_x,
            base_x_authored_temporal,
            datasets,
            base_query_name,
        )
        if own_data is None or x_field != base_x_field:
            continue
        layer_values = _orderable_axis_values(own_data, base_x_field)
        if layer_values is not None and _same_x_vocabulary(base_values, layer_values):
            for value in layer_values:
                by_band.setdefault(_band_identity(value), value)
    return sorted(by_band.values())


def _build_layer_label_specs(
    layer: ResolvedLayer,
    y_field: str,
    background: str,
    rows: list[_Row],
    band: BandLabelAnchor | None,
    val_ch: str,
) -> list[ChartSpec]:
    """Build this overlay layer's OWN value-label text layers, or [] when unset.

    Mirrors ``ValueLabelFeature``'s per-family dispatch but reads the layer's
    OWN mark style instead of the base chart's — each typed overlay layer
    carries its own full resolved mark style, so its labels are independent of
    the base chart's.

    Its POSITION is not: ``val_ch`` is the channel the base measures on, and a
    layer label pinned to VL ``y`` lands on the category axis of a horizontal
    base, opening a second quantitative scale across the category names.

    The slots themselves come from
    ``_layer_label_slots`` (``features/value_labels.py``) — the single
    per-family layer -> mark-style-attribute mapping both this function and
    ``ValueLabelFeature``'s column-existence validation read, so the two
    never drift onto different attribute paths for the same layer type.
    Callers stamp ``.data`` onto the returned specs when the layer authored
    its own ``query:`` (own_data is not None).

    ``band`` is this layer's own band verdict (its ``curve`` already applied
    the band transform above), non-None only for a line/area layer drawing a
    full-band-width plateau. It can split a horizontal position into two
    layers — see ``build_line_text_layers``.
    """
    if isinstance(layer, ResolvedBarLayer):
        (bar_labels,) = _layer_label_slots(layer)
        if bar_labels.visible is not True:
            return []
        text_layer = _build_bar_text_layer(
            bar_labels,
            y_field,
            is_horizontal=val_ch == "x",
            is_stacked=False,
            background=background,
            is_house=layer.label_is_house,
            label_is_text=labels_draw_text(bar_labels, rows),
            # An overlay bar layer never stacks (is_stacked=False above), so
            # the segment-midpoint path these feed is unreachable from here.
            category_field=None,
            stack_offset=None,
            stack_sort=None,
        )
    elif isinstance(layer, ResolvedLineLayer):
        # marks.point.labels is an alias for marks.line.labels — same fallback
        # ValueLabelFeature._apply_line uses for the base chart.
        line_labels, point_labels_slot = _layer_label_slots(layer)
        point_labels = line_labels if line_labels.visible is True else point_labels_slot
        if point_labels.visible is not True:
            return []
        return [
            text_layer_spec(built)
            for built in build_line_text_layers(
                point_labels,
                y_field,
                layer.label_is_house,
                labels_draw_text(point_labels, rows),
                band,
                measure_channel=val_ch,
            )
        ]
    elif isinstance(layer, ResolvedAreaLayer):
        (area_line_labels,) = _layer_label_slots(layer)
        if area_line_labels.visible is not True:
            return []
        return [
            text_layer_spec(built)
            for built in build_line_text_layers(
                area_line_labels,
                y_field,
                layer.label_is_house,
                labels_draw_text(area_line_labels, rows),
                band,
                measure_channel=val_ch,
            )
        ]
    else:  # ResolvedScatterLayer
        (point_labels,) = _layer_label_slots(layer)
        if point_labels.visible is not True:
            return []
        text_layer = _build_point_text_layer(
            point_labels,
            y_field,
            is_house=layer.label_is_house,
            label_is_text=labels_draw_text(point_labels, rows),
            measure_channel=val_ch,
        )

    return [text_layer_spec(text_layer)]


# A bar mark's rounded corner is Vega's own clip-path anchored to the bar's
# LOCAL (unclipped) geometry (see bar_corner_props's docstring in
# vl_field_maps.py) — `autosize: fit`'s bounds computation follows that local
# geometry rather than the outer plot clip, so a clipped-but-rounded bar
# still blows the plot out to fit its full unclipped extent. Square corners
# don't have this local clip-path, so they clip cleanly like every other
# mark type. Dropped only on a layer already being clipped for painting
# outside the pinned normalize domain — an in-range bar layer keeps its
# authored corner radius untouched.
_BAR_CORNER_RADIUS_PROPS: tuple[str, ...] = (
    "cornerRadiusEnd",
    "cornerRadiusTopLeft",
    "cornerRadiusBottomLeft",
    "cornerRadiusBottomRight",
)


def _paints_outside_domain(
    value: object,  # type-state: object_annotation — duck-types an arbitrary row value through float()
    domain: tuple[float, float],
) -> bool:
    """True iff Vega-Lite's own quantitative-encoding parse would paint
    ``value`` (VL parses anything JS's ``Number()`` accepts, including a
    numeric string, and drops non-finite/non-numeric values) AND that value
    lands outside ``domain`` — the pinned ``[0, 1]`` normalize domain, or an
    author's own wider/narrower pin (``authored_measure_domain``): a value
    inside an authored ``[0, 1.2]`` must not be clipped just because it
    exceeds the un-authored default.

    Mirrors VL's own validity/coercion rule with ``float()`` rather than
    checking Python types directly — VL parses a numeric *string* (a
    ``::text``-cast or CSV-sourced column) the same as a real number, so a
    type check alone under-clips it and the plot still collapses; a
    ``Decimal("NaN")`` passes an ``isinstance`` check but raises on ``<=``,
    while ``float("nan")`` correctly sorts as not-finite instead. All three
    ``except`` members are load-bearing: ``ArithmeticError`` also catches
    ``OverflowError`` from a Python ``int`` too large to represent as a
    ``float`` — reachable via an authored inline ``values:`` query, which
    passes rows through unchanged and so preserves arbitrary-precision
    YAML integers all the way to this call.
    """
    try:
        f = float(value)  # type: ignore[arg-type]  # type-state: type_ignore — duck-typed row value; the except below is the real type guard
    except (TypeError, ValueError, ArithmeticError):
        return False
    lo, hi = domain
    return math.isfinite(f) and not lo <= f <= hi


def _clip_layer_marks(spec: ChartSpec) -> None:
    """Set ``clip: true`` on every mark ``spec`` paints, recursing into
    ``layers`` (a line/area layer's halo/fg/hover sub-specs).

    Used only for a layer sharing a normalize base's pinned domain — ``[0, 1]``,
    or the author's own wider/narrower pin — see ``render_cartesian_overlay``'s
    ``base_stack_normalize`` docstring for why. A plain (non-wrapper) spec paints one mark; a
    wrapper (``mark == "layered"``, e.g. a line/area layer's own halo + fg +
    hover sub-layers) paints through ``layers`` instead and has nothing of
    its own to clip.
    """
    if spec.layers:
        for sub in spec.layers:
            _clip_layer_marks(sub)
    else:
        spec.mark_props = {
            **{
                k: v
                for k, v in spec.mark_props.items()
                if k not in _BAR_CORNER_RADIUS_PROPS
            },
            **CLIP_TO_PLOT,
        }


def _resolved_layer_y_orients(layers: tuple[ResolvedLayer, ...]) -> list[str] | None:
    """Per-layer resolved y-axis orient for typed overlay layers.

    A layer pins its side via ``axis_y.position``.  Returns ``None`` when no
    layer pins a side.
    """
    positions = [layer.axis_y.position for layer in layers]
    pinned = {p for p in positions if p in ("left", "right")}
    if not pinned:
        return None
    fill: str = (
        "left" if pinned == {"right"} else "right" if pinned == {"left"} else "left"
    )
    return [(p if p in ("left", "right") else fill) for p in positions]


def _mixed_mark_legend_symbols(
    vl_layers: list[ChartSpec],
    stroke_datums: set[str],
    circle_datums: set[str],
    square_datums: set[str],
    area_opacity: float,
    base_symbol: str,
    shared_color_scale: VLDict | None,
) -> None:
    """Patch symbolType/Size/StrokeWidth/Opacity exprs for mixed-mark legends.

    Gives each legend entry the glyph of its source mark — stroke for line,
    circle for area, square for bar.  Base-chart entries fall through to
    ``base_symbol`` (the glyph matching the base mark type: 'square' for bar,
    'stroke' for line, 'circle' for area/scatter).  Only patches layers whose
    color encoding already carries a non-None legend dict.
    """

    def _pred(datums: set[str]) -> str:
        return f"indexof({_json.dumps(sorted(datums))}, toString(datum.value)) >= 0"

    # Build type expression as a chain of ternaries; base_symbol is the fallback.
    type_expr = f"'{base_symbol}'"
    if square_datums:
        type_expr = f"{_pred(square_datums)} ? 'square' : {type_expr}"
    if circle_datums:
        type_expr = f"{_pred(circle_datums)} ? 'circle' : {type_expr}"
    if stroke_datums:
        type_expr = f"{_pred(stroke_datums)} ? 'stroke' : {type_expr}"

    symbol_props: dict[str, Any] = {"symbolType": {"expr": type_expr}}
    # A 'stroke' glyph needs more length/weight than circle or square to read
    # as a line — but that is a property of the RESOLVED shape, not of which
    # layer (base or overlay) contributed the entry. Re-testing the same
    # `type_expr` this glyph's own symbolType came from (rather than the
    # narrower stroke_datums membership) keeps every 'stroke' row — base
    # color-split series included — on the one size/weight spec.
    #
    # Gated on stroke_datums, NOT on `base_symbol == "stroke"`: a plain line
    # base with an area or bar overlay is a MIXED-shape legend, where the two
    # entries are meant to look different. Widening the gate to the base type
    # would resize that base's own glyph with nothing to converge it with, and
    # would also stomp the symbolSize a dashed line's legend sets to keep a
    # full dash cycle legible (line.py) — a short swatch clips it to a stub.
    if stroke_datums:
        symbol_props["symbolStrokeWidth"] = {
            "expr": f"({type_expr}) === 'stroke' ? 2 : 1.5"
        }
        symbol_props["symbolSize"] = {"expr": f"({type_expr}) === 'stroke' ? 400 : 100"}
    if circle_datums:
        area_pred = _pred(circle_datums)
        symbol_props["symbolOpacity"] = {
            "expr": f"{area_pred} ? {_json.dumps(area_opacity)} : 1"
        }

    for layer in vl_layers:
        # A dual-axis entry may be `nest_zero_rule`-wrapped in an empty
        # `mark="layered"` ChartSpec with no encoding of its own. Unwrap to
        # the entry that actually carries the color encoding this legend
        # patch targets.
        color_enc = layer_encoding_owner(layer).encoding.get("color")
        if isinstance(color_enc, dict) and isinstance(color_enc.get("legend"), dict):
            color_enc["legend"].update(symbol_props)
            if (
                stroke_datums
                and shared_color_scale is not None
                and color_enc.get("scale") is shared_color_scale
            ):
                color_enc["legend"]["symbolStrokeColor"] = {
                    "expr": "scale('color', datum.value)"
                }


def _layer_series_color(layer: ResolvedLayer) -> str | None:
    """Authored constant series color for a layer, or None to use a palette slot.

    Only line (stroke color) and scatter (point color) carry an authored series
    color; area/bar fill always comes from the palette.
    """
    if isinstance(layer, ResolvedLineLayer):
        return layer.line_mark.stroke.color
    if isinstance(layer, ResolvedScatterLayer):
        return layer.point_mark.color
    return None


def _layer_tooltip_description(
    x_enc: VLDict,
    rows: list[_Row],
    y_field: str,
    label: str,
    tooltip_format: str,
    color_field: str | None,
    span_start: str | None,
) -> tuple[str, list[VLDict]]:
    """Build this overlay layer's own structured-tooltip description expr, and
    the transforms it reads.

    A combo overlay layer joins the SAME x-unified
    bubble ``chart_interactivity.js``'s ``collectMatchingMarks`` already
    builds for the base chart's own marks (stacked segments, etc.) — no JS
    change needed, only role-marked ``description`` content on this layer's
    own mark(s), mirroring what the base already gets from
    ``StructuredTooltipFeature``.

    Header: the layer's own x field (its own authored ``x``, or the base
    chart's x encoding when unset — resolved by the caller), using the SAME
    friendly-date detection the base's own header uses
    (``header_tooltip_field``) — the rendered string must match the base's
    header EXACTLY, since the JS groups marks by header-string equality.
    Absent entirely when the base has no x at all (every x-encoding builder
    in this module always pairs "field" with "type", so a bare `"field" in
    x_enc` check is sufficient to know both keys are present) -- the header's
    own ``title`` is never rendered (bare-value header rows drop the field
    label), so an absent x carries no title to look up either.

    Series: when this layer's own ``color:`` field distinguishes rows
    (cardinality > 1, ``series_row_promoted``), the identity is read
    per-datum off that field — mirroring how the base chart's own
    structured tooltip promotes a bound color channel
    (``features/structured_tooltip.py``) — so each mark in a multi-series
    overlay layer carries its own distinct series value. The per-datum value
    is prefixed with the layer's own label (``TooltipField.prefix``):
    without it, an overlay layer sharing the base's OWN ``color:`` field
    would render the identical bare value the base's own promoted series row
    already emits, colliding on ``collectMatchingMarks``'s dedup key and
    dropping the base's or the overlay's row for that category. Otherwise
    (no promoted color) this falls back to the layer's own label
    (``literal=True`` — a Python-side label cascade result, not a query
    column) as a bare, swatched identity row — what keeps a single-series
    base (no series row of its own) from colliding with this overlay.

    No total: an overlay reference (a different unit/series than the base's
    own commensurable parts) is never folded into the base's own group-total
    — that footer is built solely from the base's own
    ``StructuredTooltipFeature`` role computation.

    A bar layer with ``y_start`` (``span_start``) is named for its two ends
    (``open to close``) and lists both plus their difference, as the base bar
    does. (A base bar with y_start replaces every same-rows layer's
    description with one shared block; see ``StructuredTooltipFeature``.)
    """
    header: tuple[TooltipField, ...] = ()
    if "field" in x_enc:
        header = (header_tooltip_field(x_enc["field"], "", rows),)
    end = TooltipField(y_field, label, kind="quantitative", format=tooltip_format)
    values = [end]
    transforms: list[VLDict] = []
    name = label
    if span_start is not None:
        start = TooltipField(
            span_start,
            default_axis_title(span_start),
            kind="quantitative",
            format=tooltip_format,
        )
        values, transforms = span_tooltip_rows(end, start, frozenset(), rows)
        name = f"{start.title} to {label}"
    if color_field is not None and series_row_promoted(color_field, rows):
        series = (
            TooltipField(color_field, color_field, kind="nominal", prefix=f"{name}: "),
        )
    else:
        series = (TooltipField(name, name, literal=True),)
    description = build_structured_tooltip_expr("line", header, series, values)
    return description, transforms


def _fix_bar_band_width(spec: ChartSpec, x_is_banded: bool) -> None:
    """Recursively pin any bar mark's width to its categorical bandwidth.

    Undoes VL's degraded-shorthand quirk (see the ``step_band_present`` call
    site in ``render_cartesian_overlay``) for every bar mark reachable from
    ``spec`` — the base chart, an overlay layer, or a mixed-sign bar's nested
    positive/negative split — however deep the layered wrapping goes. Grouped
    bars size against their ``xOffset`` sub-band; other bars use the outer
    ``x`` band.

    ``x_is_banded`` is False for a continuous (quantitative, or
    max_ordinal_buckets-promoted temporal) base x. Leave such a bar's width
    entirely alone: there is no band for ``bandwidth(...)`` to measure, so
    rewriting the width is what produced the zero-width-bar defect — VL
    evaluates ``bandwidth('xOffset')`` to 0 against a scale that has no bands.

    Measured, rather than assumed: the ``{"band": f}`` shorthand does NOT
    degrade on a continuous x the way it does on a band scale (the case the
    call site below describes). A grouped continuous-x bar with a sibling
    xOffset renders at exactly the width the same chart renders at with no
    layers at all, whether or not ``bar.size`` is set. Pinning the width to
    ``continuousBandSize`` instead would be wrong twice over: that key is
    inert while the band shorthand is present (a bar with ``size: 20`` still
    renders at the shorthand's width), and it is absent entirely when
    chart-local ``style.marks.bar.size`` is null — ``BarChartStyle.marks``
    carries an ``InheritSlot``, and only the board-tier merge runs before
    ``apply_inherit`` refills it, so the chart-local tier can genuinely
    clear it.

    A literal numeric ``width`` (an authored ``bar.size``, set by
    ``bar_mark_to_vl``) is left alone even when ``x_is_banded`` — only the
    ``{"band": f}`` shorthand needs the ``bandwidth(...)`` rewrite. Rewriting
    a literal pixel width to a bare ``bandwidth(...)`` expression here would
    discard both the authored width and (since the fraction has nothing to
    multiply against) the theme's band gutter, contradicting "authored beats
    computed, always" on the one path this fix exists to protect.
    """
    if spec.mark == "bar":
        if not x_is_banded:
            return
        width_prop = spec.mark_props.get("width")
        # A literal pixel width is an authored bar.size — leave it alone, or
        # the rewrite below would replace it with the full band and discard
        # both the author's width and the theme's gutter. An absent width
        # still needs the explicit bandwidth() the degraded shorthand loses.
        if width_prop is not None and not isinstance(width_prop, dict):
            return
        band_frac = width_prop.get("band") if width_prop is not None else None
        bandwidth_scale = "xOffset" if "xOffset" in spec.encoding else "x"
        bandwidth = f"bandwidth('{bandwidth_scale}')"
        width_expr = (
            f"{band_frac} * {bandwidth}" if band_frac is not None else bandwidth
        )
        spec.mark_props["width"] = {"expr": width_expr}
    elif spec.mark == "layered":
        for sub in spec.layers:
            _fix_bar_band_width(sub, x_is_banded)


def _apply_layer_step_band(
    curve: str | None,
    connect: bool,
    layer_encoding: VLDict,
    base_x: Any,
    data: list[dict[str, Any]],
    chart_id: str,
    cat_ch: str,
) -> list[dict[str, Any]] | None:
    """Apply the band-aware step transform to an overlay line/area layer,
    mirroring the base emitters. Doubles rows to the band edges and adds
    ``xOffset`` (+ a ``detail`` channel when ``connect`` is False) to
    ``layer_encoding``; returns the doubled rows to attach as the layer's own
    data, or None when not band-aware step. When the layer authored its own
    ``x``, ``layer_encoding[cat_ch]`` is already set to that field's own
    encoding and is left alone; otherwise it inherits the base spec's category
    encoding, copied in here — ``apply_step_band`` needs the band channel
    present to validate and offset against.

    ``cat_ch`` is the VL channel the base actually draws its category on. The
    offset this builds is spelled ``xOffset`` and sized by ``bandwidth('x')``
    all the way down into ``apply_step_band``, so the transform only exists
    for a vertical base; a horizontal one raises rather than offsetting the
    layer along an axis it does not band. That raise is also what keeps
    ``step_band_present`` — and so ``_fix_bar_band_width``, equally x-only —
    unreachable on a horizontal base.
    """
    # Not a `.get(k, default)` config fallback (SIM401) — deliberately written
    # as an if/else so the type-state counter's silent_fallback detector
    # (scripts/type_state_counter.py), which flags exactly that call shape,
    # doesn't mistake this real either/or for one.
    x_enc = layer_encoding[cat_ch] if cat_ch in layer_encoding else base_x  # noqa: SIM401
    if not isinstance(x_enc, dict):
        return None
    if not is_band_step(curve, x_enc.get("type")):
        return None
    if cat_ch != "x":
        raise ChartDataError.from_code(ERR_LAYER_STEP_ORIENTATION, chart_id=chart_id)
    layer_encoding["x"] = x_enc
    return apply_step_band(data, layer_encoding, chart_id=chart_id, connect=connect)


def _layer_band_anchor(
    label_x_field: str,
    y_field: str,
    base_x_enc: VLDict,
    base_rows: list[_Row],
    layer_rows: list[_Row],
    layer_x_columns: list[tuple[str, list[_Row]]],
) -> BandLabelAnchor:
    """Band geometry for one overlay layer's own value labels.

    Called only when the layer's curve actually applied the band transform.
    The domain is read over EVERY layer's contribution — the same union-and-sort
    ``_reconcile_x_domain`` pins below — so an authored ``sort:`` moves the
    leading/trailing band here exactly as it moves it on the rendered axis, and
    a sibling layer whose unorderable value tips the union into paint order
    moves it here too. Reading this layer's own column alone would caption the
    band the axis draws last as if it were drawn first, painting the caption
    outside the plot; reading query order would do the same.

    ``label_x_field`` is the base's x column, not the layer's — the label
    sublayer carries no x encoding of its own, so the base's is the column its
    caption is actually positioned by, and therefore the one the edge filter
    has to test. It is narrowed at the call site rather than here: absorbing the
    narrow would make a signature member optional, which costs more against the
    type-state gate than the duplicated guard costs in lines.
    """
    return band_label_anchor(
        label_x_field,
        y_field,
        rendered_x_domain(base_x_enc, base_rows, layer_x_columns, chart_id=None),
        layer_rows,
        rows_are_doubled=False,
    )


def render_cartesian_overlay(
    base_spec: ChartSpec,
    layers: tuple[ResolvedLayer, ...],
    data: list[dict[str, Any]],
    *,
    chart_id: str,
    axis_x: ResolvedAxisStyle,
    axis_y: ResolvedAxisStyle,
    base_measure_title_suppressed: bool,
    base_orientation: Literal["vertical", "horizontal"],
    base_x_authored_temporal: bool,
    tooltip_format: str,
    background: str,
    single_series_fill: str,
    legend: ResolvedLegendStyle,
    config: VLDict,
    layered_rail_may_fire: bool,
    base_stack_normalize: bool,
    base_stack_center: bool,
    multiples_scale_independent: bool,
    base_mark_type: str = "bar",
    base_label: str | None = None,
    datasets: dict[str | None, list[dict[str, Any]]] | None = None,
    base_query_name: str | None,
) -> ChartSpec:
    """Wrap ``base_spec`` + typed overlay layers into an outer layered VL spec.

    Called from each family emitter when ``chart.layers`` is non-empty.
    Paint order contract: base first (bottom), layers in authored order on top.
    Dual-axis, mixed-mark legend symbols, and per-layer y encodings are applied.
    The ``config`` (palette) is moved to the outer spec so it is not duplicated.
    ``datasets`` maps query_name → rows for layers that authored their own
    ``query:`` (overriding the base chart's query). ``base_query_name`` is
    the base chart's own resolved query name (``chart.query_name``) — every
    layer's resolved ``query_name`` defaults to it when unauthored
    (``compile/resolve/_layers.py``), so ``layer.query_name is not None`` is
    true for nearly every layer and cannot distinguish "reads its own rows"
    from "shares the base's". A layer's rows genuinely diverge from the
    base's only when ``layer.query_name != base_query_name`` — that layer
    then reads from ``datasets``; every other layer shares the base's own
    ``data`` (already gap-filled/bucket-normalized by the family emitter),
    never the raw ``datasets`` lookup, which predates that normalization.
    ``axis_x`` is the BASE chart's x axis style, passed through to
    ``canonicalize_cartesian_x_data`` so a layer that genuinely overrides
    the query (``own_data``) canonicalizes its x rows the same way the base
    did — both land on one shared x scale, so leaving ``own_data`` on a
    different JS-``Date``-parseable string form than the base (e.g. a naive
    "2024-01-01 00:00:00" next to the base's date-only "2024-01-01") paints
    the overlay a bucket off the base under any non-UTC timezone. The base's
    own verdict is threaded, not re-derived: ``base_x_authored_temporal`` is
    the caller's own ``resolve_authored_x_type(axis_x) == "temporal"`` check
    for the families whose base path actually honors that escape hatch
    (bar/line/area's ``gap_fill_ordinal_time``, gated in ``_channels.py``) —
    scatter's own base canonicalizes unconditionally regardless of authored
    type, so a scatter caller always passes ``False``. Re-deriving this
    check locally, against the wrong family's gate, produced the exact
    string-form split this parameter closes: recomputing "should this axis
    canonicalize" from ``axis_x`` alone (ignoring which family's base path
    actually consulted it) fired canonicalization on an authored-temporal
    bar/line/area axis whose own base path had just skipped it. A layer
    sharing the base's OWN x field additionally gets the base's authored
    ``time_unit``; a layer authoring a genuinely different ``x`` gets
    ``None`` (auto-detects its own grain) — the base's authored grain was
    chosen for the base's field, not an unrelated column, and forcing it
    onto one silently collapses that column's own bucketing (see the
    ``own_data`` call site below).
    ``layered_rail_may_fire`` is the caller's own
    ``dbt_charts.core.utils.layered_endpoint_rail_fires(...) and
    chart.style.endpoint_labels.visible`` (further ANDed with "not
    horizontal" on bar) — required, not defaulted, so a caller that forgets
    to compute it fails loudly rather than silently reverting to the widest
    (or narrowest) collision check.
    ``base_stack_normalize`` is the caller's own ``chart.stack ==
    "normalize"`` (``False`` for line/scatter, which have no stack concept).
    A normalize base pins its measure domain to ``[0, 1]`` (or the author's
    own pin) regardless of what shares the scale — see ``bar.py``'s
    ``_emit_vertical``/``_emit_horizontal`` and ``area.py``'s
    ``_build_area_top_encoding``/``_emit_multi_metric_area``. A shared-scale layer (no
    ``axis_y.position``: the only case that actually paints against that
    pinned domain, since a dual-axis layer gets its own independent scale)
    can carry raw values well outside it — the common invisible
    padding/reference-layer case. Vega's ``autosize: fit`` sizes the whole
    plot to fit every mark's paint, so an unclipped out-of-range layer
    collapses the plot to zero height instead of just spilling past the
    axis. Every mark this function builds for such a layer gets
    ``clip: true`` (the same idiom ``CLIP_TO_PLOT`` in ``emitters/_layers.py``
    already uses for the identical ``autosize`` risk on invisible
    halo/hit-target marks) — clipping the marks, not the shared view, keeps
    the axis/legend/category-label scaffold laid out normally; only this
    layer's own out-of-range paint disappears at the plot edge.
    ``base_stack_center`` is the caller's own ``chart.stack == "center"``
    (``False`` for bar/line/scatter: a bar's own ``stack: "center"`` is a
    diverging stack, where 0 is still the meaningful anchor, not this
    area-only streamgraph case). Mirrors
    ``BaselineFeature._apply_zero_or_top``'s own streamgraph carve-out ("y=0
    is the visual centerline of the silhouette, not a meaningful baseline")
    for the base's own dual-axis rule.
    ``multiples_scale_independent`` is the caller's own
    ``multiples_scale_independent(chart)`` (``emitters/_cartesian.py``'s
    shared predicate, also used by ``BaselineFeature``). Mirrors
    ``BaselineFeature.apply()``'s own independent-small-multiples guard: the
    base/layer zero-rule verdicts below are each decided once from the
    pooled union of every panel's rows, but under independent scale each
    panel gets its own y-domain, so a single chart-wide verdict can be wrong
    for any one panel. Dual-axis rule insertion skips entirely under this
    flag, same as ``BaselineFeature`` does for its own shared-scale rule.
    ``base_measure_title_suppressed`` says the author explicitly suppressed
    the title of the base's MEASURE axis — required, not defaulted, for the
    same reason. Only the dual-axis title restore below reads it, and only to
    split two cases the resolved axis alone cannot tell apart: an axis with no
    label is suppressed by the theme's blanket default, and dual-axis overrides
    that (both sides need labeling to tell the scales apart), whereas a
    labeled axis can only be suppressed by the author saying so — the Layer 5
    default would otherwise have forced it visible — so that one is honored and
    left alone. The caller computes it rather than this function deriving it
    from ``axis_y``, because only the caller knows whether its own family
    honored the author's suppression.

    ``base_orientation`` is the base chart's own orientation, and it decides
    which VL channel each of the resolved model's two axes lands on. The
    resolved model always carries its category on ``x`` and measures on ``y``
    whatever the visual orientation (see ``mark_extents.py``); a horizontal
    bar is that same model painted with the pair swapped. Everything below is
    written in the model's vocabulary — ``x`` means the category — and reaches
    the spec through ``cat_ch`` / ``val_ch``. Reading the pair as the literals
    ``("x", "y")`` is what shared a horizontal base's *measure* with every
    layer and bound each layer's own field to a second, unmerged scale drawn
    down the category gutter.
    """
    # The resolved model's (x, y) as VL channels. Only these two keys move.
    cat_ch, val_ch = ("y", "x") if base_orientation == "horizontal" else ("x", "y")
    # Deliberately no compose_axis_label_expr here: axis_y is the BASE
    # chart's measure axis, and its ruler (if any) was baked for the base's
    # own tick ladder. In the shared-scale case the base's axis dict already
    # carries that labelExpr (vl_layers[0]), so this dict doesn't need its
    # own copy; in the dual-axis case a layer's y-axis is a different scale
    # entirely, and applying the base's ruler to it would paint the wrong
    # magnitude. Every emitter's OWN measure axis still routes through
    # compose_axis_label_expr (see bar.py's comment on the same convention);
    # this is the one call site that must not.
    ay_vl = axis_to_vl(axis_y)

    # #12: chart-level tick count → tickCount hint on all overlay layer axes.
    # (Base chart's axis is built by build_cartesian_y_encoding using tick_values.)
    if axis_y.ticks.count is not None:
        ay_vl["tickCount"] = axis_y.ticks.count

    # Dual-axis placement. When any overlay pins a side, the base occupies the
    # OPPOSITE side. The base emitter used the theme-default orient (which may be
    # "right"), so we must re-pin the base to the free side here — otherwise a
    # right-pinned overlay collides with a right-defaulting base. Independent y
    # scales are needed whenever base and overlays don't all share one side.
    layer_orients = _resolved_layer_y_orients(layers)
    independent_y = False
    # None until the `layer_orients is not None` branch below assigns the
    # real side; every later read of base_side is itself gated on
    # `layer_orients is not None`, so it is never read as None.
    base_side: str | None = None
    if layer_orients is not None:
        if base_orientation == "horizontal":
            # axis_y.position is left/right, but a horizontal base measures
            # along VL x, whose sides are top and bottom. There is no honest
            # place to put the pinned axis, so say so rather than draw the
            # layer against a side it was not asked for.
            raise ChartDataError.from_code(
                ERR_LAYER_AXIS_POSITION_ORIENTATION, chart_id=chart_id
            )
        pinned = set(layer_orients)
        base_side = (
            "left" if pinned == {"right"} else "right" if pinned == {"left"} else "left"
        )
        independent_y = len({base_side} | pinned) > 1
        base_y = base_spec.encoding.get(val_ch) if base_spec.encoding else None
        if isinstance(base_y, dict) and isinstance(base_y.get("axis"), dict):
            prior_orient = base_y["axis"].get("orient")
            base_y["axis"]["orient"] = base_side
            if prior_orient != base_side:
                # Strip labelAlign and labelPadding only when the base axis
                # actually moves to a different edge: a house-format alias may
                # have forced labelAlign="right" and computed a right-side
                # labelPadding — those values are wrong on the opposite side.
                # When the base stays on the same edge (e.g. a left-pinned
                # overlay leaves a right-default base on the right), they
                # remain valid and must not be stripped.
                base_y["axis"].pop("labelAlign", None)
                base_y["axis"].pop("labelPadding", None)
            # Single bar/line charts suppress the y-axis title (axis.title=null);
            # a dual-axis chart needs both sides labeled to tell the scales
            # apart, so restore the base title from its encoding title —
            # unless the author explicitly suppressed the title of the
            # axis that actually lands here (see
            # base_measure_title_suppressed above), which must not be
            # overwritten.
            if (
                base_y["axis"].get("title") is None
                and base_y.get("title")
                and not base_measure_title_suppressed
            ):
                base_y["axis"]["title"] = base_y["title"]

    # Move config from base to outer (avoids duplication in nested VL specs).
    outer_config = config or dict(base_spec.config)
    base_spec.config = {}

    # Extract the shared category encoding from the base for the outer spec.
    x_enc = base_spec.encoding.get(cat_ch) if base_spec.encoding else None
    outer_encoding: dict[str, Any] = {}
    if x_enc is not None:
        outer_encoding[cat_ch] = x_enc

    # If the base carries a stack-ordering encoding (stacked-bar nominal series),
    # hoist it and its calculate transform to the outer spec so bar and text-label
    # sublayers inherit one shared stack ordering. Without this the text sublayer
    # computes its own independent stack and mismatches labels with segments.
    # Overlay line/area layers explicitly opt out via order=None below (on a line,
    # 'order' controls point-connection order, not z-order).
    outer_transforms: list[dict[str, Any]] = []
    order_hoisted = False
    order_enc = base_spec.encoding.pop("order", None)
    if order_enc is not None:
        outer_encoding["order"] = order_enc
        order_field = order_enc["field"]  # always a str; KeyError signals a bug
        moved = [t for t in base_spec.transforms if t.get("as") == order_field]
        if moved:
            base_spec.transforms = [
                t for t in base_spec.transforms if t.get("as") != order_field
            ]
        elif base_spec.mark == "layered":
            # Mixed-sign bar split (_layers.py): order is on the outer encoding but
            # the calculate lives inside each sign-filtered sub-layer. Hoist from
            # the first sub-layer (all carry identical copies) and clear every sub.
            moved = [
                t for t in base_spec.layers[0].transforms if t.get("as") == order_field
            ]
            if not moved:
                raise ChartDataError(
                    f"base spec carries order encoding for field {order_field!r} "
                    "but no matching calculate transform was found"
                )
            for sub in base_spec.layers:
                sub.transforms = [
                    t for t in sub.transforms if t.get("as") != order_field
                ]
        else:
            raise ChartDataError(
                f"base spec carries order encoding for field {order_field!r} "
                "but no matching calculate transform was found"
            )
        outer_transforms.extend(moved)
        # The outer spec carries the same rows (data= on the ChartSpec returned
        # below). Clear base_spec.data so the base layer inherits outer transforms;
        # a layer with its own data skips the outer transform cascade.
        base_spec.data = None
        order_hoisted = True

    vl_layers: list[ChartSpec] = [base_spec]

    # One shared color scale drives BOTH the painted series and the legend
    # swatches, so an authored per-layer color and its legend entry can never
    # diverge, and the base series always gets a legend entry. Slot 0 is the
    # single-series base (painted with its own fill), or the base field's existing
    # domain/range; each layer contributes its authored color, else the next
    # unclaimed palette slot.
    # Both orientations share one measure scale with their layers; `val_ch` is
    # whichever channel the base measures on, so the base's own title there is
    # its series name either way. (This block used to exclude a horizontal base
    # from the shared scale entirely, because it read the pair as the literal
    # `("x", "y")` and a horizontal base's `y` is its category.)
    palette: list[str] = list((outer_config.get("range") or {}).get("category") or [])
    # Membership test rather than `.get(k, default)`: an absent measure encoding
    # is a real either/or (the base has none to share), not a config fallback —
    # same shape `_apply_layer_step_band` uses above, and for the same reason.
    y_enc_base = (
        base_spec.encoding[val_ch]
        if base_spec.encoding and val_ch in base_spec.encoding
        else {}
    )
    # Dual-axis: give the base its own zero-baseline rule too, nested inside
    # its own vl_layers[0] entry (see `nest_zero_rule`) rather than left as a
    # naive top-level sibling, which would manufacture a degenerate [0, 0]
    # scale under `resolve.scale.y: independent`. A normalize-stacked base
    # wants 0%/100% top rules, not a plain zero rule (mirrors
    # BaselineFeature._apply_zero_or_top's own normalize branch); drawing
    # those under an independent dual-axis scale isn't handled here, so the
    # base gets neither rather than the wrong reference line. A streamgraph
    # base (`base_stack_center`) gets no rule either: y=0 is only the
    # silhouette's visual centerline there, not a meaningful baseline (same
    # carve-out). Skipped on an empty dataset (mirrors
    # `_apply_zero_or_top`'s own `chart_rows(...).all_rows()` guard) and
    # entirely under `multiples_scale_independent`; see this function's own
    # docstring on that flag. `axis_y.is_quantitative` mirrors
    # `_y_carries_the_measure` (baseline.py): a rotated scatter base (value
    # on x, category on y -- the dot-plot recipe) has no quantitative y for a
    # `datum: 0` rule to bind to.
    if (
        independent_y
        and not multiples_scale_independent
        and (base_mark_type != "scatter" or axis_y.is_quantitative)
        and not base_stack_normalize
        and not base_stack_center
        and data
        # grid.threshold.visible governs the per-scale dual-axis rules the same
        # way it governs the shared-scale one BaselineFeature emits — the off
        # switch is a property of the axis, not of which path builds the rule.
        and axis_y.grid.threshold.visible
    ):
        base_field = y_enc_base.get("field")
        if isinstance(base_field, str):
            base_continuous = (
                axis_y.scale.continuous if axis_y.scale is not None else None
            )
            base_zero_style = axis_y.grid.threshold
            base_zero_setting = (
                base_continuous.zero if base_continuous is not None else None
            )
            base_authored_domain = authored_measure_domain(axis_y)
            # Bar always fires. Line/area/scatter mirror BaselineFeature's
            # own real rule (fires unless the axis explicitly turned
            # zero-anchoring off) via non_bar_zero_rule_should_fire, not the
            # layer-only "no zero field, so line/scatter needs an explicit
            # `zero: true`" default below, which is the wrong default for a
            # base chart's own axis.
            base_should_fire = (
                True
                if base_mark_type == "bar"
                else non_bar_zero_rule_should_fire(
                    base_mark_type == "scatter",
                    base_zero_setting,
                    zero_anchored=axis_y.zero_anchored,
                    authored_domain=base_authored_domain,
                    zero_in_domain=lambda: values_straddle_zero(data, base_field),
                )
            )
            base_rule = build_zero_rule_if_applicable(
                base_field,
                val_ch,
                log_scale=base_continuous is not None and base_continuous.type == "log",
                authored_domain=base_authored_domain,
                domain_min=axis_y.domain_min,
                domain_max=axis_y.domain_max,
                grid_visible=axis_y.grid.visible,
                zero_color=base_zero_style.color,
                zero_width=base_zero_style.width,
                should_fire=base_should_fire,
            )
            if base_rule is not None:
                vl_layers[0] = nest_zero_rule(vl_layers[0], base_rule, val_ch)
    base_color_enc = base_spec.encoding.get("color") if base_spec.encoding else None
    field_color_base = (
        isinstance(base_color_enc, dict)
        and isinstance(base_color_enc.get("field"), str)
        and base_color_enc.get("type") in ("nominal", "ordinal")
    )
    use_shared_scale = (
        bool(base_spec.encoding)
        and ("color" not in base_spec.encoding or field_color_base)
        and y_enc_base.get("type") == "quantitative"
    )
    # The caller supplies the base series' legend label as plain text. Reading
    # it off ``y_enc_base["title"]`` would pick up the display title, which is
    # a ``list[str]`` once wrapped — not a value VL accepts in ``color.datum``
    # or a scale domain.
    base_label = base_label if use_shared_scale and not field_color_base else None
    scale_domain: list[str] = []
    scale_range: list[str] = []
    shared_scale: VLDict = {"domain": scale_domain, "range": scale_range}
    # Legend dicts for the synthetic `datum:`-bound shared-label scale (base +
    # authored layer `label:`s, never a real field) -- collected for a single
    # aria-label patch decision below, once scale_domain is fully known.
    label_scale_legend_encs: list[VLDict] = []
    # label -> {label, y_field, default_axis_title(y_field)}, one entry for
    # the base (added just below, when it contributes a LABEL) and one per
    # layer that does the same -- the alias map an authored `legend.values`
    # needs to resolve a y-column name against its rendered label, same as
    # an overlay layer's endpoint-rail name derivation. Consumed once,
    # after the loop, by whichever of the base_label/field_color_base
    # legend-order writes fires.
    label_domain_aliases: dict[str, frozenset[str]] = {}
    if field_color_base:
        assert isinstance(base_color_enc, dict)
        color_field = base_color_enc["field"]
        assert isinstance(color_field, str)
        base_series = distinct_series_values(data, color_field)
        # Every unauthored layer below draws its fill from `palette`
        # regardless of which branch supplies the base's own range, so an
        # empty palette is fatal here unconditionally.
        if not palette:
            raise ChartDataError(
                "layered chart has no color palette", chart_id=chart_id
            )
        existing_scale = base_color_enc.get("scale")
        existing_domain = (
            existing_scale.get("domain") if isinstance(existing_scale, dict) else None
        )
        existing_range = (
            existing_scale.get("range") if isinstance(existing_scale, dict) else None
        )
        scale_domain.extend(
            existing_domain if isinstance(existing_domain, list) else base_series
        )
        if isinstance(existing_range, list):
            scale_range.extend(existing_range)
        elif scale_domain:
            scale_range.extend(
                spatial_color_scale(base_series, tuple(palette), scale_domain)["range"]
            )
        base_color_enc["scale"] = shared_scale
    elif base_label is not None:
        scale_domain.append(base_label)
        scale_range.append(single_series_fill)
        base_y_field = y_enc_base.get("field")
        label_domain_aliases[base_label] = (
            layer_label_and_aliases(base_label, base_y_field)[1]
            if isinstance(base_y_field, str)
            else frozenset({base_label})
        )

    stroke_datums: set[str] = set()  # line overlays
    circle_datums: set[str] = set()  # area overlays
    square_datums: set[str] = set()  # bar overlays
    field_color_legend_symbols: list[tuple[ChartSpec, str]] = []
    independent_color_scale = False
    has_authored_line_color = False
    last_area_opacity: float = 1.0
    # Resolved once, up front, so every layer's own x is validated/typed
    # against the base BEFORE that layer's encoding is built (not classified
    # standalone and repaired after the loop) — see _resolve_layer_x_encoding.
    base_x_enc: VLDict = x_enc if isinstance(x_enc, dict) else {}
    base_x_field = base_x_enc.get("field")
    # A full pass over base_data to check date-shapedness is only ever
    # consulted when a layer authors its own x (_resolve_layer_x_encoding's
    # temporal-vs-date-shaped-base branch) -- skip it for the common case of
    # an overlay chart where no layer does.
    any_layer_has_own_x = any(layer.x is not None for layer in layers)
    base_domain_is_date_shaped = (
        any_layer_has_own_x
        and isinstance(base_x_field, str)
        and _base_domain_is_date_shaped(data, base_x_field)
    )
    # Each layer's own (x field, rows), resolved before the loop below rather
    # than inside it: the band anchors the loop builds caption the leading and
    # trailing band of the axis ``_reconcile_x_domain`` pins after it, and that
    # axis is a function of every layer's contribution. Resolving one layer at a
    # time would let a layer whose own values are orderable disagree with an
    # axis a sibling's unorderable value tipped into paint order.
    resolved_layer_rows = [
        _resolve_layer_rows(
            layer,
            base_x_field,
            axis_x,
            base_x_authored_temporal,
            datasets,
            base_query_name,
        )
        for layer in layers
    ]
    # Feed the union-domain reconciliation whenever a layer's rows can genuinely
    # differ from the base's — an authored own `x` field, or a genuinely
    # diverging own `query:` sharing the base's x field name. A non-diverging,
    # x-unauthored layer shares the base's own rows exactly, so it contributes
    # nothing new to the union and is correctly left out.
    layer_x_columns: list[tuple[str, list[_Row]]] = [
        (field, own_rows if own_rows is not None else data)
        for layer, (field, own_rows) in zip(layers, resolved_layer_rows, strict=True)
        if layer.y is not None
        and (layer.x is not None or layer.query_name != base_query_name)
        and field is not None
    ]
    # Any sibling in the outer `layer:` array added an xOffset scale — this
    # base spec's own curve counts too (a base area/line applying step-band to
    # itself puts xOffset on base_spec.encoding, and a bar OVERLAY layer is
    # then the sibling whose band-width shorthand degrades).
    step_band_present = "xOffset" in base_spec.encoding

    for layer_idx, layer in enumerate(layers):
        y_field = layer.y
        if y_field is None:
            # No y field on this layer — skip.
            continue

        # Engine-derived layer name: same rule as the base's, which comes
        # from titles.y_plain. Both land in one color.scale.domain and one
        # endpoint rail, so they must share a convention — an authored
        # `label:` is the only thing that opts out. layer_legend_aliases
        # is the pure config-to-config alias set (label, raw y-column,
        # default title) an authored `legend.values` may name -- shared
        # with the render-warnings detector via layer_label_and_aliases.
        label, layer_legend_aliases = layer_label_and_aliases(layer.label, y_field)

        own_data = resolved_layer_rows[layer_idx][1]
        rows_for_layer = own_data if own_data is not None else data

        # The layer's own x encoding when authored, else the base's (or {}
        # when the base has no x at all) — tooltip must reflect whichever x
        # field the layer actually renders against. An authored layer.x is
        # resolved against the base's type NOW (construction time), so every
        # downstream consumer in this loop (step-band curve detection below,
        # tooltips) sees the final type, never a standalone classification
        # that gets patched after the fact.
        layer_x_enc: VLDict = (
            _resolve_layer_x_encoding(
                layer.x,
                rows_for_layer,
                base_x_enc,
                base_domain_is_date_shaped,
                chart_id,
            )
            if layer.x is not None
            else base_x_enc
        )
        # Clip only when this layer actually needs it: it shares the base's
        # pinned normalize domain — [0, 1], or the author's own wider/
        # narrower pin (no axis_y.position of its own — layer_orients is
        # None, see base_stack_normalize's docstring; a dual-axis layer
        # paints against its own independent scale and is never at risk) —
        # AND at least one of its own values genuinely falls outside that
        # domain. A well-behaved shared-scale layer (e.g. a target-share
        # line whose values are already fractions) must render exactly as
        # it would with no clip at all — clipping unconditionally for every
        # shared-scale layer cuts off an in-range value sitting right at the
        # domain edge (a label, or half a stroke width) and, for an authored
        # domain, would drop that layer's own corner radius for no reason.
        layer_shares_normalize_domain = base_stack_normalize and layer_orients is None
        if layer_shares_normalize_domain:
            # An authored domain with no numeric arms (e.g. a "flip" string on
            # scale.continuous.domain) leaves this None even on a fully-baked
            # normalize chart -- the axis itself still renders that authored
            # (non-[0,1]) domain via emit_resolved_scale_vl, so [0, 1] here is
            # only a floor for this gate, not a claim about the rendered axis.
            authored_domain = authored_measure_domain(axis_y)
            normalize_domain = (
                (min(authored_domain), max(authored_domain))
                if authored_domain is not None
                else (0.0, 1.0)
            )
            layer_shares_normalize_domain = any(
                _paints_outside_domain(row.get(y_field), normalize_domain)
                for row in rows_for_layer
            )

        # Per-layer y axis: dual-axis layers get their own orient + title.
        layer_axis_y = layer.axis_y
        if layer_orients is not None:
            # Drop labelAlign from the dual-axis layer template: ay_vl was built
            # from the base axis whose forced labelAlign (e.g. from a house-format
            # alias) is valid only for the base's own edge.  A layer re-oriented to
            # the opposite edge would inherit an invading labelAlign otherwise.
            layer_ay_vl: dict[str, Any] = {
                k: v for k, v in ay_vl.items() if k != "labelAlign"
            }
            layer_ay_vl["orient"] = layer_orients[layer_idx]
            axis_title = layer_axis_y.title or label
            layer_ay_vl["title"] = axis_title
        else:
            # Shared scale: the layer stays on the base's own edge, so the
            # base's OWN already-emitted axis dict (built by the family
            # emitter via measure_axis_to_vl) is the correct template --
            # unlike the bare `ay_vl` above (axis_to_vl only), it already
            # went through measure_axis_to_vl's own-side-invasion safety net
            # (drops labelAlign when the axis can't be safely measured --
            # an authored labelExpr, upper/lower font.case -- else keeps it
            # with a real measured labelPadding). A layer axis seeded from
            # the unsafe `ay_vl` instead can inherit an own-side labelAlign
            # (e.g. from a house-format alias's force-right) with only the
            # theme's flat labels.padding reserved, right-anchoring the
            # layer's own tick labels with no gutter -- the mark then draws
            # over them.
            base_y_enc = base_spec.encoding.get(val_ch) if base_spec.encoding else None
            base_y_axis = (
                base_y_enc.get("axis") if isinstance(base_y_enc, dict) else None
            )
            layer_ay_vl = (
                dict(base_y_axis) if isinstance(base_y_axis, dict) else dict(ay_vl)
            )
            if "tickCount" in ay_vl:
                layer_ay_vl["tickCount"] = ay_vl["tickCount"]

        # #11: apply per-layer axis_y chrome overrides.
        if layer_axis_y.ticks is not None and layer_axis_y.ticks.count is not None:
            layer_ay_vl["tickCount"] = layer_axis_y.ticks.count
        if layer_axis_y.grid is not None and layer_axis_y.grid.visible is not None:
            layer_ay_vl["grid"] = layer_axis_y.grid.visible
        # The layer's own authored number format (e.g. a percent d3-format on a
        # conversion-rate overlay), else the base chart's tooltip_format — used
        # for the VALUE (tooltip row + encoding.y.format), not the axis ticks
        # (layer_ay_vl["format"] below stays conditional: an un-authored axis
        # keeps VL's own default tick format, unrelated to the base's unit).
        layer_value_format = (
            layer_axis_y.labels.format
            if layer_axis_y.labels is not None
            and layer_axis_y.labels.format is not None
            else tooltip_format
        )
        if layer_axis_y.labels is not None and layer_axis_y.labels.format is not None:
            layer_ay_vl["format"] = layer_axis_y.labels.format

        # #11: per-layer scale.domain sets the VL y encoding scale.
        layer_y_scale: VLDict | None = None
        if layer_axis_y.scale is not None and layer_axis_y.scale.domain is not None:
            layer_y_scale = {"domain": list(layer_axis_y.scale.domain)}

        # "" (not a real description -- same empty-string-means-unset
        # convention as TooltipField.format) when the base family doesn't
        # support structured tooltips; every assignment site below only
        # stamps `.tooltip_description` when this is truthy.
        layer_tooltip_description, layer_tooltip_transforms = (
            _layer_tooltip_description(
                layer_x_enc,
                rows_for_layer,
                y_field,
                label,
                layer_value_format,
                layer.color,
                layer.y_start if isinstance(layer, ResolvedBarLayer) else None,
            )
            if base_mark_type in _STRUCTURED_TOOLTIP_BASE_FAMILIES
            else ("", [])
        )

        y_enc: VLDict = {
            "field": y_field,
            "type": "quantitative",
            "title": label,
            "axis": layer_ay_vl,
            "format": layer_value_format,
        }
        if layer_y_scale is not None:
            y_enc["scale"] = layer_y_scale
        layer_color_field = layer.color
        if layer_color_field is None:
            layer_color_type: str | None = None
            color_enc: VLDict = {"datum": label}
        else:
            layer_color_type = infer_vega_type_from_data(
                rows_for_layer, layer_color_field
            )
            color_enc = {"field": layer_color_field, "type": layer_color_type}
        has_color_encoding = layer_color_field is not None
        if (
            has_color_encoding
            and layer_color_type not in ("nominal", "ordinal")
            and use_shared_scale
        ):
            independent_color_scale = True
        # Only the base's merged legend (apply_legend_entry_order, below)
        # ever writes a resolved `values` -- this per-layer legend hasn't
        # seen the SHARED domain yet (every layer's contribution isn't
        # known until after this loop), so drop `values` here rather than
        # carry an unresolved list. Gated on use_shared_scale: when the
        # base's y isn't quantitative, no shared scale gets built at all,
        # so neither post-loop restore site below ever fires -- dropping
        # unconditionally would discard a correct authored entry with no
        # fallback. Leave legend_to_vl's own verbatim passthrough in place
        # for that shape instead.
        apply_color_legend(color_enc, legend, drop_values=use_shared_scale)
        if layer_color_field is None:
            label_scale_legend_encs.append(color_enc)
        # The series' color: authored constant if present, else the next palette
        # slot. It paints the mark (as the emitter's single-series color) AND is
        # the legend swatch (shared-scale range) — one value, so they can't
        # diverge.
        layer_color_values: list[str] = []
        layer_series_fill: str
        if layer_color_field is not None and layer_color_type in ("nominal", "ordinal"):
            layer_color_values = distinct_series_values(
                rows_for_layer, layer_color_field
            )
            if use_shared_scale:
                if not palette:
                    raise ChartDataError(
                        "layered chart has no color palette", chart_id=chart_id
                    )
                for value in layer_color_values:
                    if value in scale_domain:
                        continue
                    scale_domain.append(value)
                    scale_range.append(palette[len(scale_range) % len(palette)])
                color_enc["scale"] = shared_scale
            layer_series_fill = single_series_fill
        elif use_shared_scale:
            # Collision only matters where it's load-bearing: the layered
            # endpoint-label rail's anchors dict (_apply_layered_single_series)
            # is keyed by label, so two entries sharing one silently collapse
            # to a single anchor. field_color_base's own painted domain/range
            # has the same silent-collapse risk independent of the rail. An
            # ordinary layered chart with no rail (endpoint_labels off, or a
            # non-firing shape) authoring the same label as its base y title
            # or another layer is unremarkable — VL just reuses that color
            # scale slot — so it must not raise.
            if (field_color_base or layered_rail_may_fire) and label in scale_domain:
                raise ChartDataError(
                    f"layer label {label!r} collides with an existing color "
                    "scale entry. To fix: give this layer (or the colliding "
                    "series) a distinct label:.",
                    chart_id=chart_id,
                )
            authored_fill = _layer_series_color(layer)
            if isinstance(layer, ResolvedLineLayer) and authored_fill is not None:
                has_authored_line_color = True
            if authored_fill is None:
                layer_ord = len(
                    scale_domain
                )  # slots already taken by base + prior layers
                # Recycles past the last slot via modulo, same as the base's own
                # field-color scale (spatial_color_scale in _cartesian.py).
                authored_fill = (
                    palette[layer_ord % len(palette)] if palette else single_series_fill
                )
            layer_series_fill = authored_fill
            scale_domain.append(label)
            scale_range.append(layer_series_fill)
            color_enc["scale"] = shared_scale
            label_domain_aliases[label] = layer_legend_aliases
        else:
            layer_series_fill = single_series_fill
        layer_encoding: VLDict = {val_ch: y_enc, "color": color_enc}
        if isinstance(layer, ResolvedBarLayer) and layer.y_start is not None:
            layer_encoding[f"{val_ch}2"] = {"field": layer.y_start}
            y_enc["stack"] = None
        # Overlay layers must not inherit the outer stacked-bar ordering: on
        # line/trail/area 'order' controls point-connection order, not z-order,
        # so inheriting __df_series_order disconnects line segments.
        if order_hoisted:
            layer_encoding["order"] = None
        # A layer authoring its own `x` gets its own x encoding (field +
        # inferred VL type from its own rows) instead of silently inheriting
        # the base chart's x channel. Unset (the common case) leaves "x"
        # absent here, so the sub-layer keeps inheriting the base's x
        # encoding verbatim, unchanged from prior behavior.
        if layer.x is not None:
            layer_encoding[cat_ch] = layer_x_enc
        # Non-None only for a line/area layer whose curve actually applied the
        # band transform below — the layer's own labels then resolve against
        # the band it draws, not against the datum at its center.
        layer_band: BandLabelAnchor | None = None

        if isinstance(layer, ResolvedLineLayer):
            step_data = _apply_layer_step_band(
                layer.line_mark.curve,
                layer.line_mark.connect is not False,
                layer_encoding,
                base_spec.encoding.get(cat_ch),
                rows_for_layer,
                chart_id,
                cat_ch,
            )
            if step_data is not None and isinstance(base_x_field, str):
                layer_band = _layer_band_anchor(
                    base_x_field,
                    y_field,
                    base_x_enc,
                    data,
                    rows_for_layer,
                    layer_x_columns,
                )
            sub_layers = emit_line_layer(
                line_mark=layer.line_mark,
                point_mark=layer.point_mark,
                halo_color=background,
                single_series_color=layer_series_fill,
                has_color_encoding=has_color_encoding,
                tooltip=[],
                suppress_halo=True,
                band_step=step_data is not None,
                pin_child_colors=True,
                inherit_parent_color=use_shared_scale,
                series_encoding=color_enc if has_color_encoding else {},
            )
            wrapper = ChartSpec(
                mark="layered", encoding=layer_encoding, layers=sub_layers
            )
            if layer_tooltip_description:
                wrapper.tooltip_description = layer_tooltip_description
            if step_data is not None:
                wrapper.data = step_data
                step_band_present = True
            elif own_data is not None:
                wrapper.data = own_data
            if layer_shares_normalize_domain:
                _clip_layer_marks(wrapper)
            vl_layers.append(wrapper)
            if layer_color_values:
                stroke_datums.update(layer_color_values)
            else:
                stroke_datums.add(label)
            if has_color_encoding and not layer_color_values:
                field_color_legend_symbols.append((wrapper, "stroke"))

        elif isinstance(layer, ResolvedAreaLayer):
            # Area always renders as a continuous silhouette — connect=True.
            step_data = _apply_layer_step_band(
                layer.area_mark.curve,
                True,
                layer_encoding,
                base_spec.encoding.get(cat_ch),
                rows_for_layer,
                chart_id,
                cat_ch,
            )
            if step_data is not None and isinstance(base_x_field, str):
                layer_band = _layer_band_anchor(
                    base_x_field,
                    y_field,
                    base_x_enc,
                    data,
                    rows_for_layer,
                    layer_x_columns,
                )
            sub_layers = emit_area_layer(
                area_mark=layer.area_mark,
                line_mark=layer.line_mark,
                point_mark=layer.point_mark,
                background=background,
                single_series_fill=layer_series_fill,
                has_color_encoding=has_color_encoding,
                tooltip=[],
                is_stacked=False,
                # Overlay layers never stack (is_stacked=False below), so they
                # want no sparse-band transforms.
                band_transforms=[],
                suppress_halo=True,
                band_step=step_data is not None,
                pin_child_colors=True,
                inherit_parent_color=use_shared_scale,
                series_encoding=color_enc if has_color_encoding else {},
            )
            last_area_opacity = layer.area_mark.opacity
            wrapper = ChartSpec(
                mark="layered", encoding=layer_encoding, layers=sub_layers
            )
            if layer_tooltip_description:
                wrapper.tooltip_description = layer_tooltip_description
            if step_data is not None:
                wrapper.data = step_data
                step_band_present = True
            elif own_data is not None:
                wrapper.data = own_data
            if layer_shares_normalize_domain:
                _clip_layer_marks(wrapper)
            vl_layers.append(wrapper)
            if layer_color_values:
                circle_datums.update(layer_color_values)
            else:
                circle_datums.add(label)
            if has_color_encoding and not layer_color_values:
                field_color_legend_symbols.append((wrapper, "circle"))

        elif isinstance(layer, ResolvedBarLayer):
            bar_spec = emit_bar_layer(
                bar_mark=layer.bar_mark,
                orientation=base_orientation,
                has_color_encoding=has_color_encoding,
                single_series_color=layer_series_fill,
                radius=bar_mark_radius(layer.bar_mark),
                encoding=layer_encoding,
                data=rows_for_layer,
                measure_field=y_field,
                start_field=layer.y_start,
                config={},
                transforms=[],
                # layer_x_enc is this layer's OWN resolved x (its authored x,
                # or the base's when it doesn't author one) — see its
                # definition above for why that's already the right encoding
                # to classify, not necessarily the outer chart's.
                x_is_banded=x_encoding_is_banded(layer_x_enc),
                cat_field=layer_x_enc.get("field"),
            )
            if layer_tooltip_description:
                bar_spec.tooltip_description = layer_tooltip_description
                bar_spec.transforms = [
                    *bar_spec.transforms,
                    *layer_tooltip_transforms,
                ]
            if own_data is not None:
                bar_spec.data = own_data
            if layer_shares_normalize_domain:
                _clip_layer_marks(bar_spec)
            vl_layers.append(bar_spec)
            if layer_color_values:
                square_datums.update(layer_color_values)
            else:
                square_datums.add(label)
            if has_color_encoding and not layer_color_values:
                field_color_legend_symbols.append((bar_spec, "square"))

        else:  # ResolvedScatterLayer
            mark_props = emit_scatter_layer(
                point_mark=layer.point_mark,
                has_color_encoding=has_color_encoding,
                single_series_fill=layer_series_fill,
            )
            scatter_spec = ChartSpec(
                mark="point", mark_props=mark_props, encoding=layer_encoding
            )
            if layer_tooltip_description:
                scatter_spec.tooltip_description = layer_tooltip_description
            if own_data is not None:
                scatter_spec.data = own_data
            if layer_shares_normalize_domain:
                _clip_layer_marks(scatter_spec)
            vl_layers.append(scatter_spec)
            circle_datums.update(layer_color_values)
            if has_color_encoding and not layer_color_values:
                field_color_legend_symbols.append((scatter_spec, "circle"))

        # Dual-axis: this layer's own zero-baseline rule, nested into the
        # entry just appended (vl_layers[-1], whichever branch above built
        # it) so it binds to THIS layer's own independent scale. Gated on a
        # different side than the base (layer_orients[i] != base_side): a
        # layer resolved to the SAME side as the base takes no rule of its
        # own here and relies on the base's injection above -- whether VL's
        # independent-scale resolution truly unifies same-side entries onto
        # one scale, or merely renders them on the same visual side with
        # each on its own, is a separate mixed-pin/duplicate-axis question,
        # out of scope here. Skipped on an empty dataset for this layer,
        # mirroring the base's own `and data` guard above -- the always-fire
        # bar/area arm below short-circuits before consulting rows, so an
        # empty-rows layer must be excluded here rather than relying on that
        # branch to notice.
        if (
            not multiples_scale_independent
            and layer_orients is not None
            and layer_orients[layer_idx] != base_side
            and rows_for_layer
            # Gate the RULE only. This block is followed by the layer's own
            # value-label specs, so an early `continue` here would silence the
            # threshold and delete that layer's labels with it.
            and axis_y.grid.threshold.visible
        ):
            layer_domain = numeric_domain_bounds(
                layer_axis_y.scale.domain if layer_axis_y.scale is not None else None
            )
            layer_grid_visible = (
                layer_axis_y.grid.visible
                if layer_axis_y.grid is not None
                and layer_axis_y.grid.visible is not None
                else axis_y.grid.visible
            )
            layer_zero_style = axis_y.grid.threshold
            # A layer carries no `scale.continuous.zero` field to check at
            # all (`LayerAxisYScale` has only `domain`), so bar/area's
            # own-mark-default always-fire is the only unconditional case;
            # line/scatter fall through to the straddle check.
            layer_should_fire = layer.type in ("bar", "area") or values_straddle_zero(
                rows_for_layer, y_field
            )
            layer_rule = build_zero_rule_if_applicable(
                y_field,
                val_ch,
                log_scale=False,
                authored_domain=layer_domain,
                # Nothing to pin: LayerAxisYScale carries only `domain`, and
                # resolve bakes no per-layer headroom — a layer's scale always
                # auto-fits, so its datum legitimately stretches it to 0.
                domain_min=None,
                domain_max=None,
                grid_visible=layer_grid_visible,
                zero_color=layer_zero_style.color,
                zero_width=layer_zero_style.width,
                should_fire=layer_should_fire,
            )
            if layer_rule is not None:
                vl_layers[-1] = nest_zero_rule(vl_layers[-1], layer_rule, val_ch)

        # This layer's OWN value-label text layer, from its OWN mark style —
        # independent of the base chart's labels (dispatched separately by
        # ValueLabelFeature). Reads from the layer's own dataset when it
        # genuinely diverges from the base's (own_data), else inherits the
        # outer spec's top-level data — this function's own `data=` stamp
        # below, the same already-normalized rows the base renders against —
        # same fallback contract as the wrapper specs above.
        for label_spec in _build_layer_label_specs(
            layer, y_field, background, rows_for_layer, layer_band, val_ch
        ):
            if own_data is not None:
                label_spec.data = own_data
            if layer_shares_normalize_domain:
                _clip_layer_marks(label_spec)
            vl_layers.append(label_spec)

    # A step-band curve's xOffset scale — whether on an overlay line/area
    # layer or on the base's own curve — is a sibling of any bar mark's band
    # scale inside the outer `layer:` array (a bar can be the base OR an
    # overlay layer here). Vega-Lite's mark.width `{"band": v}` shorthand
    # degrades to a static default step (not bandwidth('x')) once ANY sibling
    # layer in the same layer array carries a discrete xOffset scale — a VL
    # compiler quirk, confirmed empirically (bars render at a fixed ~18px
    # regardless of the actual band width). Pin every bar mark's width to an
    # explicit bandwidth('x') expression so it keeps tracking the real band
    # width VL would have given it without the sibling xOffset.
    if step_band_present:
        x_is_banded = x_encoding_is_banded(base_x_enc)
        for vl_spec in vl_layers:
            _fix_bar_band_width(vl_spec, x_is_banded)

    if isinstance(x_enc, dict):
        _reconcile_x_domain(x_enc, data, layer_x_columns, chart_id)

    # Give the single-series base its own legend entry via the shared scale, so
    # a bar+line combo shows both series (not just the overlay). Its glyph is the
    # base mark's symbol (below), since base_label is in no overlay datum set.
    if base_label is not None:
        base_color: VLDict = {"datum": base_label, "scale": shared_scale}
        apply_color_legend(base_color, legend)
        # scale_domain is complete now (base entry + every layer's
        # contribution); an authored `legend.values` resolves against the
        # FULL domain here. Only fires when authored -- this shared scale
        # is `datum:`-bound, not a stacked field scale, so there is no
        # Vega alphabetical-fallback bug to close for the unauthored case.
        if legend.values is not None:
            apply_legend_entry_order(
                base_color,
                scale_domain,
                authored=legend.values,
                aliases=label_domain_aliases,
            )
        label_scale_legend_encs.append(base_color)
        base_spec.encoding["color"] = base_color
    elif field_color_base:
        assert isinstance(base_color_enc, dict)
        base_legend = base_color_enc.get("legend")
        # `base_legend["values"]` may already carry the base emitter's own
        # (narrower, layer-unaware) engine order -- pass `legend.values`,
        # the resolved style, as `authored`, never this dict's render-time
        # state, so resolution runs against the now-complete overlay
        # domain instead.
        if isinstance(base_legend, dict) and (
            legend.values is not None or isinstance(base_legend.get("values"), list)
        ):
            apply_legend_entry_order(
                base_color_enc,
                scale_domain,
                authored=legend.values,
                aliases=label_domain_aliases,
            )

    # scale_domain is complete now and is what Vega will actually enumerate --
    # deduped, since two series can legitimately share one label (see the
    # collision guard above) and collapse to one legend entry despite each
    # still painting its own mark.
    #
    # Vega joins the domain with ", " to build its own aria-label, genuinely
    # ambiguous the moment a value already contains a comma. That is the only
    # trigger. There is no VL-level property for a bounded replacement text;
    # `encode.legend.update.description` is Vega's own mark-encode escape
    # hatch, confirmed (via `vl_convert`-compiled SVG output) to replace the
    # guide's auto aria-label outright.
    domain_values = list(dict.fromkeys(scale_domain))
    if any("," in value for value in domain_values):
        description = f"{len(domain_values)} series"
        for label_scale_enc in label_scale_legend_encs:
            enc_legend = label_scale_enc.get("legend")
            if isinstance(enc_legend, dict):
                enc_legend["encode"] = {
                    "legend": {"update": {"description": {"value": description}}}
                }

    # Mark-aware legend glyphs: patch symbolType exprs when any typed overlay
    # is present and the legend is visible. Skip when the author pinned a
    # constant shape (symbol_shape) — legend_to_vl already emitted it.
    _base_symbols = {
        "bar": "square",
        "line": "stroke",
        "area": "circle",
        "scatter": "circle",
    }
    if (
        (stroke_datums or circle_datums or square_datums or base_label is not None)
        and legend.visible
        and legend.symbol_shape is None
    ):
        _mixed_mark_legend_symbols(
            vl_layers,
            stroke_datums,
            circle_datums,
            square_datums,
            last_area_opacity,
            _base_symbols.get(base_mark_type, "square"),
            (
                shared_scale
                if has_authored_line_color and not independent_color_scale
                else None
            ),
        )
    if legend.symbol_shape is None:
        for field_spec, symbol_type in field_color_legend_symbols:
            field_color = field_spec.encoding.get("color")
            if isinstance(field_color, dict) and isinstance(
                field_color.get("legend"), dict
            ):
                field_color["legend"]["symbolType"] = symbol_type

    resolve_scale: dict[str, Any] = {}
    if independent_y:
        resolve_scale[val_ch] = "independent"
    if independent_color_scale:
        resolve_scale["color"] = "independent"
    resolve = {"scale": resolve_scale} if resolve_scale else {}

    # A non-diverging layer (see `_resolve_layer_rows`) carries no explicit
    # .data of its own and inherits this outer spec's — which must be the
    # SAME already gap-filled/bucket-normalized `data` the base renders
    # against, not BoardRenderSession's own later fallback (chart_rows(),
    # the raw pre-normalization query rows) that would otherwise apply once
    # this spec reaches it, since that fallback only fires when .data is
    # still None.
    return ChartSpec(
        mark="layered",
        encoding=outer_encoding,
        transforms=outer_transforms,
        layers=vl_layers,
        config=outer_config,
        resolve=resolve,
        data=normalize_data_types(data),
        # The base owns the x-axis (paint-order contract above), so the outer
        # spec's labels are the ones it already measured — carried across
        # rather than dropped, or a layered chart would lose the tilt
        # reservation its unlayered twin gets.
        x_label_block_height=base_spec.x_label_block_height,
        # Same carry-across for the base's own computed stacked-series order
        # — dropping it here would leave a layered stacked bar's per-series
        # support-table strip re-deriving its own (possibly disagreeing)
        # verdict instead of reading the one the base layer actually paints.
        stacked_series_order=base_spec.stacked_series_order,
    )
