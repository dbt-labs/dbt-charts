"""Vega-Lite config mapping from ChartStyleContext.

style_to_vega_lite() is the sole place that maps dbt charts' nested field
names to flat Vega-Lite camelCase; effective_vega_config() is its sole
production caller, dumping the overlay it produces.

effective_vega_config() is called from compile/resolve/style/board.py
(_finalize_style).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dbt_charts.core.compile.models.style.resolved._base import _VLConfig
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.theme.marks import (
        CircleMarkStyle,
        RectMarkStyle,
        RuleMarkStyle,
    )


def effective_vega_config(chart_style_context: ChartStyleContext) -> _VLConfig:
    """Final VL config dict for a board: the style overlay, nothing else.

    Called by ``_finalize_style`` (bakes onto ``ResolvedStyle``).

    Background is not part of the config: it is emitted top-level on the
    assembled spec (``apply_presentation_defaults``), the home Vega-Lite
    resolves with precedence over any config copy.
    """
    vlc = style_to_vega_lite(chart_style_context)
    overlay: _VLConfig = vlc.model_dump(by_alias=True, exclude_unset=True)
    return overlay


# =============================================================================
# VEGA-LITE MAPPING (nested dbt charts fields → flat camelCase)
# =============================================================================


def style_to_vega_lite(
    chart_style_context: ChartStyleContext,
) -> Any:  # -> vlc.VegaLiteConfig, avoid circular import
    """Map ChartStyleContext to VegaLiteConfig.

    Translates nested dbt charts field names to flat Vega-Lite camelCase.
    This is the sole place that knows about Vega-Lite naming.

    Sole VL mapper. The overloaded style_to_vega_lite(Style) signature was removed.
    """
    from dbt_charts.core.compile.models.vega_lite import config as vlc

    data: dict[str, Any] = {}

    # Root font family → VL top-level `font` key
    if chart_style_context.font_family is not None:
        data["font"] = chart_style_context.font_family

    # Color palette → range.category. (Dashes flow through encoding-level
    # `scale.range` on the strokeDash channel, not config.range — vl_convert's
    # Vega-Lite v6 ignores `range.dashPattern` even though the docs list it.)
    if chart_style_context.palette:
        data["range"] = {"category": chart_style_context.palette}

    # axis/axisX/axisY/axisQuantitative/axisBand: all moved to encoding level.
    # The cascade layers global + channel + type-conditional before render.
    # No config.axis* keys emitted here.

    # legend: moved to encoding.color.legend.* / encoding.size.legend.* etc.
    # No config.legend key emitted here.

    # bar/line/area/scatter: moved to spec.mark.{...} extended mark object.
    # The family emitters merge theme + chart-local mark style.
    # No config.bar/line/area/point keys emitted here.

    # Generic mark types: map opacity/stroke/strokeWidth where set.
    # These live under charts.marks.* instead of charts.*.
    def _mark_config(
        m: CircleMarkStyle | RuleMarkStyle | RectMarkStyle,
    ) -> dict[str, float | str]:
        d: dict[str, float | str] = {}
        if m.opacity is not None:
            d["opacity"] = m.opacity
        stroke = m.stroke
        if stroke is not None:
            if stroke.color is not None:
                d["stroke"] = stroke.color
            if stroke.width is not None:
                d["strokeWidth"] = stroke.width
        return d

    # Generic mark config carries only theme-configurable opacity/stroke —
    # fill defaults are baked per-chart by the emitter that owns the mark
    # (e.g. the heatmap emitter bakes config.rect.fill from its own resolved
    # effective palette; no VL mark type here needs a config-level fallback
    # fill, since every emitted mark either sets its own fill/stroke or has
    # an explicit color encoding).
    for mark_name, mark in [
        ("circle", chart_style_context.marks.circle),
        ("rule", chart_style_context.marks.rule),
        ("rect", chart_style_context.marks.rect),
    ]:
        cfg = _mark_config(mark)
        if cfg:
            data[mark_name] = vlc.MarkConfig.model_validate(cfg)

    # View
    view = chart_style_context.view
    view_data: dict[str, Any] = {
        "continuousWidth": view.continuous_width,
        "continuousHeight": view.continuous_height,
        # Always emit stroke. ``None`` is meaningful here: VL's default view
        # stroke is ``"#ddd"``, so an absent key draws a default border. To
        # actually disable the bounding box, the spec must contain
        # ``stroke: null``.
        "stroke": view.stroke,
    }
    if view.discrete_width is not None:
        view_data["discreteWidth"] = view.discrete_width
    if view.discrete_height is not None:
        view_data["discreteHeight"] = view.discrete_height
    data["view"] = view_data

    # Text mark (data labels, annotations) — renamed from label to marks.text
    text_mark = chart_style_context.marks.text
    tf_text = text_mark.font
    text_data: dict[str, Any] = {}
    # All TextMarkStyle fields are cascade tier sentinels — only emit when set.
    if text_mark.align is not None:
        text_data["align"] = text_mark.align
    if tf_text.family is not None:
        text_data["font"] = tf_text.family
    if tf_text.color is not None:
        text_data["fill"] = tf_text.color  # VL text mark uses fill for color
    if tf_text.size is not None:
        text_data["fontSize"] = tf_text.size
    if tf_text.weight is not None:
        text_data["fontWeight"] = tf_text.weight
    if text_data:
        data["text"] = text_data

    # Title (board/chart title styling)
    title = concrete_title_style(chart_style_context)
    title_data: dict[str, Any] = {}
    tf = title.font
    # FontStyle fields are Optional — only emit when authored (not None)
    if tf.color is not None:
        title_data["color"] = tf.color
    if tf.family is not None:
        title_data["font"] = tf.family
    if tf.size is not None:
        title_data["fontSize"] = tf.size
    if tf.weight is not None:
        title_data["fontWeight"] = tf.weight
    title_data["anchor"] = title.position.anchor
    if title.position.angle is not None:
        title_data["angle"] = title.position.angle
    if title.position.offset is not None:
        title_data["offset"] = title.position.offset
    if title.position.baseline is not None:
        title_data["baseline"] = title.position.baseline
    sf = title.subtitle.font
    if sf.color is not None:
        title_data["subtitleColor"] = sf.color
    if sf.family is not None:
        title_data["subtitleFont"] = sf.family
    if sf.size is not None:
        title_data["subtitleFontSize"] = sf.size
    if sf.weight is not None:
        title_data["subtitleFontWeight"] = sf.weight
    if title_data:
        data["title"] = title_data

    return vlc.VegaLiteConfig.model_validate(data)


__all__ = [
    "effective_vega_config",
    "style_to_vega_lite",
]
