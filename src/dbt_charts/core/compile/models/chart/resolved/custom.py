"""Resolved custom chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from dbt_charts.core.compile.models.primitives import ResolvedFontStyle, ResolvedFormat
from dbt_charts.core.compile.template.custom_template import OptionValue

from ._base import _SharedResolvedChartFields


class ResolvedCustomStyle(BaseModel):
    """What a custom chart's template and title read from the style cascade."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title_font: ResolvedFontStyle = Field(
        description="Baked title font (size/weight/family/…) from the width tier."
    )
    tree: dict[str, JsonValue] = Field(
        description=(
            "The board's merged style tree as plain data — the template's "
            "`style`. `palettes.<role>` holds hex lists (a dict of aliases for "
            "a tone role) instead of palette names."
        )
    )


class ResolvedCustomChart(_SharedResolvedChartFields):
    """Render-ready custom chart."""

    chart_type: Literal["custom"] = Field(
        description="Discriminator key; always 'custom'.",
    )
    template_name: str = Field(description="Template name, or 'inline svg:'.")
    template_source: str = Field(description="Jinja SVG template source.")
    channels: dict[str, str | list[str] | None] = Field(
        description="Declared channel name -> column(s); unset optional is None."
    )
    options: dict[str, OptionValue] = Field(
        description="Validated options; color tokens resolved to hex."
    )
    aspect_ratio: float | None = Field(
        default=None, description="Authored width-to-height ratio."
    )
    min_height: float | None = Field(default=None, description="Authored height floor.")
    max_height: float | None = Field(
        default=None, description="Authored height ceiling."
    )
    column_options: tuple[str, ...] = Field(
        description="Names of the options that name query columns."
    )
    column_formats: dict[str, ResolvedFormat] = Field(
        description=(
            "Every column the query returned -> the number format a built-in "
            "chart would print it with (chart cascade: query `formats:`, "
            "`style.formats`, the `number` default); what `format(column=...)` "
            "prints through. A date column is not formatted by it."
        )
    )
    category_pins: dict[str, dict[str, str]] = Field(
        description="Board category color pins: column → value → hex."
    )
    style: ResolvedCustomStyle = Field(description="Custom family style slice.")
