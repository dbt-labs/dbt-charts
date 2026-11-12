//! `style/theme/scatter.py`: the scatter chart family.

use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, QuantitativeAxisChartStyleMixin,
    SideLegendedChartStyleBase,
};
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{
    PointMarkStyle, PointMarkStyleConditional, TextMarkStyle, TextMarkStyleConditional,
};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Scatter-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterChartMarksStyle {
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
}

/// The part of [`ScatterChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterChartMarksStyleConditional {
    /// Point mark overrides; inherits from global.
    #[model(absent = "the point mark's own style")]
    pub point: Option<PointMarkStyleConditional>,
    /// Text mark overrides (the value labels); inherits from global.
    #[model(absent = "the text mark's own style")]
    pub text: Option<TextMarkStyleConditional>,
}

/// The part of [`ScatterChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct ScatterChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Scatter-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: ScatterChartMarksStyleConditional,
}

/// Required wrapper for scatter-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct ScatterLayerStyle {
    /// Mark overrides for this scatter layer.
    pub marks: ScatterChartMarksStyle,
}

/// Scatter chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct ScatterChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: SideLegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Scatter-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: ScatterChartMarksStyle,
    /// Rules that restyle a point by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.point color, fill, filled, size, shape, opacity and stroke_width, and marks.text font color and weight. A rule size or shape overrides a `size:` or `shape:` channel for the points it matches; the legend is unchanged. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property. Rules style the base layer, not `layers:`.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<ScatterChartStyleConditional>>,
}

impl ScatterChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<ScatterChartStyle>) -> Vec<Diagnostic> {
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
