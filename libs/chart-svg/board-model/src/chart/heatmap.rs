//! `chart/authored/heatmap.py`: cells shaded by a measure over two bands.

use super::{CartesianChartFields, PatternChannelField};
use crate::style::heatmap::HeatmapChartStyle;
use model_derive::Model;

/// Heatmap chart. `pattern` patterns the measure it shades; a heatmap draws no support table.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(forbids(support_table)),
    constraint(forbids(pattern, "style.pattern", when(set(multiples))))
)]
pub struct HeatmapChart {
    #[flatten]
    pub cartesian: CartesianChartFields,
    #[flatten]
    pub pattern: PatternChannelField,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: HeatmapChartStyle,
}
