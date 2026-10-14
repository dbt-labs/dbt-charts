"""Normalized custom chart."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from dbt_charts.core.compile.template.custom_template import (
    CustomTemplateHeader,
    OptionValue,
)

from ._base import _SharedChartFields


class CustomChart(_SharedChartFields):
    """Normalized custom chart."""

    type: Literal["custom"]
    template_name: str = Field(
        description="`templates/<name>.svg.j2` name, or 'inline svg:' for `svg:` source."
    )
    template_source: str = Field(description="Jinja SVG template source.")
    template_header: CustomTemplateHeader = Field(
        description="Parsed template header (declared channels and options)."
    )
    channels: dict[str, str | list[str] | None] = Field(
        description=(
            "Declared channel name -> column, or a list for a `multiple` "
            "channel; an unset optional channel is None."
        )
    )
    options: dict[str, OptionValue] = Field(
        description="Validated options with the template's defaults filled."
    )
    aspect_ratio: float | None = Field(
        default=None, description="Width-to-height ratio."
    )
    min_height: float | None = Field(default=None, description="Minimum height floor.")
    max_height: float | None = Field(
        default=None, description="Maximum height ceiling."
    )
    style: None = Field(
        default=None,
        description=(
            "Always None — a custom chart has no style patch. Present so the "
            "style cascade can read chart.style on every family."
        ),
    )
