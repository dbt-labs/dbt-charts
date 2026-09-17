"""Vega-Lite assembly for render-v2.

``assemble_final_vl`` is the single assembly point: it takes a ``ChartSpec``
produced by an emitter + feature pipeline and a ``ResolvedStyle``, and returns
a final Vega-Lite JSON dict with config, background, and title applied.

``ChartSpec`` is VL-only; non-VL families (kpi, table) are routed before emit and
never produce a ``ChartSpec``.  Emitters write VL mark names directly
(``"bar"``, ``"point"``, ``"arc"``, …) so there is no dbt charts-native mark
vocabulary to translate.  The two structural dispatch sentinels —
``"layered"`` and ``"geoshape"`` — are not VL marks; they drive composition
shape detection inside this module.

Non-VL families (kpi, table) route to SVG renderers before reaching this module;
``assemble_final_vl`` raises ``ValueError`` if one slips through.

Rule overlays emitted by baseline features are VL ``rule`` marks and flow through
the standard path like any other sub-spec.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.vega_lite import VEGA_LITE_SCHEMA_URL
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.presentation import (
    _HREF_FIELD,
    apply_presentation_defaults,
)
from dbt_charts.core.render.chart.spec import ChartSpec, EndpointLabelData
from dbt_charts.core.render.utils import (
    DomainValue,
    normalize_data_types,
    normalize_scalar_for_json,
)
from dbt_charts.core.utils import CellValue

# Non-VL families — assemble_final_vl raises ValueError for these.
_NON_VL_MARKS: frozenset[str] = frozenset({"kpi", "table"})

# VL mark names emitted directly by v2 emitters and overlay features.
# Validated in translate_to_vl (top-level spec.mark dispatch) to catch bad emitter output.
_VL_MARKS: frozenset[str] = frozenset(
    {"bar", "line", "area", "point", "rule", "text", "arc", "rect", "circle"}
)


def _base_spec(mark: str, spec: ChartSpec) -> dict[str, Any]:
    """Build a minimal single-mark VL spec from a ChartSpec."""
    vl_mark: str | dict[str, Any] = (
        {"type": mark, **spec.mark_props} if spec.mark_props else mark
    )
    vl: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA_URL,
        "mark": vl_mark,
        "encoding": spec.encoding,
        "background": None,
        "autosize": {"type": "fit", "contains": "padding", "resize": True},
        "padding": {"left": 0, "right": 0, "top": 0, "bottom": 0},
    }
    if spec.data is not None:
        vl["data"] = {"values": spec.data}
    if spec.config:
        vl["config"] = spec.config
    if spec.transforms:
        vl["transform"] = spec.transforms
    if spec.projection:
        vl["projection"] = (
            spec.projection
            if isinstance(spec.projection, dict)
            else {"type": spec.projection}
        )
    return vl


def _translate_standard(vl_mark: str, spec: ChartSpec) -> dict[str, Any]:
    """Translate a standard single-mark ChartSpec to a VL spec dict.

    If the spec has overlay layers appended by baseline features, the output is
    promoted to a VL ``layer[]`` spec: ``spec.underlays`` first, then the main
    chart, then ``spec.layers``. A feature chooses the side it needs — above a
    bar's fills, beneath a scatter's points.
    """
    vl = _base_spec(vl_mark, spec)

    # Collect VL overlay layers (rule/point marks from baseline features).
    rule_layers = [_translate_layer(layer) for layer in spec.layers]
    under_layers = [_translate_layer(layer) for layer in spec.underlays]

    if rule_layers or under_layers:
        main_layer: dict[str, Any] = {"mark": vl.pop("mark")}
        if spec.main_layer_encoding:
            main_layer["encoding"] = spec.main_layer_encoding
        vl["layer"] = [*under_layers, main_layer, *rule_layers]
        # Overlay layers made this a layered spec — carry any resolve block
        # (e.g. axis.y independent from MirrorAxisFeature) onto the output.
        if spec.resolve:
            vl["resolve"] = spec.resolve
            _isolate_independent_y_axis(vl, main_layer)

    if spec.endpoint_label_layout is not None and spec.endpoint_label_data is not None:
        vl = _wrap_endpoint_labels(
            vl, spec.endpoint_label_layout, spec.endpoint_label_data
        )

    return vl


def _isolate_independent_y_axis(vl: dict[str, Any], main_layer: dict[str, Any]) -> None:
    """Give the main layer sole ownership of the y-axis under independent resolve.

    ``MirrorAxisFeature`` sets ``resolve.axis.y = independent`` so the ghost
    layer's opposite-edge axis renders alongside the main one. But a y-axis on
    the SHARED top-level encoding is then redrawn by every layer (each inherits
    it), stacking N identical axes per edge — invisible on a standalone chart
    (they overlap exactly) but multiplied across panels once the spec is wrapped
    in a facet. Move that axis onto the main layer alone and strip it from the
    shared encoding so exactly one main axis (plus the ghost's opposite) renders.
    """
    if vl.get("resolve", {}).get("axis", {}).get("y") != "independent":
        return
    shared_enc = vl.get("encoding")
    if not isinstance(shared_enc, dict):
        return
    shared_y = shared_enc.get("y")
    if not isinstance(shared_y, dict) or not isinstance(shared_y.get("axis"), dict):
        return
    # Attach the axis to the main layer (merging into any existing y-encoding so
    # its scale/stack props survive), then strip it from the shared encoding.
    layer_enc = main_layer.setdefault("encoding", {})
    existing_y = layer_enc.get("y")
    if isinstance(existing_y, dict):
        layer_enc["y"] = {**existing_y, "axis": shared_y["axis"]}
    else:
        layer_enc["y"] = dict(shared_y)
    shared_enc["y"] = {**shared_y, "axis": None}


def _translate_layer(layer: ChartSpec) -> dict[str, Any]:
    """Translate a single overlay layer sub-spec to a VL layer dict.

    A sub-layer may itself be layered (e.g. step-band overlays that emit
    area+point mark pairs) — recurse into a VL nested-layer dict in that case.

    ``layer.tooltip_description``: a combo base or
    overlay layer's OWN structured-tooltip content, set directly on ITS OWN
    VL dict here — never on a shared parent encoding relying on VL's
    layer-cascade, which is unreliable across sibling layers whose rows may
    come from different pipelines (e.g. one layer gap-filled, another not).
    Reuses ``_apply_structured_tooltip`` so a "layered" sub-spec (a line/area
    overlay's own halo/fg/hover trio) gets the same private-data guard the
    top-level spec gets.
    """
    if layer.mark == "layered":
        # layer.underlays is never populated on a sub-layer: the only writer
        # (baseline.py's scatter branch) appends to the top-level spec passed
        # into apply(), never to a nested ChartSpec — so this reads layer.layers
        # only, unlike the top-level assembly below.
        nested: dict[str, Any] = {
            "layer": [_translate_layer(sub) for sub in layer.layers]
        }
        if layer.encoding:
            nested["encoding"] = layer.encoding
        if layer.data is not None:
            nested["data"] = {"values": normalize_data_types(layer.data)}
        elif layer.data_name:
            nested["data"] = {"name": layer.data_name}
        if layer.transforms:
            nested["transform"] = layer.transforms
        if layer.tooltip_description:
            nested = _apply_structured_tooltip(nested, layer.tooltip_description)
        return nested
    if layer.mark not in _VL_MARKS:
        raise ValueError(
            f"translate_to_vl: overlay layer has unknown mark {layer.mark!r}; "
            f"allowed VL marks: {sorted(_VL_MARKS)}"
        )
    vl_mark = layer.mark
    # Mirrors _translate_geo_layer: use mark object when mark_props are present.
    vl_mark_obj = {"type": vl_mark, **layer.mark_props} if layer.mark_props else vl_mark
    layer_vl: dict[str, Any] = {"mark": vl_mark_obj}
    # VL gap: Vega-Lite 6.x crashes compiling a line/area unit that has no
    # `encoding` key at all — its point/line-overlay normalization reads
    # `encoding.shape` unconditionally (TypeError). An empty object is
    # accepted and means the same thing (the layer still inherits the shared
    # parent encoding), so those marks always emit the key; a chart authoring
    # neither x nor y is the reachable all-empty case.
    if layer.encoding or vl_mark in ("line", "area"):
        layer_vl["encoding"] = layer.encoding
    if layer.data_name:
        layer_vl["data"] = {"name": layer.data_name}
    elif layer.data is not None:
        layer_vl["data"] = {"values": normalize_data_types(layer.data)}
    if layer.transforms:
        layer_vl["transform"] = layer.transforms
    if layer.tooltip_description:
        layer_vl = _apply_structured_tooltip(layer_vl, layer.tooltip_description)
    return layer_vl


def _translate_layered(spec: ChartSpec) -> dict[str, Any]:
    """Translate a layered ChartSpec (per-layer sub-specs in spec.layers) to VL."""
    vl: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA_URL,
        "background": None,
        "autosize": {"type": "fit", "contains": "padding", "resize": True},
        "padding": {"left": 0, "right": 0, "top": 0, "bottom": 0},
    }
    if spec.datasets is not None:
        vl["datasets"] = {
            name: normalize_data_types(rows) for name, rows in spec.datasets.items()
        }
        # Per-layer charts: every layer specifies its own data source (inline or named
        # dataset).  Emitting a top-level "data" block here would cause VL to use it
        # for scale domain inference, which subtly changes margin calculations.
        # V1 does not emit a top-level data block for per-layer charts; neither should we.
    elif spec.data is not None:
        vl["data"] = {"values": normalize_data_types(spec.data)}
    if spec.encoding:
        vl["encoding"] = spec.encoding
    if spec.config:
        vl["config"] = spec.config
    if spec.transforms:
        vl["transform"] = spec.transforms
    if spec.resolve:
        vl["resolve"] = spec.resolve
    # Underlays first, same contract as _translate_standard — a feature that asked
    # for a layer beneath the data must get it on the layered path too, or the rule
    # silently vanishes for a chart with authored `layers:`.
    vl["layer"] = [_translate_layer(layer) for layer in (*spec.underlays, *spec.layers)]
    if spec.layers:
        _isolate_independent_y_axis(vl, vl["layer"][len(spec.underlays) :][0])
    if spec.endpoint_label_layout is not None and spec.endpoint_label_data is not None:
        vl = _wrap_endpoint_labels(
            vl, spec.endpoint_label_layout, spec.endpoint_label_data
        )
    return vl


def _translate_geo_layer(layer: ChartSpec) -> dict[str, Any]:
    """Translate a single geo composition layer to VL.

    Handles both geoshape background/overlay layers and non-geoshape point layers
    (e.g. circle marks in a point_map with background), sharing the same layered
    geoshape composition path.
    """
    vl_layer: dict[str, Any] = {}
    if layer.geo_data:
        vl_layer["data"] = layer.geo_data
    elif layer.data is not None:
        vl_layer["data"] = {"values": layer.data}
    mark_type = layer.mark
    # Always emit as an object so callers can reliably do mark["type"];
    # layer.mark_props may be empty for layers with no extra mark properties.
    vl_layer["mark"] = {"type": mark_type, **layer.mark_props}
    if layer.encoding:
        vl_layer["encoding"] = layer.encoding
    if layer.transforms:
        vl_layer["transform"] = layer.transforms
    return vl_layer


def _translate_geoshape(spec: ChartSpec) -> dict[str, Any]:
    """Translate a geoshape ChartSpec to VL.

    Two cases:
    - No layers (no-data path): single mark spec with geo data source.
    - Has layers (choropleth path): layered spec — background + data-join overlay.
      Outer spec has no ``data`` key; each layer carries its own geo data reference.
    """
    if not spec.layers:
        # No-data: straight single-mark spec + geo data source.
        single: dict[str, Any] = _base_spec("geoshape", spec)
        if spec.geo_data:
            single["data"] = spec.geo_data
        return single

    # Layered choropleth: outer spec inherits boilerplate but no top-level data.
    layered: dict[str, Any] = {
        "$schema": VEGA_LITE_SCHEMA_URL,
        "background": None,
        "autosize": {"type": "fit", "contains": "padding", "resize": True},
        "padding": {"left": 0, "right": 0, "top": 0, "bottom": 0},
    }
    if spec.projection:
        layered["projection"] = (
            spec.projection
            if isinstance(spec.projection, dict)
            else {"type": spec.projection}
        )
    if spec.config:
        layered["config"] = spec.config
    layered["layer"] = [_translate_geo_layer(layer) for layer in spec.layers]
    return layered


# ---------------------------------------------------------------------------
# Endpoint label pane builders
# ---------------------------------------------------------------------------


def _wrap_endpoint_labels(
    main_vl: dict[str, Any],
    layout: str,
    label_data: EndpointLabelData,
) -> dict[str, Any]:
    """Wrap the main VL spec in hconcat (right_pane) or vconcat (top_rail)."""
    if layout == "right_pane":
        return _wrap_hconcat_label_pane(main_vl, label_data)
    if layout == "top_rail":
        return _wrap_vconcat_label_rail(main_vl, label_data)
    raise ValueError(f"Unknown endpoint_label_layout {layout!r}")


def _label_color_encoding(label_data: EndpointLabelData) -> dict[str, Any]:
    """Build the VL color encoding for the label pane (independent scale).

    Uses dark_companion_range for the label text color when available (preferred:
    higher contrast than the bright mark color).  Falls back to color_range.
    """
    enc: dict[str, Any] = {
        "field": label_data.series_field,
        "type": "nominal",
        "legend": None,
    }
    effective_range = label_data.dark_companion_range or label_data.color_range
    if label_data.color_domain and effective_range:
        enc["scale"] = {
            "domain": label_data.color_domain,
            "range": effective_range,
        }
    return enc


def _spread_for_measurement(
    positions: list[tuple[str, float]], y_domain_min: float, y_domain_max: float
) -> list[tuple[str, float]]:
    """Return positions guaranteed to occupy at least two distinct values.

    A no-op unless every position ties. Ties happen with real data — a stacked
    series whose trailing column is all null puts every label on the same
    total — and a tied pane renders every mark at one pixel, which no slope can
    be measured from. Spreading across the domain guarantees distinct pixels.
    Order is preserved so the probe stays comparable to the real render.
    """
    values = {y for _, y in positions}
    if len(values) > 1 or len(positions) < 2:
        return positions
    step = (y_domain_max - y_domain_min) / (len(positions) - 1)
    return [(name, y_domain_min + i * step) for i, (name, _) in enumerate(positions)]


# Scale-probe calls in mark expressions whose scale goes child-qualified once
# the chart is wrapped into a concat. Only x qualifies: y is shared by the
# wrapper, and the offset channels stay top-level under their own names
# because the label pane has no offset channel to make them independent of —
# pinned against vl_convert by
# test_right_pane_scale_names_match_vl_convert.
_CONCAT_QUALIFIED_SCALE_PROBE = re.compile(r"\b(scale|bandwidth)\('(x)'")


def _requalify_concat_scale_probes(
    node: Any,  # type-state: explicit_any — walks arbitrary VL spec fragments
    child_name: str,
) -> None:
    """Rewrite scale-name probes in *node*'s expressions for concat wrapping.

    Vega compiles a concat child's independent scales under
    ``<child>_<channel>`` names (``concat_0_x``), so a mark expression built
    against the unwrapped spec — ``scale('x', …)``, ``bandwidth('xOffset')``
    — addresses a scale that no longer exists and silently evaluates NaN,
    painting zero-width marks. Walks every ``"expr"`` string in place. The
    child name is Vega-Lite's own deterministic concat naming
    (``concat_<index>``), pinned by the endpoint-pane tests against
    vl_convert.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "expr" and isinstance(value, str):
                node[key] = _CONCAT_QUALIFIED_SCALE_PROBE.sub(
                    rf"\1('{child_name}_\2'", value
                )
            else:
                _requalify_concat_scale_probes(value, child_name)
    elif isinstance(node, list):
        for item in node:
            _requalify_concat_scale_probes(item, child_name)


def _wrap_hconcat_label_pane(
    main_vl: dict[str, Any],
    label_data: EndpointLabelData,
) -> dict[str, Any]:
    """Build the hconcat wrapper for the right-side endpoint label pane (line/area/bar)."""
    # These values exist to be *measured*, not read: the post-probe pass in
    # converters/chart.py recomputes every position from the true anchors it
    # carries on the cascade sentinel. When every series ends on the same value
    # the marks would otherwise land on one pixel, leaving no two points to
    # measure the y scale's slope from — so spread them here. The spread is
    # discarded; only the slope it makes measurable survives.
    positions = _spread_for_measurement(
        label_data.positions, label_data.y_domain_min, label_data.y_domain_max
    )
    pane_data = [
        {label_data.series_field: s, label_data.value_alias: y} for s, y in positions
    ]

    # Already capped to its share of the chart width by the measure site
    # (features/endpoint_labels.py), which is the only place that knows the
    # natural width. Vega ellipsizes overflowing names at the mark limit below.
    label_width = label_data.label_pane_width

    mark: dict[str, Any] = {"type": "text", "align": "left", "baseline": "middle"}
    if label_data.label_mark_font_props:
        mark.update(label_data.label_mark_font_props)
    if label_width > 0:
        mark["limit"] = label_width

    pane: dict[str, Any] = {
        "view": {"stroke": None},
        "data": {"values": pane_data},
        "mark": mark,
        "encoding": {
            "x": {"value": 0},
            "y": {
                "field": label_data.value_alias,
                "type": "quantitative",
                "axis": None,
            },
            "color": _label_color_encoding(label_data),
            "text": {"field": label_data.series_field},
        },
    }
    if label_width > 0:
        pane["width"] = label_width
    # Hoist spec-level VL properties to the hconcat root so vl-convert
    # resolves $schema, config, and background at the wrapper level.
    hoist_keys = ("$schema", "config", "background", "data")
    hoisted = {k: main_vl.pop(k) for k in hoist_keys if k in main_vl}

    # The chart pane's x scale resolves independent inside the concat —
    # sharing x is not an option here: side-by-side panes sharing one x range
    # overlap and blow the overshoot correction — so Vega compiles it under
    # the child-qualified name its own concat naming scheme assigns (chart
    # pane = child 0 → concat_0_x). Mark expressions that probe a scale by
    # name (continuous_bar_size_prop's scale('x', …) min-gap width) would
    # otherwise address a scale that no longer exists and silently evaluate
    # NaN → zero-width marks. y stays unqualified because it is shared below,
    # and xOffset/yOffset because the label pane has no offset channel —
    # rewriting those would inflict this exact bug on grouped bars.
    _requalify_concat_scale_probes(main_vl, "concat_0")

    return {
        **hoisted,
        "spacing": label_data.label_offset,
        "resolve": {"scale": {"color": "independent", "y": "shared"}},
        "hconcat": [main_vl, pane],
    }


def _wrap_vconcat_label_rail(
    main_vl: dict[str, Any],
    label_data: EndpointLabelData,
) -> dict[str, Any]:
    """Build the vconcat wrapper for the top-row series-label rail (horizontal stacked bar)."""
    rail_data = [
        {label_data.series_field: s, label_data.value_alias: x}
        for s, x in label_data.positions
    ]
    mark: VLDict = {"type": "text", "align": "center", "baseline": "bottom"}
    if label_data.label_mark_font_props:
        mark.update(label_data.label_mark_font_props)
    rail: dict[str, Any] = {
        "height": label_data.height,
        "view": {"stroke": None},
        "data": {"values": rail_data},
        "mark": mark,
        "encoding": {
            "x": {
                "field": label_data.value_alias,
                "type": "quantitative",
                "axis": None,
            },
            "color": _label_color_encoding(label_data),
            "text": {"field": label_data.series_field},
        },
    }

    hoist_keys = ("$schema", "config", "background", "data", "title")
    hoisted = {k: main_vl.pop(k) for k in hoist_keys if k in main_vl}

    return {
        **hoisted,
        "spacing": label_data.label_offset,
        "resolve": {"scale": {"color": "independent", "x": "shared"}},
        "vconcat": [rail, main_vl],
    }


# ---------------------------------------------------------------------------
# Post-processing: href_link and structured tooltip wiring
# ---------------------------------------------------------------------------


def _disable_private_data_channel(layers: list[VLDict], channel: str) -> list[VLDict]:
    """Set ``channel: None`` on every VL layer entry carrying its own
    private ``data`` (a rule/reference-line sub-layer), recursing into
    nested ``layer`` lists first.

    A dual-axis zero-baseline rule (``nest_zero_rule``) sits nested one
    level inside the entry whose scale it shares, so a shallow scan of the
    top-level ``layer`` array alone misses it: the same reason
    ``emitters/_cartesian.py``'s ``layer_encoding_owner`` and
    ``support_table_attachment.py``'s ``_first_layer_encoding`` recurse.
    """
    patched: list[VLDict] = []
    for layer in layers:
        nested = layer.get("layer")
        if isinstance(nested, list):
            layer = {**layer, "layer": _disable_private_data_channel(nested, channel)}
        if "data" in layer:
            enc = layer.get("encoding")
            layer = {
                **layer,
                "encoding": {**(enc if enc is not None else {}), channel: None},
            }
        patched.append(layer)
    return patched


def _in_chart_pane(vl: VLDict, stamp: Callable[[VLDict], VLDict]) -> VLDict:
    """Run ``stamp`` on the pane that carries the marks.

    An endpoint-label rail wraps the chart in a concat: ``hconcat`` for the
    vertical layout (chart first, label pane right), ``vconcat`` for the
    horizontal one (rail first, chart second). Neither VL nor Vega reads a
    top-level ``encoding``, ``transform`` or ``params`` on a concat spec, so
    anything that stamps those has to descend to the chart pane or it lands
    somewhere the renderer never looks.
    """
    if "hconcat" in vl:
        main, *rest = vl["hconcat"]
        return {**vl, "hconcat": [_in_chart_pane(main, stamp), *rest]}
    if "vconcat" in vl:
        rail, main = vl["vconcat"]
        return {**vl, "vconcat": [rail, _in_chart_pane(main, stamp)]}
    return stamp(vl)


def _apply_structured_tooltip(vl: dict[str, Any], expr: str) -> dict[str, Any]:
    """Inject the description encoding for the structured tooltip.

    VL's ``description`` channel fully replaces a mark's aria-label rather than
    merging with channel-derived content — see ``emitters/_tooltip.py``.

    Wired as a ``{"value": {"expr": ...}}`` def, NOT a ``{"field": ..., "type":
    "nominal"}`` field reference bound through a ``calculate`` transform. A
    field-bound description is a genuine encoded channel, and Vega-Lite's
    compiler folds every encoded nominal/ordinal channel into the automatic
    ``impute`` transform's ``groupby`` for line/area marks (the same list that
    already carries ``color``) — regardless of whether that channel is
    otherwise excluded from the mark's own visual "detail" grouping. Since the
    structured tooltip's header always embeds the datum's own x/time value,
    every row gets a *distinct* description, exploding the impute groupby to
    one group per row and imputing phantom zero-value points at every other x
    for every real point — corrupting (fragmenting) the stacked area/line
    path. A ``value``-typed expr def is not a data-field channel at all, so it
    never enters that analysis, while compiling to the exact same per-datum
    Vega signal expression (confirmed: identical rendered aria-label content,
    clean impute groupby with the fix, corrupted path without it).

    Endpoint labels wrap the layered spec in an ``hconcat`` (right_pane,
    vertical stacked bar/line/area, chart pane at index 0) or a ``vconcat``
    (top_rail, horizontal stacked bar, chart pane at index 1 — the rail sits
    above it) before this post-processing step runs (``_translate_layered``
    calls ``_wrap_endpoint_labels`` internally) — recurse into the actual
    chart pane rather than stamping a no-op encoding onto the wrapper itself;
    the label pane/rail carries no data marks of its own.

    A layered spec needs one more override for ``BaselineFeature``'s
    zero/top/unity rule overlays specifically: they're appended sub-layers
    carrying their OWN self-contained single-row synthetic ``data`` (e.g.
    ``[{"count": 0}]``, with no ``day_name``/``kind``/etc). Setting
    ``description`` on the shared top-level encoding is still correct and
    necessary for a chart's own inherent multi-layer composition (e.g. a
    line's halo/foreground-stroke/invisible-hover-point trio, which all
    legitimately share the base chart data) — but it ALSO cascades into a
    rule layer's private-``data`` row, whose datum lacks the fields the expr
    references, evaluating to a literal "undefined" aria-label that
    chart_interactivity.js's ``isDataMark`` can't tell apart from a real
    mark (it only checks for a role marker plus the absence of an
    axis/legend prefix) — surfacing a bogus tooltip on hover. Any layer with
    its own private ``data`` key gets an explicit ``description: None``
    override instead (VL's own "explicit None disables an inherited
    channel" convention: the same one ``_isolate_independent_y_axis``
    already relies on for ``axis``/``legend``).
    """

    def stamp(pane: VLDict) -> VLDict:
        result = dict(pane)
        enc = dict(result.get("encoding") or {})
        enc["description"] = {"value": {"expr": expr}}
        result["encoding"] = enc
        layers = result.get("layer")
        if isinstance(layers, list):
            result["layer"] = _disable_private_data_channel(layers, "description")
        return result

    return _in_chart_pane(vl, stamp)


def _apply_href_link(vl: dict[str, Any], href_link: str) -> dict[str, Any]:
    """Inject calculate transform + href encoding for click interactivity."""

    def stamp(pane: VLDict) -> VLDict:
        result = dict(pane)
        transforms = list(result.get("transform") or [])
        transforms.append({"calculate": href_link, "as": _HREF_FIELD})
        result["transform"] = transforms
        enc = dict(result.get("encoding") or {})
        enc["href"] = {"field": _HREF_FIELD, "type": "nominal"}
        result["encoding"] = enc
        # Mirror the _apply_structured_tooltip guard: a sub-layer with its own
        # private data (e.g. BaselineFeature's zero-rule layer) inherits
        # encoding.href but NOT the outer calculate transform, so __df_href__
        # is undefined on its rows.  VL base-URL-joins the undefined field
        # value into a broken external link.  Explicit None disables the
        # inherited channel (same convention as axis/legend overrides).  A
        # sub-layer with no private data of its own (e.g. BarHoverBandFeature's
        # hover band) is untouched here and correctly inherits the real href —
        # it shares the same data and calculate transform as its sibling.
        layers = result.get("layer")
        if isinstance(layers, list):
            result["layer"] = _disable_private_data_channel(layers, "href")
        return result

    return _in_chart_pane(vl, stamp)


# ---------------------------------------------------------------------------
# Assembly entry points
# ---------------------------------------------------------------------------


def translate_to_vl(spec: ChartSpec) -> dict[str, Any]:
    """Assemble a ``ChartSpec`` to a Vega-Lite struct dict (no config merge).

    Internal helper used by ``assemble_final_vl`` and by tests that exercise
    structural assembly (endpoint labels, href) without needing
    a full board style.  Dispatches on ``spec.mark``: ``"layered"`` and
    ``"geoshape"`` are structural sentinels; all other marks are VL mark strings
    emitted directly by emitters.

    Raises:
        ValueError: For non-VL family marks (kpi, table) or unknown marks.
    """
    if spec.mark == "layered":
        vl = _translate_layered(spec)
    elif spec.mark == "geoshape":
        vl = _translate_geoshape(spec)
    elif spec.mark in _VL_MARKS:
        vl = _translate_standard(spec.mark, spec)
    else:
        raise ValueError(
            f"Unknown mark {spec.mark!r}. "
            f"VL marks: {sorted(_VL_MARKS)}; structural: layered, geoshape; "
            f"non-VL (SVG path): {sorted(_NON_VL_MARKS)}"
        )

    if spec.href_link is not None:
        vl = _apply_href_link(vl, spec.href_link)

    if spec.tooltip_description:
        vl = _apply_structured_tooltip(vl, spec.tooltip_description)

    return vl


def assemble_final_vl(
    spec: ChartSpec,
    board_style: ResolvedStyle,
) -> dict[str, Any]:
    """Assemble a ``ChartSpec`` into a final Vega-Lite spec dict.

    Single assembly point for render-v2: structural VL assembly via
    ``translate_to_vl`` followed by presentation merge via
    ``apply_presentation_defaults`` (config, background, title).

    Args:
        spec: ``ChartSpec`` produced by an emitter and mutated by the feature
            pipeline.  Must carry ``title``/``subtitle``/``title_font``/
            ``background``/``title_style`` (set by
            ``BoardRenderSession.emit_chart`` from the resolved chart —
            ``title`` is the raw jinja-resolved text; title-case is applied
            here from ``title_style.font.case``, VL-presentation only).
        board_style: Fully resolved board-level style.  Supplies only
            ``vega_config`` (baked theme config) — unchanged board-level
            presentation, not a per-chart decision.

    Returns:
        Vega-Lite JSON-serializable spec dict with config and background applied.

    Raises:
        ValueError: For non-VL family marks (kpi, table).
    """
    vl = translate_to_vl(spec)

    assert spec.background is not None and spec.title_style is not None, (
        "ChartSpec.background/title_style must be set by "
        "BoardRenderSession.emit_chart before assemble_final_vl runs"
    )
    vl = apply_presentation_defaults(
        vl,
        spec.background,
        board_style.vega_config,
    )

    if spec.title or spec.subtitle:
        from dbt_charts.core.render.chart.spec_builders import set_chart_title

        set_chart_title(vl, spec.title, spec.subtitle, case=spec.title_style.font.case)

    # config.title.fontSize/fontWeight come from theme YAML as floats; coerce to int.
    # vl["config"]["title"] may be aliased to the board-wide effective_vega_config
    # (shared across every chart in a board render) when no per-chart config
    # overrides it, so build a new dict here rather than mutating it in place.
    if "config" in vl and "title" in vl["config"]:
        _tc = dict(vl["config"]["title"])
        for _key in ("fontSize", "fontWeight"):
            if _key in _tc and isinstance(_tc[_key], float) and _tc[_key].is_integer():
                _tc[_key] = int(_tc[_key])
        vl["config"]["title"] = _tc

    if spec.title_font is not None:
        from dbt_charts.core.render.font_support import (
            INTER_FONT_FAMILY,
            INTER_VARIABLE_FONT_FAMILY,
        )

        resolved_family = (
            INTER_VARIABLE_FONT_FAMILY
            if spec.title_font.family == INTER_FONT_FAMILY
            else spec.title_font.family
        )
        if "config" in vl and "title" in vl["config"]:
            vl["config"]["title"] = {**vl["config"]["title"], "font": resolved_family}
        if isinstance(vl.get("title"), dict):
            title_block = vl["title"]
            title_block["font"] = resolved_family
            title_block["fontSize"] = (
                int(spec.title_font.size)
                if spec.title_font.size.is_integer()
                else spec.title_font.size
            )
            title_block["fontWeight"] = (
                int(spec.title_font.weight)
                if isinstance(spec.title_font.weight, float)
                and spec.title_font.weight.is_integer()
                else spec.title_font.weight
            )

    if spec.facet_row is not None or spec.facet_column is not None:
        vl = _wrap_facet(vl, spec)

    return vl


def _facet_sort_values(order: tuple[CellValue, ...]) -> list[DomainValue | None]:
    """An explicit VL sort array from a facet field's query-order domain.

    ``DomainValue | None`` rather than ``DomainValue``: a facet field can
    carry a real SQL-NULL panel, which ``normalize_scalar_for_json`` passes
    through unchanged.

    Takes the non-``None`` tuple directly (never ``None``): ``FacetFeature``
    always pairs ``facet_row``/``facet_column`` with the matching
    ``*_order`` field, so a faceted spec's order tuple is never absent.
    Domain values bypass ``normalize_data_types``, so a raw date/Decimal is
    normalized individually before reaching vl_convert's JSON serialization
    (``render/utils.py``), same as ``pin_sorted_x_domain``'s x-scale domain.
    """
    return [normalize_scalar_for_json(value) for value in order]


def _wrap_facet(main_vl: dict[str, Any], spec: ChartSpec) -> dict[str, Any]:
    """Wrap a translated unit spec in a VL ``facet`` operator (small multiples).

    ``facet_row`` → ``facet.row`` (vertical stack; the facet value renders as a
    left row-header label). ``facet_column`` → ``facet.column`` (side-by-side;
    top column-header). Either or both are set (the model requires at least one),
    giving a row stack, a horizontal strip, or a grid. Panels share one measure
    scale by default; ``facet_scale == "independent"`` emits a facet-root
    ``resolve.scale`` on ``spec.measure_channel`` — VL x on a horizontal bar,
    whose flipped axes put the category on y. A second, independent producer:
    ``spec.facet_independent_channels`` (``FacetFeature`` /
    ``facet_bound_position_channels``) adds an entry per position channel
    whose per-panel domain is a proper subset of its whole domain, where the
    extra per-panel axis that narrowing forces is affordable — both producers
    write into the same ``resolve.scale`` dict, sorted for a byte-stable spec.

    The facet operator owns the shared dataset and the top-level VL properties;
    the unit keeps mark/encoding/layer (+ per-panel width/height set later by the
    render bridge). Mirrors ``_wrap_hconcat_label_pane``'s hoist pattern.

    ``title`` is hoisted too: a chart-level title frames the whole small-multiples
    set, so it belongs on the facet root, not repeated on the inner unit spec.
    """
    facet: dict[str, Any] = {}
    if spec.facet_row is not None:
        assert spec.facet_row_order is not None  # FacetFeature pairs the two
        facet["row"] = {
            "field": spec.facet_row,
            "type": "nominal",
            "title": None,
            # A facet field def has no "preserve source order" sort mode
            # (unlike a position channel's `sort: null`), so the query order
            # is pinned as an explicit values array, or VL falls back to
            # its own alphabetical default.
            "sort": _facet_sort_values(spec.facet_row_order),
            # Left row-header: the facet value sits to the left of each panel.
            "header": {
                "labelAngle": 0,
                "labelAlign": "left",
                "labelAnchor": "middle",
                "orient": "left",
            },
        }
    if spec.facet_column is not None:
        assert spec.facet_column_order is not None  # FacetFeature pairs the two
        facet["column"] = {
            "field": spec.facet_column,
            "type": "nominal",
            "title": None,
            "sort": _facet_sort_values(spec.facet_column_order),
        }
    hoist_keys = (
        "$schema",
        "config",
        "background",
        "data",
        "autosize",
        "padding",
        "title",
    )
    hoisted = {k: main_vl.pop(k) for k in hoist_keys if k in main_vl}
    wrapper: dict[str, Any] = {**hoisted, "facet": facet, "spec": main_vl}
    resolve_scale: dict[str, str] = dict.fromkeys(
        sorted(spec.facet_independent_channels), "independent"
    )
    if spec.facet_scale == "independent":
        resolve_scale[spec.measure_channel] = "independent"
    if resolve_scale:
        wrapper["resolve"] = {"scale": resolve_scale}
    return wrapper
