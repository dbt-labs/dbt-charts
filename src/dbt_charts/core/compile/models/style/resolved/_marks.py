"""Resolved mark models shared across chart-family style slices.

ResolvedStrokeStyle/ResolvedAreaMarkStyle/ResolvedLineMarkStyle have all
optional fields baked to concrete values, guaranteed by the resolve stage.
Imported by line.py and area.py.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.primitives import Curve, LineCap
from dbt_charts.core.compile.models.style.theme import PointLabelsStyle


class ResolvedStrokeStyle(BaseModel):
    """Stroke with width required — guaranteed by the resolve stage.

    cap/join/color/dasharray remain Optional: they are legitimately absent
    for some mark families (e.g. no explicit cap on area strokes).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    width: float = Field(description="Stroke width in pixels; required after cascade.")
    color: str | None = Field(
        default=None, description="Stroke color; None inherits the mark fill color."
    )
    cap: LineCap | None = Field(
        default=None, description="Stroke line cap; None uses VL default (butt)."
    )
    join: Literal["miter", "round", "bevel"] | None = Field(
        default=None, description="Stroke line join; None uses VL default (miter)."
    )
    dasharray: str | None = Field(
        default=None, description="SVG stroke-dasharray pattern; None = solid."
    )


class ResolvedAreaMarkStyle(BaseModel):
    """Area fill mark style — opacity, curve, and backdrop only.

    Stroke/halo geometry for the area's top-edge line lives on
    ``ResolvedAreaLineStyle`` (mirrors Vega-Lite itself: an area's edge line
    compiles to a genuine separate ``line`` mark, not an area property).
    ``backdrop`` is independent of the line's ``halo_multiplier`` — a fill
    has no "width" to scale, so it's a plain on/off switch, not sized by the
    edge line's halo knob.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    opacity: float = Field(description="Baked area fill opacity.")
    curve: Curve | None = Field(
        default=None,
        description="Fixed-set interpolation curve; None uses VL default.",
    )
    backdrop: bool = Field(
        description="Whether to paint an opaque fill backdrop behind the area."
    )


class ResolvedLineMarkStyle(BaseModel):
    """Line mark style with stroke/halo baked; curve and labels may be None/default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stroke: ResolvedStrokeStyle = Field(
        description="Baked stroke — width is guaranteed non-None."
    )
    halo_multiplier: float = Field(
        description="Stroke-width multiplier for the halo layer."
    )
    curve: Curve | None = Field(
        default=None,
        description="Fixed-set interpolation curve; None uses VL default.",
    )
    connect: bool | None = Field(
        default=None,
        description=(
            "Band-aware step (curve='step' on a categorical x-axis): join "
            "adjacent plateaus (True/None) or leave disconnected (False)."
        ),
    )
    disconnected_cap: LineCap | None = Field(
        default=None,
        description=(
            "Baked stroke-linecap for the sub-paths of a disconnected "
            "band-step (curve='step' + connect=False on a categorical x). "
            "Selected in place of stroke.cap only in that geometry."
        ),
    )
    labels: PointLabelsStyle = Field(
        description="Baked point labels style.",
    )


class ResolvedAreaLineStyle(BaseModel):
    """Area's top-edge line: stroke/halo geometry + value labels.

    Vega-Lite compiles an area's edge line as a genuine separate ``line``
    mark, and dbt charts' emitter builds the same separate line mark by
    hand — so its stroke geometry belongs here, not on
    ``ResolvedAreaMarkStyle`` (fill only). Deliberately NOT the full
    ``ResolvedLineMarkStyle``: no ``curve``/``connect`` — curve is a single
    shared value driving both the fill and this edge line, and lives solely
    on ``ResolvedAreaMarkStyle.curve``; ``connect`` has no area equivalent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stroke: ResolvedStrokeStyle = Field(
        description="Baked top-edge line stroke — width is guaranteed non-None."
    )
    halo_multiplier: float = Field(
        description="Stroke-width multiplier for the top-edge line's halo layer."
    )
    labels: PointLabelsStyle = Field(
        description="Baked point labels style for the area's plotted points.",
    )


class ResolvedSeriesLabelStyle(BaseModel):
    """Resolved typography + ink for series labels (endpoint labels, stack labels).

    Baked at compile time so the render layer never reaches into
    compile.resolve.style.palette (banned by the render/compile import
    boundary). The dark-companion palette is the
    full stop list; the renderer slices ``[:n_series]`` once it knows the series count.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    font_family: str = Field(description="Series-label font family.")
    font_size: float = Field(description="Series-label font size (px).")
    font_weight: str = Field(description="Series-label font weight as a CSS string.")
    font_style: Literal["normal", "italic"] = Field(
        description="Series-label upright/slanted style."
    )
    dark_companion_palette: tuple[str, ...] = Field(
        description=(
            "Label ink for the full effective palette, one stop per mark, "
            "derived from each mark color against the chart's canvas so it "
            "stays legible on light and dark canvases alike; the renderer "
            "slices [:n_series] for the labels it actually draws."
        ),
    )
    gap_px: float = Field(
        description=(
            "Target pixel separation between adjacent endpoint labels, fully "
            "resolved: the font-size multiple is applied and the tier pick for "
            "this chart's width is already chosen. Render reads this; it does "
            "not re-derive it."
        ),
    )


__all__ = [
    "ResolvedAreaLineStyle",
    "ResolvedAreaMarkStyle",
    "ResolvedLineMarkStyle",
    "ResolvedSeriesLabelStyle",
    "ResolvedStrokeStyle",
]
