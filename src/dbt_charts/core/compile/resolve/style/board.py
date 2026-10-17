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

from dbt_charts import _engine
from dbt_charts.core.colors import BY_TONE
from dbt_charts.core.compile.merge import merge_onto_base, merge_patches
from dbt_charts.core.compile.models.markers import AUTO
from dbt_charts.core.compile.models.primitives import (
    DEFAULT_REGIONAL,
    FontStyle,
    FormatTable,
    LanguageCode,
    ResolvedFontStyle,
    ToneLiteral,
    parse_box_shadow,
)
from dbt_charts.core.compile.models.style.authored import (
    EndpointLabelsConfig,
    PaginationConfig,
    StylePatch,
    SupportTableStylePatch,
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
    AreaChartStyle,
    BarChartStyle,
    CalloutChartStyle,
    ChartsStyle,
    ChartTitleStyle,
    GlobalMarksStyle,
    HeatmapChartStyle,
    HistogramChartStyle,
    KpiChartStyle,
    KpiTonesStyle,
    LegendStyle,
    LineChartStyle,
    PieChartStyle,
    ScatterChartStyle,
    SparkStyle,
    Style,
    SupportTableStyle,
    font_weight_as_css,
)
from dbt_charts.core.compile.resolve.style.fonts import (
    expand_named_fonts,
    resolve_registry,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import (
    InheritGraph,
    get_inherit_graph,
)
from dbt_charts.core.compile.resolve.style.inherit_resolver import (
    apply_inherit,
    assert_style_total,
    defer_base_none_children,
    settled,
    unset_autos,
    unset_fields_error,
)
from dbt_charts.core.compile.resolve.style.legend_position import (
    decide_legend_direction,
    decide_legend_position,
)
from dbt_charts.core.compile.resolve.style.palette import (
    color,
    ink_canvas,
    is_translucent,
    mark_ink,
)
from dbt_charts.core.compile.resolve.style.tokens import (
    _EMOJI_MODE_TO_FAMILY,
    _append_emoji_family,
    _resolve_self_tokens,
    _resolve_tokens_on_patch,
)
from dbt_charts.core.compile.resolve.style.typography import concrete_title_style
from dbt_charts.core.compile.vega_lite.mapping import effective_vega_config
from dbt_charts.core.fonts import font_is_tabular
from dbt_charts.core.text.currencies import CurrencyCode

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.context import ChartStyleContext


def resolve_cascaded_font(font: FontStyle) -> ResolvedFontStyle:
    family = settled(font.family)
    return ResolvedFontStyle(
        family=family,
        color=settled(font.color),
        size=settled(font.size),
        weight=settled(font.weight),
        style=settled(font.style),
        decoration=settled(font.decoration),
        case=settled(font.case),
        line_height=settled(font.line_height),
        tabular_figures=font_is_tabular(family),
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
    categorical = style.charts.color.categorical
    if categorical is None or categorical.single_series_palette is None:
        raise unset_fields_error(
            ["Style.charts.color.categorical.single_series_palette"]
        )
    spark_color = categorical.single_series_palette[0]
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
    position = decide_legend_position(legend.position, top_strip=False)
    return ResolvedLegendStyle(
        position=position,
        direction=decide_legend_direction(legend.direction, position.edge),
        columns=legend.columns,
        compact_columns=legend.compact_columns,
        row=legend.row,
        visible=legend.visible is not False,
        symbol_limit=legend.symbol_limit,
        values=legend.values,
        symbol_shape=legend.symbol_shape,
        symbol_fill=legend.symbol_fill,
        label=ResolvedLegendElementStyle(
            font=resolve_cascaded_font(legend.label.font),
            padding=legend.label.padding,
            max_width=legend.label.max_width,
            visible=True,
        ),
        title=ResolvedLegendElementStyle(
            font=resolve_cascaded_font(legend.title.font),
            padding=legend.title.padding,
            visible=None if legend.title.visible == "auto" else legend.title.visible,
        ),
    )


def _build_resolved_callout_chart(
    callout: CalloutChartStyle, bold_font_weight: str, tone: ToneLiteral
) -> ResolvedCalloutStyle:
    """Resolve a callout style for ``tone``.

    Each color the cascade left as ``by_tone`` (background, border color,
    title and message font color) takes the tone's ``{tone}.*`` palette role
    (bg, border, solid, text); any other value is the author's and is kept.
    """

    def tone_color(value: str, role: str) -> str:
        return color(f"{tone}.{role}") if value == BY_TONE else value

    def tone_font(font: FontStyle, role: str) -> ResolvedFontStyle:
        if font.color is not None:
            font = font.model_copy(update={"color": tone_color(font.color, role)})
        return resolve_cascaded_font(font)

    return ResolvedCalloutStyle(
        preferred_width=callout.preferred_width,
        tone=tone,
        background=tone_color(callout.background, "bg"),
        border=callout.border.model_copy(
            update={"color": tone_color(callout.border.color, "border")}
        ),
        padding=callout.padding,
        section_gap=callout.section_gap,
        bold_font_weight=bold_font_weight,
        title=ResolvedCalloutElementStyle(
            font=tone_font(callout.title.font, "solid"),
            y_offset=callout.title.y_offset,
        ),
        message=ResolvedCalloutElementStyle(
            font=tone_font(callout.message.font, "text"),
            y_offset=callout.message.y_offset,
        ),
    )


def _format_table(cascaded: Style, currency: CurrencyCode | None) -> FormatTable:
    return FormatTable(
        aliases=cascaded.formats if cascaded.formats is not None else {},
        currency=currency,
    )


def _build_chart_style_context(
    charts: ChartsStyle,
    base_background: str,
    own_background: str,
    font_family: str | None,
    title: ChartTitleStyle,
    spark: SparkStyle,
    pagination: PaginationConfig | None,
    bold_font_weight: str,
    palettes: Mapping[str, str],
    roles: dict[str, str],
    tones: KpiTonesStyle,
    pre_style: Style,
    cascaded: Style,
    card_padding: float,
    heading_sizes: list[float],
    formats: FormatTable | None = None,
) -> ChartStyleContext:
    # Deferred: a module-level import here would cycle back through
    # context.py -> resolved._base/.callout -> resolved/__init__.py ->
    # this module. By the time this function runs, both modules are
    # fully initialized.
    from dbt_charts.core.compile.models.style.context import ChartStyleContext

    # Axis/legend/callout_error are type-transformed; the rest are supplied by
    # params or copied directly from charts where the field name matches.
    _AXIS_LEGEND_ERROR = {
        "axis",
        "axis_x",
        "axis_y",
        "axis_quantitative",
        "legend",
        "callout_error",
    }
    _FROM_PARAMS = {
        "font_family",
        "title",
        "spark",
        "pagination",
        "formats",
        "background",
        "bold_font_weight",
        "dashes",
        "palettes",
        "roles",
        "tones",
        "pre_style",
        "cascaded",
        "card_padding",
        "heading_sizes",
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
        "board_canvas",
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
    _categorical_obj = settled(charts.color.categorical)
    _palette = settled(_categorical_obj.palette)
    resolved_palette = _palette if isinstance(_palette, list) else [_palette]
    _ssp = settled(_categorical_obj.single_series_palette)
    resolved_single_series_palette = _ssp if isinstance(_ssp, list) else [_ssp]
    # Two canvases, per the model: the BOARD canvas is this board's own fill
    # (style.background -- non-inherited, transparent by default on a
    # nested board that didn't author one) composited over its parent's
    # board canvas; compositing transparent is a no-op, so an unauthored
    # nested board's canvas equals its parent's unchanged. The CHART canvas
    # is the card-fill default (style.charts.background, which cascades
    # independently of style.background) composited over the board canvas
    # -- what chart ink helpers read.
    #
    # charts.background composites onto the board canvas only when SOME
    # scope in this lineage explicitly wrote style.charts.background: --
    # checked on pre_style (the pre-apply_inherit tree), where an
    # unauthored charts.background is still None. When nothing authored it,
    # `charts.background` above is merely the Inherit(from_path=
    # "Style.background") echo of own_background, already fully present in
    # board_canvas; compositing that echo again would double it.
    card_authored = pre_style.charts.background is not None
    board_canvas, chart_canvas = _canvases(
        own_background,
        charts.background if card_authored else None,
        base_background,
    )
    # A translucent echo is not repainted as the card fill: the board already
    # paints it. An opaque echo covers identical pixels, so it stays.
    card_fill = settled(charts.background)
    if not card_authored and is_translucent(card_fill):
        card_fill = "transparent"
    return ChartStyleContext(
        **passthrough,
        palette=resolved_palette,
        dark_companion_palette=tuple(
            mark_ink(c, chart_canvas) for c in resolved_palette
        ),
        ink_canvas=chart_canvas,
        board_canvas=board_canvas,
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
        callout_error=_build_resolved_callout_chart(
            charts.callout, bold_font_weight, "negative"
        ),
        bold_font_weight=bold_font_weight,
        font_family=font_family,
        title=title,
        spark=spark,
        pagination=pagination,
        background=card_fill,
        formats=formats,
        palettes=palettes,
        roles=roles,
        tones=tones,
        pre_style=pre_style,
        cascaded=cascaded,
        card_padding=card_padding,
        heading_sizes=heading_sizes,
    )


_RESOLVED_STYLE_CACHE: dict[
    tuple[int, CurrencyCode | None, LanguageCode],
    tuple[Style, ResolvedStyle, ChartStyleContext],
] = {}


def clear_resolve_style_cache() -> None:
    """Clear cached no-patch resolved styles after config reloads."""
    _RESOLVED_STYLE_CACHE.clear()


def _links_under(paths: list[str]) -> InheritGraph:
    return {
        path: parent
        for path, parent in get_inherit_graph().items()
        if any(path == held or path.startswith(f"{held}.") for held in paths)
    }


def _pie_reaches_donut(
    patch: Any,  # type-state: explicit_any — heterogeneous cascade patch fragment
) -> Any:  # type-state: explicit_any — the same patch, donut layered
    """Layer a patch's ``charts.pie`` under its ``charts.donut``.

    A layer's pie style reaches donuts, and its own donut style wins over it,
    so a board's pie beats the theme's donut default.
    """
    charts = patch.charts
    if charts is None or charts.pie is None:
        return patch
    donut = (
        charts.pie if charts.donut is None else merge_patches(charts.pie, charts.donut)
    )
    return patch.model_copy(
        update={"charts": charts.model_copy(update={"donut": donut})}
    )


def merge_style(base: Style, patch: StylePatch | None = None) -> Style:
    """Deep-merge a patch onto base.

    A partial write into an empty slot that inherits from a parent is merged
    only after the slot is filled from that parent, so it never has to be
    complete on its own.

    The base comes from ``get_theme_style``, which already ran
    ``_resolve_color_tokens`` at theme-load time. Pre-resolving the patch before
    the deep-merge resolves its color tokens to hex; theme-self tokens stay
    in place for ``_resolve_canvas_tokens``.
    """
    if patch is None:
        return base
    resolved_patch = _pie_reaches_donut(_resolve_tokens_on_patch(patch, base))
    remainder, held_patch, held = defer_base_none_children(
        base, resolved_patch, "Style"
    )
    merged = merge_onto_base(base, remainder)
    if held:
        merged = apply_inherit(merged, _links_under(held))
        merged = merge_onto_base(merged, held_patch)
    return merged


def _canvases(
    background: str, charts_background: str | None, base_background: str
) -> tuple[str, str]:
    """Return ``(board_canvas, chart_canvas)``: the page fill over its parent's
    canvas, then an authored card fill over that. Always opaque hex."""
    board_canvas = ink_canvas(background, base_background)
    chart_canvas = (
        board_canvas
        if charts_background is None
        else ink_canvas(charts_background, board_canvas)
    )
    return board_canvas, chart_canvas


def _resolve_canvas_tokens(
    base: Style,
    merged: Style,
    base_background: str,
) -> Style:
    """Substitute theme-self tokens in the merged Style.

    Knockouts sit on the card, so the canvas is the chart canvas: a layer or
    board that changes the page or card fill re-colors every knockout.
    """
    composited = _canvases(
        merged.background, merged.charts.background, base_background
    )[1]
    # `ink_canvas` lowercases; re-spelling an opaque `#FFFFFF` canvas would
    # recolor no pixel but churn every golden, so a candidate that is the
    # canvas keeps its own spelling.
    canvas = next(
        (
            c
            for c in (
                merged.charts.background,
                merged.background,
                base.background,
                base_background,
            )
            if c is not None and c.lower() == composited
        ),
        composited,
    )
    return _resolve_self_tokens(merged, canvas)


def _finalize_chart_style_context(
    cascaded: Style,
    pre_style: Style,
    base_background: str,
    currency: CurrencyCode | None,
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
    resolved_root_font = resolve_cascaded_font(cascaded.font)
    return _build_chart_style_context(
        cascaded.charts,
        base_background,
        cascaded.background,
        font_family=resolved_root_font.family,
        title=ChartTitleStyle.model_validate(
            settled(cascaded.charts.title).model_dump()
        ),
        spark=cascaded.charts.table.spark,
        pagination=cascaded.charts.table.pagination,
        bold_font_weight=font_weight_as_css(cascaded.text.bold.weight),
        palettes=settled(cascaded.palettes),
        roles=settled(cascaded.roles),
        tones=cascaded.tones,
        pre_style=pre_style,
        cascaded=cascaded,
        formats=_format_table(cascaded, currency),
        card_padding=cascaded.frame.card_padding,
        heading_sizes=pre_style.heading_ramp(),
    )


def bake_kpi_support_ink(
    kpi: KpiChartStyle, tones: KpiTonesStyle, muted: str
) -> KpiChartStyle:
    """Replace the support row's ``auto`` inks with concrete colors.

    The row takes the tone's color, else the muted ink. The glyph keeps the tone
    over an authored row color, and follows the row without one.
    """
    support = kpi.support
    tone = None if support.tone is None else getattr(tones, support.tone)
    row_color = support.font.color
    if row_color == AUTO:
        row_color = tone or muted
    glyph_color = support.glyph.font.color
    if glyph_color == AUTO:
        glyph_color = tone or row_color
    glyph_font = support.glyph.font.model_copy(update={"color": glyph_color})
    return kpi.model_copy(
        update={
            "support": support.model_copy(
                update={
                    "font": support.font.model_copy(update={"color": row_color}),
                    "glyph": support.glyph.model_copy(update={"font": glyph_font}),
                }
            )
        }
    )


@dataclasses.dataclass(frozen=True)
class ConcreteFamilyDefaults:
    """Board-wide family styles with the engine-decided leaves made concrete."""

    marks: GlobalMarksStyle
    pie: PieChartStyle
    donut: PieChartStyle
    kpi: KpiChartStyle
    support_table: SupportTableStyle
    bar: BarChartStyle
    line: LineChartStyle
    area: AreaChartStyle
    heatmap: HeatmapChartStyle
    histogram: HistogramChartStyle
    scatter: ScatterChartStyle


def concrete_family_defaults(
    chart_context: ChartStyleContext,
) -> ConcreteFamilyDefaults:
    """An `auto` endpoint rail is on, and an `auto` support-table ink is the
    table's neutral color."""
    from dbt_charts.core.compile.support_table import bake_neutral_ink  # noqa: PLC0415

    def rail_on(config: EndpointLabelsConfig) -> EndpointLabelsConfig:
        return (
            config.model_copy(update={"visible": True})
            if config.visible == "auto"
            else config
        )

    def table(style: SupportTableStyle) -> SupportTableStyle:
        return bake_neutral_ink(style)[0]

    def family_table(style: SupportTableStylePatch | None) -> SupportTableStyle | None:
        return None if style is None else table(style)

    bar, line, area = chart_context.bar, chart_context.line, chart_context.area
    heatmap, histogram = chart_context.heatmap, chart_context.histogram
    scatter = chart_context.scatter

    pie = chart_context.pie
    return ConcreteFamilyDefaults(
        marks=chart_context.marks,
        kpi=bake_kpi_support_ink(
            chart_context.kpi, chart_context.tones, chart_context.cascaded.muted
        ),
        pie=pie,
        donut=(
            pie
            if chart_context.donut is None
            else merge_onto_base(pie, chart_context.donut)
        ),
        support_table=table(chart_context.support_table),
        bar=bar.model_copy(
            update={
                "support_table": family_table(bar.support_table),
                "endpoint_labels": rail_on(bar.endpoint_labels),
            }
        ),
        line=line.model_copy(
            update={
                "support_table": family_table(line.support_table),
                "endpoint_labels": rail_on(line.endpoint_labels),
            }
        ),
        area=area.model_copy(
            update={
                "support_table": family_table(area.support_table),
                "endpoint_labels": rail_on(area.endpoint_labels),
            }
        ),
        heatmap=heatmap.model_copy(
            update={"support_table": family_table(heatmap.support_table)}
        ),
        histogram=histogram.model_copy(
            update={"support_table": family_table(histogram.support_table)}
        ),
        scatter=scatter.model_copy(
            update={"support_table": family_table(scatter.support_table)}
        ),
    )


# The theme's footer text, timestamp format and placeholder overlay text are
# `auto` until an author writes their own; the board's language supplies the
# words once, here, so nothing downstream reads the sentinel. The brand stays
# untranslated inside them, and the timestamp becomes a strftime string around
# the time and date.
_BRAND = "dbt Charts"


def _bake_theme_words(cascaded: Style, language: LanguageCode) -> Style:
    def word(message_id: str, **args: str) -> str:
        return _engine.message(language, message_id, None, args)

    footer, stamp = cascaded.footer, cascaded.timestamp
    overlay = cascaded.placeholder.overlay
    if footer.text == AUTO:
        footer = footer.model_copy(
            update={"text": word("footer.made_with", brand=_BRAND)}
        )
    if stamp.format == AUTO:
        stamp = stamp.model_copy(
            update={
                "format": word("footer.data_as_of", time="%H:%M %Z", date="%-d %b %Y")
            }
        )
    if overlay.text == AUTO:
        overlay = overlay.model_copy(update={"text": word("placeholder.add_data")})
    return cascaded.model_copy(
        update={
            "footer": footer,
            "timestamp": stamp,
            "placeholder": cascaded.placeholder.model_copy(update={"overlay": overlay}),
        }
    )


def _finalize_style(
    merged: Style,
    base_background: str,
    currency: CurrencyCode | None,
    language: LanguageCode,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Apply a single inherit cascade to a merged Style; return final style + context.

    One apply_inherit call over the full graph fills all InheritSlot links in one
    pass. The pre-inherit Style feeds ``ChartStyleContext.pre_style`` so per-chart
    style patches can re-run the full graph against a chart-local override
    without touching board-global state.
    """
    registry = merged.fonts
    assert registry is not None, "Style._validate_fonts requires style.fonts"
    registry = resolve_registry(registry)
    pre_style = expand_named_fonts(
        merged.model_copy(update={"fonts": registry}), registry
    )
    cascaded = apply_inherit(pre_style, get_inherit_graph())
    cascaded = _seed_spark_colors(cascaded)
    cascaded = _bake_theme_words(cascaded, language)
    emoji_family = _EMOJI_MODE_TO_FAMILY.get(cascaded.font.emoji)
    if emoji_family is not None:
        cascaded = _append_emoji_family(cascaded, emoji_family)
    assert_style_total(cascaded)
    cascaded = unset_autos(cascaded)
    chart_context = _finalize_chart_style_context(
        cascaded,
        pre_style,
        base_background,
        currency,
    )
    # ResolvedChartDefaults is exactly ChartStyleContext minus its sparse/
    # cascade-only fields (see the class docstring) — every remaining field
    # name matches chart_context 1:1.
    concrete = concrete_family_defaults(chart_context)
    overridden = {"title", *(f.name for f in dataclasses.fields(concrete))}
    chart_defaults = ResolvedChartDefaults(
        **{
            f.name: getattr(chart_context, f.name)
            for f in dataclasses.fields(ResolvedChartDefaults)
            if f.name not in overridden
        },
        title=concrete_title_style(chart_context),
        marks=concrete.marks,
        pie=concrete.pie,
        donut=concrete.donut,
        kpi=concrete.kpi,
        support_table=concrete.support_table,
        bar=concrete.bar,
        line=concrete.line,
        area=concrete.area,
        heatmap=concrete.heatmap,
        histogram=concrete.histogram,
        scatter=concrete.scatter,
    )

    _TRANSFORMED = {"font", "emoji_mode", "vega_config", "chart_defaults", "box_shadow"}
    passthrough = {
        name: getattr(cascaded, name)
        for name in (f.name for f in dataclasses.fields(ResolvedStyle))
        if name not in _TRANSFORMED
    }
    resolved_root_font = resolve_cascaded_font(cascaded.font)
    resolved = ResolvedStyle(
        **passthrough,
        font=resolved_root_font,
        emoji_mode=cascaded.font.emoji,
        box_shadow=(
            None
            if cascaded.box_shadow is None
            else parse_box_shadow(cascaded.box_shadow)
        ),
        vega_config=effective_vega_config(chart_context),
        chart_defaults=chart_defaults,
    )
    return resolved, chart_context


def resolve_style(
    base: Style,
    patch: StylePatch | None = None,
    *,
    language: LanguageCode = DEFAULT_REGIONAL.resolved_language,
) -> ResolvedStyle:
    """Merge compiled base with a patch, apply cascade, return the final board style."""
    return resolve_style_and_context(base, patch, language=language)[0]


def resolve_chart_style_context(
    base: Style,
    patch: StylePatch | None = None,
    *,
    language: LanguageCode = DEFAULT_REGIONAL.resolved_language,
) -> ChartStyleContext:
    """Merge compiled base with a patch, apply cascade, return the chart cascade context.

    Consumed only by runtime chart resolution (``compile/resolve/chart/``,
    ``compile/resolve/style/chart_context.py``, ``compile/support_table.py``) — never by render.
    """
    return resolve_style_and_context(base, patch, language=language)[1]


def resolve_style_and_context(
    base: Style,
    patch: StylePatch | None = None,
    *,
    base_background: str | None = None,
    currency: CurrencyCode | None = DEFAULT_REGIONAL.currency,
    language: LanguageCode = DEFAULT_REGIONAL.resolved_language,
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
    parent's own `ChartStyleContext.board_canvas` here for that case.

    `style.background` is CSS `background-color`: not inherited, initial
    value transparent. `compile_board_resolved_style` pins a nested board's
    background patch to `"transparent"` when that board's own raw style
    didn't set one, so the cascaded `Style.background` this function
    composites is already the correct value to paint -- compositing
    transparent over `base_background` is a no-op, so an unauthored nested
    scope's canvas equals its parent's unchanged with no separate flag.
    """
    background = base.background if base_background is None else base_background
    if patch is not None:
        return _finalize_style(
            _resolve_canvas_tokens(base, merge_style(base, patch), background),
            background,
            currency,
            language,
        )

    # Reached only with base_background unset (a nested board with a cascade
    # always has own_patch), so id(base), currency and language are the whole key.
    cache_key = (id(base), currency, language)
    cached = _RESOLVED_STYLE_CACHE.get(cache_key)
    if cached is not None and cached[0] is base:
        return cached[1], cached[2]

    resolved, chart_context = _finalize_style(
        _resolve_canvas_tokens(base, merge_style(base), background),
        background,
        currency,
        language,
    )
    _RESOLVED_STYLE_CACHE[cache_key] = (base, resolved, chart_context)
    return resolved, chart_context
