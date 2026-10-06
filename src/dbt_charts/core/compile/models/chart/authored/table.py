"""Authored table chart."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.markers import Channel
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

from ._base import _ConditionalFormattingField, _SharedChartFields


class TableSpanner(BaseModel):
    """One authored spanner: a label drawn over a run of adjacent columns."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(description="Group label for the listed columns.")]
    columns: Annotated[
        list[str],
        Field(
            min_length=1,
            description=(
                "Query columns the spanner covers. Must be adjacent in the "
                "query's column order and unique; a column may sit in at most "
                "one spanner."
            ),
        ),
    ]


class TableChart(_SharedChartFields, _ConditionalFormattingField):
    """Authored patch for table charts."""

    model_config = ConfigDict(extra="forbid")

    type: Annotated[Literal["table"], Field(description="Selects the chart family.")]
    style: Annotated[
        TableChartStylePatch | None,
        Field(default=None, description="Appearance overrides for this chart alone."),
    ]
    rows: Annotated[
        list[str] | None,
        Channel(),
        Field(
            default=None,
            description=(
                "Fields whose distinct values form the row dimension of a pivot cross-tab. "
                "Each string is a column name from the query result. "
                "Omit for flat (non-pivot) tables."
            ),
        ),
    ]
    columns: Annotated[
        list[str] | None,
        Channel(),
        Field(
            default=None,
            description=(
                "Fields whose distinct values become column headers in a pivot cross-tab. "
                "Multiple fields create a nested multi-dimension pivot (outer → inner). "
                "Omit for flat tables."
            ),
        ),
    ]
    values: Annotated[
        list[str] | None,
        Channel(),
        Field(
            default=None,
            description=(
                "Measure fields that fill pivot cells. "
                "Each string is a column name from the query result. "
                "When omitted, all query columns not claimed by rows or columns are used."
            ),
        ),
    ]

    spanners: Annotated[
        list[TableSpanner] | None,
        Field(
            default=None,
            description=(
                "Labels drawn in an extra header row above runs of adjacent "
                "columns of a flat table. Not combinable with the pivot "
                "rows/columns/values channels."
            ),
        ),
    ]

    @model_validator(mode="after")
    def _validate_pivot_channels(self) -> TableChart:
        owner: dict[str, TableSpanner] = {}
        for spanner in self.spanners if self.spanners is not None else ():
            for col in spanner.columns:
                if owner.get(col) is spanner:
                    raise ValueError(
                        f"Spanner {spanner.label!r} lists column {col!r} twice."
                    )
                if col in owner:
                    raise ValueError(
                        f"Column {col!r} is in spanner {owner[col].label!r} and "
                        f"spanner {spanner.label!r}; a column may sit in at most "
                        "one spanner."
                    )
                owner[col] = spanner
        if self.spanners and (self.rows or self.columns or self.values):
            raise ValueError(
                "'spanners' cannot be combined with the pivot channels "
                "rows/columns/values: a pivot already draws its own spanners "
                "from its column dimensions."
            )
        # A field may appear on at most one channel.
        seen: dict[str, str] = {}
        for channel, fields in (
            ("rows", self.rows or []),
            ("columns", self.columns or []),
            ("values", self.values or []),
        ):
            for f in fields:
                if f in seen:
                    raise ValueError(
                        f"Field {f!r} appears on both '{seen[f]}' and '{channel}' channels. "
                        "A field may appear on at most one of rows/columns/values."
                    )
                seen[f] = channel
        return self
