"""Table chart authored patch and table column configuration types.

Types below (``EndpointLabelsConfig`` excluded — that one lives in ``_base.py``
since it's shared across line/area/bar) live here rather than in
``chart/authored.py`` so that ``TableChartStyle`` in ``style/theme.py`` can use
``TableColumnConfig`` without a circular import. ``chart/authored.py``
re-imports them for external callers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.factories import (
    build_patch_model,
    build_patch_model_ext,
)
from dbt_charts.core.compile.models.markers import Color, DisplayText, Format, Url
from dbt_charts.core.compile.models.primitives import (
    FontStyle,
    FormatConfig,
    ScaleTargetConfig,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.compile.models.style.theme import (
    SparkStyle,
    TableChartStyle,
    _normalize_overflow_value,
)
from dbt_charts.core.utils import classify_date_column_align


class PaginationConfig(BaseModel):
    """Table pagination configuration.

    Controls client-side row paging for table charts. This is a presentation
    concern (how many rows to show per page), distinct from query-level ``limit``
    which controls how many rows are *fetched*.

    Shorthand forms are normalized by a ``field_validator`` on the containing
    style model:
    - ``pagination: 25``   → PaginationConfig(enabled=True, page_rows=25)
    - ``pagination: true``  → PaginationConfig(enabled=True)
    - ``pagination: false`` → PaginationConfig(enabled=False)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = Field(
        default=True, description="Enable client-side pagination for table charts."
    )
    page_rows: int | None = Field(
        default=None,
        description=(
            "Rows per page, a ceiling bounded by the tile height. The theme "
            "defaults it to 20; null means unlimited (the tile height alone "
            "sizes the page)."
        ),
    )

    @field_validator("page_rows")
    @classmethod
    def _page_rows_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page_rows must be a positive integer")
        return v


PixelWidth = int | str | None


class TableColumnDefaultsConfig(BaseModel):
    """Table-level defaults applied to every column unless overridden per-column.

    Only fields that are meaningful as uniform defaults are included.
    Data-dependent fields (link, header_link, spark, scale, glyph, glyph_color,
    header_overflow) are excluded — they depend on per-column data shape or
    interaction intent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="One header text applied to every column, unless a column sets its own.",
    )
    width: int | str | None = Field(
        default=None,
        description="Override column width in pixels (integer) or a CSS width string.",
    )
    align: Literal["left", "center", "right"] | None = Field(
        default=None,
        description="Override cell text alignment (left, center, or right).",
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="How values are written in every column, unless a column sets its own.",
    )
    background: Annotated[str | None, Color()] = Field(
        default=None, description="Override cell background color (CSS color string)."
    )
    font: FontStyle | None = Field(
        default=None,
        description="Override cell font style (size, weight, color, family).",
    )

    @field_validator("width")
    @classmethod
    def _validate_width(cls, value: PixelWidth) -> PixelWidth:
        return _validate_positive_pixel_width(value, "TableColumnDefaultsConfig.width")


SparkTypeLiteral = Literal[
    "line",
    "area",
    "bar",
    "bar-normalize",
    "column",
    "columns",
]

_SPARK_RENAMED_AWAY: dict[str, str] = {
    "progress": (
        "spark.type 'progress' renamed to 'bar' (no max, no track) or "
        "'bar-normalize' (with max, with background track)"
    ),
    "bars": "spark.type 'bars' renamed to 'columns' (multi-value vertical bars)",
    "histogram": (
        "spark.type 'histogram' renamed to 'columns' — pre-bin in SQL "
        "(width_bucket / histogram_continuous) and render as columns"
    ),
}


class SparkConfig(BaseModel):
    """Configuration for spark charts (inline sparklines) in table columns."""

    model_config = ConfigDict(extra="forbid")

    type: SparkTypeLiteral = Field(
        default="line",
        description="Spark chart type (line, area, bar, bar-normalize, column, columns).",
    )
    color: str | None = Field(default=None, description="Color for the spark mark.")
    height: int | None = Field(
        default=None, gt=0, description="Spark chart height in pixels."
    )
    width: int | None = Field(
        default=None, gt=0, description="Spark chart width in pixels."
    )

    # Line/area specific
    last_visible: bool | None = Field(
        default=None,
        description="Highlight the last data point (line/area spark charts).",
    )
    min_max_visible: bool | None = Field(
        default=None,
        description="Annotate the min and max data points (line spark charts only).",
    )
    fill_opacity: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Fill opacity for area spark charts (0–1).",
    )

    # Bar / bar-normalize specific
    max: float | None = Field(
        default=None,
        description=(
            "Scaling ceiling: bar-normalize and column clamp the value to it; "
            "bar uses it in place of the column's data max."
        ),
    )
    thresholds: dict[int | float, str] | None = Field(
        default=None,
        description=(
            "Color thresholds for bar / bar-normalize / column: "
            "{value: CSS color string}."
        ),
    )
    background: str | None = Field(
        default=None, description="Background track color for bar-normalize chart."
    )
    border_radius: float | None = Field(
        default=None,
        description=(
            "Corner radius in pixels for bar, bar-normalize, and column bars, "
            "and for the bar-normalize track."
        ),
    )
    value_visible: bool | None = Field(
        default=None, description="Show numeric value label alongside the bar."
    )
    value_suffix: str | None = Field(
        default=None,
        description="Text placed after the displayed value (e.g., '%').",
    )
    negative_color: bool = Field(
        default=False,
        description=(
            "Paint negative values (bar / column / columns) with the "
            "theme's tones.negative color instead of the shared spark color. "
            "Has no effect on columns with no negative values."
        ),
    )

    @field_validator("type", mode="before")
    @classmethod
    def _reject_renamed_away_type(cls, value: Any) -> Any:
        if isinstance(value, str) and value in _SPARK_RENAMED_AWAY:
            raise ValueError(_SPARK_RENAMED_AWAY[value])
        return value


def _validate_glyph_pair(
    glyph: str | None, glyph_color: str | None, label: str
) -> None:
    """Shared check for the (glyph, glyph_color) authoring contract."""
    if glyph is not None and not glyph.strip():
        raise ValueError(f"{label} glyph must be a non-empty string")
    if glyph_color is not None and glyph is None:
        raise ValueError(f"{label} glyph_color requires glyph to be set")


def _validate_positive_pixel_width(value: PixelWidth, field_name: str) -> PixelWidth:
    """Shared check for width/max_width: a CSS string passes through untouched;
    an int must be a positive pixel value."""
    if isinstance(value, int) and value <= 0:
        raise ValueError(f"{field_name} must be a positive pixel value, got {value}")
    return value


class ColumnScaleConfig(BaseModel):
    """Scale-based continuous color mapping for a table column."""

    model_config = ConfigDict(extra="forbid")

    background: ScaleTargetConfig | None = Field(
        default=None, description="Continuous background color mapping for this column."
    )
    color: ScaleTargetConfig | None = Field(
        default=None, description="Continuous text color mapping for this column."
    )

    @model_validator(mode="after")
    def _validate_color_palettes(self) -> ColumnScaleConfig:
        for key, target in (("background", self.background), ("color", self.color)):
            if target is not None and not all(
                isinstance(c, str) for c in target.palette
            ):
                raise ValueError(
                    f"ColumnScaleConfig.{key}.palette must be CSS color strings, not numbers."
                )
        return self


class TableColumnConfig(BaseModel):
    """Configuration for a single table column."""

    model_config = ConfigDict(extra="forbid")

    visible: bool | None = Field(
        default=None,
        description=(
            "Whether this column renders. Defaults to true: style.columns is "
            "styling only, so naming a column here never hides it or any other "
            "column. Set false to hide it while keeping its values available "
            "to link: templates and style-input references. A column consumed "
            "as a style input (another column's background / font.color / "
            "font.weight names it) is hidden automatically unless it has "
            "its own style.columns entry; an explicit entry is a display "
            "signal and the column renders."
        ),
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None, description="Display header label (defaults to column name)."
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="How the number is written: a D3 spec, a preset name, or a format block.",
    )
    spark: SparkConfig | SparkTypeLiteral | None = Field(
        default=None,
        description="Miniature chart drawn inside each cell: a type name, or a full block.",
    )
    swatch: bool | None = Field(
        default=None,
        description=(
            "When True, render this column's cells as small rounded color squares "
            "instead of text. Cell value must be a CSS color string (e.g. '#3164a3'). "
            "Useful for series-keyed tables, e.g. a 'Series' column where each row "
            "is identified by its color in the parent chart's palette."
        ),
    )
    width: int | str | None = Field(
        default=None,
        description="Column width (integer pixels or CSS string like '10%').",
    )
    max_width: int | str | None = Field(
        default=None,
        description=(
            "Maximum column width for auto-sized text columns "
            "(integer pixels or CSS string like '30%'). "
            "Cannot be set together with width:."
        ),
    )
    align: Literal["left", "center", "right"] | None = Field(
        default=None, description="Text alignment in cells (left, center, right)."
    )
    header_overflow: Literal["clip", "truncate", "wrap-two", "wrap"] | None = Field(
        default=None,
        description="What happens to header text too wide for its column (clip, truncate, wrap-two, wrap).",
    )
    header_link: Annotated[str | None, Url()] = Field(
        default=None, description="URL template that makes the column header clickable."
    )
    link: Annotated[str | None, Url()] = Field(
        default=None,
        description="URL template for cell values (Jinja template with row fields available).",
    )
    background: str | None = Field(
        default=None,
        description=(
            "Cell background color (hex string, or 'transparent'/'none'), or a "
            "column ID: a value matching a query column name uses that row's "
            "value in the named column instead of the literal string."
        ),
    )
    font: FontStyle | None = Field(
        default=None,
        description=(
            "Cell font style overrides. `color` and `weight` resolve column-ID-"
            "first, the same as `background`: a value matching a query column "
            "name uses that row's value in the named column."
        ),
    )
    scale: ColumnScaleConfig | None = Field(
        default=None,
        description="Continuous color mapping configuration for this column.",
    )
    glyph: str | None = Field(
        default=None,
        description="Text shown before every cell value; fills the same slot as format.prefix and wins over it.",
    )
    glyph_color: str | None = Field(
        default=None, description="Color for the glyph. Requires glyph to be set."
    )

    @field_validator("header_overflow", mode="before")
    @classmethod
    def _normalize_header_overflow(cls, value: Any) -> Any:
        return _normalize_overflow_value(value)

    @field_validator("spark", mode="before")
    @classmethod
    def _normalize_spark_shorthand(cls, value: Any) -> Any:
        """Normalize ``spark: <type>`` string shorthand to ``{"type": <type>}``.

        Runs before union validation so ``.spark`` is always ``SparkConfig | None``
        after construction — resolve and render never branch on the raw string form.
        """
        if isinstance(value, str):
            if value in _SPARK_RENAMED_AWAY:
                raise ValueError(_SPARK_RENAMED_AWAY[value])
            return {"type": value}
        return value

    @model_validator(mode="after")
    def _validate_glyph(self) -> TableColumnConfig:
        _validate_glyph_pair(self.glyph, self.glyph_color, label="TableColumnConfig")
        return self

    @model_validator(mode="after")
    def _validate_width_max_width_exclusive(self) -> TableColumnConfig:
        if self.width is not None and self.max_width is not None:
            raise ValueError(
                "TableColumnConfig: 'width' and 'max_width' are mutually exclusive. "
                "Use 'width' for a hard pin, or 'max_width' to cap auto-sizing."
            )
        return self

    @field_validator("width", "max_width")
    @classmethod
    def _validate_width_fields(
        cls, value: PixelWidth, info: ValidationInfo
    ) -> PixelWidth:
        return _validate_positive_pixel_width(
            value, f"TableColumnConfig.{info.field_name}"
        )


_SIZING_FIELDS = frozenset({"width", "max_width"})


def fill_table_column_defaults(
    source: TableColumnConfig | None,
    defaults: TableColumnDefaultsConfig | None,
    *,
    fallback_label: str | None = None,
    link_override: str | None = None,
    values: Sequence[Any] = (),
    auto_hidden: bool = False,
) -> TableColumnConfig:
    """Build one final ``TableColumnConfig`` from an optional explicit source
    and table-level ``column_defaults``, constructed once (never
    ``model_copy(update=...)``).

    Precedence per field: ``source``'s own value > ``defaults`` (fills only
    ``None`` fields) > ``fallback_label`` for ``label`` / ``link_override``
    for ``link`` / a value-classified verdict for ``align`` (see below) /
    ``auto_hidden`` for ``visible`` (a column resolve identified as a style
    input finalizes to ``visible=False`` unless the author said otherwise),
    each used only when neither of the above set one. ``width``/``max_width``
    are one sizing slot — a source that claims either is never filled from
    the other by ``defaults``.

    When ``align`` is still unset after ``source``/``defaults``, it is
    finalized from ``classify_date_column_align(values)`` — "right" iff
    every non-null value is date-like, else left unset (the column's normal
    default applies; an empty/omitted ``values`` is just another case of
    "no non-null values", so callers that have no column-value context to
    offer can omit it). A column is not a set of independently-aligned
    cells: this is what lets every cell in a date-like column share one
    text-anchor regardless of which cells a looser per-cell detector would
    have matched. Numeric columns are deliberately not classified here —
    they already right-align unconditionally at render (see the table
    render AGENTS.md "Table numeric columns center on one midpoint"
    invariant), independent of this field.

    The single constructor this repo uses both for a resolved chart's own
    columns (``compile/resolve/chart/_table.py``) and for a table renderer's two
    render-native column key spaces — pivot leaf columns and transpose's
    ``__metric__``/``__value__`` pair — whose key space doesn't exist until
    a render-time data transform runs, so ``column_defaults`` reaches them
    here instead of at resolve.
    """
    sizing_claimed = source is not None and (
        source.width is not None or source.max_width is not None
    )
    defaults_fields = (
        frozenset(type(defaults).model_fields) if defaults is not None else frozenset()
    )
    fields: dict[str, Any] = {}
    for name in TableColumnConfig.model_fields:
        value = getattr(source, name) if source is not None else None
        if (
            defaults is not None
            and value is None
            and name in defaults_fields
            and not (name in _SIZING_FIELDS and sizing_claimed)
        ):
            value = getattr(defaults, name)
        fields[name] = value
    if fields["label"] is None and fallback_label is not None:
        fields["label"] = fallback_label
    if fields["align"] is None:
        fields["align"] = classify_date_column_align(values)
    if fields["link"] is None and link_override is not None:
        fields["link"] = link_override
    # A column consumed as a style input (another column's background /
    # font.color / font.weight names it) is hidden unless the author says
    # otherwise — its values are paint, not display data.
    if fields["visible"] is None and auto_hidden:
        fields["visible"] = False
    return TableColumnConfig(**fields)


if TYPE_CHECKING:

    class SparkStylePatch(SparkStyle):
        pass

else:
    SparkStylePatch = build_patch_model(SparkStyle)


if TYPE_CHECKING:

    class TableChartStylePatch(TableChartStyle):  # noqa: F811
        pass

else:
    TableChartStylePatch = build_patch_model_ext(TableChartStyle)
