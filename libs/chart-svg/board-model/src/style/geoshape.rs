//! `style/theme/geoshape.py`: the geoshape (choropleth) chart family.

use crate::style::chart_base::GeoChartStyle;
use crate::style::marks::GeoshapeMarkStyle;
use model_derive::Model;

/// Geoshape (choropleth)-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChartMarksStyle {
    /// Geoshape mark overrides; inherits from global.
    pub geoshape: GeoshapeMarkStyle,
}

/// Geoshape (choropleth) chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChartStyle {
    #[flatten]
    pub geo: GeoChartStyle,
    /// Geoshape (choropleth)-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: GeoshapeChartMarksStyle,
}
