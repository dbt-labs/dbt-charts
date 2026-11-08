//! `style/theme/scatter.py`: the scatter chart family.

use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, QuantitativeAxisChartStyleMixin,
    SideLegendedChartStyleBase,
};
use crate::style::marks::{PointMarkStyle, TextMarkStyle};
use model_derive::Model;

/// Scatter-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterChartMarksStyle {
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// Required wrapper for scatter-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct ScatterLayerStyle {
    /// Mark overrides for this scatter layer.
    pub marks: ScatterChartMarksStyle,
}

/// Scatter chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: SideLegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Scatter-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: ScatterChartMarksStyle,
}
