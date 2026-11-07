//! `chart/authored/line.py`: a line over x. It takes no size or shape channel.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{CartesianChartFields, TooltipFields};
use crate::style::line::LineChartStyle;
use model_derive::Model;

/// Line chart.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(constraint(forbids(tooltip, when(set(layers)))))]
pub struct LineChart {
    #[flatten]
    pub tooltip: TooltipFields,
    #[flatten]
    pub cartesian: CartesianChartFields,
    /// Appearance overrides for this chart alone.
    #[model(nullable)]
    pub style: LineChartStyle,
    /// Column whose order the line follows, instead of x.
    #[model(absent = "the line follows x")]
    pub connect: Option<String>,
    /// Extra marks on one shared x, each with its own type.
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart.
    pub annotations: Vec<Annotation>,
}
