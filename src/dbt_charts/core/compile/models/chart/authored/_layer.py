"""LAYER PATCH — per-layer authored input for layered charts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dbt_charts.core.compile.models.markers import DisplayText, Format
from dbt_charts.core.compile.models.schema_names import FormatAlias


class LayerAxisYScale(BaseModel):
    """Per-layer y-axis scale patch."""

    model_config = ConfigDict(extra="forbid")

    domain: list[float] | None = Field(
        default=None, description="Explicit [min, max] domain for this layer's y scale."
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

    format: Annotated[FormatAlias | str | None, Format()] = Field(
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


def reject_blank_y_start(
    data: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
    """Refuse a y_start key with no column: YAML parses a blank value as None,
    which would otherwise read as unset and quietly draw the bar from zero."""
    if isinstance(data, dict) and "y_start" in data:
        value = data["y_start"]
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(
                "y_start names no column; remove it for bars that start at zero."
            )
    return data


class TypedLayerBase(BaseModel):
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
    color: str | None = Field(
        default=None,
        description="Column whose values split this layer into colored series; bare column name only.",
    )
    axis_y: LayerAxisYStyle | None = Field(
        default=None,
        description="This layer's own y axis: which side it sits on, its title, scale, ticks, grid.",
    )

    @field_validator("color", mode="before")
    @classmethod
    def _reject_non_column_color(cls, v: Any) -> Any:
        if isinstance(v, str) and v.startswith("#"):
            raise ValueError(
                "Layer.color is a data channel (bare field name only). Paint "
                f"belongs under the layer's `style.marks`, and '{v}' should be "
                "a palette token there (`category[1]`, `negative.solid`) — the "
                "mark key differs per layer type, so see `dct docs charts`."
            )
        if isinstance(v, dict):
            raise ValueError(
                "Layer.color only accepts a bare field name (string). "
                "Dicts (value, scale, when forms) are not supported on layers."
            )
        return v

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

    type: Annotated[Literal["bar"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        BarLayerStylePatch | None,
        Field(
            default=None, description="Appearance overrides for this layer's bar marks."
        ),
    ] = None


class BarChartBarLayer(BarLayer):
    """A bar-type layer on a bar chart, which may start somewhere other than zero.

    Only a bar chart's layers take `y_start`: its bar and theirs share the one
    value axis whose span rules the bar chart checks.
    """

    y_start: str | None = Field(
        default=None,
        description="Column each bar starts from, so it runs from y_start to y instead of from zero. Same kind as the chart's y: both numeric, or both dates.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_blank_y_start(
        cls,
        data: Any,  # type-state: explicit_any — mode="before" validator input; raw YAML value
    ) -> Any:  # type-state: explicit_any — passthrough of the same boundary value
        return reject_blank_y_start(data)


class LineLayer(TypedLayerBase):
    """A line-type layer on a cartesian chart."""

    type: Annotated[Literal["line"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        LineLayerStylePatch | None,
        Field(
            default=None,
            description="Appearance overrides for this layer's line marks.",
        ),
    ] = None


class AreaLayer(TypedLayerBase):
    """An area-type layer on a cartesian chart."""

    type: Annotated[Literal["area"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        AreaLayerStylePatch | None,
        Field(
            default=None,
            description="Appearance overrides for this layer's area marks.",
        ),
    ] = None


class ScatterLayer(TypedLayerBase):
    """A scatter-type layer on a cartesian chart."""

    type: Annotated[Literal["scatter"], Field(description="Selects the layer's mark.")]
    style: Annotated[
        ScatterLayerStylePatch | None,
        Field(
            default=None,
            description="Appearance overrides for this layer's point marks.",
        ),
    ] = None


# Discriminated union for the typed layer list on cartesian charts.
# Field(discriminator="type") lets Pydantic pick the right class from the
# `type` key before validating the rest of the fields.
CartesianLayer = Annotated[
    BarLayer | LineLayer | AreaLayer | ScatterLayer,
    Field(discriminator="type"),
]

# A bar chart's layers: the same, with a bar layer that may take y_start.
BarChartLayer = Annotated[
    BarChartBarLayer | LineLayer | AreaLayer | ScatterLayer,
    Field(discriminator="type"),
]

# Chart `type:` values whose authored class declares a `layers` field.
# BarChart's `type:` also covers "histogram", which does NOT support layers
# (see BarChart._reject_histogram_layers) — kept out of this set on purpose.
CARTESIAN_LAYER_SUPPORTED_CHART_TYPES: frozenset[str] = frozenset(
    {"area", "bar", "line", "scatter"}
)
