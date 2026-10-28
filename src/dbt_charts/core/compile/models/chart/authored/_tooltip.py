"""CHART.TOOLTIP — query columns appended to a mark's hover card."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from dbt_charts.core.compile.models.markers import Channel, DisplayText, Format
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.schema_names import FormatAlias


def _bare_column(
    data: object,  # type-state: object_annotation — validator input: any raw YAML scalar/mapping pre-coercion; narrowed by isinstance below
) -> object:  # type-state: object_annotation — same boundary as the parameter above
    """A bare column name is `{column: name}`."""
    return {"column": data} if isinstance(data, str) else data


class TooltipField(BaseModel):
    """One query column shown on the hover card."""

    model_config = ConfigDict(extra="forbid")

    column: Annotated[str, Channel()] = Field(
        description="Query column whose value the row shows."
    )
    label: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Row label. Defaults to the chart's `field_labels:` entry for the column, else its derived label.",
    )
    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="D3 format string or format config object. Defaults to the query's `formats:` entry for the column.",
    )


ChartTooltip = Annotated[
    list[
        Annotated[
            TooltipField,
            BeforeValidator(_bare_column, json_schema_input_type=str | TooltipField),
        ]
    ],
    Field(min_length=1),
]


class _TooltipFields(BaseModel):
    """Mixin adding `tooltip:` to the families whose marks carry a hover card
    of their own: bar, line, area, scatter, pie. Every other family's
    `extra="forbid"` makes authoring it a parse-time ValidationError."""

    model_config = ConfigDict(extra="forbid")

    tooltip: Annotated[
        ChartTooltip | None,
        Field(
            default=None,
            description=(
                "Query columns to add to the hover card, after the chart's own "
                "rows. Each entry is a column name or `{column, label, format}`. "
                "On a stacked or grouped bar, or a multi-series line or area, "
                "the card lists every series at the hovered x: a series' extra "
                "rows sit under its own row."
            ),
        ),
    ]
