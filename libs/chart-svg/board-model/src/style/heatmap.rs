//! `style/theme/heatmap.py`: the heatmap chart family.
//!
//! `marks.text` is `TextMarkStyle | None` under `SkipInheritSlots(cascade=True)`: its `None`
//! only meant "copy the global text mark", which the enclosing `marks` slot's leaf mirroring
//! does here, so the field is the total model.

use crate::primitives::PatternStyle;
use crate::style::chart_base::{CartesianChartStyle, LegendKeyedChartStyleBase};
use crate::chart::conditional_formatting::Predicate;
use crate::style::axis::AxisXConditional;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{RectMarkStyle, RectMarkStyleConditional, TextMarkStyle, TextMarkStyleConditional};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Heatmap-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HeatmapChartMarksStyle {
    /// Rect mark overrides; inherits from global.
    pub rect: RectMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// The part of [`HeatmapChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HeatmapChartMarksStyleConditional {
    /// Rect mark overrides; inherits from global.
    #[model(absent = "the rect mark's own style")]
    pub rect: Option<RectMarkStyleConditional>,
    /// Text mark overrides (the cell labels); inherits from global.
    #[model(absent = "the text mark's own style")]
    pub text: Option<TextMarkStyleConditional>,
}

/// The part of [`HeatmapChartStyle`] a conditional rule may set: one predicate plus the mark style
/// that varies per row.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator"
    )),
    constraint(at_most_one_of(eq, ne, lt, lte, gt, gte, between, "in", is_null, default,
        message = "A conditional rule requires exactly one condition operator, got: {set}"
    )),
    constraint(at_least_one_of(axis_x, marks,
        message = "A conditional rule requires at least one style to apply"
    ))
)]
pub struct HeatmapChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Restyles the x-axis labels the rule matches. A rule on the x column tests each label's own value; a rule on another column tests the rows the label names, which must agree.
    #[model(absent = "the axis's own style")]
    pub axis_x: Option<AxisXConditional>,
    /// Mark overrides.
    #[model(absent = "the marks' own style")]
    pub marks: Option<HeatmapChartMarksStyleConditional>,
}

/// Heatmap chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct HeatmapChartStyle {
    #[flatten]
    pub keyed: LegendKeyedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Padding between heatmap cells in pixels.
    pub cell_padding: f64,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Draw a numeric color as flat tints of the pattern color: each cell takes the tint of its `style.pattern` ramp step.
    #[model(absent = "the color scale's own fills")]
    pub tints: Option<bool>,
    /// Draw the `tints` key as one strip of square swatches in place of the side key, labeled at its two ends in the value-label format.
    #[model(absent = "the side key")]
    pub strip_key: Option<bool>,
    /// Center each x label on the divide where its column starts, as an hour names the moment it begins, instead of on the column's middle.
    #[model(absent = "labels centered on their cells")]
    pub label_divides: Option<bool>,
    /// Heatmap-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: HeatmapChartMarksStyle,
    /// Rules that restyle a cell by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.rect color, opacity and stroke (color, width), and marks.text font color and weight (the cell labels). A rule's color overrides an authored color channel for the matched cell only; the legend is unchanged. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<HeatmapChartStyleConditional>>,
}

impl HeatmapChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<HeatmapChartStyle>) -> Vec<Diagnostic> {
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
