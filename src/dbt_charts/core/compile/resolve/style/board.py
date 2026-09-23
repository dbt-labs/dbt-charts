"""Style cascade resolution engine — resolve_style() and all its helpers.

This module owns the mechanics of merging a compiled Style with zero or more
optional *Patch overlays and returning a ResolvedStyle.  It intentionally
touches every chart family in one pass; per-family fragmentation here would
hurt readability more than it helps.

Public surface:
  resolve_style()           — the primary entry point for render/
  resolve_cascaded_font()   — convert FontStyle → ResolvedFontStyle
  resolve_mark()            — merge global + family-level mark defaults
  clear_resolve_style_cache() — evict the no-patch cache after config reloads

Private helpers are prefixed with _ and not intended for callers outside
this module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from dbt_charts.core.compile.merge import merge_onto_base, merge_patches
from dbt_charts.core.compile.models.primitives import (
    FontStyle,
    ResolvedFontStyle,
    ToneLiteral,
)
from dbt_charts.core.compile.models.style.authored import (
    CalloutChartStylePatch,
    ChartsStylePatch,
    PaginationConfig,
)
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedChartDefaults,
    ResolvedLegendElementStyle,
    ResolvedLegendStyle,
    ResolvedStyle,
)
from dbt_charts.core.compile.models.style.resolved.callout import (
    ResolvedCalloutElementStyle,
    ResolvedCalloutStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    CalloutChartStyle,
    ChartsStyle,
    KpiTonesStyle,
    LegendStyle,
    SparkStyle,
    Style,
    TitleStyle,
    font_weight_as_css,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit
from dbt_charts.core.compile.resolve.style.palette import color, ink_canvas, mark_ink
from dbt_charts.core.compile.resolve.style.tokens import (
    _EMOJI_MODE_TO_FAMILY,
    _append_emoji_family,
    _resolve_tokens_on_patch,
)
from dbt_charts.core.compile.vega_lite.mapping import effective_vega_config
from dbt_charts.core.fonts import font_is_tabular

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.context import ChartStyleContext


def resolve_cascaded_font(font: FontStyle, path: str = "") -> ResolvedFontStyle:
    """Convert FontStyle to ResolvedFontStyle, raising if any required field is None."""
    if (
        font.family is None
        or font.color is None
        or font.size is None
        or font.weight is None
        or font.style is None
        or font.decoration is None
        or font.case is None
        or font.line_height is None
    ):
        missing = [
            f
            for f, v in [
                ("family", font.family),
                ("color", font.color),
                ("size", font.size),
                ("weight", font.weight),
                ("style", font.style),
                ("decoration", font.decoration),
                ("case", font.case),
                ("line_height", font.line_height),
            ]
            if v is None
        ]
        raise ValueError(
            f"Cannot resolve font{' at ' + path if path else ''}: "
            f"missing required fields after cascade: {missing}"
        )
    return ResolvedFontStyle(
        family=font.family,
        color=font.color,
        size=font.size,
        weight=font.weight,
        style=font.style,
        decoration=font.decoration,
        case=font.case,
        line_height=font.line_height,
        tabular_figures=font_is_tabular(font.family),
    )


T = TypeVar("T", bound=BaseModel)


def resolve_mark(global_mark: Any, family_override: Any) -> Any:
    """Merge the global mark default with an optional family-level override.

    Implements tier-1 + tier-2 of the three-tier mark cascade (global → family).
    Board-level overrides (tier 3) are applied by build_chart_style_context if present.

    Returns ``global_mark`` unchanged when ``family_override`` is None.
    """
    if family_override is None:
        return global_mark
    return merge_onto_base(global_mark, family_override)


def _fill_none(model: T, **tokens: Any) -> T:
    """Fill only None fields of model from tokens; explicit values are preserved."""
    updates = {f: v for f, v in tokens.items() if getattr(model, f) is None}
    return model.model_copy(update=updates) if updates else model


def _seed_spark_colors(style: Style) -> Style:
    """Seed spark color fields from single_series_palette[0].

    Spark marks (table spark lines/bars, spark_bar charts) are single-series
    surfaces — one quantity per row, no color encoding.  They use the same ink
    as a single-series chart, not the UI-affordance accent.  single_series_palette
    is a list, so Inherit can't express this link; a small explicit function is the
    right tool.

    Called from both cascade entry points: ``_finalize_charts``/``_finalize_style``
    (board-level resolve) and ``build_chart_style_context()``'s per-chart re-inherit
    path in chart_context.py. Both must seed here — a table/spark_bar chart
    carrying any style patch would otherwise extract an unseeded family with
    ``spark.bar.color is None``, which render's own assert turns into a crash.
    """
    _categorical = style.charts.color.categorical
    assert (
        _categorical is not None and _categorical.single_series_palette is not None
    ), (
        "style.charts.color.categorical.single_series_palette must be populated before "
        "_seed_spark_colors is called — check that the theme YAML sets this field"
    )
    spark_color = _categorical.single_series_palette[0]
    spark = style.charts.table.spark
    new_spark = _fill_none(spark, color=spark_color).model_copy(
        update={"bar": _fill_none(spark.bar, color=spark_color)}
    )
    new_spark_bar_bar = _fill_none(style.charts.spark_bar.bar, color=spark_color)
    new_charts = style.charts.model_copy(
        update={
            "table": style.charts.table.model_copy(update={"spark": new_spark}),
            "spark_bar": style.charts.spark_bar.model_copy(
                update={"bar": new_spark_bar_bar}
            ),
        }
    )
    return style.model_copy(update={"charts": new_charts})


def _build_resolved_legend(legend: LegendStyle) -> ResolvedLegendStyle:
    return ResolvedLegendStyle(
        position=legend.position,
        direction=legend.direction,
        columns=legend.columns,
        compact_columns=legend.compact_columns,
        visible=legend.visible is not False,
        symbol_limit=legend.symbol_limit,
        values=legend.values,
        symbol_shape=legend.symbol_shape,
        symbol_fill=legend.symbol_fill,
        label=ResolvedLegendElementStyle(
            font=resolve_cascaded_font(legend.label.font, "charts.legend.label.font"),
            padding=legend.label.padding,
            max_width=legend.label.max_width,
            visible=True,
        ),
        title=ResolvedLegendElementStyle(
            font=resolve_cascaded_font(legend.title.font, "charts.legend.title.font"),
            padding=legend.title.padding,
            visible=legend.title.visible,
        ),
    )


def _callout_tone_colors_patch(tone: ToneLiteral) -> CalloutChartStylePatch:
    """Background/border/title/message colors for a callout tone.

    Colors are resolved from ``{tone}.*`` palette roles (bg, border, solid,
    text) — the same four roles the render layer used to look up itself. The
    single mapper for this concept: both ``_build_resolved_callout_chart``
    below (the board-default/error-card path) and
    ``compile/resolve/chart/simple.py::_resolve_callout`` (which must re-derive
    after a chart-local ``style.tone`` override) merge this same patch onto
    a callout style, rather than each re-implementing the four-role mapping.
    """
    return CalloutChartStylePatch.model_validate(
        {
            "background": color(f"{tone}.bg"),
            "border": {"color": color(f"{tone}.border")},
            "title": {"font": {"color": color(f"{tone}.solid")}},
            "message": {"font": {"color": color(f"{tone}.text")}},
        }
    )


def _build_resolved_callout_chart(
    callout: CalloutChartStyle,
    bold_font_weight: str,
    *,
    tone: ToneLiteral,
) -> ResolvedCalloutStyle:
    """Build a ResolvedCalloutStyle for the given tone.

    Background/border/title/message colors are resolved from ``{tone}.*``
    palette roles via ``_callout_tone_colors_patch``, once — the render layer
    used to look these up itself at every call, keyed off whichever tone a
    particular callout instance ended up with (the theme's own default, a
    chart-local override, or the runtime error-fallback's forced "negative").
    """
    callout = merge_onto_base(callout, _callout_tone_colors_patch(tone))
    return ResolvedCalloutStyle(
        preferred_width=callout.preferred_width,
        tone=tone,
        background=callout.background,
        border=callout.border,
        padding=callout.padding,
        section_gap=callout.section_gap,
        bold_font_weight=bold_font_weight,
        title=ResolvedCalloutElementStyle(
            font=resolve_cascaded_font(callout.title.font, "charts.callout.title.font"),
            y_offset=callout.title.y_offset,
        ),
        message=ResolvedCalloutElementStyle(
            font=resolve_cascaded_font(
                callout.message.font, "charts.callout.message.font"
            ),
            y_offset=callout.message.y_offset,
        ),
    )


def _build_chart_style_context(
    charts: ChartsStyle,
    base_background: str,
    font_family: str | None,
    title: TitleStyle,
    spark: SparkStyle,
    pagination: PaginationConfig | None,
    bold_font_weight: str,
    palettes: Mapping[str, str],
    roles: dict[str, str],
    tones: KpiTonesStyle,
    pre_style: Style,
    charts_board_overrides: ChartsStylePatch,
    card_padding: float,
    formats: dict[str, str] | None = None,
    background_authored: bool = True,
) -> ChartStyleContext:
    # Deferred: a module-level import here would cycle back through
    # context.py -> resolved._base/.callout -> resolved/__init__.py ->
    # this module. By the time this function runs, both modules are
    # fully initialized.
    from dbt_charts.core.compile.models.style.context import ChartStyleContext

    # Axis/legend/callout are type-transformed; the rest are supplied by params or
    # copied directly from charts where the field name matches.
    _AXIS_LEGEND_ERROR = {
        "axis",
        "axis_x",
        "axis_y",
        "axis_quantitative",
        "legend",
        "callout",
        "callout_error",
    }
    _FROM_PARAMS = {
        "font_family",
        "title",
        "spark",
        "pagination",
        "formats",
        "background",
        "dashes",
        "palettes",
        "roles",
        "tones",
        "pre_style",
        "charts_board_overrides",
        "card_padding",
    }
    # Chart-local-only fields that have no representation on ChartsStyle —
    # they're populated only by build_chart_style_context from a chart's
    # ChartStylePatch. Default to None at theme-resolve time.
    # "palette" and "single_series_palette" are excluded because they derive from
    # charts.color.categorical below; "requested_alias_palette" derives from the
    # same source.
    _CHART_LOCAL_ONLY = {
        "palette",
        "dark_companion_palette",
        "ink_canvas",
        "single_series_palette",
        "requested_alias_palette",
        "axis_overrides_global",
        "axis_overrides_x",
        "axis_overrides_y",
        "axis_overrides_quantitative",
        "axis_overrides_band",
        "color",
        # Board-wide, and only knowable after execute — filled in post-resolve
        # by execute.category_colors, never read off ChartsStyle.
        "category_colors",
        # Wired explicitly below: the field is named differently on ChartsStyle
        # (`category_colors`) than on ChartStyleContext (`category_color_pins`,
        # to stay distinct from the planned-scales field above), so the
        # generic same-name passthrough can't reach it.
        "category_color_pins",
    }
    passthrough = {
        name: getattr(charts, name)
        for name in (f.name for f in dataclasses.fields(ChartStyleContext))
        if name not in _AXIS_LEGEND_ERROR
        and name not in _FROM_PARAMS
        and name not in _CHART_LOCAL_ONLY
    }
    # palette and single_series_palette derive from charts.color.categorical (theme-required).
    _color = charts.color
    _categorical_obj = _color.categorical
    assert _categorical_obj is not None, (
        "ChartsStyle.color.categorical must be populated by the theme; got None — "
        "check that the theme YAML sets charts.color.categorical"
    )
    _palette = _categorical_obj.palette
    assert _palette is not None, (
        "ChartsStyle.color.categorical.palette must be populated by the theme; got None — "
        "check that the theme YAML sets charts.color.categorical.palette"
    )
    resolved_palette = _palette if isinstance(_palette, list) else [_palette]
    _ssp = _categorical_obj.single_series_palette
    assert _ssp is not None, (
        "ChartsStyle.color.categorical.single_series_palette must be populated by the theme; "
        "got None — check that the theme YAML sets charts.color.categorical.single_series_palette"
    )
    resolved_single_series_palette = _ssp if isinstance(_ssp, list) else [_ssp]
    # apply_inherit fills charts.background from Style.background before we get here.
    assert charts.background is not None, (
        "charts.background must be filled by apply_inherit(Inherit(from_path='Style.background')); "
        "got None — missing Inherit marker or apply_inherit not called"
    )
    # An authored background composites exactly once over the canvas beneath
    # it. A scope that only INHERITS its background (charts.background here
    # merely echoes the parent's own value, unauthored at this scope) adds
    # nothing -- ink is derived against what an author actually asked to
    # paint, not repainted per inheriting layer (that's a render concern,
    # tracked separately, not modeled into ink). base_background IS the
    # parent's own already-composited ink_canvas in that case; inherit it
    # verbatim instead of compositing the echoed value over it again.
    board_ink_canvas = (
        ink_canvas(charts.background, base_background)
        if background_authored
        else base_background
    )
    return ChartStyleContext(
        **passthrough,
        palette=resolved_palette,
        dark_companion_palette=tuple(
            mark_ink(c, board_ink_canvas) for c in resolved_palette
        ),
        ink_canvas=board_ink_canvas,
        single_series_palette=resolved_single_series_palette,
        requested_alias_palette=_categorical_obj.requested_alias_palette,
        dashes=charts.dashes if charts.dashes is not None else [],
        category_color_pins=charts.category_colors,
        # axis is a raw BaseAxisStyle passthrough — no eager resolution here.
        # resolved_axis_style() in axis_cascade.py merges it with channel-typed
        # axis_x/axis_y at emit time, seeding channel-specific fields from the typed value.
        axis=charts.axis,
        axis_x=charts.axis_x,
        axis_y=charts.axis_y,
        axis_quantitative=charts.axis_quantitative,
        legend=_build_resolved_legend(charts.legend),
        callout=_build_resolved_callout_chart(
            charts.callout, bold_font_weight, tone=charts.callout.tone
        ),
        callout_error=_build_resolved_callout_chart(
            charts.callout, bold_font_weight, tone="negative"
        ),
        font_family=font_family,
        title=title,
        spark=spark,
        pagination=pagination,
        background=charts.background,
        formats=formats,
        palettes=palettes,
        roles=roles,
        tones=tones,
        pre_style=pre_style,
        charts_board_overrides=charts_board_overrides,
        card_padding=card_padding,
    )


_RESOLVED_STYLE_CACHE: dict[int, tuple[Style, ResolvedStyle, ChartStyleContext]] = {}


def clear_resolve_style_cache() -> None:
    """Clear cached no-patch resolved styles after config reloads."""
    _RESOLVED_STYLE_CACHE.clear()


def _merge_style(
    base: Style,
    *patches: Any,  # type-state: explicit_any — heterogeneous cascade patch fragments
) -> tuple[Style, ChartsStylePatch]:
    """Deep-merge patches onto base. No apply_inherit — pure merge only.

    Callers that need a fully-cascaded ResolvedStyle call _finalize_style() on
    the result.

    The base comes from ``get_theme_style``, which already ran
    ``_resolve_self_tokens`` and ``_resolve_color_tokens`` at theme-load time.
    Pre-resolving patches before the deep-merge means the merged result carries
    only resolved hex values.

    Also accumulates the boards' authored ``charts`` sub-patch and returns it
    alongside the merged ``Style`` — distinct from ``merged.charts.*``, which
    commingles board leaves with the theme's own values.  ``_merge_axis_cascade``
    reads the accumulator as board layers 6-9.
    """
    merged: Style = base
    # model_construct() seeds a genuinely empty patch with model_fields_set
    # empty, exactly like merge_patches's own construction.
    acc = ChartsStylePatch.model_construct()
    for patch in patches:
        resolved_patch = _resolve_tokens_on_patch(patch, base)
        merged = merge_onto_base(merged, resolved_patch)
        if resolved_patch.charts is not None:
            acc = merge_patches(acc, resolved_patch.charts, nested=False)
    return merged, acc


def _finalize_chart_style_context(
    cascaded: Style,
    pre_style: Style,
    charts_board_overrides: ChartsStylePatch,
    base_background: str,
    background_authored: bool = True,
) -> ChartStyleContext:
    """Build ChartStyleContext from an already-seeded, already-cascaded Style.

    Used by ``_finalize_style()`` for the initial board-level resolve, after it
    has run ``_seed_spark_colors``/``_append_emoji_family`` on ``cascaded``.
    ``build_chart_style_context()`` in chart_context.py (the per-chart re-inherit
    path) does NOT call this helper — it patches an already-built
    ``ChartStyleContext`` via ``dataclasses.replace`` instead of rebuilding
    one — but it MUST independently call ``_seed_spark_colors`` on its own
    cascaded pre-inherit tree before extracting families, for the same reason:
    skipping it would silently drop spark-color seeding for chart-local style
    overrides on ``table``/``spark_bar``.

    ``cascaded`` must already be the output of apply_inherit(...).
    """
    resolved_root_font = resolve_cascaded_font(cascaded.font, "font")
    if cascaded.palettes is None or cascaded.roles is None:
        raise ValueError(
            "style.palettes / style.roles are unset after the theme cascade; "
            "the _base theme seeds both for every built-in theme."
        )
    return _build_chart_style_context(
        cascaded.charts,
        base_background,
        font_family=resolved_root_font.family,
        title=cascaded.title,
        spark=cascaded.charts.table.spark,
        pagination=cascaded.charts.table.pagination,
        bold_font_weight=font_weight_as_css(cascaded.text.bold.weight),
        palettes=cascaded.palettes,
        roles=cascaded.roles,
        tones=cascaded.tones,
        pre_style=pre_style,
        formats=cascaded.formats,
        charts_board_overrides=charts_board_overrides,
        card_padding=cascaded.frame.card_padding,
        background_authored=background_authored,
    )


def _finalize_style(
    merged: Style,
    charts_board_overrides: ChartsStylePatch,
    base_background: str,
    background_authored: bool = True,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Apply a single inherit cascade to a merged Style; return final style + context.

    One apply_inherit call over the full graph fills all InheritSlot links in one
    pass. The pre-inherit Style feeds ``ChartStyleContext.pre_style`` so per-chart
    style patches can re-run the full graph against a chart-local override
    without touching board-global state.
    """
    pre_style = merged
    cascaded = apply_inherit(pre_style, get_inherit_graph())
    cascaded = _seed_spark_colors(cascaded)
    emoji_family = _EMOJI_MODE_TO_FAMILY.get(cascaded.font.emoji)
    if emoji_family is not None:
        cascaded = _append_emoji_family(cascaded, emoji_family)
    chart_context = _finalize_chart_style_context(
        cascaded,
        pre_style,
        charts_board_overrides,
        base_background,
        background_authored,
    )
    # ResolvedChartDefaults is exactly ChartStyleContext minus its sparse/
    # cascade-only fields (see the class docstring) — every remaining field
    # name matches chart_context 1:1.
    chart_defaults = ResolvedChartDefaults(
        **{
            f.name: getattr(chart_context, f.name)
            for f in dataclasses.fields(ResolvedChartDefaults)
        }
    )

    _TRANSFORMED = {"font", "emoji_mode", "vega_config", "chart_defaults"}
    passthrough = {
        name: getattr(cascaded, name)
        for name in (f.name for f in dataclasses.fields(ResolvedStyle))
        if name not in _TRANSFORMED
    }
    resolved_root_font = resolve_cascaded_font(cascaded.font, "font")
    from dbt_charts.core.compile.config import get_config

    base_vl = get_config().vega.config.model_dump(by_alias=True, exclude_unset=True)

    resolved = ResolvedStyle(
        **passthrough,
        font=resolved_root_font,
        emoji_mode=cascaded.font.emoji,
        vega_config=effective_vega_config(base_vl, chart_context),
        chart_defaults=chart_defaults,
    )
    return resolved, chart_context


def resolve_style(base: Style, *patches: Any) -> ResolvedStyle:
    """Merge compiled base with patches, apply cascade, return the final board style."""
    return resolve_style_and_context(base, *patches)[0]


def resolve_chart_style_context(base: Style, *patches: Any) -> ChartStyleContext:
    """Merge compiled base with patches, apply cascade, return the chart cascade context.

    Consumed only by runtime chart resolution (``compile/resolve/chart/``,
    ``compile/resolve/style/chart_context.py``, ``compile/support_table.py``) — never by render.
    """
    return resolve_style_and_context(base, *patches)[1]


def resolve_style_and_context(
    base: Style,
    *patches: Any,  # type-state: explicit_any — heterogeneous patch types (StylePatch, ChartsStylePatch, ...) merged in sequence
    base_background: str | None = None,
    background_authored: bool = True,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Shared cascade entry point behind resolve_style()/resolve_chart_style_context().

    Both public getters run the same cascade once and read their half of the
    result — the two products are always built together, so no-patch calls
    share one cache entry keyed on `id(base)`.

    `base_background` is the canvas this scope's own background composites
    over. Every root-board resolve leaves it unset, so `base.background` --
    the raw theme's own canvas -- is what a root paints beneath its own
    background, correctly. A nested board paints on top of what its PARENT
    scope actually composited, not the theme's raw canvas underneath that --
    `compile_board_resolved_style` (normalize/dispatch.py) passes the
    parent's own `ChartStyleContext.ink_canvas` here for that case.

    `background_authored` is False only for a nested board that authored no
    `background:` of its own (`compile_board_resolved_style` checks the raw,
    unmerged patch) -- then `base_background` is inherited verbatim as
    `ink_canvas`, uncomposited: an authored background composites exactly
    once, and a scope that only inherits one adds nothing.
    """
    background = base.background if base_background is None else base_background
    if patches:
        return _finalize_style(
            *_merge_style(base, *patches), background, background_authored
        )

    # The no-patch fast path is only ever reached today with base_background
    # unset (a nested board with a cascade to run always has own_patch, so it
    # takes the patches branch above) -- id(base) alone is the correct key.
    cache_key = id(base)
    cached = _RESOLVED_STYLE_CACHE.get(cache_key)
    if cached is not None and cached[0] is base:
        return cached[1], cached[2]

    resolved, chart_context = _finalize_style(*_merge_style(base), background)
    _RESOLVED_STYLE_CACHE[cache_key] = (base, resolved, chart_context)
    return resolved, chart_context
