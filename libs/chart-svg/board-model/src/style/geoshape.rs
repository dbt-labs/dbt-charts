//! `style/theme/geoshape.py`: the geoshape (choropleth) chart family.

use crate::style::chart_base::GeoChartStyle;
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{GeoshapeMarkStyle, GeoshapeMarkStyleConditional};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Geoshape (choropleth)-family mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChartMarksStyle {
    /// Geoshape mark overrides; inherits from global.
    pub geoshape: GeoshapeMarkStyle,
}

/// The part of [`GeoshapeChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChartMarksStyleConditional {
    /// Geoshape mark overrides; inherits from global.
    #[model(absent = "the geoshape mark's own style")]
    pub geoshape: Option<GeoshapeMarkStyleConditional>,
}

/// The part of [`GeoshapeChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct GeoshapeChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Geoshape-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: GeoshapeChartMarksStyleConditional,
}

/// Geoshape (choropleth) chart style.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct GeoshapeChartStyle {
    #[flatten]
    pub geo: GeoChartStyle,
    /// Geoshape (choropleth)-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: GeoshapeChartMarksStyle,
    /// Rules that restyle a region by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.geoshape fill and stroke (color, width). A rule's color overrides an authored color channel for the matched region only; the legend is unchanged. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<GeoshapeChartStyleConditional>>,
}

impl GeoshapeChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<GeoshapeChartStyle>) -> Vec<Diagnostic> {
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
