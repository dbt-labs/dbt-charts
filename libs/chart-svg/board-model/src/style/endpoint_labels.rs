//! `style/authored/_base.py` `EndpointLabelsConfig`: the series-name labels that area, bar and
//! line charts print on the chart in place of a legend.

use crate::rule::Auto;
use model_core::Engine;
use model_derive::Model;

/// Series names printed on the chart itself instead of in a legend.
///
/// Also called direct labels, series labels, or an in-chart legend. Each series gets one label in
/// its own color, beside the plot: to the right on line, area, and vertical stacked bar charts,
/// above the top row on horizontal stacked bars.
///
/// Only shapes that can name every series get them. Line and area charts and stacked bars do;
/// grouped bars, layered charts, small multiples, and very narrow cards keep a legend instead.
/// Where the labels do appear they replace the color legend, so `visible` here, and not `legend`,
/// is what takes series names off the side of those charts. Their font comes from
/// `style.charts.series_label.font`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct EndpointLabelsConfig {
    /// Print series names on the chart instead of in a legend. 'auto' on every built-in theme: used wherever the shape can name every series (line and area charts, and stacked bars), with a legend on grouped bars, layered charts, small multiples, and very narrow cards. Set true to print them on every shape that can, or false to move the names back into a legend everywhere. Where the labels do appear they replace the color legend, so this setting and not `legend` is what removes them.
    pub visible: Engine<bool, Auto>,
    /// Gap in pixels between the plot and the series labels.
    pub label_offset: f64,
    /// Height in pixels of the label strip above a horizontal stacked bar.
    pub height: f64,
}
