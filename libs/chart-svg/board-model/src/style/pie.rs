//! `style/theme/pie.py`: the pie and donut chart family.

use crate::primitives::{ColorStyle, PatternStyle};
use crate::style::chart_base::{ChartStyleBase, PieLegendedChartStyleBase, TitledChartStyleBase};
use crate::style::marks::TotalStyle;
use crate::style::marks::{SliceMarkStyle, TextMarkStyle};
use crate::unit::Fraction;
use model_derive::Model;

/// Pie/donut-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieChartMarksStyle {
    /// Slice mark overrides; inherits from global.
    pub slice: SliceMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// Pie/donut chart style: geometry + total (flat) + marks sub-block.
///
/// `total` stays flat at this level (compositional slot, not a mark). A copy of
/// `RadialChartStyle` with one redeclared field: `aspect_ratio` has no inherit link here, and a
/// flatten cannot override.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieChartStyle {
    #[flatten]
    pub titled: TitledChartStyleBase,
    #[flatten]
    pub legended: PieLegendedChartStyleBase,
    #[flatten]
    pub base: ChartStyleBase,
    /// Aspect ratio (width/height) of the pie chart viewport.
    pub aspect_ratio: f64,
    /// Minimum chart height in pixels.
    #[model(inherit = "Style.charts.min_height")]
    pub min_height: f64,
    /// Maximum chart height in pixels.
    #[model(inherit = "Style.charts.max_height")]
    pub max_height: f64,
    /// Chart color: static mark paint, categorical palette, and/or gradient scale. Only the
    /// single-series ink is the enclosing tier's when unset; the rest of the slot is local.
    #[model(inherit_slot = "Style.charts.color")]
    pub color: ColorStyle,
    /// Hole-to-disk ratio 0–1 (inner radius / outer radius). 0 is a solid pie. A `type: donut` chart reads `charts.donut` over this.
    pub inner_radius: Fraction,
    /// Donut center total paint (value and label).
    pub total: TotalStyle,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Pie-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: PieChartMarksStyle,
}
