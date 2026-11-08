//! `style/theme/spark_bar.py`: the standalone spark_bar chart family.

use crate::facet::Color;
use crate::primitives::CornerStyle;
use crate::style::board::PaddingStyle;
use crate::style::font::FontStyle;
use crate::style::marks::SubtitleStyle;
use model_core::Patch;
use model_derive::Model;

/// Bar geometry sub-block for `SparkBarChartStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarBarStyle {
    /// Height of each bar in pixels.
    pub height: f64,
    /// Vertical padding between bars in pixels.
    pub padding: f64,
    /// Bar fill color.
    #[model(absent = "seeds from style.charts.color.categorical.single_series_palette[0]")]
    pub color: Option<Color>,
    /// Bar track background color - fill-grade, pinned per theme.
    pub background: Color,
}

/// Category-label sub-block for `SparkBarChartStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarChartLabelStyle {
    /// Show series label text next to bars.
    pub visible: bool,
    /// Reserved width for bar label text in pixels.
    pub width: f64,
}

/// Count-value sub-block for `SparkBarChartStyle`.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarCountStyle {
    /// Show count/value text next to bars.
    pub visible: bool,
    /// Reserved width for bar count text in pixels.
    pub width: f64,
}

/// The "+N" note under a truncated spark_bar.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarMoreRowsStyle {
    /// Font style of the "+N" note; the theme binds it to the secondary font.
    #[model(absent = "the enclosing text font applies")]
    pub font: Option<Patch<FontStyle>>,
}

/// Chart-family style for `type: spark_bar` charts.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SparkBarChartStyle {
    /// Preferred spark_bar chart width in pixels.
    pub preferred_width: f64,
    /// Minimum spark_bar chart width in pixels.
    pub min_width: f64,
    /// Padding as {top, right, bottom, left} in pixels, never one number.
    #[model(inherit_slot = "Style.charts.padding")]
    pub padding: PaddingStyle,
    /// Bar geometry (height, padding, color).
    pub bar: SparkBarBarStyle,
    /// Category-label column (visibility, reserved width).
    pub label: SparkBarChartLabelStyle,
    /// Count-value column (visibility, reserved width).
    pub count: SparkBarCountStyle,
    /// Maximum number of bars to render.
    pub max_bars: u32,
    /// Spark_bar font style overrides.
    #[model(inherit_slot = "Style.charts.font")]
    pub font: FontStyle,
    /// Spark_bar outer corner rounding.
    pub border: CornerStyle,
    /// Spark_bar subtitle font-sizing constants.
    pub subtitle: SubtitleStyle,
    /// The "+N" note shown when bars exceed max_bars.
    pub more_rows: SparkBarMoreRowsStyle,
}
