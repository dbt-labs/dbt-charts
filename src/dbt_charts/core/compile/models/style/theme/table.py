"""Theme-stage style classes: table chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

if TYPE_CHECKING:
    # These authored types are injected into theme/__init__.py's module globals at
    # runtime by dbt_charts.core.compile.models.style.authored (after authored.py
    # finishes executing). The import here is TYPE_CHECKING-only to give mypy and
    # ruff the static definitions they need without creating a circular import.
    from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
        PaginationConfig,
        TableColumnConfig,
        TableColumnDefaultsConfig,
    )

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.constraints import (
    ConstrainedModel,
    Constraint,
    Requires,
)
from dbt_charts.core.compile.models.markers import (
    Color,
    Inherit,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    CornerStyle,
    DividerStyle,
    FontStyle,
    SpacingValues,
    StaticGradientColorStyle,
    StrokeStyle,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _ChartCardStyleMixinAllOptional,
    _ChartStyleBaseAllOptional,
    _TitledChartStyleBaseAllOptional,
)


class TableDividerStyle(DividerStyle):
    """A table divider that also chooses how far the line runs."""

    continuous: bool = Field(
        description=(
            "Draw one unbroken line across the full table width (true) or only "
            "under each column (false)."
        ),
    )


class TableColumnsStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_width: float = Field(description="Default column width in pixels.")
    cell_padding: float = Field(
        description="Horizontal padding inside table cells in pixels."
    )
    width_similarity_threshold: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Auto-width columns whose min/max ratio >= this threshold are snapped "
            "to a shared width before budget allocation. 1.0 disables clustering; "
            "0.0 forces all auto-columns to equal width."
        ),
    )
    content_headroom: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fractional breathing room added above the raw p95 column demand when "
            "pinning compact columns in mixed (compact + text) tables.  0.10 means "
            "each compact column is pinned at 10 % above its measured demand; the "
            "extra width is funded by the text-column budget.  Has no effect on "
            "all-compact tables (proportional scaling already fills the budget).  "
            "0.0 disables headroom."
        ),
    )


class TableHeaderStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visible: bool = Field(
        description=(
            "Show the header row (column labels + divider). Set to False on "
            "series-keyed tables where column meanings are obvious from context "
            "(e.g. donut-attached tables: swatch / share / name / value). "
            "Theme YAML supplies the UX default (true) via _base.yaml."
        ),
    )
    height: float = Field(description="Header row height in pixels.")
    # InheritSlot: table.header.font fills from table.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.table.font")] = (
        Field(default_factory=FontStyle, description="Header font style overrides.")
    )
    font_compact: FontStyle | None = Field(
        default=None,
        description="Compact-tier font overrides applied when body size ≤11px; None means no compact override.",
    )  # None = no compact-tier override; header uses font at all body sizes
    # None = no header fill. The header divider alone separates header from body.
    # Themes may set a fill (cream, dark) when paper-warmth or
    # contrast demands it.
    background: Annotated[str | None, Color()] = Field(
        default=None,
        description="Header background color; None means no fill (the divider alone separates header from body).",
    )
    overflow: Literal["clip", "truncate", "wrap-two", "wrap"] = Field(
        description="What happens to header text too wide for its column (clip, truncate, wrap-two, wrap)."
    )
    divider: TableDividerStyle = Field(
        description="Line between the header row and the body."
    )


class TableRowRoleStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    divider: DividerStyle = Field(description="Line above rows with this role.")
    font: FontStyle | None = Field(
        default=None,
        description="Per-role font style override; None uses the default row font.",
    )
    background: Annotated[str | None, Color()] = Field(
        default=None,
        description="Per-role row background color; None means no override.",
    )


class TableRowRolesStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: TableRowRoleStyle = Field(description="Style for summary role rows.")
    total: TableRowRoleStyle = Field(description="Style for total role rows.")


class TableRowStripeStyle(BaseModel):
    """Alternating row stripe style."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str | None, Color()] = Field(  # None = no alternating row fill
        default=None,
        description="Alternating stripe background color; None means no alternating row fill.",
    )


class TableRowStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    height: float = Field(description="Body row height in pixels.")
    stripe: TableRowStripeStyle | None = Field(  # None = no alternating row fill
        default=None,
        description="Alternating row stripe style; None means no stripe.",
    )
    divider: TableDividerStyle = Field(description="Line under each body row.")
    role: str | None = Field(
        default=None,
        description="Default row role assignment; None means plain body row.",
    )
    roles: TableRowRolesStyle = Field(
        description="Per-role style overrides for summary and total rows."
    )


class TableTitleStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    height: float = Field(
        description=(
            "Minimum title-block height in pixels. Applies as a floor only when a "
            "subtitle is present; a title-only block sizes to its natural content "
            "height instead."
        )
    )
    # InheritSlot: table.title_row.font fills from table.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.table.font")] = (
        Field(
            default_factory=FontStyle, description="Table title font style overrides."
        )
    )


class TableEdgeStyle(BaseModel):
    """more_rows or empty_state edge-case UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: more_rows/empty_state font fills from table.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.table.font")] = (
        Field(
            default_factory=FontStyle,
            description="Edge-case UI (more_rows / empty_state) font style overrides.",
        )
    )


class SparkEmptyStyle(ConstrainedModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    __constraints__: ClassVar[tuple[Constraint, ...]] = tuple(
        Requires(
            (f"stroke.{field}",),
            f"spark.empty.stroke.{field} is required "
            "(renderer reads color/width/dasharray)",
        )
        for field in ("color", "width", "dasharray")
    )
    inset_x: float = Field(
        description="Horizontal inset for the empty sparkline placeholder in pixels."
    )
    stroke: StrokeStyle = Field(description="Empty-state placeholder stroke style.")


class SparkSingleValueStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inset_x: float = Field(
        description="Horizontal inset for the single-value sparkline in pixels."
    )
    marker_radius: float = Field(
        description="Radius of the single-value marker circle in pixels."
    )


class SparkColumnsStyle(BaseModel):
    """Inline `spark.type: columns` (multi-value vertical bars) defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    gap: float = Field(description="Gap between column bars in pixels.")
    padding: float = Field(
        description="Horizontal outer padding of the columns sparkline in pixels."
    )
    min_bar_height: float = Field(description="Minimum rendered bar height in pixels.")
    border: CornerStyle = Field(description="Column bar corner rounding.")


class SparkBarLabelStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inset_x: float = Field(
        description="Horizontal inset for the spark bar label in pixels."
    )
    fill: Annotated[str, Color()] = Field(description="Label text fill color.")
    fill_opacity: float = Field(description="Label text fill opacity (0–1).")
    min_size: float = Field(
        description="Minimum bar fill width required to show the label in pixels."
    )
    height_offset: float = Field(
        description="Vertical offset of the label from its bar top in pixels."
    )


class SparkBarCellStyle(BaseModel):
    """Inline `spark.type: bar` and `bar-normalize` (single horizontal bar) defaults.

    The variant decides whether the background track is drawn:
    `bar` paints only the fill; `bar-normalize` paints a track + fill.
    Style sub-keys are shared between both variants.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    background: Annotated[str, Color()] = Field(
        description="Track background color - fill-grade, pinned per theme."
    )
    color: Annotated[str | None, Color()] = Field(
        default=None,
        description="Bar fill color; None seeds from style.charts.color.categorical.single_series_palette[0].",
    )
    default_max: float = Field(
        description="Default maximum value for bar scale when no explicit max is authored."
    )
    border: CornerStyle = Field(description="Corner rounding for spark bar cells.")
    # InheritSlot: table.spark.bar.font fills from charts.font (not table.font).
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Spark bar cell font style overrides.",
    )
    label: SparkBarLabelStyle = Field(description="Spark bar inline label style.")


class SparkAreaStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fill_opacity: float = Field(description="Spark area fill opacity (0–1).")


class SparkColumnStyle(BaseModel):
    """Inline `spark.type: column` (single vertical bar) defaults.

    Geometric defaults only. Color/border/default_max are shared with
    `spark.bar` since `column` is `bar-normalize`'s vertical mirror
    without the track — see render_spark_column.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    width: float = Field(description="Spark column default width in pixels.")
    height: float = Field(description="Spark column default height in pixels.")


class SparkStyle(BaseModel):
    """Inline sparkline defaults (inside table cells)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    color: Annotated[str | None, Color()] = Field(
        default=None,
        description="Sparkline line/point color; None seeds from style.charts.color.categorical.single_series_palette[0].",
    )
    padding: SpacingValues = Field(
        description="Cell padding around the sparkline in pixels."
    )
    empty: SparkEmptyStyle = Field(description="Style for empty/no-data sparklines.")
    single_value: SparkSingleValueStyle = Field(
        description="Style for single-data-point sparklines."
    )
    columns: SparkColumnsStyle = Field(description="Style for column-type sparklines.")
    column: SparkColumnStyle = Field(
        description="Style for the single vertical `column` spark mark."
    )
    bar: SparkBarCellStyle = Field(
        description="Style for bar/bar-normalize sparklines."
    )
    area: SparkAreaStyle = Field(description="Style for area sparklines.")


class TableRowNumbersStyle(BaseModel):
    """Leading row-number column (style.table.row_numbers).

    When ``show=True`` the renderer injects a synthetic column at index 0
    that displays the absolute 1-based row index in the original unpaginated
    dataset. Pagination is continuous: page 2 of a 10-per-page table starts
    at 11. The column is transparent to ``style.columns`` (not listed), to
    conditional_formatting rules keyed by column name, and to data-pipeline
    transforms — it exists only in the chart layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Feature toggle — False is the opt-out default; not a visual theme value.
    visible: bool = Field(
        default=False, description="Show a leading row-number column; false by default."
    )
    # Fixed column header label — not a theme style value.
    header: str = Field(
        default="#", description="Header label for the row-number column."
    )
    # Fixed alignment for the row-number column — not a theme style value.
    align: Literal["left", "right"] = Field(
        default="right", description="Text alignment for the row-number column."
    )


class PaginatorStyle(BaseModel):
    """Visual style for the paginator control (chevrons + page numbers).

    Distinct from PaginationConfig which controls *behavior* (enabled,
    page_rows). PaginatorStyle owns the look: color ramp across the three
    item states (active, inactive, disabled), font sizing, weight contrast
    between the current page and its neighbors, and the slot width that
    drives the hit target.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    color_active: Annotated[str, Color()] = Field(
        description="Current page and live chevron color (theme body ink)."
    )
    color_inactive: Annotated[str, Color()] = Field(
        description="Other pages and ellipsis color (theme secondary text)."
    )
    color_disabled: Annotated[str, Color()] = Field(
        description="Chevron color when at first/last page (signals disabled by tone)."
    )
    font: FontStyle = Field(
        default_factory=FontStyle,
        description="Paginator font overrides (size, family).",
    )
    weight_active: int = Field(
        description="Font weight for the current (selected) page number."
    )
    weight_inactive: int = Field(
        description="Font weight for other pages and ellipsis."
    )
    weight_chevron: int = Field(
        description=(
            "Font weight for the prev/next chevrons (live and disabled). "
            "Usually heavier than weight_active so the chevrons read as "
            "interactive affordances against the lighter page numbers."
        )
    )
    item_width: float = Field(
        description="Per-item slot width in pixels (drives layout step)."
    )


class TableChartStyle(
    _ChartCardStyleMixinAllOptional,
    _ChartStyleBaseAllOptional,
    _TitledChartStyleBaseAllOptional,
):
    """Table chart style overrides layered on top of shared chart defaults.

    Table uses a fixed sizing contract and paints no legend — ``aspect_ratio``,
    ``min_height``, ``max_height``, and ``legend`` are absent by construction
    (``_ChartStyleBaseAllOptional``, not ``_PaintedChartStyleBaseAllOptional``).

    ``border`` inherits from ``style.charts.border``: table draws its own card.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not authored"; the cascade fills it from
    # charts.border, so no theme populates it.
    border: Annotated[
        BorderStyle | None, InheritSlot(from_path="Style.charts.border")
    ] = Field(default=None, description="Chart card border style.")

    preferred_width: Annotated[
        float, Inherit(from_path="Style.charts.preferred_width")
    ] = Field(
        description=(
            "Width the board asks for when sizing to a table before its data is "
            "known. With data, a board with no width measures the table's columns "
            "instead. Only a value set on the chart itself pins the width; a "
            "board- or theme-level value is just the estimate used before data is "
            "known."
        ),
    )

    # Override the mixin's font to declare the inherit link.
    # InheritSlot: table.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="Table chart-level font overrides.",
    )

    # Color overrides (None = inherit from theme)
    background: Annotated[str | None, Color()] = Field(
        default=None, description="Table background color; None inherits from theme."
    )
    # Tables have no series axis — extra_forbidden via StaticGradientColorStyle
    # (no categorical arm). Not an override: _ChartStyleBaseAllOptional carries
    # no color field (it lives on _PaintedChartStyleBase, which table does not
    # inherit), so this is a fresh field declaration.
    color: StaticGradientColorStyle | None = Field(
        default=None,
        description="Table color: static text paint or gradient scale only (no categorical arm).",
    )

    outer_padding: float = Field(description="Outer table padding in pixels.")
    bottom_padding: float = Field(
        description="Extra bottom padding below the last row in pixels."
    )
    # Layout/structural column settings (width budget, cell padding, clustering).
    # Renamed from 'columns' to avoid collision with the per-column display config.
    column_layout: TableColumnsStyle = Field(
        description="Default column width and cell padding settings."
    )
    header: TableHeaderStyle = Field(description="Table header row style.")
    row: TableRowStyle = Field(description="Table body row style.")
    row_numbers: TableRowNumbersStyle = Field(
        default_factory=TableRowNumbersStyle,
        description="Leading row-number column configuration.",
    )
    title_row: TableTitleStyle = Field(
        description="Title block rendered above the header row."
    )
    wrap: bool = Field(
        description="Allow cell text wrapping; false clips to single line."
    )
    pagination: PaginationConfig = Field(
        description="Client-side pagination defaults for table charts."
    )
    # Cascade-managed sentinels — None means "not overridden at this tier".
    column_defaults: TableColumnDefaultsConfig | None = Field(
        default=None,
        description="Table-level column defaults applied to every column; None means no defaults authored.",
    )
    columns: dict[str, TableColumnConfig] | None = Field(
        default=None,
        description="Per-column display configuration keyed by column name (label, format, width, etc.).",
    )
    header_overflow: Literal["clip", "truncate", "wrap-two", "wrap"] | None = Field(
        default=None,
        description="Table column header text overflow mode; None inherits from theme.",
    )
    paginator: PaginatorStyle = Field(
        description="Visual style for the paginator control (chevrons + page numbers)."
    )
    symbol_mode: Literal["all", "anchors"] = Field(
        description=(
            "Where to show currency-prefix and magnitude/unit suffix symbols in a "
            "numeric column. 'all' shows the full formatted value on every row; "
            "'anchors' shows it only on the first data row and summary/total rows, "
            "stripping prefix and suffix from plain middle rows so the anchors "
            "guide the reader at the top and bottom of the value field."
        )
    )
    more_rows: TableEdgeStyle = Field(
        description="Style for the 'more rows' edge-case indicator."
    )
    empty_state: TableEdgeStyle = Field(
        description="Style for the empty-state (no data) indicator."
    )
    spark: SparkStyle = Field(description="Inline sparkline defaults for table cells.")

    # Authored convenience — not a theme constant; defaults False so themes need
    # not supply it.  When True the renderer pivots a single wide data row into
    # N (label, value) rows, one per column.
    transpose: bool = Field(
        default=False,
        description=(
            "When True, render a single wide data row as N (label, value) rows, "
            "one per column. Raises ChartDataError when data has more than one row. "
            "Used for Looker single-value summary tiles with multiple measures."
        ),
    )

    # SVG layout constants (theme-config, no in-code defaults)
    text_baseline_offset: float = Field(
        description="Vertical offset to align SVG text baseline with cell grid in pixels."
    )
    # No title baseline offset field: the baseline follows the resolved title
    # font size, as in every chart family, so a flat constant cannot go stale
    # when the title size changes with the width tier.
    title_subtitle_gap: float = Field(
        description=(
            "Pure whitespace between the title's descent and the subtitle's "
            "ascent, in pixels, not a baseline-to-baseline distance. Combined "
            "with the title and subtitle font sizes to reproduce Vega-Lite's "
            "title->subtitle spacing at any font size, not one calibrated "
            "pair. Font size and "
            "color for the subtitle itself come from style.title.subtitle "
            "(the same source chart-family titles use), not a table-local "
            "constant."
        )
    )


class SupportTableRowPaddingStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vertical: float = Field(
        description="Vertical (top/bottom) padding inside support_table rows in pixels."
    )
    horizontal: float = Field(
        description="Horizontal (left/right) padding inside support_table rows in pixels."
    )


class SupportTableRowStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    padding: SupportTableRowPaddingStyle = Field(description="Row padding style.")
    divider: DividerStyle = Field(description="Line under each strip row.")


class SupportTableLabelStyle(BaseModel):
    """Row label styling.

    The row label's side (left-gutter or right-gutter) is a pure function of
    the chart's resolved ``axis_y.orient`` — there is no authorable
    ``position`` or ``align`` override. The compiler derives both from the
    axis orientation at render time:

    - ``axis_y.orient == "right"`` → label at ``spec_width + axis_y.labels.padding``,
      ``mark.align = "left"`` (text-anchor start, extends rightward).
    - ``axis_y.orient == "left"`` → label at ``-axis_y.labels.padding``,
      ``mark.align = "right"`` (text-anchor end, extends leftward).

    ``extra="forbid"`` rejects any YAML that still authors the deleted
    ``position`` or ``align`` fields with a ``ValidationError`` at compile time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: support_table.label.font fills from support_table.font.
    font: Annotated[
        FontStyle, InheritSlot(from_path="Style.charts.support_table.font")
    ] = Field(default_factory=FontStyle, description="Row label font style overrides.")


class SupportTableStyle(BaseModel):
    """Attached support_table style. Lives at style.charts.support_table.*.

    Also nestable under per-chart-type blocks (bar.support_table, line.support_table,
    area.support_table) for per-chart-type overrides.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: support_table.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle, description="Support_table font style overrides."
    )
    divider: DividerStyle = Field(
        description=(
            "Line at the boundary between the chart plot and the data strip. "
            "For position='bottom': the line sits above the strip (below the axis). "
            "For position='top': the line sits below the strip rows (above the plot top)."
        )
    )
    row: SupportTableRowStyle = Field(
        description="Support_table row padding and divider style."
    )
    label: SupportTableLabelStyle = Field(description="Row label (series name) style.")
    padding_top: float = Field(
        description=(
            "Padding above the topmost strip row in pixels. "
            "For position='bottom': gap between axis labels and the first row. "
            "For position='top': space above the topmost row (outer edge of strip)."
        )
    )
    padding_bottom: float = Field(
        description=(
            "Padding below the last strip row in pixels. "
            "For position='bottom': space below the last row. "
            "For position='top': gap between the last row and the plot top edge."
        )
    )
    # Number of x-axis label lines to reserve above the strip. Vega emits an
    # array label expression (e.g. ['Jan', '2024']) at year boundaries, so the
    # strip must reserve space for 2 lines even on months that emit a single
    # line — otherwise the strip overlaps labels at year ticks. Set to 1 if the
    # theme is known to emit only single-line labels (e.g. year-only cadence).
    label_max_lines: int = Field(
        description=(
            "Number of x-axis label lines to reserve in the axis gap "
            "(only used for position='bottom'; ignored for position='top'). "
            "Typically 1 or 2."
        )
    )
    # Cascade-managed sentinel: None means "not authored" — the effective
    # placement then depends on the chart's category-axis orientation, a fact
    # only known once the chart resolves (vertical bar vs. vertical/line/area),
    # so no theme YAML supplies a literal default here. An explicit author
    # value is validated against that orientation at render time and always
    # wins over the orientation-derived default; see
    # render/chart/support_table_attachment.py's position resolution.
    position: Literal["top", "bottom", "left", "right"] | None = Field(
        default=None,
        description=(
            "Strip placement relative to the chart plot. 'top'/'bottom' apply "
            "when the chart's category axis is horizontal (vertical bar, line, "
            "area): 'top' places the strip above the plot, 'bottom' places it "
            "below with the x-axis between plot and strip. 'left'/'right' apply "
            "when the category axis is vertical (a horizontal bar): the strip "
            "renders as value columns beside the plot instead of rows above or "
            "below it. Left unset, 'top' is used on a horizontal category axis "
            "and the side the category labels are on is used on a vertical one."
        ),
    )
