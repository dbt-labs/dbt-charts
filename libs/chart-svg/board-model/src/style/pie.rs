//! `style/theme/pie.py`: the pie and donut chart family.

use crate::primitives::{ColorStyle, PatternStyle};
use crate::style::chart_base::{ChartStyleBase, PieLegendedChartStyleBase, TitledChartStyleBase};
use crate::style::marks::TotalStyle;
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{SliceMarkStyle, SliceMarkStyleConditional, TextMarkStyle, TextMarkStyleConditional};
use crate::unit::Fraction;
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Pie/donut-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieChartMarksStyle {
    /// Slice mark overrides; inherits from global.
    pub slice: SliceMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// The part of [`PieChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PieChartMarksStyleConditional {
    /// Slice mark overrides; inherits from global.
    #[model(absent = "the slice mark's own style")]
    pub slice: Option<SliceMarkStyleConditional>,
    /// Text mark overrides (the slice labels); inherits from global.
    #[model(absent = "the text mark's own style")]
    pub text: Option<TextMarkStyleConditional>,
}

/// The part of [`PieChartStyle`] a conditional rule may set: one predicate plus the mark style
/// that varies per row.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator"
    )),
    constraint(at_most_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator, got: {set}"
    ))
)]
pub struct PieChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Pie-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: PieChartMarksStyleConditional,
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
    /// Chart color scales: categorical palette and/or gradient. Only the
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
    /// Rules that restyle a slice by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.slice color, opacity and stroke (color, width), and marks.text font color and weight. A rule's color overrides an authored color channel for the matched slice only; the legend is unchanged. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<PieChartStyleConditional>>,
}

impl PieChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<PieChartStyle>) -> Vec<Diagnostic> {
        style
            .conditional
            .iter()
            .flat_map(|(column, rules)| {
                let rules = rules.as_deref().unwrap_or_default();
                let defaults: Vec<bool> = rules.iter().map(|r| matches!(r.predicate.default, Tri::Value(_))).collect();
                misplaced_defaults(column, &defaults)
            })
            .collect()
    }
}
