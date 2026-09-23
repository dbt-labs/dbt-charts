"""Resolved pie/donut chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle

from ._base import _SharedResolvedChartFields
from ._layer import FormatState
from .table import ResolvedTableChart


class ResolvedPieChart(_SharedResolvedChartFields):
    """Render-ready pie or donut chart."""

    chart_type: Literal["pie"] = Field(
        description="Discriminator key; always 'pie'.",
    )
    theta: str = Field(
        description="Data column driving angular extent (required).",
    )
    color: str | None = Field(
        default=None,
        description="Data column driving slice color.",
    )
    # Column used to identify each wedge in labels, tooltip, and the
    # attached table -- may differ from `color`. Equals `color`'s resolved
    # field when a color channel is bound; otherwise the query's sole
    # non-theta column, when unambiguous (see infer_implicit_color_field),
    # so a no-`color:` pie can still name its wedges without gaining a
    # distinct-hue paint encoding. None when neither is available.
    identity_field: str | None = Field(
        default=None,
        description="Column identifying each wedge in labels/tooltip/table.",
    )
    total: ChartTotal | None = Field(
        default=None,
        description="Total-value annotation for donut center.",
    )
    format: FormatState = Field(
        default=None,
        description="D3-format string, FormatConfig, or None for slice values.",
    )
    style: ResolvedPieStyle = Field(
        description="Pie family style slice.",
    )
    # Slice label ink, pre-baked from the resolved palette at resolve time
    # (avoids importing compile.palette in render/chart/). One stop per
    # distinct color value in data order when a color channel is present;
    # a single stop for palette[0] otherwise.
    dark_companion_stops: tuple[str, ...] = Field(
        description=(
            "Slice label ink, derived from each mark color against the "
            "chart's canvas so it stays legible on light and dark canvases "
            "alike: a per-category color scale when a color channel is "
            "present, a single static fill otherwise."
        ),
    )
    resolution_width: float = Field(
        description="Card width used to finalize pie presentation policy.",
    )
    outer_fraction: float = Field(
        description="Final wheel radius fraction used by the pie emitter.",
    )
    attached_table_gap: float = Field(
        description="Final gap between the wheel and companion table.",
    )
    hybrid_heading_gap: float = Field(
        description="Final gap below the hybrid companion-table heading.",
    )
    presentation_fingerprint: str = Field(
        description="Canonical fingerprint of the rows used to finalize pie policy.",
    )
    slice_label_indices: tuple[int, ...] = Field(
        description="Row indices whose mechanically rendered slice labels are visible.",
    )
    attached_table: ResolvedTableChart | None = Field(
        default=None,
        description="Final companion table, when direct labels do not suffice.",
    )
    attached_row_indices: tuple[int, ...] = Field(
        default=(), description="Row indices projected into the companion table."
    )
    attached_table_placement: Literal["none", "below", "right"] = Field(default="none")
    attached_table_width: float = Field(default=0.0)
    wheel_width: float = Field(default=0.0)
    attached_heading: str = Field(default="")
    attached_heading_font: ResolvedFontStyle | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_attachment(self) -> ResolvedPieChart:
        if self.attached_table is None:
            if (
                self.attached_row_indices
                or self.attached_table_placement != "none"
                or self.attached_table_width != 0.0
                or self.wheel_width != 0.0
                or self.attached_heading
                or self.attached_heading_font is not None
            ):
                raise ValueError("pie attachment fields must all be absent")
        elif (
            not self.attached_row_indices
            or self.attached_table_placement == "none"
            or self.attached_table_width <= 0.0
            or self.wheel_width <= 0.0
            or self.attached_heading_font is None
        ):
            raise ValueError("pie attachment fields must all be present")
        return self
