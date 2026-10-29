"""Resolved pie/donut chart model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.primitives import (
    PieMode,
    ResolvedFontStyle,
    ResolvedFormat,
)
from dbt_charts.core.compile.models.style.resolved import ResolvedPieStyle
from dbt_charts.core.compile.models.style.theme import LegendAlign, LegendEdge

from ._base import _PieResolvedChartFields, _TooltipResolvedField
from .table import ResolvedTableChart


class PieAttachment(BaseModel):
    """A pie's companion table and where it sits, resolved for one layout."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table: ResolvedTableChart = Field(description="The resolved table.")
    row_indices: tuple[int, ...] = Field(
        min_length=1, description="Row indices projected into the table."
    )
    placement: Literal["left", "right", "top", "bottom"] = Field(
        description="The edge of the wheel the table sits on."
    )
    align: LegendAlign = Field(description="Table position along its edge.")
    table_width: float = Field(gt=0.0, description="The table's measured width.")
    wheel_width: float = Field(gt=0.0, description="The width the wheel renders in.")
    heading: str = Field(description="Heading over the table; empty for none.")
    heading_font: ResolvedFontStyle = Field(description="The heading's font.")
    overridden_edge: LegendEdge | None = Field(
        description="The edge the author asked for when the card's width forced another.",
    )


class ResolvedPieChart(_PieResolvedChartFields, _TooltipResolvedField):
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
    is_donut: bool = Field(
        description="Authored as a donut: the board's donut slot sizes it, not the pie one.",
    )
    height: float | None = Field(
        default=None,
        description="Authored chart-root height, which pins the content height.",
    )
    total: ChartTotal | None = Field(
        default=None,
        description="Total-value annotation for donut center.",
    )
    format: ResolvedFormat | None = Field(
        default=None,
        description="Authored slice-value format, resolved; None when unauthored.",
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
    pin_companions: tuple[tuple[str, str], ...] = Field(
        description=(
            "Slice label ink for each color a `category_colors` pin names, as "
            "(pinned color, ink) pairs derived against the chart's canvas the "
            "way the palette's label ink is, so a slice pinned to a color "
            "outside the palette is labeled in that color's ink."
        ),
    )
    resolution_width: float = Field(
        description="Card width used to finalize pie presentation policy.",
    )
    outer_fraction: float = Field(
        description="Final wheel radius fraction the pie renders at.",
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
    labeled_mode: Literal["direct", "hybrid", "none"] = Field(
        description="Labeled layout the data allows: 'direct', 'hybrid' or 'none'.",
    )
    hybrid_attachment: PieAttachment | None = Field(
        default=None,
        description="Companion table of the hybrid layout, present when labeled_mode is 'hybrid'.",
    )
    full_attachment: PieAttachment | None = Field(
        default=None,
        description="Companion table of every slice; absent when no slice shows.",
    )

    @model_validator(mode="after")
    def _validate_attachments(self) -> ResolvedPieChart:
        if (self.hybrid_attachment is not None) != (self.labeled_mode == "hybrid"):
            raise ValueError("a hybrid attachment goes with labeled_mode 'hybrid'")
        # A pie with no slice to show (no rows, a non-numeric or all-zero theta)
        # resolves without a full-table candidate; any pie with a slice needs one.
        if self.full_attachment is None and (
            self.labeled_mode != "direct" or self.slice_label_indices
        ):
            raise ValueError("a pie with a slice to show needs a full-table attachment")
        return self

    def attachment_in(self, mode: PieMode) -> PieAttachment | None:
        """The companion table `mode` draws: none for direct."""
        if mode == "hybrid":
            return self.hybrid_attachment
        if mode == "full_table":
            return self.full_attachment
        return None
