//! `style/theme/line.py`: the line chart family.

use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, LegendedChartStyleBase,
    QuantitativeAxisChartStyleMixin,
};
use crate::style::endpoint_labels::EndpointLabelsConfig;
use crate::style::marks::{LineMarkStyle, PointMarkStyle, RuleMarkStyle, TextMarkStyle};
use model_derive::Model;

/// Line-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineChartMarksStyle {
    /// Line mark overrides; inherits from global.
    pub line: LineMarkStyle,
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
    /// Rule mark overrides; inherits from global.
    pub rule: RuleMarkStyle,
}

/// Required wrapper for line-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct LineLayerStyle {
    /// Mark overrides for this line layer.
    pub marks: LineChartMarksStyle,
}

/// Line chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: LegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Series names printed at the end of each line instead of in a legend.
    pub endpoint_labels: EndpointLabelsConfig,
    /// Line-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: LineChartMarksStyle,
}
