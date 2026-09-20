"""Normalized chart base classes (never instantiated directly).

Base-class tree (shallow):
  _BaseChartFields
      _SharedChartFields        (+ title/subtitle)
          _CartesianChartFields (+ x/y/color/sort/support_table/…)
          _GeoChartFields       (+ geo/projection/basemap/…)

Fields are declared ONCE on the appropriate base and never re-declared on
family models. extra="forbid" on the root propagates to all subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.chart.authored import (
    BasemapConfig,
    ChartSort,
    ChartSupportTable,
    FieldConditionalFormatting,
    MultiplesConfig,
)
from dbt_charts.core.compile.models.primitives import FormatConfig, VariableDependencies
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.vega_lite.contracts import Projection


class _BaseChartFields(BaseModel):
    """Universal fields present on every normalized chart family.

    Mirrors authored._BaseChartFields: notes, link, and
    conditional_formatting belong here — declared once, never re-declared
    on any family model.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique identifier within the dashboard.")
    query: AnyQuery | None = Field(
        default=None,
        description="Resolved query object. None for blank/placeholder charts.",
    )
    query_name: str | None = Field(
        default=None,
        description="String name for executor lookup. None for blank/placeholder charts.",
    )
    variable_dependencies: VariableDependencies = Field(
        default_factory=frozenset,
        description="Variable names this chart depends on.",
    )
    source_path: str = Field(
        default="",
        description="YAML path for edit-back support (e.g. 'charts.revenue').",
    )
    # Computed during compilation — not an authored field.
    defined_in_other_file: bool = Field(
        default=False,
        description=(
            "Internal: True when a ChartRef pulled this definition in from "
            "another file, so source_path names a real spot in the importing "
            "file (the reference string) but the renderer withholds the "
            "authoring handle there — an edit belongs in the other file."
        ),
    )
    query_is_inline: bool = Field(
        default=False,
        description="True when query is defined inline; False for named reference.",
    )
    notes: str = Field(
        default="",
        description="Prose summary of what this chart shows, carried onto the rendered chart for tools to surface.",
    )
    # False is the authored per-chart auto_link opt-out; resolve clears it to
    # None at the resolved boundary, so ResolvedChart.link stays str | None.
    link: str | Literal[False] | None = Field(
        default=None,
        description="Click-through URL template, or false to stay unlinked.",
    )
    conditional_formatting: dict[str, FieldConditionalFormatting] | None = Field(
        default=None,
        description="Rule-driven style overrides indexed by column name.",
    )
    rhythm_slot: int = Field(
        default=0,
        description=(
            "Single-series palette rhythm slot assigned by allocate_single_series_slots. "
            "Render uses single_series_palette[rhythm_slot % len] for the chart's ink color."
        ),
    )
    warnings_ignore: list[str] = Field(
        default_factory=list,
        description="Warning codes to suppress for this chart.",
    )


class _SharedChartFields(_BaseChartFields):
    """Chart families that carry a title/subtitle display envelope.

    KpiChart inherits _BaseChartFields directly (uses label: instead).
    All other families inherit this class.
    """

    title: str = Field(default="", description="Display title.")
    subtitle: str = Field(default="", description="Secondary title line.")


class _CartesianChartFields(_SharedChartFields):
    """Cartesian (x/y-axis) chart families: bar, line, area, scatter, heatmap."""

    x: str | None = Field(default=None, description="Field mapped to the x-axis.")
    y: str | list[str] | None = Field(
        default=None, description="Field(s) mapped to the y-axis."
    )
    color: str | None = Field(
        default=None, description="Field mapped to the color channel."
    )
    x_label: str | None = Field(default=None, description="X-axis label override.")
    y_label: str | None = Field(default=None, description="Y-axis label override.")
    sort: ChartSort | None = Field(default=None, description="Chart-level sort config.")
    multiples: MultiplesConfig | None = Field(
        default=None,
        description="Small-multiples partition (canonical form); None = single chart.",
    )
    support_table: ChartSupportTable | None = Field(
        default=None,
        description="Mini data-grid attached to the chart.",
    )
    height: int | float | None = Field(
        default=None, description="Explicit chart height in pixels."
    )
    width: int | float | None = Field(
        default=None, description="Explicit chart width in pixels."
    )
    aspect_ratio: float | None = Field(
        default=None, description="Width-to-height ratio when height is unset."
    )
    min_height: float | None = Field(default=None, description="Minimum height floor.")
    max_height: float | None = Field(
        default=None, description="Maximum height ceiling."
    )
    format: str | FormatConfig | None = Field(
        default=None,
        description="How the number is written: a D3 spec, a preset name, or a format block.",
    )


class _GeoChartFields(_SharedChartFields):
    """Geographic chart families: point_map/bubble_map, geoshape/map."""

    color: str | None = Field(default=None, description="Field mapped to fill color.")
    geo: str | dict[str, Any] | None = Field(
        default=None, description="Named geographic data source or inline config."
    )
    geo_source: str | None = Field(
        default=None,
        description="Named geographic boundary source for overlay rendering.",
    )
    lookup: str | None = Field(
        default=None,
        description="Data field to join against geographic boundary data.",
    )
    value: str | None = Field(
        default=None,
        description="Data field mapped to fill color / choropleth value.",
    )
    projection: str | Projection | None = Field(
        default=None, description="Map projection name or config."
    )
    basemap: BasemapConfig | None = Field(
        default=None, description="Styled geographic background layer."
    )
    height: int | float | None = Field(
        default=None, description="Explicit chart height in pixels."
    )
    width: int | float | None = Field(
        default=None, description="Explicit chart width in pixels."
    )
    aspect_ratio: float | None = Field(
        default=None, description="Width-to-height ratio when height is unset."
    )


# satisfy F401 — Literal/Annotated are re-exported via _base for convenience
__all__ = [
    "_BaseChartFields",
    "_CartesianChartFields",
    "_GeoChartFields",
    "_SharedChartFields",
]


# SVG-family chart types that use hand-crafted SVG renderers instead of Vega-Lite.
# Used for renderer-family classification and to exclude these types from:
#   - aspect-ratio-driven sizing (they don't use width/aspect_ratio for height)
#   - Vega render-first sizing (not Vega charts)
#   - cols height alignment re-renders (non-Vega, don't benefit from re-render)
# Each renderer owns its own sizing contract — there is no shared fixed height for this group.
NON_ASPECT_RATIO_TYPES = frozenset({"table", "kpi", "spark_bar", "callout"})
SVG_LAYOUT_PADDED_TYPES = frozenset({"table", "kpi", "spark_bar"})

# Chart-type strings dispatched through get_emitter() (render/chart/emitters/__init__.py)
# — the families that emit Vega-Lite marks. The complement (table, kpi, spark_bar,
# callout) never paints a mark; it is drawn by a hand-crafted SVG renderer instead.
# This is a different fact from SVG_LAYOUT_PADDED_TYPES above (that one is about
# layout padding, not mark-painting) even though their complements happen to
# coincide today — don't fold them into one set.
#
# These are RESOLVED-stage chart_type strings (ResolvedChart.chart_type), not
# normalized-stage ones. The normalized `.type` vocabulary also contains
# `donut`, `map`, and `bubble_map` — they collapse to `pie`/`geoshape`/
# `point_map` only at resolve. Applying this set to a normalized `.type` would
# silently misclassify those three as non-painting.
PAINTS_MARKS = frozenset(
    {
        "bar",
        "histogram",
        "line",
        "area",
        "scatter",
        "heatmap",
        "pie",
        "geoshape",
        "point_map",
    }
)


@dataclass(frozen=True)
class ChartDependencies:
    """Dependencies required by a chart for rendering.

    Tracks all sources, variables, and queries that a chart needs.
    Used for extracting minimal preview boards.
    """

    sources: frozenset[str] = field(default_factory=frozenset)
    variables: frozenset[str] = field(default_factory=frozenset)
    queries: frozenset[str] = field(default_factory=frozenset)
