//! `style/theme/point_map.py`: the point map chart family.

use crate::style::chart_base::GeoChartStyle;
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{PointMarkStyle, PointMarkStyleConditional};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Point map-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMapChartMarksStyle {
    /// Point mark overrides; inherits from global.
    pub point: PointMarkStyle,
}

/// The part of [`PointMapChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMapChartMarksStyleConditional {
    /// Point mark overrides; inherits from global.
    #[model(absent = "the point mark's own style")]
    pub point: Option<PointMarkStyleConditional>,
}

/// The part of [`PointMapChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct PointMapChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// PointMap-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: PointMapChartMarksStyleConditional,
}

/// Point map chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct PointMapChartStyle {
    #[flatten]
    pub geo: GeoChartStyle,
    /// Point map-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: PointMapChartMarksStyle,
    /// Rules that restyle a point by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.point color, fill, filled, size, shape, opacity and stroke_width. A rule size overrides a `size:` channel for the points it matches; the legend is unchanged. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property. Not available with `collapse`.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<PointMapChartStyleConditional>>,
}

impl PointMapChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<PointMapChartStyle>) -> Vec<Diagnostic> {
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
