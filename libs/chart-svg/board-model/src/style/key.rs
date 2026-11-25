//! `style/theme/key.py`: how a chart names its series, in a legend or on the chart itself.

use crate::chart::annotation::AnchorScalar;
use crate::string_enum;
use crate::style::legend::{LegendStyle, PieLegendStyle};
use crate::style::marks::SeriesLabelFontStyle;
use crate::rule::Auto;
use indexmap::IndexMap;
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// Name series with a legend or on the chart itself; `auto` is the engine's pick.
    KeyType { Legend = "legend", Direct = "direct" }
);
string_enum!(
    /// A key that can only draw a legend; `auto` is the engine's pick.
    LegendOnlyKeyType { Legend = "legend" }
);

string_enum!(
    DirectPosition { End = "end", Overlay = "overlay" }
);

/// The direct-key leaves every family that names series on the chart reads.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct DirectKeyStyleBase {
    /// Gap in pixels between the plot and the series labels.
    pub offset: f64,
    /// Direct label font; unset fields come from the broader tier.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: SeriesLabelFontStyle,
}

/// The direct-key leaves a line or area reads.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineAreaDirectKeyStyle {
    #[flatten]
    pub base: DirectKeyStyleBase,
    /// Beside the lines' end, over the plot, or `auto` to pick.
    pub position: Engine<DirectPosition, Auto>,
    /// Where each series prefers its name inside the plot, by series name.
    #[model(fill = fill_empty, local)]
    pub overlay_x: IndexMap<String, AnchorScalar>,
}

/// The direct-key leaves a bar reads.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarDirectKeyStyle {
    #[flatten]
    pub base: DirectKeyStyleBase,
    /// Height in pixels of the label strip above a horizontal stacked bar.
    pub height: f64,
}

/// Every direct-key leaf: the board-wide style each family reads its own from.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct DirectKeyStyle {
    #[flatten]
    pub base: LineAreaDirectKeyStyle,
    /// Height in pixels of the label strip above a horizontal stacked bar.
    pub height: f64,
}

/// The board-wide key every family inherits.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ChartsKeyStyle {
    /// Name the series at all; false hides the key.
    pub visible: bool,
    /// Legend, direct labels, or `auto` to let the engine pick.
    pub r#type: Engine<KeyType, Auto>,
    /// Legend style.
    pub legend: LegendStyle,
    /// Direct label style.
    pub direct: DirectKeyStyle,
}

/// The key of a line or area chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineAreaKeyStyle {
    /// Name the series at all; false hides the key.
    pub visible: bool,
    /// Legend, direct labels, or `auto` to let the engine pick.
    pub r#type: Engine<KeyType, Auto>,
    /// Legend style.
    pub legend: LegendStyle,
    /// Direct label style.
    #[model(local)]
    pub direct: LineAreaDirectKeyStyle,
}

/// The key of a bar chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarKeyStyle {
    /// Name the series at all; false hides the key.
    pub visible: bool,
    /// Legend, direct labels, or `auto` to let the engine pick.
    pub r#type: Engine<KeyType, Auto>,
    /// Legend style.
    pub legend: LegendStyle,
    /// Direct label style.
    #[model(local)]
    pub direct: BarDirectKeyStyle,
}

/// The key of a pie or donut chart.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieKeyStyle {
    /// Name the series at all; false hides the key.
    pub visible: bool,
    /// Legend, direct wedge labels, or `auto` to let the engine pick.
    pub r#type: Engine<KeyType, Auto>,
    /// Legend style.
    pub legend: PieLegendStyle,
}

/// The key of a scatter, heatmap, histogram or geo chart: a legend only.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LegendOnlyKeyStyle {
    /// Name the series at all; false hides the key.
    pub visible: bool,
    /// A legend, or `auto` to let the engine decide.
    pub r#type: Engine<LegendOnlyKeyType, Auto>,
    /// Legend style.
    pub legend: LegendStyle,
}
