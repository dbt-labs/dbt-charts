//! `chart/authored/scatter.py`: points on an x/y plane, optionally sized and shaped by a column.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{CartesianChartFields, TooltipFields};
use crate::facet::Channel;
use crate::style::scatter::ScatterChartStyle;
use model_derive::Model;

/// Scatter chart. `support_table:` is declared but refused: only bar, line and area draw one.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(forbids(support_table)),
    constraint(forbids(tooltip, when(set(layers)))),
    constraint(forbids(tooltip, when(list(y))))
)]
#[allow(clippy::duplicated_attributes)]
pub struct ScatterChart {
    #[flatten]
    pub tooltip: TooltipFields,
    #[flatten]
    pub cartesian: CartesianChartFields,
    /// Column used to size-encode data points (quantitative).
    #[model(absent = "points share one size")]
    pub size: Option<Channel>,
    /// Column used to shape-encode data points (categorical).
    #[model(absent = "points share one shape")]
    pub shape: Option<Channel>,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: ScatterChartStyle,
    /// Extra marks on one shared x, each with its own type.
    #[model(fill = fill_empty, nullable)]
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart.
    #[model(fill = fill_empty, nullable)]
    pub annotations: Vec<Annotation>,
}
