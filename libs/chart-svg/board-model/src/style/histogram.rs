//! `style/theme/histogram.py`: the histogram chart family.
//!
//! `marks.rule` is `RuleMarkStyle | None` under `SkipInheritSlots(cascade=True)`; as in
//! `heatmap`, its `None` only meant "copy the global rule mark", so the field is the total model.

use crate::style::chart_base::{CartesianChartStyle, QuantitativeAxisChartStyleMixin};
use crate::style::marks::{BarMarkStyle, RuleMarkStyle};
use model_derive::Model;

/// Histogram-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HistogramChartMarksStyle {
    /// Bar mark overrides; inherits from global.
    pub bar: BarMarkStyle,
    /// Rule mark overrides; inherits from global.
    pub rule: RuleMarkStyle,
}

/// Histogram chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HistogramChartStyle {
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Maximum number of bins for auto-binning.
    pub bin_maxbins: i32,
    /// Histogram-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: HistogramChartMarksStyle,
}
