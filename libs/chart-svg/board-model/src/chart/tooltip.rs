//! `chart/authored/_tooltip.py`: query columns appended to a mark's hover card.

use crate::facet::{Channel, DisplayText};
use crate::primitives::FormatSpec;
use model_derive::Model;

/// One query column shown on the hover card; a bare column name is `{column: name}`.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(shorthand = "column")]
pub struct TooltipField {
    /// Query column whose value the row shows.
    pub column: Channel,
    /// Row label. Defaults to the chart's `field_labels:` entry for the column, else its derived label.
    #[model(absent = "the chart's field label, else the derived one")]
    pub label: Option<DisplayText>,
    /// D3 format string or format config object. Defaults to the query's `formats:` entry for the column.
    #[model(absent = "the query's format for the column")]
    pub format: Option<FormatSpec>,
}
