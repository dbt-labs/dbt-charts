//! `style/theme/heatmap.py`: the heatmap chart family.
//!
//! `marks.text` is `TextMarkStyle | None` under `SkipInheritSlots(cascade=True)`: its `None`
//! only meant "copy the global text mark", which the enclosing `marks` slot's leaf mirroring
//! does here, so the field is the total model.

use crate::primitives::PatternStyle;
use crate::style::chart_base::{CartesianChartStyle, SideLegendedChartStyleBase};
use crate::style::marks::{RectMarkStyle, TextMarkStyle};
use model_derive::Model;

/// Heatmap-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HeatmapChartMarksStyle {
    /// Rect mark overrides; inherits from global. A stroke that leaves its color out strokes in
    /// `auto` (Python's `AutoUnset`), here only: `StrokeStyle` is shared with marks that state
    /// other colors.
    #[model(fill_at(to = "stroke.color", value = "auto"))]
    pub rect: RectMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// Heatmap chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HeatmapChartStyle {
    #[flatten]
    pub legended: SideLegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Padding between heatmap cells in pixels.
    pub cell_padding: f64,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Heatmap-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: HeatmapChartMarksStyle,
}
