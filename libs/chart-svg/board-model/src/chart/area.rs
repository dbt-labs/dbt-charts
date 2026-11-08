//! `chart/authored/area.py`: a filled region under a line.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{CartesianChartFields, PatternChannelField, TooltipFields};
use crate::style::area::AreaChartStyle;
use model_derive::Model;

/// Area chart.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(forbids(tooltip, when(set(layers)))),
    constraint(forbids(layers, when(list(y)), nonempty(layers)))
)]
#[allow(clippy::duplicated_attributes)]
pub struct AreaChart {
    #[flatten]
    pub tooltip: TooltipFields,
    #[flatten]
    pub cartesian: CartesianChartFields,
    #[flatten]
    pub pattern: PatternChannelField,
    /// Appearance overrides for this chart alone.
    #[model(nullable)]
    pub style: AreaChartStyle,
    /// Extra marks on one shared x, each with its own type. Not supported with a multi-measure `y:` list.
    #[model(fill = fill_empty)]
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart.
    #[model(fill = fill_empty)]
    pub annotations: Vec<Annotation>,
}
