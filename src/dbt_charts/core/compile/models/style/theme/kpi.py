"""Theme-stage style classes: KPI chart family."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    Color,
    Format,
    InheritSlot,
)
from dbt_charts.core.compile.models.primitives import (
    BorderStyle,
    FontStyle,
    FormatConfig,
    SpacingValues,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _ChartStyleBaseAllOptional,
)


class KpiValueStyle(BaseModel):
    """KPI headline value slot: font and format. Theme populates font.size directly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: kpi.value.font fills from kpi.font, except `color` — that
    # leaf stays a cascade-managed sentinel so "author named this slot's ink"
    # is distinguishable from "the KPI's ink cascaded down". The renderer needs
    # the difference to let it beat the whole-card `style.font.color` fallback.
    font: Annotated[
        FontStyle,
        InheritSlot(from_path="Style.charts.kpi.font", exclude=frozenset({"color"})),
    ] = Field(
        default_factory=FontStyle,
        description="Headline value font.",
    )
    # None = author did not specify a format; resolve() finalizes the default
    # via finalize_kpi_value_format() from the headline row value.
    # Cascade-managed sentinel: None at theme level; author sets via style.value.format.
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="Format for the KPI headline value: D3 format string, preset name, or FormatConfig object. A date value defaults to date_short when unformatted.",
    )


class KpiSlotStyle(BaseModel):
    """KPI per-slot style for label, affix, and glyph slots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # InheritSlot: kpi.{label,affix,glyph}.font fills from kpi.font, except
    # `color` — see KpiValueStyle.font.
    font: Annotated[
        FontStyle,
        InheritSlot(from_path="Style.charts.kpi.font", exclude=frozenset({"color"})),
    ] = Field(
        default_factory=FontStyle,
        description="Slot font style.",
    )
    # Authored glyph character (e.g. '▲', '▼', '●'). None = no glyph rendered.
    # Only meaningful on the `glyph` slot — ignored on label/affix slots.
    character: str | None = Field(
        default=None,
        description="Glyph character to render (e.g. '▲'). None = no glyph.",
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
    so only ``support.tone`` (KpiSupportConfig) reads this palette for KPI.
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

    Four per-slot font blocks (value, label, affix, glyph) plus a parent
    ``font`` that cascades into every slot and doubles as the support-row
    font. Concrete pixel values live in ``stark.yaml``.

    * ``font`` — parent font; also the support row (small text under value)
    * ``value.font`` — headline number typography; size/weight from theme (authoritative)
    * ``label.font`` — card label typography
    * ``affix.font`` — currency/percent affixes
    * ``glyph.font`` — indicator glyphs (▲▼●)
    * ``content_padding`` — per-side inner inset for the value/label/support text block

    KPI uses a fixed sizing contract and paints no legend — ``aspect_ratio``,
    ``min_height``, ``max_height``, and ``legend`` are absent by construction
    (``_ChartStyleBaseAllOptional``, not ``_PaintedChartStyleBaseAllOptional``).
    ``color`` is likewise absent by construction — KPI has no series axis and
    no gradient-eligible channel, so it has nothing for a paint config to
    configure; ``font.color`` / ``value.font.color`` / ``label.font.color``
    are how a KPI's text gets painted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Override _ChartStyleBaseAllOptional.font to declare the inherit link.
    # InheritSlot: kpi.font fills from charts.font.
    font: Annotated[FontStyle, InheritSlot(from_path="Style.charts.font")] = Field(
        default_factory=FontStyle,
        description="KPI chart-level font overrides.",
    )

    value: KpiValueStyle = Field(description="KPI headline value slot.")
    label: KpiSlotStyle = Field(description="KPI card label slot.")
    affix: KpiSlotStyle = Field(description="Currency/percent affix slot.")
    glyph: KpiSlotStyle = Field(
        # Indicator glyphs (▲▼●) sit next to the value as markers, not second
        # headlines — drawn at body/narrow tier so a triangle's visual mass
        # doesn't compete with the digits.
        description="Indicator glyph (▲▼●) slot.",
    )
    min_card_width: float = Field(description="Minimum KPI card width in pixels.")
    default_height: float = Field(description="Default KPI card height in pixels.")
    border: BorderStyle = Field(description="KPI card border style.")
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
