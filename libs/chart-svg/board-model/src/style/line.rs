//! `style/theme/line.py`: the line chart family.

use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, LegendedChartStyleBase,
    QuantitativeAxisChartStyleMixin,
};
use crate::style::endpoint_labels::EndpointLabelsConfig;
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{
    LineMarkStyle, LineMarkStyleConditional, PointMarkStyle, PointMarkStyleConditional, RuleMarkStyle, TextMarkStyle,
    TextMarkStyleConditional,
};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Line-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineChartMarksStyle {
    /// Line mark overrides; inherits from global.
    pub line: LineMarkStyle,
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
    /// Text mark overrides; inherits from global.
    pub text: TextMarkStyle,
    /// Rule mark overrides; inherits from global.
    pub rule: RuleMarkStyle,
}

/// The part of [`LineChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineChartMarksStyleConditional {
    /// Line mark overrides; inherits from global.
    #[model(absent = "the line mark's own style")]
    pub line: Option<LineMarkStyleConditional>,
    /// Point mark overrides; inherits from global.
    #[model(absent = "the point mark's own style")]
    pub point: Option<PointMarkStyleConditional>,
    /// Text mark overrides (the value labels); inherits from global.
    #[model(absent = "the text mark's own style")]
    pub text: Option<TextMarkStyleConditional>,
}

/// The part of [`LineChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct LineChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Line-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: LineChartMarksStyleConditional,
}

/// Required wrapper for line-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct LineLayerStyle {
    /// Mark overrides for this line layer.
    pub marks: LineChartMarksStyle,
}

/// Line chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct LineChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: LegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Series names printed at the end of each line instead of in a legend.
    pub endpoint_labels: EndpointLabelsConfig,
    /// Line-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: LineChartMarksStyle,
    /// Rules that restyle a point by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.point color, fill, filled, size, shape, opacity and stroke_width, marks.text font color and weight (the value labels), and marks.line.stroke color, width and dasharray. A line segment takes a stroke key only when both of its end points resolve the same value for it, so a run of matching points restyles the segments between them, and the segment stepping into the run keeps the base style; include the boundary point in the condition to draw it. Curve, connect and the other whole-series properties cannot vary per row. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property. Rules style the base layer, not `layers:`.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<LineChartStyleConditional>>,
}

impl LineChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<LineChartStyle>) -> Vec<Diagnostic> {
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
