"""Theme-stage style classes: area chart family."""

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

from dbt_charts.core.compile.models.markers import InheritSlot, SkipInheritSlots
from dbt_charts.core.compile.models.style.theme._chart_base import (
    _CartesianChartStyle,
    _QuantitativeAxisChartStyleMixin,
)
from dbt_charts.core.compile.models.style.theme.marks import (
    AreaMarkStyle,
    PointLabelsStyle,
    PointMarkStyle,
    StrokeStyle,
)


class AreaLineStyle(BaseModel):
    """Stroke/halo/labels for an area chart's top-edge line and value labels.

    Vega-Lite itself compiles an area's edge line as a genuine separate
    ``line`` mark (``mark: {type: area, line: {...}}`` produces independent
    ``area``/``line`` marks in the compiled Vega spec), and dbt charts'
    emitter builds the same separate line mark by hand. So the edge's
    stroke geometry belongs here, on the "line" identity — not on
    ``AreaMarkStyle`` (fill only).

    Deliberately NOT the full ``LineMarkStyle``: no ``curve``/``connect``.
    Curve is a single shared value driving both the fill AND this edge line
    (they trace one silhouette) and lives solely on ``AreaMarkStyle.curve``;
    ``connect`` has no area equivalent (see ``AreaMarkStyle``'s docstring).
    A second, differently-scoped ``curve`` field here would silently do
    nothing — the exact bug this type was split out to prevent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cascade tier sentinel: None means "not specified at this tier".
    # SkipInheritSlots(cascade=True): apply_inherit copies the entire stroke object
    # from the parent slot when stroke is None.
    stroke: Annotated[StrokeStyle | None, SkipInheritSlots(cascade=True)] = Field(
        default=None, description="Area top-edge line stroke style."
    )
    # Halo: a knockout-colored line drawn behind the foreground top-edge line so
    # that crossings between series read cleanly.
    # Cascade tier sentinel: None means "not specified at this tier".
    halo_multiplier: float | None = Field(
        default=None,
        description="Halo stroke width multiplier relative to stroke.width; 0 disables the halo.",
    )
    # SkipInheritSlots(cascade=True): leaf fields inside labels inherit individually
    # from the parent slot (no container-copy since labels is never None).
    labels: Annotated[PointLabelsStyle, SkipInheritSlots(cascade=True)] = Field(
        default_factory=PointLabelsStyle,
        description="Value label style for the area's plotted points; inherits from global.",
    )


class AreaChartMarksStyle(BaseModel):
    """Area-family mark overrides.

    ``area`` owns fill (opacity, curve, stacked recipe). ``line`` owns the
    top-edge line's stroke/halo/labels (see ``AreaLineStyle``). ``point``
    overlays real point markers at each plotted value (size 0 by default —
    invisible unless authored, matching the line-chart precedent). No
    ``text`` field: no text-mark authored surface exists for area.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    area: AreaMarkStyle = Field(
        default_factory=AreaMarkStyle,
        description="Area fill mark overrides; inherits from global.",
    )
    line: AreaLineStyle = Field(
        default_factory=AreaLineStyle,
        description="Top-edge line stroke/halo/label overrides; inherits from global.",
    )
    point: PointMarkStyle = Field(
        default_factory=PointMarkStyle,
        description="Point-overlay mark overrides; inherits from global.",
    )


class AreaLayerStyle(BaseModel):
    """Required wrapper for area-layer mark overrides (built into a Patch by build_patch_model)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marks: AreaChartMarksStyle = Field(
        description="Mark overrides for this area layer."
    )


class AreaChartStyle(_CartesianChartStyle, _QuantitativeAxisChartStyleMixin):
    """Area chart style: chart-level fields + marks sub-block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Stack default for the area family. ``"none"`` disables stacking
    # (overlapping silhouettes); other modes are passed through to VL.
    # Used when a chart doesn't author ``chart.stack`` explicitly.
    stack: Literal["none", "zero", "normalize", "center"] = Field(
        description="Default stack mode for area charts: 'none', 'zero', 'normalize', or 'center'."
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
    endpoint_labels: EndpointLabelsConfig = Field(
        description="Series names printed on the bands instead of in a legend."
    )
    marks: Annotated[
        AreaChartMarksStyle, InheritSlot(from_path="Style.charts.marks")
    ] = Field(
        default_factory=AreaChartMarksStyle,
        description="Area-family mark overrides.",
    )
