"""Theme-stage style classes: bar chart family."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

if TYPE_CHECKING:
    # These authored types are injected into theme/__init__.py's module globals at
    # runtime by dbt_charts.core.compile.models.style.authored (after authored.py
    # finishes executing). The import here is TYPE_CHECKING-only to give mypy and
    # ruff the static definitions they need without creating a circular import.
    from dbt_charts.core.compile.models.style.authored import (  # noqa: PLC0415
        EndpointLabelsConfig,
    )

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.markers import (
    InheritSlot,
    SkipInheritSlots,  # noqa: F401  # pyright: ignore[reportUnusedImport] — required in module globals for model_rebuild() to resolve inherited base-class annotations
)
from dbt_charts.core.compile.models.primitives import (
    OverlapSpec,
)
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    BarMarkStyle,
    TextMarkStyle,
)


class BarChartMarksStyle(BaseModel):
    """Bar-family mark overrides. Only bar and text (endpoint labels) are valid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bar: BarMarkStyle = Field(
        default_factory=BarMarkStyle,
        description="Bar mark overrides for bar charts; inherits from global.",
    )
    text: TextMarkStyle = Field(
        default_factory=TextMarkStyle,
        description="Text mark overrides for bar endpoint labels; inherits from global.",
    )


class BarLayerStyle(BaseModel):
    """Required wrapper for bar-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: BarChartMarksStyle = Field(description="Mark overrides for this bar layer.")


class BarChartStyle(_CartesianChartStyle, _QuantitativeAxisChartStyleMixin):
    """Bar chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade-managed sentinels — None means "not overridden at this tier".
    orientation: Literal["horizontal", "vertical", "auto"] | None = Field(
        default=None,
        description=(
            "Preferred bar orientation; None behaves like 'auto', which picks "
            "horizontal for a categorical x and vertical for a continuous one "
            "(temporal, quantitative, or date-like). Never remaps x/y."
        ),
    )
    # Stack default for the bar family. ``"none"`` renders grouped (side-by-side)
    # columns when a color channel is present; other modes are passed through to VL.
    # Used when a chart doesn't author ``chart.style.stack`` explicitly.
    stack: Literal["none", "zero", "normalize", "center"] = Field(
        description="Default stack mode for bar charts; none renders side-by-side columns.",
    )
    # Cascade-managed sentinel: None means "not overridden at this tier" and the
    # renderer falls back to 'auto'. Within-group spacing for grouped bars.
    overlap: OverlapSpec = Field(
        default=None,
        description=(
            "Within-group spacing for grouped bars. Keywords: 'auto' "
            "(2 series → partial, 3+ → none), 'none' (small gap), 'flush' "
            "(bars touch), 'partial' (25% overlap), 'full' (bars coincide). Or a "
            "number as a fraction of bar width: >0 overlaps, 0 touches, <0 gaps; "
            "1 is the maximum (bars fully coincide, same as 'full') and values "
            "above 1 are clamped to 1; bars never cross past each other. None "
            "uses the renderer default ('auto'). Only applies to grouped bars; "
            "setting it together with an active stack mode is an error."
        ),
    )
    # None is the unset sentinel: render ranks by value, and no theme sets it.
    stack_order: Literal["value", "data", "alphabetical"] | None = Field(
        default=None,
        description=(
            "Z-order of stacked segments. None/'value' puts the largest aggregate at baseline. "
            "'data' stacks series in first-appearance order: SQL row order for color:, "
            "the listed order for y: [a, b, ...] (a measure null in the first rows "
            "appears later). "
            "'alphabetical' sorts by series name. Ignored when stacking is off or "
            "there is only one series."
        ),
    )
    # Stacked only: resolve turns the rail back off for a grouped bar, whose
    # series all rise from the same baseline and would collide.
    endpoint_labels: EndpointLabelsConfig = Field(
        description="Series names printed on stacked bars instead of in a legend."
    )
    marks: Annotated[
        BarChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=BarChartMarksStyle,
        description="Bar-family mark overrides.",
    )
