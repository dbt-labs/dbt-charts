"""Hand-owned Vega-Lite contracts for dbt charts.

Replaces the auto-generated 458-model Pydantic tree with a small set of
structural contracts that dbt charts actually needs.  Every model uses
``extra="allow"`` so unknown Vega-Lite properties pass through without
error — the contracts enforce *shape*, not exhaustive field-level typing.

The validation layer (``validation.py``) uses these contracts via
``TypeAdapter`` to catch gross structural mistakes (wrong top-level keys,
missing mark type, etc.) while letting Vega-Lite own the full property
semantics at render time.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class VegaLiteBaseModel(BaseModel):
    """Base for all hand-owned Vega-Lite contracts.

    ``extra="allow"`` lets any Vega-Lite property pass through — we
    validate shape, not every field.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Mark types
# ---------------------------------------------------------------------------

Mark = Literal[
    "arc",
    "area",
    "bar",
    "boxplot",
    "circle",
    "errorband",
    "errorbar",
    "geoshape",
    "image",
    "line",
    "point",
    "rect",
    "rule",
    "square",
    "text",
    "tick",
    "trail",
]
"""Valid Vega-Lite mark type strings (primitive + composite)."""


# ---------------------------------------------------------------------------
# Encoding / VLConfig / Scale / Axis / Projection
# ---------------------------------------------------------------------------


class VLConfig(VegaLiteBaseModel):
    """Structural contract for Vega-Lite top-level config."""


class Scale(VegaLiteBaseModel):
    """Structural contract for Vega-Lite scale definitions."""


class Axis(VegaLiteBaseModel):
    """Structural contract for Vega-Lite axis definitions."""


ProjectionType = Literal[
    "mercator",
    "transverseMercator",
    "equirectangular",
    "equalEarth",
    "naturalEarth1",
    "mollweide",
    "azimuthalEqualArea",
    "azimuthalEquidistant",
    "gnomonic",
    "orthographic",
    "stereographic",
    "albers",
    "albersUsa",
    "conicConformal",
    "conicEqualArea",
    "conicEquidistant",
    "identity",
]
"""Every projection Vega registers; the authored ``projection`` is closed to it."""

_LonLat = tuple[float, float]


class Projection(BaseModel):
    """A map projection: a Vega projection type plus its typed parameters.

    Spelled as Vega spells it (``clipAngle``), so the authored object is the
    one Vega-Lite receives. Unlike the rest of this module it is closed: an
    unknown type or key is an authoring error, not a pass-through.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ProjectionType = Field(description="Projection name.")
    center: _LonLat | None = Field(
        default=None, description="Map center as [longitude, latitude]."
    )
    rotate: _LonLat | tuple[float, float, float] | None = Field(
        default=None, description="Rotation as [lambda, phi] or [lambda, phi, gamma]."
    )
    scale: float | None = Field(default=None, description="Projection scale factor.")
    translate: _LonLat | None = Field(
        default=None, description="Pixel offset of the projection center, as [x, y]."
    )
    parallels: _LonLat | None = Field(
        default=None, description="Standard parallels for conic projections."
    )
    clipAngle: float | None = Field(
        default=None, description="Clip angle in degrees from the projection center."
    )
    clipExtent: tuple[_LonLat, _LonLat] | None = Field(
        default=None, description="Clip rectangle as [[x0, y0], [x1, y1]] in pixels."
    )
    precision: float | None = Field(
        default=None, description="Adaptive resampling threshold in pixels."
    )
    reflectX: bool | None = Field(
        default=None, description="Mirror the projected x axis."
    )
    reflectY: bool | None = Field(
        default=None, description="Mirror the projected y axis."
    )
    pointRadius: float | None = Field(
        default=None, description="Radius in pixels for point geometries."
    )


# ---------------------------------------------------------------------------
# Transform / Parameter
# ---------------------------------------------------------------------------


class Transform(VegaLiteBaseModel):
    """Structural contract for a single Vega-Lite transform step."""


class TopLevelParameter(VegaLiteBaseModel):
    """Structural contract for a top-level Vega-Lite parameter."""

    name: str


# ---------------------------------------------------------------------------
# Top-level spec
# ---------------------------------------------------------------------------


class TopLevelUnitSpec(VegaLiteBaseModel):
    """Structural contract for a Vega-Lite top-level unit spec.

    Lists every valid top-level field so ``validation.py`` can derive
    passthrough fields (= all fields minus system-owned ones).
    """

    schema_: str | None = Field(default=None, alias="$schema")
    data: Any = None
    mark: Any = None
    encoding: Any = None
    config: Any = None
    projection: Any = None
    transform: list[Any] | None = None
    params: list[Any] | None = None
    hconcat: list[Any] | None = None
    vconcat: list[Any] | None = None
    concat: list[Any] | None = None
    repeat: Any = None

    # Passthrough-eligible fields (not system-owned)
    align: Any = None
    autosize: Any = None
    background: Any = None
    bounds: Any = None
    center: Any = None
    datasets: Any = None
    description: str | None = None
    height: Any = None
    name: str | None = None
    padding: Any = None
    resolve: Any = None
    spacing: Any = None
    title: Any = None
    usermeta: Any = None
    view: Any = None
    width: Any = None


class TopLevelCompositeSpec(VegaLiteBaseModel):
    """Structural contract for Vega-Lite top-level composition specs."""

    schema_: str | None = Field(default=None, alias="$schema")
    config: Any = None
    projection: Any = None
    transform: list[Any] | None = None
    params: list[Any] | None = None
    hconcat: list[Any] | None = None
    vconcat: list[Any] | None = None
    concat: list[Any] | None = None
    repeat: Any = None
    align: Any = None
    autosize: Any = None
    background: Any = None
    bounds: Any = None
    center: Any = None
    data: Any = None
    datasets: Any = None
    description: str | None = None
    name: str | None = None
    padding: Any = None
    resolve: Any = None
    spacing: Any = None
    title: Any = None
    usermeta: Any = None


TopLevelSpec = TopLevelUnitSpec | TopLevelCompositeSpec
