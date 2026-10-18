"""LAYER PATCH — per-layer authored input for layered charts."""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from dbt_charts.core.compile.models.constraints import (
    ConstrainedModel,
    Constraint,
    NotNull,
)
from dbt_charts.core.compile.models.markers import DisplayText, Format
from dbt_charts.core.compile.models.primitives import (
    CHANNEL_COLUMN,
    NON_BLANK,
    ErrorHint,
    FormatConfig,
)
from dbt_charts.core.compile.models.schema_names import FormatAlias


class LayerAxisYScale(BaseModel):
    """Per-layer y-axis scale patch."""

    model_config = ConfigDict(extra="forbid")

    domain: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="Explicit [min, max] domain for this layer's y scale.",
    )
    clip: bool = Field(
        default=True,
        description="Whether marks outside the domain are cut off at the plot edge. False lets them run past the plot.",
    )


class LayerAxisYTicks(BaseModel):
    """Per-layer y-axis tick patch."""

    model_config = ConfigDict(extra="forbid")

    count: int | None = Field(
        default=None,
        description="Target number of ticks on this layer's y axis; a target, never an exact count.",
    )


class LayerAxisYGrid(BaseModel):
    """Per-layer y-axis grid patch."""

    model_config = ConfigDict(extra="forbid")

    visible: bool | None = Field(
        default=None, description="Whether to show grid lines on this layer's y axis."
    )


class LayerAxisYLabels(BaseModel):
    """Per-layer y-axis tick-label format patch."""

    model_config = ConfigDict(extra="forbid")

    format: Annotated[FormatAlias | str | FormatConfig | None, Format()] = Field(
        default=None,
        description="d3 format string for this layer's y-axis tick labels.",
    )


class LayerAxisYStyle(BaseModel):
    """Per-layer y-axis settings on a layered chart."""

    model_config = ConfigDict(extra="forbid")

    position: Literal["left", "right"] | None = Field(
        default=None, description="Y-axis side for this layer (left or right)."
    )
    title: Annotated[str | None, DisplayText()] = Field(
        default=None,
        description="Title on this layer's own y axis; defaults to the layer's label.",
    )
    scale: LayerAxisYScale | None = Field(
        default=None,
        description="Scale options for this layer's y axis, holding the [min, max] domain.",
    )
    ticks: LayerAxisYTicks | None = Field(
        default=None,
        description="Tick options for this layer's y axis, holding the target count.",
    )
    grid: LayerAxisYGrid | None = Field(
        default=None,
        description="Grid options for this layer's y axis, holding its visibility.",
    )
    labels: LayerAxisYLabels | None = Field(
        default=None,
        description="Tick-label options for this layer's y axis, holding the number format.",
    )


# ── typed layer union (BarLayer / LineLayer / AreaLayer / ScatterLayer) ────────
#
# Each typed layer carries its own marks-group style so per-layer style fields
# are validated against the correct family's mark patches at authored time.
# Validators are shared via TypedLayerBase.

from dbt_charts.core.compile.models.style.authored._base import (
    AreaLayerStylePatch,
    BarLayerStylePatch,
    LineLayerStylePatch,
    ScatterLayerStylePatch,
)

LayerColorColumn = Annotated[
    str,
    StringConstraints(pattern=CHANNEL_COLUMN),
    ErrorHint(
        "Layer.color is a data channel (bare field name only). Paint belongs "
        "under the layer's `style.marks`, and '{value}' should be a palette "
        "token there (`category[1]`, `negative.solid`) — the mark key differs "
        "per layer type (`dct skills styling`)."
    ),
]

Y_START_NO_COLUMN = "y_start names no column; remove it for bars that start at zero."
YStartColumn = Annotated[
    str, StringConstraints(pattern=NON_BLANK), ErrorHint(Y_START_NO_COLUMN)
]
# A bare `y_start:` is null, which must not read as "start at zero".
Y_START_NOT_NULL = NotNull(("y_start",), Y_START_NO_COLUMN)


class TypedLayerBase(ConstrainedModel):
    """Common identity fields and validators shared by all typed layer variants."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None,
        description="Query name for this layer's data (overrides chart-level query).",
    )
    x: str | None = Field(
        default=None, description="X-axis column name for this layer."
    )
    y: str | None = Field(
        default=None, description="Y-axis column name for this layer."
    )
    label: str | None = Field(
        default=None,
        description="Name for this layer's measure wherever the layer is identified. Defaults to its y column name.",
    )
    color: LayerColorColumn | None = Field(
        default=None,
        description="Column whose values split this layer into colored series; bare column name only. Paint belongs under the layer's `style.marks`.",
    )
    axis_y: LayerAxisYStyle | None = Field(
        default=None,
        description="This layer's own y axis: which side it sits on, its title, scale, ticks, grid.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_vl_passthrough_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and "encoding" in data:
            raise ValueError(
                "`encoding` is not part of the authored dbt charts layer surface. "
                "Use typed layer channels (`color`) instead."
            )
        return data


class BarLayer(TypedLayerBase):
    """A bar-type layer on a cartesian chart."""

    __constraints__: ClassVar[tuple[Constraint, ...]] = (Y_START_NOT_NULL,)

    type: Annotated[Literal["bar"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        BarLayerStylePatch | None,
        Field(
            default=None,
            description="Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`, a scatter's `marks: {point: {color: '#hex'}}`.",
        ),
    ] = None
    y_start: Annotated[
        YStartColumn | None,
        Field(
            default=None,
            description="Column each bar starts from instead of zero (waterfall, range, Gantt, lollipop). Same kind as y: both numeric, or both dates.",
        ),
    ]


class LineLayer(TypedLayerBase):
    """A line-type layer on a cartesian chart."""

    type: Annotated[Literal["line"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        LineLayerStylePatch | None,
        Field(
            default=None,
            description="Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`, a scatter's `marks: {point: {color: '#hex'}}`.",
        ),
    ] = None


class AreaLayer(TypedLayerBase):
    """An area-type layer on a cartesian chart."""

    type: Annotated[Literal["area"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        AreaLayerStylePatch | None,
        Field(
            default=None,
            description="Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`, a scatter's `marks: {point: {color: '#hex'}}`.",
        ),
    ] = None


class ScatterLayer(TypedLayerBase):
    """A scatter-type layer on a cartesian chart."""

    type: Annotated[Literal["scatter"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        ScatterLayerStylePatch | None,
        Field(
            default=None,
            description="Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`, a scatter's `marks: {point: {color: '#hex'}}`.",
        ),
    ] = None


# Discriminated union for the typed layer list on cartesian charts.
# Field(discriminator="type") lets Pydantic pick the right class from the
# `type` key before validating the rest of the fields.
CartesianLayer = Annotated[
    BarLayer | LineLayer | AreaLayer | ScatterLayer,
    Field(discriminator="type"),
]

# Chart `type:` values whose authored class declares a `layers` field.
# BarChart's `type:` also covers "histogram", which does NOT support layers
# (see BarChart.__constraints__) — kept out of this set on purpose.
CARTESIAN_LAYER_SUPPORTED_CHART_TYPES: frozenset[str] = frozenset(
    {"area", "bar", "line", "scatter"}
)
