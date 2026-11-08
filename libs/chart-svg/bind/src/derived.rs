//! The derived style facts a drawn chart needs that bind does not derive yet: its frame, its
//! single-series fill (which reads the board's single-series rhythm) and its walked legend.
//! A stopgap like [`crate::Seating`]: the caller supplies them, computed from the total style
//! as production's style finalization does, until bind derives them itself.

use board_model::ids::ItemId;
use board_model::style::legend::SideLegendStyle;
use color::Rgba;
use indexmap::IndexMap;

use crate::model::Frame;

/// Each drawn placement's derived facts, by where the chart is placed: a chart placed twice
/// in boards styled differently derives differently.
#[derive(Debug, Default)]
pub struct Derived {
    pub charts: IndexMap<ItemId, DerivedChart>,
}

#[derive(Debug, Clone)]
pub struct DerivedChart {
    pub frame: Frame,
    pub single_series_fill: Rgba,
    pub legend: SideLegendStyle,
}
