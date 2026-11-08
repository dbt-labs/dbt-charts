//! `style/authored/_base.py` `EndpointLabelsConfig`: the series-name labels that area, bar and
//! line charts print on the chart in place of a legend.

use model_derive::Model;

/// Styling of the series names printed on the chart itself instead of in a legend.
///
/// The names appear when `legend.position.edge` is `endpoints`, which `auto` resolves to
/// wherever they can name every series (line and area charts and stacked bars). Also called
/// direct labels, series labels, or an in-chart legend. Each series gets one label in its own
/// color, beside the plot: to the right on line, area, and vertical stacked bar charts, above
/// the top row on horizontal stacked bars. Their font comes from `style.charts.series_label.font`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct EndpointLabelsConfig {
    /// Gap in pixels between the plot and the series labels.
    pub label_offset: f64,
    /// Height in pixels of the label strip above a horizontal stacked bar.
    pub height: f64,
}
