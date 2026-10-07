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


class Projection(VegaLiteBaseModel):
    """A Vega-Lite projection; any other Vega-Lite projection property is accepted."""

    type: str | None = Field(
        default=None, description="Projection name, such as mercator."
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
