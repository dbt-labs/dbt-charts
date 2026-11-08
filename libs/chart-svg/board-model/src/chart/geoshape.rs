//! `chart/authored/geoshape.py`: filled regions from a boundary source.

use super::GeoChartFields;
use crate::style::geoshape::GeoshapeChartStyle;
use model_derive::Model;

/// Map or geoshape chart (`type: map` / `type: geoshape`, synonyms of one model).
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChart {
    #[flatten]
    pub geo: GeoChartFields,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: GeoshapeChartStyle,
}
