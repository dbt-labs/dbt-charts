//! `chart/authored/pie.py`: pie and donut charts.

use super::{PatternChannelField, RadialChartFields, TooltipFields};
use crate::style::pie::PieChartStyle;
use model_derive::Model;

/// Pie or donut chart (`type: pie` / `type: donut`; the donut's hole is `style.inner_radius`,
/// which a donut defaults to 0.6).
///
/// Pie charts use theta (angular) and color (segment) channels. x, y, format, sort and the
/// other cartesian fields are not valid here.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieChart {
    #[flatten]
    pub tooltip: TooltipFields,
    #[flatten]
    pub radial: RadialChartFields,
    #[flatten]
    pub pattern: PatternChannelField,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: PieChartStyle,
}
