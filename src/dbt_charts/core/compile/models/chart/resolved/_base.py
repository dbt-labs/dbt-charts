"""Shared base classes for discriminated resolved chart models.

Stage: COMPILE (parallel stack, types only)

Hierarchy
---------
_BaseResolvedChartFields  — universal (all 13 families, except CalloutChart)
  └─ _SharedResolvedChartFields  — adds title/subtitle (all except KPI + Callout)
       ├─ _CartesianResolvedChartFields  — x/y/color/sort/format/aspect_ratio/…
       └─ _GeoResolvedChartFields        — geo/lookup/value/projection/…

Fields are declared ONCE on the relevant base, never re-declared on family
models.  extra="forbid" propagates to all subclasses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.models.chart.authored import (
    ChartSort,
    ChartSupportTable,
    FieldConditionalFormatting,
    MultiplesConfig,
)
from dbt_charts.core.compile.models.chart.resolved import (
    PartitionAxis,
    ResolvedStyleChannel,
)
from dbt_charts.core.compile.models.primitives import FormatConfig, VariableDependencies
from dbt_charts.core.compile.models.query.normalized import AnyQuery
from dbt_charts.core.compile.models.style.resolved import (
    ResolvedLegendStyle,
)
from dbt_charts.core.compile.models.style.theme.board import PaddingStyle, TitleStyle
from dbt_charts.core.compile.models.style.theme.category_colors import (
    CategoryColorScale,
)
from dbt_charts.core.compile.models.style.theme.table import SupportTableStyle
from dbt_charts.core.compile.models.vega_lite.contracts import Projection


class _BaseResolvedChartFields(BaseModel):
    """Universal fields on every resolved chart (except the minimal CalloutChart).

    Mirrors compiled._BaseCompiledChartFields: notes, link,
    conditional_formatting declared here once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Unique chart id within the board.")
    source_path: str = Field(
        default="",
        description=(
            "Absolute dotted authoring path of this chart (rows.0.cols.1), "
            "carried through resolution so render and diagnostics name it the "
            "same way without re-deriving it from the layout walk. Empty for a "
            "chart with no authored coordinates in this file."
        ),
    )
    defined_in_other_file: bool = Field(
        default=False,
        description=(
            "Internal: True when a ChartRef pulled this definition in from "
            "another file. source_path still names a real spot in the "
            "importing file, but render withholds the authoring handle for it."
        ),
    )
    query: AnyQuery | None = Field(
        default=None,
        description="Resolved query; None for blank/placeholder charts.",
    )
    query_name: str | None = Field(
        default=None,
        description="String name when query is a named reference.",
    )
    variable_dependencies: VariableDependencies = Field(
        description="Variable names this chart depends on.",
    )
    notes: str = Field(
        default="",
        description="Human-readable chart notes. Never rendered.",
    )
    link: str | None = Field(
        default=None,
        description="URL to navigate to on chart click.",
    )
    conditional_formatting: dict[str, FieldConditionalFormatting] | None = Field(
        default=None,
        description="Conditional-formatting rules keyed by column name.",
    )
    # Shared-envelope fields projected from the style cascade.
    # The resolver bakes the style cascade internally, then projects into
    # (1) these typed shared-envelope fields and (2) the per-family style slice
    # on each Resolved*Chart subclass.
    palette: tuple[str, ...] = Field(
        description="Baked color-stop sequence from the style cascade.",
    )
    requested_alias_palette: str | None = Field(
        default=None,
        description=(
            "Set when this chart's palette was authored as a known "
            "WARN-PALETTE-UNSUPPORTED anti-pattern alias (e.g. RdYlGn); holds "
            "the originally-requested name for the render-stage detector."
        ),
    )
    requested_alias_substitute: str | None = Field(
        default=None,
        description=(
            "Display name of the palette dbt charts substituted for "
            "requested_alias_palette — baked alongside it so the "
            "WARN-PALETTE-UNSUPPORTED detector never calls "
            "compile.resolve.style.palette itself."
        ),
    )
    category_colors: tuple[CategoryColorScale, ...] = Field(
        default=(),
        description=(
            "Board-wide value→color scales for the categorical fields THIS "
            "chart encodes. Empty when no field it draws is bound."
        ),
    )
    resolved_channels: dict[str, ResolvedStyleChannel] = Field(
        description="Channel bindings resolved from authored encoding.",
    )
    label: str = Field(
        default="",
        description="Chart label text. KPI overrides to str = '' (headline label). "
        "All other families default to '' (unused). Present on the base so "
        "generic chart-walking code (rendering.py) can access chart.label "
        "without isinstance guards.",
    )
    legend: ResolvedLegendStyle = Field(
        description="Baked board-level legend style from the style cascade.",
    )
    layout_padding: PaddingStyle = Field(
        description=(
            "Effective per-chart layout padding, baked in at construction time by "
            "every family resolver. Combines board.charts.padding with any "
            "chart-local style.<family>.padding override."
        ),
    )


class _SharedResolvedChartFields(_BaseResolvedChartFields):
    """Adds title/subtitle/background for all families except KPI and Callout."""

    title: str | None = Field(
        default=None,
        description=(
            "Jinja-resolved chart title, raw (no title-case transform). Also "
            "backs non-VL consumers (e.g. the data-chart-title wire attribute) "
            "that have always emitted the authored text as-is."
        ),
    )
    subtitle: str | None = Field(default=None, description="Chart subtitle.")
    background: str = Field(
        description=(
            "Effective chart background: chart-local style.<family>.background "
            "when authored, otherwise the board/board background."
        ),
    )
    title_style: TitleStyle = Field(
        description=(
            "Effective title style — chart-local style.<family>.title merged "
            "onto the board title style. Render-time VL assembly reads "
            "title_style.font.case to case-transform `title` (VL presentation "
            "only), and title_style.overflow for title-wrap mode."
        ),
    )


class _CartesianResolvedChartFields(_SharedResolvedChartFields):
    """Cartesian families: bar, line, area, scatter, heatmap.

    Family-specific fields (orientation, stack, size, shape) are declared on
    the individual family models, not here.
    """

    x: str | None = Field(default=None, description="X-axis data column.")
    y: str | list[str] | None = Field(
        default=None, description="Y-axis data column(s)."
    )
    color: str | None = Field(default=None, description="Color-encoding data column.")
    x_label: str | None = Field(
        default=None, description="Override label for the X axis."
    )
    y_label: str | None = Field(
        default=None, description="Override label for the Y axis."
    )
    sort: ChartSort | None = Field(default=None, description="Chart sort config.")
    multiples: MultiplesConfig | None = Field(
        default=None,
        description="Small-multiples partition (canonical form); None = single chart.",
    )
    panel_axes: tuple[PartitionAxis, ...] = Field(
        description=(
            "Baked small-multiples partition axes, derived once from the query "
            "rows at resolve. Every chart carries this — a non-faceted chart "
            "(``multiples is None``) bakes the N=1 case: an empty tuple, not a "
            "special-cased absence. Render regroups its rows by these axes "
            "rather than re-deriving the partition from ``multiples`` itself."
        ),
    )
    support_table: ChartSupportTable | None = Field(
        default=None,
        description=(
            "Attached support-table config, final: entries reading a single "
            "numeric string y column with no authored format already carry "
            "the theme's default number format stamped on."
        ),
    )
    format: str | FormatConfig | None = Field(
        default=None,
        description="D3-format string, FormatConfig, or None for primary numeric axis.",
    )
    aspect_ratio: float | None = Field(
        default=None, description="Width:height aspect ratio."
    )
    min_height: float | None = Field(default=None, description="Minimum chart height.")
    max_height: float | None = Field(default=None, description="Maximum chart height.")
    # Baked at resolve time so render never re-runs the axis cascade to
    # compute support_table strip geometry — see compile/support_table.py's
    # axis_offset()/resolve_effective_support_table_style(), the per-chart
    # cascade steps this projects. None when support_table is None.
    support_table_axis_offset: float | None = Field(
        default=None,
        description="Pixel offset from plot bottom to the x-axis block bottom, for support_table strip placement.",
    )
    effective_support_table_style: SupportTableStyle | None = Field(
        default=None,
        description="Final per-chart support_table style (board theme merged with the family override).",
    )


class _GeoResolvedChartFields(_SharedResolvedChartFields):
    """Geographic families: geoshape/map, point_map/bubble_map.

    Only the projection hint is shared across geo families.  The heavier geo
    descriptor fields (geo_url, geo_format_type, geo_feature, geo_join_key,
    lookup_field, value_field, …) live on ``ResolvedGeoshapeChart`` only —
    point_map and bubble_map are driven by latitude/longitude channels, not
    TopoJSON feature lookups, so those fields have no meaning there.
    """

    projection: str | Projection | None = Field(
        default=None, description="Map projection name or config."
    )


__all__ = [
    "_BaseResolvedChartFields",
    "_CartesianResolvedChartFields",
    "_GeoResolvedChartFields",
    "_SharedResolvedChartFields",
]
