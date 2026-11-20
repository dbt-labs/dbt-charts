//! `style/theme/_chart_base.py`: the base every chart family's style builds on.
//!
//! A private Python base is a model of its own and a subclass is `#[flatten]`
//! composition, in Python's field order. `_GeoChartStyle` overrides `color`, which
//! cannot be flattened, so it lists the painted fields itself.
//!
//! A per-family `*StylePatch | None` that `SkipInheritSlots` keeps sparse (`title`,
//! the axes, `annotations`) or that Python leaves `None` unless authored (`legend`)
//! is a `Patch<T>` slot marked `local`: the specificity walk finishes it, never the
//! static inherit. One that is slot-filled (`support_table`,
//! the geo `border`) is the total model with `inherit_slot`; its `None` only meant
//! "copy the parent". `ChartStyleBase.background` is the one `None` that is a state:
//! no chart-local fill.
//!
//! The `*AllOptional` classes are the Python cascade's machinery; the patch twin
//! of each model replaces them.

use crate::facet::Color;
use crate::primitives::{BorderStyle, ColorStyle, GradientColorStyle};
use crate::style::annotations::AnnotationsStyle;
use crate::style::axis::{
    AxisXStyle, AxisYStyle, BandAxisStyle, BaseAxisStyle, QuantitativeAxisStyle,
};
use crate::style::board::{ChartTitleStyle, PaddingStyle};
use crate::style::font::FontStyle;
use crate::style::key::{BarKeyStyle, LegendOnlyKeyStyle, LineAreaKeyStyle, PieKeyStyle};
use crate::style::marks::{BasemapStyle, TotalStyle};
use crate::style::table::SupportTableStyle;
use crate::unit::Fraction;
use model_core::Patch;
use model_derive::Model;

/// Required geometry and frame fields: the cascade source for all chart families.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartStyleBase {
    /// Preferred chart width in pixels.
    #[model(inherit = "Style.charts.preferred_width")]
    pub preferred_width: f64,
    /// Padding as {top, right, bottom, left} in pixels, never one number.
    #[model(inherit_slot = "Style.charts.padding")]
    pub padding: PaddingStyle,
    /// Chart-local background color override.
    #[model(absent = "the theme's background")]
    pub background: Option<Color>,
}

/// The per-family title slot, a sparse override of `ChartsStyle.title`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TitledChartStyleBase {
    /// Chart-level title style override over the theme title style.
    #[model(absent = "the chart's title follows style.charts.title", local)]
    pub title: Option<Patch<ChartTitleStyle>>,
}

/// Font for families with a hand-drawn per-chart card render surface.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartCardStyleMixin {
    /// Chart-level font overrides. Absent: the chart draws in the board's font
    /// (`style.charts.font`).
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
}

/// Families with their own geometry and a legend: cartesian, radial and geo.
///
/// KPI and table use a fixed sizing contract and paint no legend, so they build on
/// [`ChartStyleBase`] alone.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PaintedChartStyleBase {
    #[flatten]
    pub base: ChartStyleBase,
    /// Chart aspect ratio (width/height).
    #[model(inherit = "Style.charts.aspect_ratio")]
    pub aspect_ratio: f64,
    /// Minimum chart height in pixels.
    #[model(inherit = "Style.charts.min_height")]
    pub min_height: f64,
    /// Maximum chart height in pixels.
    #[model(inherit = "Style.charts.max_height")]
    pub max_height: f64,
    /// Chart color scales: categorical palette and/or gradient. Only the
    /// single-series ink is the enclosing tier's when unset; the rest of the slot is local.
    #[model(inherit_slot = "Style.charts.color")]
    pub color: ColorStyle,
}

/// The key slot for line and area.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineAreaKeyedChartStyleBase {
    /// How the chart names its series, a sparse override of `ChartsStyle.key`.
    #[model(absent = "the chart's key follows style.charts.key", local)]
    pub key: Option<Patch<LineAreaKeyStyle>>,
}

/// The key slot for bar.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarKeyedChartStyleBase {
    /// How the chart names its series, a sparse override of `ChartsStyle.key`.
    #[model(absent = "the chart's key follows style.charts.key", local)]
    pub key: Option<Patch<BarKeyStyle>>,
}

/// The key slot for families that can only draw a legend.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendKeyedChartStyleBase {
    /// How the chart names its series, a sparse override of `ChartsStyle.key`.
    #[model(local, default_empty)]
    pub key: Patch<LegendOnlyKeyStyle>,
}

/// The key slot for pie and donut.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieKeyedChartStyleBase {
    /// How the chart names its slices, a sparse override of `ChartsStyle.key`.
    #[model(absent = "the pie key follows style.charts.key", local)]
    pub key: Option<Patch<PieKeyStyle>>,
}

/// Cartesian families: bar, line, area, scatter, histogram, heatmap.
///
/// Each family mixes in its own key slot: only line, area, bar and pie accept
/// `type: direct`.
///
/// The axis slots are sparse overlays: only what a theme authors for the family is
/// set, and every other leaf comes from the global axis when the slot is read, so
/// authoring `bar.axis_x.labels.padding` does not drag prose ink into the label color.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct CartesianChartStyle {
    #[flatten]
    pub titled: TitledChartStyleBase,
    #[flatten]
    pub painted: PaintedChartStyleBase,
    /// Override applied to both x and y axes; unset takes the global axis.
    #[model(absent = "the chart's axes follow style.charts.axis", local)]
    pub axis: Option<Patch<BaseAxisStyle>>,
    /// The category or time axis, in either orientation (a horizontal bar's category axis is still `axis_x`). Takes the global axis_x.
    #[model(absent = "the chart's x axis follows style.charts.axis_x", local)]
    pub axis_x: Option<Patch<AxisXStyle>>,
    /// The measure axis, in either orientation: its number format goes here, a horizontal bar's too. Takes the global axis_y.
    #[model(absent = "the chart's y axis follows style.charts.axis_y", local)]
    pub axis_y: Option<Patch<AxisYStyle>>,
    /// Per-chart-type categorical (band) axis overrides; unset takes the global band axis.
    #[model(absent = "the chart's band axis follows style.charts.axis_band", local)]
    pub axis_band: Option<Patch<BandAxisStyle>>,
    /// Per-chart-type support_table style override.
    #[model(inherit_slot = "Style.charts.support_table")]
    pub support_table: SupportTableStyle,
}

/// `axis_quantitative`: cartesian families with a quantitative axis to style.
///
/// Heatmap has none: both its axes are nominal, so authoring it there is an error.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct QuantitativeAxisChartStyleMixin {
    /// Per-chart-type quantitative-axis overrides; unset takes the global axis_quantitative.
    #[model(
        absent = "the chart's quantitative axis follows style.charts.axis_quantitative",
        local
    )]
    pub axis_quantitative: Option<Patch<QuantitativeAxisStyle>>,
}

/// `annotations`, for the families that draw them: bar, line, area, scatter.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AnnotatedChartStyleMixin {
    /// Default look of this chart's annotations per type (rule, band, note,
    /// trend), over the board's `style.annotations`; an annotation's own `style`
    /// wins over both. The board's slot owns the inheritance.
    #[model(absent = "the board's annotation defaults", local)]
    pub annotations: Option<Patch<AnnotationsStyle>>,
}

/// Radial families: pie, donut.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct RadialChartStyle {
    #[flatten]
    pub titled: TitledChartStyleBase,
    #[flatten]
    pub keyed: PieKeyedChartStyleBase,
    #[flatten]
    pub painted: PaintedChartStyleBase,
    /// Hole-to-disk ratio 0–1 (inner radius / outer radius). 0 is a solid pie. A `type: donut` chart reads `charts.donut` over this.
    pub inner_radius: Fraction,
    /// Donut center total paint (value and label).
    pub total: TotalStyle,
}

/// Geo families: geoshape (choropleth), point_map.
///
/// `geo_source` is a chart-root channel field, not a style concern.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoChartStyle {
    #[flatten]
    pub titled: TitledChartStyleBase,
    #[flatten]
    pub keyed: LegendKeyedChartStyleBase,
    #[flatten]
    pub base: ChartStyleBase,
    /// Chart aspect ratio (width/height).
    #[model(inherit = "Style.charts.aspect_ratio")]
    pub aspect_ratio: f64,
    /// Minimum chart height in pixels.
    #[model(inherit = "Style.charts.min_height")]
    pub min_height: f64,
    /// Maximum chart height in pixels.
    #[model(inherit = "Style.charts.max_height")]
    pub max_height: f64,
    /// Geo color: gradient scale only (no categorical arm).
    #[model(default_empty)]
    pub color: GradientColorStyle,
    #[flatten]
    pub card: ChartCardStyleMixin,
    /// Chart card border style.
    #[model(inherit_slot = "Style.charts.border")]
    pub border: BorderStyle,
    /// Background map layer; no source = no topo layer.
    #[model(default_empty)]
    pub basemap: BasemapStyle,
}
