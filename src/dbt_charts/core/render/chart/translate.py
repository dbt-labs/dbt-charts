"""Vega-Lite assembly for the geo maps.

``assemble_final_vl`` is the single assembly point: it takes a ``ChartSpec``
produced by a geo emitter + feature pipeline and a ``ResolvedStyle``, and returns
a final Vega-Lite JSON dict with config, background, and title applied.

Every other family renders in the Rust engine and never reaches this module.
Emitters write VL mark names directly (``"circle"``, ``"geoshape"``); the
``"geoshape"`` mark also drives layered-composition detection here.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.vega_lite import VEGA_LITE_SCHEMA_URL
from dbt_charts.core.render.chart._types import VLDict
from dbt_charts.core.render.chart.presentation import (
    _HREF_FIELD,
    apply_presentation_defaults,
)
from dbt_charts.core.render.chart.spec import ChartSpec


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
    if layer.tooltip_description:
        vl_layer = _apply_structured_tooltip(vl_layer, layer.tooltip_description)
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


def _disable_private_data_channel(layers: list[VLDict], channel: str) -> list[VLDict]:
    """Set ``channel: None`` on every VL layer entry carrying its own private
    ``data`` (a geo layer's own geometry or data source).

    A layer with its own data would otherwise inherit a channel stamped on the
    shared top-level encoding whose fields its rows lack.
    """
    patched: list[VLDict] = []
    for layer in layers:
        if "data" in layer:
            enc = layer.get("encoding")
            layer = {
                **layer,
                "encoding": {**(enc if enc is not None else {}), channel: None},
            }
        patched.append(layer)
    return patched


def _apply_structured_tooltip(vl: dict[str, Any], expr: str) -> dict[str, Any]:
    """Inject the description encoding for the hover card.

    VL's ``description`` channel fully replaces a mark's aria-label rather than
    merging with channel-derived content — see ``emitters/_tooltip.py``.

    Wired as a ``{"value": {"expr": ...}}`` def, NOT a ``{"field": ..., "type":
    "nominal"}`` field reference: a field-bound description is a genuine encoded
    channel that Vega-Lite folds into an automatic ``impute`` transform's
    ``groupby``, while a ``value``-typed expr def never enters that analysis and
    compiles to the same per-datum Vega signal expression.

    A layer carrying its own private ``data`` gets an explicit ``description:
    None`` override instead of inheriting the shared one.
    """
    result = dict(vl)
    enc = dict(result["encoding"]) if "encoding" in result else {}
    enc["description"] = {"value": {"expr": expr}}
    result["encoding"] = enc
    layers = result.get("layer")
    if isinstance(layers, list):
        result["layer"] = _disable_private_data_channel(layers, "description")
    return result


def _apply_href_link(vl: dict[str, Any], href_link: str) -> dict[str, Any]:
    """Inject calculate transform + href encoding for click interactivity."""
    result = dict(vl)
    transforms = list(result["transform"]) if "transform" in result else []
    transforms.append({"calculate": href_link, "as": _HREF_FIELD})
    result["transform"] = transforms
    enc = dict(result["encoding"]) if "encoding" in result else {}
    enc["href"] = {"field": _HREF_FIELD, "type": "nominal"}
    result["encoding"] = enc
    # A sub-layer with its own private data inherits encoding.href but NOT the
    # outer calculate transform, so __df_href__ is undefined on its rows. Explicit
    # None disables the inherited channel (same convention as axis/legend
    # overrides).
    layers = result.get("layer")
    if isinstance(layers, list):
        result["layer"] = _disable_private_data_channel(layers, "href")
    return result


def translate_to_vl(spec: ChartSpec) -> dict[str, Any]:
    """Assemble a geo ``ChartSpec`` to a Vega-Lite struct dict (no config merge).

    Dispatches on ``spec.mark``: ``"geoshape"`` (a single mark, or a layered
    choropleth / point map over a background) or ``"circle"`` (a bare point map).

    Raises:
        ValueError: For any other mark.
    """
    if spec.mark == "geoshape":
        vl = _translate_geoshape(spec)
    elif spec.mark == "circle":
        vl = _base_spec(spec.mark, spec)
    else:
        raise ValueError(
            f"Unknown mark {spec.mark!r}: a geo spec's mark is geoshape or circle"
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

    return vl
