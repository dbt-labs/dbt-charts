//! `chart/authored/_layer.py`: the layers of a layered cartesian chart.
//!
//! A layer is one of four marks, told apart by `type:`. Each carries its own marks-group style, so
//! a layer style is checked against its own family's mark styles at authoring time.
//!
//! Dissolved into parse: `encoding` (a Vega-Lite passthrough key) is an unknown key.

use super::{ColorColumn, NonBlankColumn, NumberPair};
use crate::facet::{Color, DisplayText};
use crate::primitives::FormatSpec;
use crate::rule::Auto;
use crate::style::area::AreaLayerStyle;
use crate::style::axis::YAxisPosition;
use crate::style::bar::BarLayerStyle;
use crate::style::line::LineLayerStyle;
use crate::style::scatter::ScatterLayerStyle;
use diagnostic::diag;
use diagnostic::Diagnostic;
use model_core::{Authored, Engine, Patch, Tri};
use model_derive::Model;

/// Per-layer y-axis scale patch.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerAxisYScale {
    /// Explicit [min, max] domain for this layer's y scale.
    #[model(absent = "the domain follows the layer's data")]
    pub domain: Option<NumberPair>,
    /// Whether marks outside the domain are cut off at the plot edge. False lets them run past the plot.
    #[model(fill = true)]
    pub clip: bool,
}

/// Per-layer y-axis tick patch.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerAxisYTicks {
    /// Target number of ticks on this layer's y axis; a target, never an exact count.
    #[model(absent = "the axis decides")]
    pub count: Option<i64>,
}

/// Per-layer y-axis grid patch.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerAxisYGrid {
    /// Whether to show grid lines on this layer's y axis.
    #[model(absent = "the theme's grid visibility")]
    pub visible: Option<bool>,
    /// Grid line color on this layer's y axis.
    #[model(absent = "the axis color")]
    pub color: Option<Color>,
    /// Grid line width on this layer's y axis, in pixels.
    #[model(absent = "the theme's grid width")]
    pub width: Option<f64>,
    /// Grid line dash pattern on this layer's y axis; [] is solid.
    #[model(absent = "the theme's grid dash")]
    pub dash: Option<Vec<f64>>,
    /// Grid line opacity on this layer's y axis, 0 to 1.
    #[model(absent = "the theme's grid opacity")]
    pub opacity: Option<f64>,
}

/// Per-layer y-axis tick-label format patch.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerAxisYLabels {
    /// d3 format string for this layer's y-axis tick labels.
    #[model(absent = "the layer's measure format")]
    pub format: Option<FormatSpec>,
}

/// Per-layer y-axis settings on a layered chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerAxisYStyle {
    /// Y-axis side for this layer (left or right).
    #[model(absent = "the primary axis side")]
    pub position: Option<YAxisPosition>,
    /// Title on this layer's own y axis; defaults to the layer's label.
    #[model(absent = "the layer's label")]
    pub title: Option<DisplayText>,
    /// Color of this axis's grid lines, tick labels and title; 'auto' uses the layer's color.
    #[model(absent = "the theme's axis color")]
    pub color: Option<Engine<Color, Auto>>,
    /// Scale options for this layer's y axis, holding the [min, max] domain.
    #[model(absent = "the layer's default scale")]
    pub scale: Option<LayerAxisYScale>,
    /// Tick options for this layer's y axis, holding the target count.
    #[model(absent = "the axis decides")]
    pub ticks: Option<LayerAxisYTicks>,
    /// Grid options for this layer's y axis, holding its visibility.
    #[model(absent = "the theme's grid")]
    pub grid: Option<LayerAxisYGrid>,
    /// Tick-label options for this layer's y axis, holding the number format.
    #[model(absent = "the layer's measure format")]
    pub labels: Option<LayerAxisYLabels>,
}

/// Identity fields shared by every layer type.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LayerBase {
    /// Query for this layer's data (overrides the chart-level query).
    #[model(absent = "the chart's query")]
    pub query: Option<super::LayerQuery>,
    /// X-axis column name for this layer.
    #[model(absent = "the chart's x column")]
    pub x: Option<String>,
    /// Y-axis column name for this layer.
    #[model(absent = "the chart's y column")]
    pub y: Option<String>,
    /// Name for this layer's measure wherever the layer is identified. Defaults to its y column name.
    #[model(absent = "the y column name")]
    pub label: Option<String>,
    /// Column whose values split this layer into colored series; bare column name only. Paint belongs under the layer's `style.marks`.
    #[model(absent = "the layer is one series", arm_pattern)]
    pub color: Option<ColorColumn>,
    /// This layer's own y axis: which side it sits on, its title, scale, ticks, grid.
    #[model(absent = "the layer shares the chart's y axis")]
    pub axis_y: Option<LayerAxisYStyle>,
    /// Set false to drop this layer's legend entry.
    #[model(fill = true)]
    pub legend: bool,
}

/// A bar-type layer on a cartesian chart.
///
/// A written `y_start` is never `null` (the schema states it; [`BarLayer`]'s reader refuses it).
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(not_null(y_start,
        message = "y_start names no column; remove it for bars that start at zero."
    )))]
pub struct BarLayer {
    #[flatten]
    pub base: LayerBase,
    /// Only `marks`: a bar layer's color is `marks: {bar: {fill: {color: '#hex'}}}`.
    #[model(absent = "the theme's bar layer style", local)]
    pub style: Option<Patch<BarLayerStyle>>,
    /// Column each bar starts from instead of zero (waterfall, range, Gantt, lollipop). Same kind as y: both numeric, or both dates.
    #[model(absent = "bars start at zero", arm_pattern)]
    pub y_start: Option<NonBlankColumn>,
}

/// A line-type layer on a cartesian chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineLayer {
    #[flatten]
    pub base: LayerBase,
    /// Only `marks`: a line layer's color is `marks: {line: {stroke: {color: '#hex'}}}`.
    #[model(absent = "the theme's line layer style", local)]
    pub style: Option<Patch<LineLayerStyle>>,
}

/// An area-type layer on a cartesian chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaLayer {
    #[flatten]
    pub base: LayerBase,
    /// Only `marks`.
    #[model(absent = "the theme's area layer style", local)]
    pub style: Option<Patch<AreaLayerStyle>>,
}

/// A scatter-type layer on a cartesian chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterLayer {
    #[flatten]
    pub base: LayerBase,
    /// Only `marks`: a scatter's color is `marks: {point: {color: '#hex'}}`.
    #[model(absent = "the theme's scatter layer style", local)]
    pub style: Option<Patch<ScatterLayerStyle>>,
}

/// One layer of a cartesian chart, by `type:`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(tag = "type")]
pub enum CartesianLayer {
    Bar(BarLayer),
    Line(LineLayer),
    Area(AreaLayer),
    Scatter(ScatterLayer),
}

/// A bare `y_start:` is null, which must not read as "start at zero". The spine's `absent` field
/// takes null as its absent state, so the authored twin is where it is still seen. Python's one
/// `Y_START_NOT_NULL` rule serves the bar layer and the bar chart alike.
pub fn y_start_not_null(y_start: &Tri<NonBlankColumn>) -> Vec<Diagnostic> {
    if matches!(y_start, Tri::Null) {
        return vec![diag::err_validation_field(
            "y_start",
            "Value error, y_start names no column; remove it for bars that start at zero.",
        )];
    }
    Vec::new()
}

impl BarLayer {
    /// [`y_start_not_null`] on a bar layer.
    pub fn null_y_start(layer: &Authored<BarLayer>) -> Vec<Diagnostic> {
        y_start_not_null(&layer.y_start)
    }
}
