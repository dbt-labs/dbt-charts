//! `chart/authored/area.py`: a filled region under a line.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{CartesianChartFields, PatternChannelField, TooltipFields};
use crate::style::area::AreaChartStyle;
use model_derive::Model;

/// Area chart.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(forbids(tooltip, when(set(layers)),
        message = "tooltip is not supported with layers: an overlay mark's card describes the layer, not a column of the base query."
    )),
    constraint(forbids(layers, when(list(y)), nonempty(layers),
        message = "Area chart: layers are not supported with multi-metric (y: [...]) charts. Use a long-form query with one y field."
    ))
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
    #[model(fill = fill_style, nullable)]
    pub style: AreaChartStyle,
    /// Extra marks on one shared x, each with its own type. Not supported with a multi-measure `y:` list.
    #[model(fill = fill_empty, nullable)]
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart.
    #[model(fill = fill_empty, nullable)]
    pub annotations: Vec<Annotation>,
}
