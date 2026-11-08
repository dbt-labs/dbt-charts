//! The chart: what every family shares, and the tagged family enum
//! (`core/compile/models/chart/authored/{__init__,_base,_type,_tooltip}.py`).
//!
//! A [`Chart`] is one of its families, read by `type:` first (an unknown tag lists the valid
//! tags and the nearest). The enum is declared with the families whose module exists; a family
//! port adds its module and one variant.
//!
//! The pieces every family composes with `#[flatten]`, as the Python bases do:
//!
//! - [`ChartBase`]: the data link, notes, link and ignored warnings of every chart.
//! - [`SharedChartFields`]: the base plus the title and subtitle of every family but KPI.
//! - [`TooltipFields`]: the `tooltip:` of the families whose marks carry a hover card.
//! - [`CartesianChartFields`], [`GeoChartFields`], [`RadialChartFields`]: the channels of the
//!   axis, map and arc families, each over [`SharedChartFields`] and [`FieldLabelsField`].
//! - [`PatternChannelField`], [`ConditionalFormattingField`]: one optional field each.
//!
//! A callout inherits none of them (its Python class does not): it declares what it takes.
//!
//! Which family composes which (Python's class bases; `+` is a `#[flatten]`):
//!
//! | Family | Takes |
//! |---|---|
//! | bar, area | `PatternChannelField` + `CartesianChartFields` + `TooltipFields` |
//! | line, scatter | `CartesianChartFields` + `TooltipFields` |
//! | heatmap | `PatternChannelField` + `CartesianChartFields` |
//! | pie | `PatternChannelField` + `RadialChartFields` + `TooltipFields` |
//! | table | `SharedChartFields` + `ConditionalFormattingField` |
//! | kpi | `ChartBase` + `ConditionalFormattingField` |
//! | spark_bar, custom | `SharedChartFields` |
//! | geoshape, point_map | `GeoChartFields` |
//!
//! `CartesianChartFields` holds `support_table:`, which only bar, line and area draw: the other
//! cartesian families forbid it with a type rule of their own, declared where the family is.
//! The rules a mixin owns are checked by functions on the models, which a family's `check` calls:
//! [`support_table::SupportTableSpec::shape`] and [`CartesianChartFields::support_table_rules`],
//! [`conditional_formatting::FieldConditionalFormatting::default_position`],
//! [`layer::BarLayer::null_y_start`] and [`annotation::NoteAnnotation::pairing`].
//!
//! Off the spine, on purpose: `id` (the key in `Board.charts`, or the tree position),
//! `visible` (the layout item's), `type` (the enum tag), and the facts normalize derives
//! (`query_name`, `query_is_inline`, `variable_dependencies`, `text_queries`,
//! `defined_in_other_file`: `BoardIndex`).

/// A hand-written union leaf: its `ReadScalar` is the reader, `$expr` the schema type it names.
/// Replaced wholesale by an upper layer, like [`FormatSpec`](crate::primitives::FormatSpec).
macro_rules! union_leaf {
    ($t:ty, $expr:expr) => {
        impl ::model_core::FromNode for $t {
            fn authored(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Self::Authored {
                <Self as ::model_core::ReadScalar>::read(node, cx)
            }
            fn patch(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Self::Patch {
                <Self as ::model_core::ReadScalar>::read(node, cx)
            }
            fn whole(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<Self::Whole> {
                <Self as ::model_core::ReadScalar>::read(node, cx)
            }
        }
        impl ::model_core::Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: ::model_core::Merge = ::model_core::Merge::Override;
            const EXPR: ::model_core::TypeExpr = $expr;
        }
        ::model_core::build_leaf!($t);
    };
}

pub mod annotation;
pub mod area;
pub mod bar;
pub mod callout;
pub mod columns;
pub mod conditional_formatting;
pub mod custom;
pub mod geoshape;
pub mod heatmap;
pub mod kpi;
pub mod layer;
pub mod line;
pub mod pie;
pub mod point_map;
pub mod projection;
pub mod query;
pub mod scatter;
pub mod spark_bar;
pub mod support_table;
pub mod table;
pub mod tooltip;

use crate::board::Visible;
use crate::facet::{Channel, Color, DisplayText};
use crate::names::ChartWarningCode;
use crate::primitives::LinkSpec;
use crate::string_enum;
use crate::style::charts::ChartsStyle;
use crate::style::style::Style;
use annotation::{ChartSort, ChartTotal};
use conditional_formatting::FieldConditionalFormatting;
use indexmap::IndexMap;
use model_core::Leaf;
use model_derive::Model;
use projection::ProjectionChoice;
use support_table::SupportTableSpec;

pub use area::AreaChart;
pub use bar::BarChart;
pub use callout::CalloutChart;
pub use columns::{ColorColumn, Columns, LonLat, NonBlankColumn, NumberPair, PatternColumn};
pub use custom::CustomChart;
pub use geoshape::GeoshapeChart;
pub use heatmap::HeatmapChart;
pub use kpi::{KpiChart, KpiSupportConfig};
pub use line::LineChart;
pub use pie::PieChart;
pub use point_map::PointMapChart;
pub use query::{ChartQuery, LayerQuery};
pub use scatter::ScatterChart;
pub use spark_bar::SparkBarChart;
pub use table::{TableChart, TableSpanner};
pub use tooltip::TooltipField;

/// A chart, by family. `type: pie` and `type: donut` are one model under two tags; the donut's
/// hole is `style.inner_radius`.
#[derive(Debug, Clone, PartialEq, Model)]
// A chart is built once per board and held in a map: the family styles are large, not hot.
#[allow(clippy::large_enum_variant)]
#[model(tag = "type")]
pub enum Chart {
    Scatter(ScatterChart),
    Bar(BarChart),
    /// Read as `BarChart`, the model Python's `type: bar | histogram` shares.
    Histogram(BarChart),
    Line(LineChart),
    Area(AreaChart),
    Callout(CalloutChart),
    Heatmap(HeatmapChart),
    Kpi(KpiChart),
    Pie(PieChart),
    Donut(PieChart),
    SparkBar(SparkBarChart),
    Table(TableChart),
    Geoshape(GeoshapeChart),
    /// Read as `GeoshapeChart`, a synonym of `geoshape`.
    Map(GeoshapeChart),
    PointMap(PointMapChart),
    /// Read as `PointMapChart`, a synonym of `point_map`.
    BubbleMap(PointMapChart),
    Custom(CustomChart),
}

impl ChartAuthored {
    /// The `query:` slot, for every family that reads a query (a callout reads none): where
    /// normalize swaps an inline query for its hoisted id.
    pub fn query_mut(&mut self) -> Option<&mut model_core::Tri<ChartQuery>> {
        Some(match self {
            Self::Scatter(c) => &mut c.cartesian.shared.base.query,
            Self::Bar(c) | Self::Histogram(c) => &mut c.cartesian.shared.base.query,
            Self::Line(c) => &mut c.cartesian.shared.base.query,
            Self::Area(c) => &mut c.cartesian.shared.base.query,
            Self::Heatmap(c) => &mut c.cartesian.shared.base.query,
            Self::Kpi(c) => &mut c.base.query,
            Self::Pie(c) | Self::Donut(c) => &mut c.radial.shared.base.query,
            Self::SparkBar(c) => &mut c.shared.base.query,
            Self::Table(c) => &mut c.shared.base.query,
            Self::Geoshape(c) | Self::Map(c) => &mut c.geo.shared.base.query,
            Self::PointMap(c) | Self::BubbleMap(c) => &mut c.geo.shared.base.query,
            Self::Custom(c) => &mut c.shared.base.query,
            Self::Callout(_) => return None,
        })
    }
}

impl ChartAuthored {
    /// The `query:` slot of every layer, for the families that draw layers (bar, line, area and
    /// scatter): where normalize swaps a layer's query name for its hoisted id.
    pub fn layer_queries_mut(&mut self) -> Vec<&mut model_core::Tri<LayerQuery>> {
        let layers = match self {
            Self::Scatter(c) => &mut c.layers,
            Self::Bar(c) | Self::Histogram(c) => &mut c.layers,
            Self::Line(c) => &mut c.layers,
            Self::Area(c) => &mut c.layers,
            _ => return Vec::new(),
        };
        layers
            .iter_mut()
            .flatten()
            .map(|layer| match layer {
                layer::CartesianLayerAuthored::Bar(l) => &mut l.base.query,
                layer::CartesianLayerAuthored::Line(l) => &mut l.base.query,
                layer::CartesianLayerAuthored::Area(l) => &mut l.base.query,
                layer::CartesianLayerAuthored::Scatter(l) => &mut l.base.query,
            })
            .collect()
    }
}

/// Declares, once, which `style.charts.<slot>` each chart variant's own `style:` folds into (Python's
/// `_AUTHORED_FAMILY_TO_MONOLITHIC_KEY`: a histogram reads the bar slot, a donut the pie slot, a
/// `map` the geoshape slot and a `bubble_map` the point-map slot) and builds from it
/// [`ChartAuthored::style_overlay`].
macro_rules! family_slots {
    ($($variant:ident $(| $alias:ident)* => $slot:ident),* $(,)?) => {
        impl ChartAuthored {
            /// This chart's own `style:` as a board-style layer: `charts.<slot>` carrying it, the
            /// shape the chart-local fold applies over its placement's scope. `None` for a chart
            /// that authored no style, and for a custom chart, which has no slot (`compile` refuses
            /// custom charts before it asks).
            pub fn style_overlay(&self) -> Option<<Style as Leaf>::Patch> {
                use model_core::{Empty, ToPatch};
                match self {
                    $(Self::$variant(c) $(| Self::$alias(c))* => {
                        let own = c.style.clone().to_patch();
                        if own.is_empty() {
                            return None;
                        }
                        let mut charts = <ChartsStyle as Leaf>::Patch::default();
                        charts.$slot = Box::new(own);
                        let mut layer = <Style as Leaf>::Patch::default();
                        layer.charts = Box::new(charts);
                        Some(layer)
                    })*
                    Self::Custom(_) => None,
                }
            }

            /// Replace this chart's own `style:` with the family style `scope` holds for its slot:
            /// the total the board is built from. A custom chart has no slot and keeps its own.
            pub fn adopt_style(&mut self, scope: &<Style as Leaf>::Patch) {
                use model_core::ToAuthored;
                match self {
                    $(Self::$variant(c) $(| Self::$alias(c))* => {
                        c.style = (*scope.charts.$slot).clone().to_authored();
                    })*
                    Self::Custom(_) => {}
                }
            }
        }
    };
}

family_slots! {
    Scatter => scatter,
    Bar | Histogram => bar,
    Line => line,
    Area => area,
    Callout => callout,
    Heatmap => heatmap,
    Kpi => kpi,
    Pie | Donut => pie,
    SparkBar => spark_bar,
    Table => table,
    Geoshape | Map => geoshape,
    PointMap | BubbleMap => point_map,
}

/// A chart's `link:`: a URL or `{url, open}`, or `false` to suppress the board's automatic link.
#[derive(Debug, Clone, PartialEq)]
pub enum ChartLink {
    Off,
    On(LinkSpec),
}

impl model_core::ReadScalar for ChartLink {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        use model_core::Kind::Scalar;
        match &node.kind {
            Scalar(yaml::Value::Bool(false)) => Some(Self::Off),
            Scalar(yaml::Value::Bool(true)) => {
                cx.invalid(node.span, "Input should be a valid string");
                None
            }
            Scalar(yaml::Value::Number(_)) => {
                cx.invalid(
                    node.span,
                    "Value error, link must be a URL string, or false to stay unlinked",
                );
                None
            }
            _ => LinkSpec::read(node, cx).map(Self::On),
        }
    }
}
// The schema states the URL and block arms, stricter than the model, which also takes `false`.
union_leaf!(ChartLink, <LinkSpec as model_core::Leaf>::EXPR);

/// Fields every chart family takes.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartBase {
    /// Human-readable notes used by AI search. Emitted into the SVG DOM as a data-chart-notes attribute; never painted as visible pixels.
    #[model(absent = "no notes")]
    pub notes: Option<DisplayText>,
    /// Where this chart reads its data: a query name or SQL text, an inline query block, or a cross-file `{ref: ..}`.
    #[model(absent = "the chart reads no query of its own")]
    pub query: Option<ChartQuery>,
    /// Click-through link for drill-down, or just its URL.
    #[model(absent = "no link of its own; the board's links.auto decides")]
    pub link: Option<ChartLink>,
    /// Whether the chart is drawn where the layout places it: a bool, a variable name, a Jinja expression, or a `{query, column}` probe. Normalize moves it onto the layout placement.
    #[model(directive, absent = "always drawn")]
    pub visible: Option<Visible>,
    /// Codes of render warnings to suppress for this chart.
    #[model(fill = fill_empty)]
    pub warnings_ignore: Vec<ChartWarningCode>,
}

/// Fields every family but KPI takes: the base and a heading.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SharedChartFields {
    #[flatten]
    pub base: ChartBase,
    /// Heading naming what the chart shows, in Title Case like every heading.
    #[model(absent = "no title")]
    pub title: Option<DisplayText>,
    /// Supporting text beneath the chart title.
    #[model(absent = "no subtitle")]
    pub subtitle: Option<DisplayText>,
}

/// The `tooltip:` of bar, line, area, scatter and pie.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TooltipFields {
    /// Query columns to add to the hover card, after the chart's own rows. Each entry is a column name or `{column, label, format}`.
    #[model(fill = fill_empty)]
    pub tooltip: Vec<TooltipField>,
}

/// The `pattern:` channel, for the families that fill marks with it.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PatternChannelField {
    /// Column whose values pick each mark's fill pattern (hatching, dots, dashes), alone or beside hue. When the chart also splits series by hue, both must name the same column; a heatmap patterns the measure it shades.
    #[model(absent = "marks take no fill pattern")]
    pub pattern: Option<PatternColumn>,
}

string_enum!(
    /// Whether small-multiples panels share a measure scale.
    MultiplesScale { Shared = "shared", Independent = "independent" }
);

/// Small-multiples partition, keyed by layout direction.
///
/// `rows` stacks one panel per value vertically; `columns` lays them out side by side. Either or
/// both may be set (rows-only a row stack, columns-only a strip, both a grid), at least one is
/// required.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(at_least_one_of(rows, columns)))]
pub struct MultiplesConfig {
    /// Column whose distinct values become vertically stacked panel rows.
    #[model(absent = "no row partition")]
    pub rows: Option<String>,
    /// Column whose distinct values become side-by-side panel columns.
    #[model(absent = "no column partition")]
    pub columns: Option<String>,
    /// Measure-scale sharing across panels. 'shared' (default) makes panels visually comparable; 'independent' gives each panel its own scale.
    #[model(fill = "shared")]
    pub scale: MultiplesScale,
}

/// The `conditional_formatting:` of table and KPI, indexed by column name.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ConditionalFormattingField {
    /// Discrete rule-driven style overrides indexed by column name. Available on type: table and type: kpi only. On a KPI, rules on the `support.value` column style the support row (font.color, glyph, glyph_color, tone); rules on other columns style the headline.
    #[model(fill = fill_empty)]
    pub conditional_formatting: IndexMap<String, FieldConditionalFormatting>,
}

/// The `field_labels:` of the families that derive text from field names.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct FieldLabelsField {
    /// Display text per query column in legends, tooltips and axis titles. The default is the column name prettified (`model_year` reads "Model year"): set one only to say something different. Painted as written wherever the chart would otherwise derive text from the column's name; x_label/y_label and a layer's label win on their own surface. Not for kpi or table: use `style.columns.<column>.label`.
    #[model(fill = fill_empty)]
    pub field_labels: IndexMap<String, String>,
}

/// Channels of the families with x/y axes (bar, line, area, scatter, heatmap).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(height, gt = 0)), constraint(range(width, gt = 0)))]
#[allow(clippy::duplicated_attributes)]
pub struct CartesianChartFields {
    #[flatten]
    pub labels: FieldLabelsField,
    #[flatten]
    pub shared: SharedChartFields,
    /// X-axis column name from the query result.
    #[model(absent = "column not encoded")]
    pub x: Option<Channel>,
    /// Y-axis column name(s). Accepts a single column or list for multi-series charts.
    #[model(absent = "column not encoded")]
    pub y: Option<Columns>,
    /// X axis title, replacing the column-derived one (`model_year` reads "Model year"): set it only to say something different. Quote a number, as in '2013'.
    #[model(absent = "the column-derived title")]
    pub x_label: Option<String>,
    /// Y axis title, replacing the derived one (`model_year` reads "Model year"): set it only to say something different. Quote a number, as in '2013'.
    #[model(absent = "the column-derived title")]
    pub y_label: Option<String>,
    /// Column that splits the marks into colored series; on heatmap, the measure its cells are shaded by. Bare column name only; a literal color goes in `style.color.static`.
    #[model(absent = "no color encoding: a solid fill from the theme")]
    pub color: Option<ColorColumn>,
    /// Which column orders the marks, and in which direction (asc/desc).
    #[model(absent = "marks keep the query's order")]
    pub sort: Option<ChartSort>,
    /// Small multiples by a `rows` column, a `columns` column, or both. Panels share one measure scale by default. A layer's own `query` must not return the partition column.
    #[model(absent = "one panel")]
    pub multiples: Option<MultiplesConfig>,
    /// Optional mini data-grid attached below/above the chart.
    #[model(absent = "no support table")]
    pub support_table: Option<SupportTableSpec>,
    /// Explicit chart height in pixels. Positive number only. When set, overrides aspect_ratio and theme cascade.
    #[model(absent = "sized by aspect_ratio and the theme cascade")]
    pub height: Option<crate::unit::Number>,
    /// Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot.
    #[model(absent = "the layout's width")]
    pub width: Option<crate::unit::Number>,
}

impl CartesianChartFields {
    /// `_validate_support_table`: how a `support_table:` reads `y:` and `color:`. The entry
    /// variants and `by_measure` flags and the length of `y:` are not field values, so no
    /// `constraint` kind tests them. `y` and `color` are the chart's, as authored.
    pub fn support_table_rules(
        support: &SupportTableSpec,
        y: Option<&Columns>,
        color: Option<&ColorColumn>,
    ) -> Vec<diagnostic::Diagnostic> {
        use diagnostic::diag::err_validation_field as err;
        use support_table::SupportTableEntry::PerSeries;
        let entries = support.entries();
        let mut out = Vec::new();
        if y.is_some_and(|y| y.len() > 1)
            && !entries
                .iter()
                .all(|e| matches!(e, PerSeries(p) if p.by_measure == Some(true)))
        {
            out.push(err("support_table", "Value error, chart.support_table is not supported on charts with a multi-field `y:` list unless every entry is a `per_series:` entry with `by_measure: true`. Collapse `y:` to one field, or use `{per_series: <alias>, by_measure: true}` entries."));
        }
        let needs_color = entries
            .iter()
            .any(|e| matches!(e, PerSeries(p) if p.by_measure != Some(true)));
        if needs_color && color.is_none_or(|c| c.as_str().is_empty()) {
            out.push(err("support_table", "Value error, chart.support_table per_series: entries require the chart to have a color: channel. Add `color: <field>` to the chart."));
        }
        for message in support.shape() {
            out.push(err("support_table", format!("Value error, {message}")));
        }
        out
    }
}

/// Basemap geometry for point-map charts: a styled geographic background layer.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BasemapConfig {
    /// Named geographic boundary source (e.g. 'us-states') for overlay rendering.
    #[model(absent = "no basemap source")]
    pub source: Option<String>,
    /// Fill color for geographic boundary overlay.
    #[model(absent = "the theme's basemap fill")]
    pub fill: Option<Color>,
    /// Stroke color for geographic boundary overlay.
    #[model(absent = "the theme's basemap stroke")]
    pub stroke: Option<Color>,
}

/// A geometry source: a named boundary set, or inline GeoJSON.
#[derive(Debug, Clone, PartialEq)]
pub enum GeoSource {
    Name(String),
    Inline(serde_json::Value),
}

impl model_core::ReadScalar for GeoSource {
    fn read(node: &model_core::Node, cx: &mut model_core::Cx) -> Option<Self> {
        match &node.kind {
            model_core::Kind::Map(_) => Some(Self::Inline(node.to_json())),
            _ => String::read(node, cx).map(Self::Name),
        }
    }
}
union_leaf!(
    GeoSource,
    ::model_core::TypeExpr::Facet("geo_source", ::model_core::Scalar::Str)
);

/// Channels of the map families (geoshape, point_map).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(height, gt = 0)), constraint(range(width, gt = 0)))]
#[allow(clippy::duplicated_attributes)]
pub struct GeoChartFields {
    #[flatten]
    pub labels: FieldLabelsField,
    #[flatten]
    pub shared: SharedChartFields,
    /// Vega projection name, or an object with type and parameters.
    #[model(absent = "the geo source's registry projection")]
    pub projection: Option<ProjectionChoice>,
    /// Column carried on the color channel: fill for geoshape regions, hue for point-map points. Bare column name only; a literal color goes in `style.color.static`.
    #[model(absent = "no color encoding")]
    pub color: Option<ColorColumn>,
    /// Named GeoJSON boundary source, or inline GeoJSON spec, for geoshape charts.
    #[model(absent = "no boundary source")]
    pub geo: Option<GeoSource>,
    /// Named geographic data source for loading GeoJSON boundaries.
    #[model(absent = "no named data source")]
    pub geo_source: Option<String>,
    /// Data column to join against geographic data (map join key).
    #[model(absent = "no join column")]
    pub lookup: Option<Channel>,
    /// Data column mapped to the fill color on geoshape, taking precedence over `color:` when both are set. Ignored on point_map and bubble_map.
    #[model(absent = "no value encoding: the fill uses the static color")]
    pub value: Option<Channel>,
    /// Explicit chart height in pixels. Positive number only.
    #[model(absent = "sized by the theme cascade")]
    pub height: Option<crate::unit::Number>,
    /// Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot.
    #[model(absent = "the layout's width")]
    pub width: Option<crate::unit::Number>,
}

/// Channels of the arc families (pie, donut).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(range(height, gt = 0)), constraint(range(width, gt = 0)))]
#[allow(clippy::duplicated_attributes)]
pub struct RadialChartFields {
    #[flatten]
    pub labels: FieldLabelsField,
    #[flatten]
    pub shared: SharedChartFields,
    /// Column for angular encoding in pie (arc) charts.
    #[model(absent = "bound by position from the query's columns")]
    pub theta: Option<Channel>,
    /// Column naming each wedge, giving it its own hue. Bare column name only; a literal color goes in `style.color.static`.
    #[model(absent = "no color encoding")]
    pub color: Option<ColorColumn>,
    /// Sum of the slice values, drawn in the donut hole.
    #[model(absent = "the donut's auto total")]
    pub total: Option<ChartTotal>,
    /// Explicit chart height in pixels. Positive number only.
    #[model(absent = "sized by the theme cascade")]
    pub height: Option<crate::unit::Number>,
    /// Chart width in pixels. Positive number only. In a rows layout the chart's slot pins to this width (a fixed footprint, capped at the row). In cols and grid layouts it contributes to the dashboard's intrinsic width measurement when the board has no width of its own, and the layout still owns the final slot.
    #[model(absent = "the layout's width")]
    pub width: Option<crate::unit::Number>,
}
