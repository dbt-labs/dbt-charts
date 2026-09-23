"""Chart style cascade context — compiler working state, not a resolved value.

Stage: COMPILE (resolve, working state).

``ChartStyleContext`` is the non-``Resolved`` home for everything the chart
style cascade needs while it is still running: sparse axis overlays,
chart-local patch sentinels, per-family theme defaults still awaiting a
per-chart merge, the pre-``apply_inherit`` ``Style`` tree, and the theme's
palette/role token bindings. It is produced once per board (by
``resolve_chart_style_context()`` in ``compile/resolve/style/board.py``) and
re-merged once per chart-local style override (by
``build_chart_style_context()`` in ``compile/resolve/style/chart_context.py``)
— the merge-with-update operations that pattern implies are legal here
precisely because this type carries no ``Resolved`` prefix.

Compare ``ResolvedStyle`` (``resolved/_base.py``): final board/chrome
presentation only, construction-final, never copied-with-update. Compare the
per-family ``Resolved*Style`` types (``resolved/bar.py`` etc.): final
per-chart presentation, projected from this context by ``compile/resolve/``.

Consumers: ``compile/resolve/*`` (per-chart resolution), ``compile/resolve/
style/chart_context.py`` (the per-chart cascade), ``compile/support_table.py``
(axis-offset geometry). Normalized ``Board``/execute orchestration carry an instance for
runtime chart resolution via a separately named field
(``Board.chart_style_context``); ``ResolvedBoard`` and every render API do not
accept this type — see ``dbt-charts/src/dbt_charts/core/AGENTS.md``'s reach-back section.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from dbt_charts.core.compile.models.primitives import BorderStyle
from dbt_charts.core.compile.models.style.authored import (
    AxisXStylePatch,
    AxisYStylePatch,
    BandAxisStylePatch,
    BaseAxisStylePatch,
    ChartsStylePatch,
    PaginationConfig,
    QuantitativeAxisStylePatch,
)

# Real (non-TYPE_CHECKING) imports: ChartStyleContext is embedded as a field
# on the pydantic Board model, so pydantic eagerly builds a dataclass schema
# for it at Board's class-definition time — every field type must be a real,
# resolvable name in this module's namespace, not a deferred string. The
# compile/resolve/style/board.py -> ChartStyleContext runtime dependency
# (constructing instances) is the one broken lazily instead, via a
# function-local import.
from dbt_charts.core.compile.models.style.resolved._base import (
    ResolvedLegendStyle,
)
from dbt_charts.core.compile.models.style.resolved.callout import ResolvedCalloutStyle
from dbt_charts.core.compile.models.style.theme import (
    AreaChartStyle,
    AxisXStyle,
    AxisYStyle,
    BarChartStyle,
    BaseAxisStyle,
    GeoshapeChartStyle,
    GlobalMarksStyle,
    HeatmapChartStyle,
    HistogramChartStyle,
    HoverEmphasisStyle,
    KpiChartStyle,
    KpiTonesStyle,
    LineChartStyle,
    PaddingStyle,
    PieChartStyle,
    PointMapChartStyle,
    QuantitativeAxisStyle,
    ScatterChartStyle,
    SeriesLabelStyle,
    SparkBarChartStyle,
    SparkStyle,
    Style,
    SupportTableStyle,
    TableChartStyle,
    TitleStyle,
    TooltipStyle,
    ViewStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
    CategoryColorScale,
)


@dataclasses.dataclass(frozen=True)
class ChartStyleContext:
    """Chart style cascade working state — board-level, re-merged per chart.

    Not a resolved value: ``axis``/``axis_x``/``axis_y``/``axis_quantitative``
    are raw theme passthroughs (not yet merged with chart-local overrides),
    ``axis_overrides_*``/``color`` are chart-local patch sentinels
    (``None`` until a chart authors an override), and ``pre_style`` is the
    pre-``apply_inherit`` tree kept around so a chart-local override can
    re-run the full inherit graph. ``build_chart_style_context()`` in
    ``compile/resolve/style/chart_context.py`` is the only place that
    copies-with-update an instance of this type — legal because it is not
    ``Resolved*``.

    Board-level fields (title, pagination, font_family) are carried here for
    the VL config mapper (``style_to_vega_lite``), which takes a
    ``ChartStyleContext``, not the full ``ResolvedStyle``. They are required
    (not defaulted) to enforce that every instance is constructed by
    ``resolve_chart_style_context()``.
    """

    # --- Palette & color tokens ---
    palette: list[str]
    # label_ink(mark, canvas) for each `palette` slot (same index), for
    # label text painted over that slot's mark color. Baked once here so
    # render never calls compile.resolve.style.palette.label_ink itself.
    # This is the board palette, not a chart's effective (possibly
    # chart-local-overridden) one — support_table_attachment.py's strip ink
    # intentionally keys off the board palette; do not consolidate with
    # ResolvedSeriesLabelStyle.dark_companion_palette (chart-effective)
    # without checking that call site.
    dark_companion_palette: tuple[str, ...]
    # Theme default ink palette for non-layered, single-encoding marks.
    single_series_palette: list[str]
    # Set when `palette`/`single_series_palette` was authored as a known
    # WARN-PALETTE-UNSUPPORTED anti-pattern alias (e.g. "RdYlGn"); holds the
    # originally-requested name for the render-stage detector. None otherwise.
    requested_alias_palette: str | None
    # Dash palette for line-family marks. Empty means "no strokeDash encoding".
    dashes: list[list[int]]
    # Theme role bindings carried alongside the cascade so chart-level style
    # patches (resolved after the Style tree is gone) can resolve
    # role-indirected tokens like `category_dark[3]` / `chrome.ink` against
    # the active theme. Always concrete: `_base.yaml` seeds both blocks for
    # every built-in theme; an empty dict is the canonical "no bindings"
    # value (role tokens then fail loudly with UnknownColorError).
    palettes: Mapping[str, str]
    roles: dict[str, str]
    # Semantic tone palette (positive/negative/warning/info) — board level,
    # shared by KPI support rows and table conditional glyphs.
    tones: KpiTonesStyle

    # Authored board-wide value→color pins (``style.charts.category_colors``),
    # keyed by data field. Passed through from ``ChartsStyle.category_colors``
    # at construction time — known at compile time, unlike the planned
    # ``category_colors`` scales below (which need executed rows and are
    # filled in after execute). Two fields, two lifecycle stages of the same
    # feature: this one is the INPUT the planner reads, ``category_colors``
    # is its OUTPUT. Named apart so neither reads as a duplicate of the
    # other. Empty = no pins authored.
    category_color_pins: dict[str, CategoryColorBinding]

    # --- Board-wide sizing defaults ---
    preferred_width: float
    default_chart_height: float
    default_table_height: float
    label_usable_ratio: float
    # `style.frame.card_padding`, carried down from the board frame because a
    # resolver reasoning about a card's total height needs it: the renderer's
    # card is `aspect-ratio height + 2 * card_padding` (render/sizing.py's
    # `get_item_content_height`), so a resolver that stops at the aspect-ratio
    # half is measuring a shorter card than the one that gets drawn. Read by
    # `_resolve_bar` (resolve/chart/bar.py) for the plot-height floor. Lives
    # on the frame, not on `charts`, so it is passed in rather than picked up
    # by the `passthrough` sweep in `_build_chart_style_context`.
    card_padding: float
    # Board-wide chart dimension defaults (per-chart resolvers seed the
    # concrete ResolvedChart.aspect_ratio/min_height/max_height from these
    # when the author leaves them unset).
    aspect_ratio: float
    min_height: float
    max_height: float
    padding: PaddingStyle
    border: BorderStyle

    # --- Axis cascade state (theme layers, not yet chart-merged) ---
    # `axis` is Layer 1's channel-agnostic base (BaseAxisStyle — raw passthrough,
    # not eagerly resolved). `axis_x`/`axis_y`/`axis_quantitative` are
    # authored-only overlays — sparse by design (SkipInheritSlots means
    # apply_inherit never fills their None leaves from `axis`).
    # resolved_axis_style() merges them onto a starting AxisXStyle/AxisYStyle
    # in the canonical cascade order at emit time.
    axis: BaseAxisStyle
    axis_x: AxisXStyle
    axis_y: AxisYStyle
    axis_quantitative: QuantitativeAxisStyle

    # --- Global mark/legend/label defaults (tier 1 of the three-tier cascade) ---
    legend: ResolvedLegendStyle
    marks: GlobalMarksStyle
    view: ViewStyle
    # Series-label primitive — shared typography for endpoint and stack labels.
    series_label: SeriesLabelStyle

    # --- Per-family theme style ---
    bar: BarChartStyle
    line: LineChartStyle
    area: AreaChartStyle
    scatter: ScatterChartStyle
    pie: PieChartStyle
    geoshape: GeoshapeChartStyle
    point_map: PointMapChartStyle
    histogram: HistogramChartStyle
    heatmap: HeatmapChartStyle
    kpi: KpiChartStyle
    table: TableChartStyle
    spark: SparkStyle
    spark_bar: SparkBarChartStyle
    support_table: SupportTableStyle
    # Callout chart-family style (type: callout charts only — board-level
    # default tone, before any chart-local style.tone override).
    callout: ResolvedCalloutStyle
    # Runtime chart-error fallback card style — same family, tone forced to
    # "negative" regardless of the theme's chosen callout default.
    callout_error: ResolvedCalloutStyle

    # --- Board/board chrome carried for the VL config mapper ---
    tooltip: TooltipStyle
    # Hover-emphasis switch — board-wide, read by the JS runtime the same
    # way it reads tooltip above.
    hover_emphasis: HoverEmphasisStyle
    font_family: str | None  # root font.family; emitted as top-level VL `font`
    title: TitleStyle
    pagination: PaginationConfig | None

    # --- Pre-`apply_inherit` Style tree ---
    # Per-chart style patches use this to re-run the full inherit graph
    # against a chart-local override without touching board-global state.
    pre_style: Style

    # --- Board-authored chart style overrides (board layers of the axis cascade) ---
    # The board's accumulated `style.charts.*` patch, kept separate from the
    # theme+board-commingled `axis`/`axis_x`/`axis_y`/`axis_quantitative`
    # above so board-authored leaves survive the theme's chart-type defaults
    # at Layer 4 instead of being silently overwritten.
    # Always a real, possibly-empty patch: model_fields_set is empty when the
    # board authored nothing, and merge_onto_base no-ops on unset fields.
    charts_board_overrides: ChartsStylePatch

    # === Sparse / cascade-only fields — populated only by
    # === build_chart_style_context() from a chart's ChartStylePatch; None on
    # === the board-level instance resolve_chart_style_context() produces. ===

    # Format alias vocabulary from the root Style.formats cascade.
    formats: dict[str, str] | None = None

    # Chart-local axis overrides (Layers 11/12/13) as typed patches. None = no
    # chart-local override on that axis variant. These are NOT exposed to
    # chart emit code directly; resolved_axis_style is the single canonical
    # reader.
    axis_overrides_global: BaseAxisStylePatch | None = None
    axis_overrides_x: AxisXStylePatch | None = None
    axis_overrides_y: AxisYStylePatch | None = None
    axis_overrides_quantitative: QuantitativeAxisStylePatch | None = None
    axis_overrides_band: BandAxisStylePatch | None = None
    # Chart-local-only Vega passthrough, from style.color. None = no
    # chart-local color override.
    color: str | None = None
    # Materialized chart background: chart-local style.background when authored,
    # otherwise the board/board background propagated from ResolvedStyle.background
    # at compile time. Raw -- may be translucent (rgba(...)), a CSS name, or
    # transparent; label ink never reads this directly, see ink_canvas below.
    background: str = ""
    # The opaque canvas label ink derives contrast against (`ink_canvas()`
    # in compile/resolve/style/palette.py). Always a fully opaque hex. An
    # authored translucent style.background composites exactly once over
    # this value; a scope that only inherits a background carries it
    # forward unchanged. Computed, not authored -- excluded from the
    # generic charts.* passthrough below. kw_only, not defaulted: every
    # constructor call sets it explicitly (see the class docstring's
    # requirement).
    ink_canvas: str = dataclasses.field(kw_only=True)

    # Board-wide value→color scales, one per bound categorical field — the
    # PLANNED output the planner builds from ``category_color_pins`` plus
    # executed rows. Unlike the required fields above (including
    # ``category_color_pins``), this one is NOT knowable at
    # ``resolve_chart_style_context()`` time — enumerating a field's values
    # needs executed rows — so it defaults to empty and is filled in after
    # execute by ``execute.category_colors.with_category_colors()``. Empty is
    # the honest "no board binding" state: a data-free resolve, or a board
    # whose categorical fields never meet the two-chart threshold.
    category_colors: tuple[CategoryColorScale, ...] = ()


__all__ = ["ChartStyleContext"]
