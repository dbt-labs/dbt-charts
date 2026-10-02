"""Theme-stage style classes: ChartsStyle registry."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.markers import (
    Color,
    Inherit,
    InheritSlot,
    Merge,
    SkipInheritSlots,
    Strategy,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    TitleStylePatch,
    _LegendedChartStyleBase,
    _PaintedChartStyleBase,
)
from dbt_charts.core.compile.models.style.theme.area import (
    AreaChartStyle,
)
from dbt_charts.core.compile.models.style.theme.axis import (
    AxisXStyle,
    AxisYStyle,
    BaseAxisStyle,
    QuantitativeAxisStyle,
    TooltipStyle,
)
from dbt_charts.core.compile.models.style.theme.bar import (
    BarChartStyle,
)
from dbt_charts.core.compile.models.style.theme.board import (
    PaddingStyle,
)
from dbt_charts.core.compile.models.style.theme.callout import (
    CalloutChartStyle,
)
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorBinding,
)
from dbt_charts.core.compile.models.style.theme.geoshape import (
    GeoshapeChartStyle,
)
from dbt_charts.core.compile.models.style.theme.heatmap import (
    HeatmapChartStyle,
)
from dbt_charts.core.compile.models.style.theme.histogram import (
    HistogramChartStyle,
)
from dbt_charts.core.compile.models.style.theme.kpi import (
    KpiChartStyle,
)
from dbt_charts.core.compile.models.style.theme.layout import (
    ViewStyle,
)
from dbt_charts.core.compile.models.style.theme.line import (
    LineChartStyle,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    GlobalMarksStyle,
    SeriesLabelStyle,
)
from dbt_charts.core.compile.models.style.theme.pie import (
    PieChartStyle,
)
from dbt_charts.core.compile.models.style.theme.point_map import (
    PointMapChartStyle,
)
from dbt_charts.core.compile.models.style.theme.scatter import (
    ScatterChartStyle,
)
from dbt_charts.core.compile.models.style.theme.spark_bar import (
    SparkBarChartStyle,
)
from dbt_charts.core.compile.models.style.theme.table import (
    SupportTableStyle,
    TableChartStyle,
)


class HoverEmphasisStyle(BaseModel):
    """Whether a chart visually answers "what am I pointing at", beyond the tooltip.

    Hovering a mark recedes the others so the one under the cursor stands out.
    Bar, histogram, pie/donut, and heatmap charts recede their other marks;
    line, area, and scatter instead draw a datum marker plus a drop line down
    to the axis, since receding a stroke reads as an interruption rather than
    emphasis. A family that does not yet answer the question ignores the
    switch rather than half-answering it. Named for what it does rather than
    for the trigger, which would read as gating the tooltip beside it (it
    does not).

    Strength is engine config, not a theme value: there is no wide range of
    settings that read well, so we tune it rather than the author. The
    line/area drop line's color and width ARE theme values: each theme picks
    a neutral one scaffold rung past its own measure gridline, at twice that
    gridline's width, so the line reads as structure rather than as another
    data series.

    Runtime-only, deliberately: nothing about it reaches the rendered SVG, so
    a board renders the same file either way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(
        description="Whether hovering a mark visually emphasizes it; theme always provides this."
    )
    drop_line_color: Annotated[str, Color()] = Field(
        description="Color of the vertical drop line from a hovered line/area datum down to its axis."
    )
    drop_line_width: float = Field(
        description="Width, in pixels, of the vertical drop line from a hovered line/area datum down to its axis."
    )


class ChartsStyle(_PaintedChartStyleBase, _LegendedChartStyleBase):
    """Registry of all chart-type styles plus shared chart configuration.

    Inherits ``_PaintedChartStyleBase`` — most shared chart fields (padding,
    aspect_ratio, min_height, max_height, legend) are populated by theme YAML
    at the global ``charts:`` level and serve as the cascade source for
    per-family defaults.  ``font``/``border`` are declared directly on this
    class rather than inherited — ``_ChartStyleBase`` deliberately does not
    carry them (see its docstring): only families with a hand-drawn per-chart
    card render surface consume a per-family font/border, so bar/line/area/
    scatter/histogram/heatmap/pie/donut never get them back.  ``tooltip``
    lives here as the sole authoritative slot — no per-family class declares
    it, matching how every real consumer reads it
    (``chart_style_context.tooltip``, ``chart_interactivity.py``'s board-wide JS
    tooltip runtime).  ``height`` and ``width`` are NOT in the style cascade —
    they live only at chart root in the authored YAML.

    Per-family styles inherit ``_PaintedChartStyleBaseAllOptional`` (all-optional
    variant for the nine painting families) or ``_ChartStyleBaseAllOptional`` (for
    kpi/table, which paint no legend and use a fixed sizing contract); the cascade
    fills their ``None`` sentinels from the values here.

    The ``marks:`` block holds global mark defaults (tier-1 of the three-tier mark
    cascade). Legacy top-level VL mark fields (circle, square, label, tick,
    rule, trail, rect, geoshape, map) are removed.
    Use ``marks.circle``, ``marks.text``, etc. instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Declared here, not on _ChartStyleBase (see that class's docstring):
    # charts.font.* ← font.*
    font: Annotated[FontStyle, InheritSlot(from_path="Style.font")] = Field(
        default_factory=FontStyle,
        description="Chart-level font overrides.",
    )
    # Declared here, not on _ChartStyleBase (see that class's docstring): the
    # board-level authoritative default. No InheritSlot — themes must set it.
    border: BorderStyle = Field(description="Chart card border style.")

    # Override _ChartStyleBase.padding to break the self-loop the InheritSlot
    # would otherwise create on the board-level slot (Style.charts.padding cannot
    # inherit from Style.charts.padding). Per-family classes inherit
    # _ChartStyleBase.padding with the InheritSlot intact, which is what wires
    # the board → family fill at apply_inherit time.
    padding: Annotated[PaddingStyle, SkipInheritSlots()] = Field(
        description="Per-chart-type padding override; 4 sides in pixels."
    )

    # Override _ChartStyleBase.title (SkipInheritSlots there — per-family
    # title overrides stay sparse patches, see its docstring) to give this
    # one board-wide slot the actual cascade fill: apply_inherit fills every
    # unset leaf here from the board title, and copies whole unset
    # sub-objects (font, width_offsets, position, subtitle) wholesale via
    # each one's own container-level link.
    title: Annotated[TitleStylePatch | None, InheritSlot(from_path="Style.title")] = (
        Field(
            default=None,
            description="Chart-level title style override; None inherits the theme title style.",
        )
    )

    # Override to declare: charts.background ← background (board canvas).
    # apply_inherit fills this from Style.background when no theme/board patch
    # sets charts.background explicitly.  After resolve, background is always
    # a concrete str — render reads it without any OR fallback.
    background: Annotated[str | None, Inherit(from_path="Style.background")] = Field(
        default=None,
        description="Chart canvas background; None inherits from the board background via apply_inherit.",
    )

    # Not theme-populated; per-board value→color pins for categorical fields.
    # Empty is a real state, not a missing one: "no pins authored". The engine
    # still resolves a board-wide binding from the executed data — pins only
    # override which swatch a value lands on. So this is a fully-defaulted
    # container (models/AGENTS.md), not a defaulted-theme-populated field.
    # Charts-specific (not board-level Style): pinned category colors are a
    # chart-drawing concern, so they live in the same global-charts scope as
    # every other chart-color default.
    category_colors: dict[str, CategoryColorBinding] = Field(
        default_factory=dict,
        description=(
            "Board-wide category→color bindings, keyed by data field name. "
            "Pins a category to one swatch across every chart on the board."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_mark_keys(cls, data: Any) -> Any:
        """Reject legacy top-level mark keys renamed/moved under marks.* with actionable hints."""
        if not isinstance(data, dict):
            return data
        hints: list[str] = []
        if "label" in data:
            hints.append("'label' was renamed to 'text' under marks: → marks.text.*")
        if "map" in data:
            hints.append("'map' was renamed to 'geoshape' (the chart family)")
        if hints:
            raise ValueError(
                "ChartsStyle: renamed/moved keys detected. " + "; ".join(hints)
            )
        return data

    # Tooltip — the one authoritative slot; no per-family class redeclares it.
    # All real consumers (chart_style_context.tooltip, chart_interactivity.py's
    # board-wide JS tooltip runtime) read this path; per-family tooltip overrides
    # were added as scaffolding and never wired to anything.
    tooltip: TooltipStyle = Field(description="Board-wide chart tooltip style.")

    # Hover emphasis — one authoritative slot, declared exactly like tooltip
    # above: no per-family class redeclares it, and the JS runtime reads it
    # board-wide via ChartStyleContext / ResolvedChartDefaults.
    hover_emphasis: HoverEmphasisStyle = Field(
        description="Board-wide switch for hover emphasis on charts."
    )

    # Global dash palette for line-family marks. None means "no strokeDash encoding";
    # themes that want dash-based categorical distinction set this. Global-only sentinel
    # — no per-family slot carries dashes; the render layer reads
    # ChartStyleContext.dashes (passed through from this field) and skips emission
    # when None.
    dashes: Annotated[list[list[int]] | None, Merge(Strategy.OVERRIDE)] = Field(
        default=None,
        description="Ordered list of Vega-Lite strokeDash arrays for line-family categorical encoding; None disables dash emission.",
    )

    # Fallback sizing constants — used when explicit sizing is unavailable.
    default_chart_height: float = Field(
        description="Fallback chart height in pixels when aspect-ratio sizing is unavailable."
    )
    default_table_height: float = Field(
        description="Placeholder table height in pixels; replaced by data-aware row-count sizing at render time."
    )
    label_usable_ratio: float = Field(
        description="Fraction of chart width usable for axis labels (0–1); labels are tilted when full labels exceed this width."
    )

    # Axis sections — per-variant typed slots.
    # axis: channel-agnostic shared baseline applied to all axes before channel overrides.
    axis: BaseAxisStyle = Field(
        description="Shared axis style applied to all axes before per-axis overrides."
    )
    # SkipInheritSlots (not InheritSlot): axis_x/axis_y/axis_quantitative are
    # authored-only overlays, never independently filled from `axis`. The
    # render-time merge cascade in resolved_axis_style() already applies them
    # on top of `axis` in the correct layer order — pre-filling their None
    # leaves here would let a field neither axis_x/axis_y/axis_quantitative
    # ever authored (merely inherited from the same `axis` global) look
    # identical to an explicit override and wrongly clobber a sibling layer's
    # own deviation from that global. Only `axis` itself inherits (from
    # non-axis theme fields, e.g. label.font from Style.charts.font).
    axis_x: Annotated[AxisXStyle, SkipInheritSlots()] = Field(
        description="X-axis style overrides applied after the shared axis. "
        "Unset fields fall back to style.charts.axis."
    )
    axis_y: Annotated[AxisYStyle, SkipInheritSlots()] = Field(
        description="Y-axis style overrides applied after the shared axis. "
        "Unset fields fall back to style.charts.axis."
    )
    axis_quantitative: Annotated[QuantitativeAxisStyle, SkipInheritSlots()] = Field(
        description="Quantitative axis style overrides applied after axis_x/axis_y. "
        "Unset fields fall back to style.charts.axis."
    )

    # View
    view: ViewStyle = Field(description="Vega-Lite view dimensions and border.")

    # Global mark defaults — tier-1 of the three-tier mark cascade
    marks: GlobalMarksStyle = Field(
        default_factory=GlobalMarksStyle,
        description="Global mark defaults (tier-1 of the marks cascade).",
    )

    # Primary chart families
    bar: BarChartStyle = Field(
        description="Bar chart style; histogram has its own block."
    )
    line: LineChartStyle = Field(description="Line chart style.")
    area: AreaChartStyle = Field(description="Area chart style.")
    scatter: ScatterChartStyle = Field(description="Scatter chart style.")
    histogram: HistogramChartStyle = Field(description="Histogram chart style.")
    heatmap: HeatmapChartStyle = Field(description="Heatmap chart style.")
    geoshape: GeoshapeChartStyle = Field(
        description="Geoshape (choropleth) chart style."
    )
    point_map: PointMapChartStyle = Field(description="Point map chart style.")
    pie: PieChartStyle = Field(description="Pie/donut chart style.")

    # Series-label primitive — typography for Cartesian series-naming text marks
    # (endpoint labels and direct stack labels).
    series_label: SeriesLabelStyle = Field(
        description="Shared series-label typography for endpoint and stack labels."
    )

    # Non-Vega chart types (required — theme pipeline always populates)
    kpi: KpiChartStyle = Field(description="KPI card chart style.")
    table: TableChartStyle = Field(description="Table chart style.")
    spark_bar: SparkBarChartStyle = Field(
        description="Spark_bar (full-chart horizontal bar) style."
    )

    # Attached support_table primitive (per-chart strip; populated by the theme cascade).
    support_table: SupportTableStyle = Field(
        description="Attached support_table strip style."
    )

    # Callout chart-family style (type: callout and runtime chart-error fallback)
    callout: CalloutChartStyle = Field(
        description="Callout chart-family style for type:callout and runtime fallback cards."
    )
