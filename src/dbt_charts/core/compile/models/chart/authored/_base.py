"""Authored chart base classes.

  _BaseChartFields       — shared by ALL chart families (id, query, link, ...)
  _SharedChartFields     — + title/subtitle (all families except KPI)
  _CartesianChartFields  — + x/y/color/sort/support_table/... (bar, line, area, scatter, heatmap)
  _GeoChartFields        — + geo/projection/basemap/... (map/geoshape, point_map/bubble_map)
  _RadialChartFields     — + theta/color/total (pie, donut)

Fields are declared ONCE on the appropriate base and never re-declared on
family models. extra="forbid" on each base propagates to all subclasses.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    field_validator,
    model_validator,
)

from dbt_charts.core.compile.models.constraints import (
    AtLeastOneOf,
    ConstrainedModel,
    Constraint,
    Forbids,
    ValueIn,
)
from dbt_charts.core.compile.models.markers import Channel, Color, DisplayText, Url
from dbt_charts.core.compile.models.primitives import CHANNEL_COLUMN, ErrorHint, Link
from dbt_charts.core.compile.models.query.authored import AuthoredQuery
from dbt_charts.core.compile.models.refs import (
    QueryRef,
    normalize_query_value,
    ref_or_inline,
)
from dbt_charts.core.compile.models.schema_names import ChartWarningCode
from dbt_charts.core.compile.models.variable.authored import SingleRowBoolProbe
from dbt_charts.core.compile.models.vega_lite.contracts import Projection
from dbt_charts.core.compile.vega_lite.validation import validate_projection_definition
from dbt_charts.core.diagnostics.suppression import warn_retired_codes

from ._annotations import (
    ChartSort,
    ChartTotal,
)
from ._conditional_formatting import FieldConditionalFormatting
from ._support_table import (
    CHART_SUPPORT_TABLE_SUPPORTED_TYPES,
    ChartSupportTableOrList,
    ChartSupportTablePerSeries,
    validate_support_table_shape,
)


def _normalize_chart_query(v: object) -> object:
    return normalize_query_value(v) if isinstance(v, dict) else v


def _chart_query_tag(
    v: object,  # type-state: object_annotation — Discriminator callable receives raw pre-validation input
) -> str:
    """Discriminate a chart's query field into '@str' / '@inline' / '@ref'.

    A bare string is always a query name or SQL text, never a cross-file
    ref — unlike board-level queries, chart-level strings are resolved
    later in compile/normalize/charts.py, so '@str' short-circuits before
    ref_or_inline's string-to-'@ref' rule can apply.
    """
    if isinstance(v, str):
        return "@str"
    return ref_or_inline(v, QueryRef)


ChartQuery = Annotated[
    Annotated[str, Tag("@str")]
    | Annotated[AuthoredQuery, Tag("@inline")]
    | Annotated[QueryRef, Tag("@ref")],
    Discriminator(_chart_query_tag),
]


def _reject_numeric_link(
    value: object,  # type-state: object_annotation — BeforeValidator receives raw unvalidated YAML input
) -> object:  # type-state: object_annotation — passthrough returns the raw input for Pydantic to validate
    # Lax number→bool coercion would otherwise let link: 0 / 0.0 reach the
    # false arm.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise ValueError("link must be a URL string, or false to stay unlinked")
    return value


# The YAML each chart-color rejection tells the author to write instead.
#
# Named constants rather than inline strings so the tests can *render* the exact
# block the author is handed. Two rounds of review caught a remediation that
# parsed and validated but could not render — first `style: color: '#...'` (a
# string where the field wants a mapping), then a one-element gradient
# `palette: ["dbt-seq-blue"]` (inside a list a name is a stop, not a palette),
# which dead-ended the author in a second, unrelated error. Validating a hint
# does not establish that following it works.
STATIC_COLOR_FIX = "  style:\n    color:\n      static: category[1]"
GRADIENT_COLOR_FIX = (
    "  style:\n    color:\n      gradient:\n        palette: dbt-seq-blue"
)

ChartColorColumn = Annotated[
    str,
    StringConstraints(pattern=CHANNEL_COLUMN),
    ErrorHint(
        "chart.color is a data channel (bare field name only); a literal color "
        "like '{value}' belongs in style:\n" + STATIC_COLOR_FIX
    ),
]

IgnoredChartCode = Annotated[
    ChartWarningCode,
    ErrorHint(
        "unknown or query-domain warning code {value!r} in warnings_ignore. "
        "Use a registered WARN-* code (error codes cannot be suppressed); "
        "query-domain codes such as WARN-REAGGREGATION never carry a chart id, so "
        "suppress them under `queries.<name>.ignore` instead."
    ),
]


def _reject_color_dict(v: Any) -> Any:
    """Shared validator: reject non-string chart.color values."""
    if isinstance(v, dict):
        if "value" in v:
            raise ValueError(
                "color: {value: ...} no longer accepted at chart root. "
                "Move literal colors to style:\n" + STATIC_COLOR_FIX
            )
        if "scale" in v:
            raise ValueError(
                "Inline scale config no longer accepted at chart.color. Keep "
                "your `color: <field>` binding at chart root and move the "
                "scale to style — gradient, not categorical, since a scale "
                "over a field is the continuous arm:\n" + GRADIENT_COLOR_FIX
            )
        if "when" in v:
            raise ValueError(
                "Inline conditional no longer accepted at chart.color. This "
                "family no longer supports rule-driven conditional_formatting; "
                "for a data-driven fill use a gradient scale instead:\n"
                + GRADIENT_COLOR_FIX
            )
        raise ValueError(
            "chart.color only accepts a bare field name (string). "
            "Use style.color for static paint or gradient scale."
        )
    return v


class MultiplesConfig(ConstrainedModel):
    """Small-multiples partition, keyed by layout direction.

    ``rows`` stacks one panel per value vertically; ``columns`` lays them out
    side by side. Both are optional and either or both may be set — rows-only is
    a row stack, columns-only a horizontal strip, both a grid — but at least one
    is required. Naming the direction makes the authored surface self-describing
    (no positional list to document) and lets a single dimension pick its axis.
    """

    model_config = ConfigDict(extra="forbid")

    __constraints__: ClassVar[tuple[Constraint, ...]] = (
        AtLeastOneOf(
            ("rows", "columns"),
            "multiples requires at least one of `rows` or `columns`.",
        ),
    )

    rows: str | None = Field(
        default=None,
        description="Column whose distinct values become vertically stacked panel rows.",
    )
    columns: str | None = Field(
        default=None,
        description="Column whose distinct values become side-by-side panel columns.",
    )
    scale: Literal["shared", "independent"] = Field(
        default="shared",
        description=(
            "Measure-scale sharing across panels. 'shared' (default) makes panels "
            "visually comparable; 'independent' gives each panel its own scale."
        ),
    )


# ============================================================================
# CHART FIELD BASES
# ============================================================================


class _BaseChartFields(ConstrainedModel):
    """Private base shared by ALL chart families including KPI.

    Contains fields that every chart type meaningfully uses, excluding
    title/subtitle which KPI charts reject.
    """

    model_config = ConfigDict(extra="forbid")

    # None = auto-generated chart id from YAML key when omitted.
    id: Annotated[
        str | None,
        Field(
            default=None,
            description="Identifier for this chart. Generated from its `charts:` key, or, written inline, from its title (its `label:` on type: kpi), falling back to its position.",
        ),
    ]
    notes: Annotated[
        str | None,
        DisplayText(),
        Field(
            default=None,
            description=(
                "Human-readable notes used by AI search. Emitted into the "
                "SVG DOM as a data-chart-notes attribute; never painted "
                "as visible pixels."
            ),
        ),
    ]
    # None = author omitted query; normalizer supplies or errors at compile.
    query: Annotated[
        ChartQuery | None,
        BeforeValidator(_normalize_chart_query),
        Field(
            default=None,
            description="Where this chart reads its data: a named query, an inline query block, or a SQL string.",
        ),
    ]
    link: Annotated[
        str | Link | Literal[False] | None,
        BeforeValidator(_reject_numeric_link),
        Url(),
        Field(
            default=None,
            description=(
                "Click-through URL template for drill-down links, or {url, open} "
                "to choose how a click opens the target. Set to false "
                "to suppress this chart's automatic link on a board with "
                "auto_link: true; table column links are unaffected."
            ),
        ),
    ]

    @model_validator(mode="before")
    @classmethod
    def _reject_href(cls, data: Any) -> Any:
        if isinstance(data, dict) and "href" in data:
            raise ValueError(
                "'href:' was renamed to 'link:'. Use `link:` for click-through URLs."
            )
        return data

    # Layout hint, read from the chart wherever it is placed; the layout
    # normalizer keeps it out of the compiled chart dict.
    visible: Annotated[
        bool | str | SingleRowBoolProbe | None,
        Field(
            default=None,
            description="Controls whether this layout item is rendered.",
        ),
    ]
    # Widest base declaration; concrete subclasses narrow via Literal.
    # Required — missing or unknown type raises a discriminator ValidationError.
    type: str
    warnings_ignore: Annotated[
        list[IgnoredChartCode] | None,
        Field(
            default=None,
            description="Codes of render warnings to suppress for this chart. Query-domain codes such as WARN-REAGGREGATION go in `queries.<name>.ignore` instead.",
        ),
    ]

    @field_validator("warnings_ignore")
    @classmethod
    def _warn_retired_ignore_codes(
        cls, v: list[ChartWarningCode] | None
    ) -> list[ChartWarningCode] | None:
        if v is not None:
            warn_retired_codes(v, source="chart.warnings_ignore")
        return v


class _ConditionalFormattingField(BaseModel):
    """Mixin adding conditional_formatting to the chart families that lower it.

    Attached individually to table/kpi — the only families that honor the
    full (table) or partial (kpi) rule-output set. Every other family's
    `extra="forbid"` config makes authoring conditional_formatting on it a
    parse-time ValidationError.
    """

    model_config = ConfigDict(extra="forbid")

    conditional_formatting: Annotated[
        dict[str, FieldConditionalFormatting] | None,
        Field(
            default=None,
            description=(
                "Discrete rule-driven style overrides indexed by column name. "
                "Available on type: table and type: kpi only. On a KPI, rules "
                "on the `support.value` column style the support row (font.color, "
                "glyph, glyph_color, tone); rules on other columns style the "
                "headline."
            ),
        ),
    ]


class _FieldLabelsField(BaseModel):
    """Mixin adding field_labels to the families that derive text from field names."""

    model_config = ConfigDict(extra="forbid")

    field_labels: Annotated[
        dict[str, str],
        Field(
            default_factory=dict,
            description=(
                "Display text per query column in legends, tooltips and axis titles. "
                "Painted as written wherever the chart would otherwise derive text "
                "from the column's name; x_label/y_label and a layer's label win "
                "on their own surface. Not for kpi or table: use "
                "`style.columns.<column>.label`."
            ),
        ),
    ]


class _SharedChartFields(_BaseChartFields):
    """Private base for chart families that support title/subtitle (all except KPI).

    KPI charts must not have title/subtitle — use _BaseChartFields instead.
    """

    # None = no chart title (KPI charts use label: on KpiChart, not title:).
    title: Annotated[
        str | None,
        DisplayText(),
        Field(
            default=None,
            description="Heading naming what the chart shows. Rejected on type: kpi.",
        ),
    ]
    # None = no subtitle.
    subtitle: Annotated[
        str | None,
        DisplayText(),
        Field(
            default=None,
            description="Supporting text beneath the chart title; a KPI uses `support.label` instead.",
        ),
    ]


# Declared on each concrete family whose type can lack support_table, since
# the shared base has no `type` field to key the rule on.
SUPPORT_TABLE_TYPE_RULE = Forbids(
    ("support_table",),
    "chart.support_table is not supported for chart type {type!r} in v1. "
    "Supported chart types: "
    f"{', '.join(sorted(CHART_SUPPORT_TABLE_SUPPORTED_TYPES))}.",
    when=(
        ValueIn(
            "type",
            tuple(sorted(CHART_SUPPORT_TABLE_SUPPORTED_TYPES)),
            negate=True,
        ),
    ),
)


class _CartesianChartFields(_SharedChartFields, _FieldLabelsField):
    """Private base for cartesian chart families (bar, line, area, scatter, heatmap).

    Holds channel fields that only make sense on charts with x/y axes.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    # None = column not encoded.
    x: Annotated[
        str | None,
        Channel(),
        Field(default=None, description="X-axis column name from the query result."),
    ]
    y: Annotated[
        str | list[str] | None,
        Channel(),
        Field(
            default=None,
            description="Y-axis column name(s). Accepts a single column or list for multi-series charts.",
        ),
    ]
    x_label: Annotated[
        str | None,
        Field(
            default=None,
            description="X axis title, replacing the column-derived one. Quote a number, as in '2013'.",
        ),
    ]
    y_label: Annotated[
        str | None,
        Field(
            default=None,
            description="Y axis title, replacing the derived one. Quote a number, as in '2013'.",
        ),
    ]
    # None = no color encoding (solid fill from theme).
    color: Annotated[
        ChartColorColumn | None,
        Channel(),
        Field(
            default=None,
            description="Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only; a literal color goes in `style.color.static`.",
        ),
    ]
    sort: Annotated[
        ChartSort | None,
        Field(
            default=None,
            description="Which column orders the marks, and in which direction (asc/desc).",
        ),
    ]
    # Partition into small multiples by `rows` and/or `columns` (at least one).
    multiples: Annotated[
        MultiplesConfig | None,
        Field(
            default=None,
            description=(
                "Small multiples by a `rows` column, a `columns` column, or both. "
                "Panels share one measure scale by default. A layer's own `query` "
                "must not return the partition column."
            ),
        ),
    ]
    support_table: Annotated[
        ChartSupportTableOrList | None,
        # Every entry names a query column (`source:`/`aggregate.source:`/
        # `per_series:`) — a design surface projects this field to a `list`
        # of those names, the one nested spec that gets a real control rather
        # than a drill-in group (`agent_api/design.py::_support_table_property`).
        Channel(),
        Field(
            default=None,
            description="Optional mini data-grid attached below/above the chart.",
        ),
    ]
    # Box geometry — height and width at chart root, not in style:.
    # aspect_ratio, min_height, max_height belong in style: (promoted to compiled Chart root).
    # Also declared on _GeoChartFields and _RadialChartFields (pie/donut); table,
    # kpi, callout, and spark_bar reject these by omitting them from their
    # authored models — those families own their sizing contract elsewhere.
    height: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Explicit chart height in pixels. Positive number only. When set, "
                "overrides aspect_ratio and theme cascade. Valid on cartesian chart "
                "families (area, bar, heatmap, histogram, line, scatter), pie/donut, "
                "and geo families (geoshape, map, point_map, bubble_map). Other chart "
                "families use renderer-owned or layout-owned sizing contracts."
            ),
        ),
    ]
    width: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Chart width in pixels. Positive number only. In a rows layout "
                "the chart's slot pins to this width (a fixed footprint, capped "
                "at the row). In cols and grid layouts it contributes to the "
                "dashboard's intrinsic width measurement when the board has no "
                "width of its own, and the layout still owns the final slot. "
                "Valid on cartesian chart families (area, bar, heatmap, "
                "histogram, line, scatter), pie/donut, and geo families "
                "(geoshape, map, point_map, bubble_map). Other chart families "
                "use renderer-owned or layout-owned sizing contracts."
            ),
        ),
    ]

    @field_validator("color", mode="before")
    @classmethod
    def _reject_non_column_color(cls, v: Any) -> Any:
        """Color is a data channel — only bare field names accepted at chart root."""
        return _reject_color_dict(v)

    @model_validator(mode="after")
    def _validate_support_table(self) -> _CartesianChartFields:
        if self.support_table is None:
            return self
        if isinstance(self.y, list) and len(self.y) > 1:
            all_by_measure = all(
                isinstance(e, ChartSupportTablePerSeries) and e.by_measure
                for e in self.support_table.entries
            )
            if not all_by_measure:
                raise ValueError(
                    "chart.support_table is not supported on charts with a "
                    "multi-field `y:` list unless every entry is a "
                    "`per_series:` entry with `by_measure: true`. "
                    "Collapse `y:` to one field, or use "
                    "`{per_series: <alias>, by_measure: true}` entries."
                )
        has_per_series_needing_color = any(
            isinstance(e, ChartSupportTablePerSeries) and not e.by_measure
            for e in self.support_table.entries
        )
        if has_per_series_needing_color:
            color = self.color
            has_color = color is not None and color != ""
            if not has_color:
                raise ValueError(
                    "chart.support_table per_series: entries require the chart to have "
                    "a color: channel. Add `color: <field>` to the chart."
                )
        validate_support_table_shape(self.support_table.entries)
        return self


class BasemapConfig(BaseModel):
    """Styled geographic background layer for point_map and bubble_map charts."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(
        default=None,
        description="Named geographic boundary source (e.g. 'us-states') for overlay rendering.",
    )
    fill: Annotated[str | None, Color()] = Field(
        default=None, description="Fill color for geographic boundary overlay."
    )
    stroke: Annotated[str | None, Color()] = Field(
        default=None, description="Stroke color for geographic boundary overlay."
    )


class _GeoChartFields(_SharedChartFields, _FieldLabelsField):
    """Private base for geographic chart families (map/geoshape, point_map/bubble_map)."""

    model_config = ConfigDict(allow_inf_nan=False)

    # None = no projection specified; renderer uses default.
    projection: Annotated[
        str | Projection | None,
        Field(
            default=None,
            description="Map projection name or Vega-Lite projection config.",
        ),
    ]
    # None = no color encoding.
    color: Annotated[
        ChartColorColumn | None,
        Channel(),
        Field(
            default=None,
            description="Column carried on the color channel: fill for geoshape regions, hue for point-map points. Bare column name only; a literal color goes in `style.color.static`.",
        ),
    ]
    geo: Annotated[
        str | dict[str, Any] | None,
        Field(
            default=None,
            description="Named GeoJSON boundary source, or inline GeoJSON spec, for geoshape charts.",
        ),
    ]
    geo_source: Annotated[
        str | None,
        Field(
            default=None,
            description="Named geographic data source for loading GeoJSON boundaries.",
        ),
    ]
    lookup: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Data column to join against geographic data (map join key).",
        ),
    ]
    # None = no value encoding (fill uses static color).
    value: Annotated[
        str | None,
        Channel(),
        Field(
            default=None,
            description="Data column mapped to the fill color on geoshape, taking precedence over `color:` when both are set. Ignored on point_map and bubble_map.",
        ),
    ]
    # Box geometry — height and width at chart root, not in style:.
    height: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description="Explicit chart height in pixels. Positive number only.",
        ),
    ]
    width: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Chart width in pixels. Positive number only. In a rows layout "
                "the chart's slot pins to this width (a fixed footprint, capped "
                "at the row). In cols and grid layouts it contributes to the "
                "dashboard's intrinsic width measurement when the board has no "
                "width of its own, and the layout still owns the final slot."
            ),
        ),
    ]

    @field_validator("projection", mode="before")
    @classmethod
    def _validate_projection(cls, value: Any) -> str | Projection | None:
        return validate_projection_definition(value)


class _RadialChartFields(_SharedChartFields, _FieldLabelsField):
    """Private base for radial/arc chart families (pie, donut).

    Holds channel fields that only make sense on arc charts with angular encoding.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    theta: Annotated[
        str,
        Channel(),
        Field(description="Column for angular encoding in pie (arc) charts."),
    ]
    # None = no color encoding.
    color: Annotated[
        ChartColorColumn | None,
        Channel(),
        Field(
            default=None,
            description="Column naming each wedge, giving it its own hue. Bare column name only; a literal color goes in `style.color.static`.",
        ),
    ]
    total: Annotated[
        ChartTotal | None,
        Field(
            default=None,
            description="Sum of the slice values, drawn in the donut hole.",
        ),
    ]
    # Box geometry — height and width at chart root, not in style:.
    height: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description="Explicit chart height in pixels. Positive number only.",
        ),
    ]
    width: Annotated[
        int | float | None,
        Field(
            default=None,
            gt=0,
            description=(
                "Chart width in pixels. Positive number only. In a rows layout "
                "the chart's slot pins to this width (a fixed footprint, capped "
                "at the row). In cols and grid layouts it contributes to the "
                "dashboard's intrinsic width measurement when the board has no "
                "width of its own, and the layout still owns the final slot."
            ),
        ),
    ]
