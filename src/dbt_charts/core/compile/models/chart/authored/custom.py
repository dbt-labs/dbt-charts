"""Authored custom chart (drawn by a sandboxed Jinja SVG template)."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    AtMostOneOf,
    Constraint,
)
from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.template.custom_template import OptionValue

from ._base import _SharedChartFields


class CustomChartStylePatch(BaseModel):
    """Sizing for a custom chart; its look comes from the board's `style:`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    aspect_ratio: float | None = Field(
        default=None, gt=0, description="Chart aspect ratio (width/height)."
    )
    min_height: float | None = Field(
        default=None, description="Minimum chart height in pixels."
    )
    max_height: float | None = Field(
        default=None, description="Maximum chart height in pixels."
    )


class CustomChart(_SharedChartFields):
    """Authored patch for custom charts: rows in, SVG out, drawn by a template."""

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(
            ("template", "svg"),
            "Custom chart needs `template:` (a project template name) or "
            "`svg:` (inline template source).",
        ),
        AtMostOneOf(
            ("template", "svg"),
            "Custom chart takes `template:` or `svg:`, not both.",
        ),
    )

    type: Annotated[Literal["custom"], Field(description="Selects the chart family.")]
    template: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$",
            description=(
                "Name of a project template: `templates/<name>.svg.j2`. "
                "Exactly one of `template` or `svg` is required."
            ),
        ),
    ]
    svg: Annotated[
        str | None,
        Field(
            default=None,
            description="Inline Jinja SVG template source, used instead of `template`.",
        ),
    ]
    x: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `x` channel; the template header declares it.",
        ),
    ]
    y: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `y` channel; the template header declares it.",
        ),
    ]
    color: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `color` channel; the template header declares it.",
        ),
    ]
    size: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `size` channel; the template header declares it.",
        ),
    ]
    shape: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `shape` channel; the template header declares it.",
        ),
    ]
    theta: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `theta` channel; the template header declares it.",
        ),
    ]
    value: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Column(s) for the template's `value` channel; the template header declares it.",
        ),
    ]
    options: Annotated[
        dict[str, OptionValue] | None,
        Field(
            default=None,
            description="Values for the options the template declares in its header.",
        ),
    ]
    style: Annotated[
        CustomChartStylePatch | None,
        Field(default=None, description="Sizing overrides for this chart alone."),
    ]
