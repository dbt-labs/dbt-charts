//! `chart/authored/point_map.py`: points placed by latitude and longitude.

use super::{BasemapConfig, Channel, GeoChartFields};
use crate::style::point_map::PointMapChartStyle;
use model_derive::Model;

/// Point or bubble map chart (`type: point_map` / `type: bubble_map`, synonyms of one model).
/// `collapse` sizes each mark by the count of rows at its location: `size` would bind the same
/// channel, and `color` would split the count back into the piles it merges.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(forbids(size, when(is_in(collapse, true)))),
    constraint(forbids(color, when(is_in(collapse, true))))
)]
#[allow(clippy::duplicated_attributes)]
pub struct PointMapChart {
    #[flatten]
    pub geo: GeoChartFields,
    /// Column containing latitude values for point/bubble maps.
    #[model(absent = "lat/lon not specified")]
    pub latitude: Option<Channel>,
    /// Column containing longitude values for point/bubble maps.
    #[model(absent = "lat/lon not specified")]
    pub longitude: Option<Channel>,
    /// Quantitative column that scales point area. Mutually exclusive with `collapse`.
    #[model(absent = "points share one radius")]
    pub size: Option<Channel>,
    /// Collapse marks sharing an exact latitude/longitude into one mark sized by count. Mutually exclusive with `size` and with any color channel.
    #[model(fill = false)]
    pub collapse: bool,
    /// Styled geographic background layer.
    #[model(absent = "no basemap")]
    pub basemap: Option<BasemapConfig>,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: PointMapChartStyle,
}
