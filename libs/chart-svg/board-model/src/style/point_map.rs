//! `style/theme/point_map.py`: the point map chart family.

use crate::style::chart_base::GeoChartStyle;
use crate::style::marks::PointMarkStyle;
use model_derive::Model;

/// Point map-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMapChartMarksStyle {
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
}

/// Point map chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMapChartStyle {
    #[flatten]
    pub geo: GeoChartStyle,
    /// Point map-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: PointMapChartMarksStyle,
}
