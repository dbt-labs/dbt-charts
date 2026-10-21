"""Theme-stage style classes: KPI chart family."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    AUTO,
    Absent,
    Color,
    Format,
    Inherit,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FormatConfig,
    SingleLineFontStyle,
    SpacingValues,
    ToneLiteral,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _ChartStyleBaseAllOptional,
)
from dbt_charts.core.compile.models.style.theme.board import OverflowMode

_KPI_FONT = InheritSlot(from_path="Style.charts.kpi.font")
_KPI_VALUE_INK_FONT = InheritSlot(
    from_path="Style.charts.kpi.font",
    links={"color": "Style.charts.kpi.value.font.color"},
)


class KpiTextSlotStyle(BaseModel):
    """A KPI text slot styled by its font alone (affix, headline glyph)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[SingleLineFontStyle, _KPI_VALUE_INK_FONT] = Field(
        default_factory=SingleLineFontStyle, description="Slot font."
    )


class KpiCompactStyle(BaseModel):
    """When an unformatted KPI headline compacts, and to what."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: str = Field(description="D3 format spec the headline compacts to.")
    threshold: float = Field(
        gt=0,
        description="Smallest absolute value that compacts; below it, exact digits.",
    )


class KpiValueStyle(BaseModel):
    """KPI headline value slot: font, format, and the affix and glyph beside it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[SingleLineFontStyle, _KPI_FONT] = Field(
        default_factory=SingleLineFontStyle,
        description="Headline value font.",
    )
    # None = author did not specify a format; resolve() finalizes the default
    # via finalize_kpi_value_format() from the headline row value.
    # Cascade-managed sentinel: None at theme level; author sets via style.value.format.
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="Format for the KPI headline value: D3 format string, preset name, or FormatConfig object. A date value defaults to date_short when unformatted.",
    )
    compact: KpiCompactStyle = Field(
        description="Compaction applied to an unformatted headline number."
    )
    affix: KpiTextSlotStyle = Field(
        default_factory=KpiTextSlotStyle,
        description="Currency or percent sign written around the headline number.",
    )
    glyph: KpiTextSlotStyle = Field(
        default_factory=KpiTextSlotStyle,
        description="Indicator glyph drawn before the headline value.",
    )


class KpiLabelStyle(BaseModel):
    """KPI label slot: font and how a long label fits the card."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[SingleLineFontStyle, _KPI_FONT] = Field(
        default_factory=SingleLineFontStyle, description="Label font."
    )
    # Cascade-managed sentinel: apply_inherit fills it from the chart title's
    # overflow, so it is never None after resolve.
    overflow: Annotated[
        OverflowMode | None, Inherit(from_path="Style.charts.title.overflow")
    ] = Field(default=None, description="How a label too wide for the card is fit.")


class KpiSupportFontStyle(SingleLineFontStyle):
    """The support row and glyph font. ``color`` also takes ``auto``."""

    color: Annotated[str | None, Color(keywords=(AUTO,))] = Field(
        default=None,
        description=(
            "Text color. 'auto' is the support tone's color, or the muted ink "
            "when the row has no tone."
        ),
    )


class KpiSupportGlyphStyle(BaseModel):
    """KPI support-row glyph slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[
        KpiSupportFontStyle, InheritSlot(from_path="Style.charts.kpi.support.font")
    ] = Field(default_factory=KpiSupportFontStyle, description="Glyph font.")


class KpiSupportStyle(BaseModel):
    """KPI support row: font, format, tone, and its glyph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    font: Annotated[KpiSupportFontStyle, _KPI_FONT] = Field(
        default_factory=KpiSupportFontStyle,
        description="Support row font; its color paints the support value.",
    )
    # None = inferred: a date value defaults to date_short, a number to the
    # default number format.
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="How the support value is written: a D3 spec, a preset name, or a format block.",
    )
    # None = no semantic color; the support row paints in muted ink.
    tone: Annotated[ToneLiteral | None, Absent("muted ink")] = Field(
        default=None,
        description=(
            "Semantic color for the support value and glyph. On a delta format "
            "(delta, percent_delta, percent_number_delta, percentage_points_delta) "
            "the color follows the sign and tone is the direction: positive "
            "(the default) means up is good, negative means up is bad; zero is "
            "muted. warning and info are fixed colors, as is any tone on a "
            "non-delta format."
        ),
    )
    glyph: KpiSupportGlyphStyle = Field(
        default_factory=KpiSupportGlyphStyle,
        description="Glyph drawn before the support value.",
    )


class KpiTonesStyle(BaseModel):
    """Semantic tone palette shared by the KPI support row and table conditional glyphs.

    Colors are theme tokens — the renderer reads them through this model
    rather than hardcoding hexes, so themes can rebrand the semantic
    vocabulary without touching the renderer. Lives on ``Style.tones``
    (board level) rather than under any one chart family, since both KPI
    and table read it.

    Four tones — positive, negative, warning, info (neutral blue). Tone
    lives on the block it paints: a KPI's headline value stays neutral by
    design (NYT/FT convention — the absolute number is direction-neutral),
    so only ``style.support.tone`` reads this palette for KPI.
    A support row without a tone uses default chrome colors.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    positive: Annotated[str, Color()] = Field(
        description="Color for positive/good tone indicators."
    )
    negative: Annotated[str, Color()] = Field(
        description="Color for negative/bad tone indicators."
    )
    warning: Annotated[str, Color()] = Field(
        description="Color for warning/caution tone indicators."
    )
    info: Annotated[str, Color()] = Field(
        description="Color for neutral/informational tone indicators."
    )


class KpiChartStyle(_ChartStyleBaseAllOptional):
    """Produced by cascade from theme YAML.

    ``font`` is the parent every slot font fills from (except ``color``); each
    slot paints only from its own block. Concrete values live in ``_base.yaml``.

    KPI uses a fixed sizing contract and paints no legend — ``aspect_ratio``,
    ``min_height``, ``max_height``, and ``legend`` are absent by construction
    (``_ChartStyleBaseAllOptional``, not ``_PaintedChartStyleBaseAllOptional``).
    ``color`` is likewise absent by construction — KPI has no series axis and
    no gradient-eligible channel, so it has nothing for a paint config to
    configure; slot ``font.color`` is how a KPI's text gets painted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Override _ChartStyleBaseAllOptional.font to declare the inherit link.
    # InheritSlot: kpi.font fills from charts.font.
    font: Annotated[SingleLineFontStyle, InheritSlot(from_path="Style.charts.font")] = (
        Field(
            default_factory=SingleLineFontStyle,
            description="Font every KPI text slot starts from.",
        )
    )

    variant: Literal["stacked", "inline", "compact"] = Field(
        description=(
            "Card layout. 'stacked' puts value, label, and support on three lines; "
            "'inline' sets them on one baseline; 'compact' puts the value left "
            "and up to two lines right."
        )
    )
    value: KpiValueStyle = Field(description="KPI headline value slot.")
    label: KpiLabelStyle = Field(
        default_factory=KpiLabelStyle, description="KPI card label slot."
    )
    support: KpiSupportStyle = Field(
        default_factory=KpiSupportStyle, description="KPI support row slot."
    )
    min_card_width: float = Field(description="Minimum KPI card width in pixels.")
    default_height: float = Field(description="Default KPI card height in pixels.")
    # Cascade tier sentinel: None means "not authored"; the cascade fills it from
    # charts.border, so no theme populates it.
    border: Annotated[
        BorderStyle | None, InheritSlot(from_path="Style.charts.border")
    ] = Field(default=None, description="KPI card border style.")
    content_padding: SpacingValues = Field(
        description="Inner inset (top/right/bottom/left) for KPI card content in pixels."
    )
    # Cascade-managed sentinel: None means "not authored" — the value, label,
    # and support rows form one stacked text column at a shared content edge,
    # so alignment is one chart-level choice, not a per-slot field. No theme
    # default populates this; unauthored keeps today's left-anchored geometry.
    align: Literal["left", "center", "right"] | None = Field(
        default=None,
        description="Horizontal alignment of the value, label, and support text within the card.",
    )
