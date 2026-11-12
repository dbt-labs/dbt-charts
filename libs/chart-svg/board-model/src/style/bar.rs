//! `style/theme/bar.py`: the bar chart family.

use crate::primitives::PatternStyle;
use crate::rule::Auto;
use crate::string_enum;
use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, LegendedChartStyleBase,
    QuantitativeAxisChartStyleMixin,
};
use crate::style::endpoint_labels::EndpointLabelsConfig;
use crate::style::marks::{
    BarMarkStyle, BarMarkStyleConditional, TextMarkStyle, TextMarkStyleConditional,
};
use crate::chart::conditional_formatting::Predicate;
use diagnostic::{diag, Diagnostic};
use model_core::{Authored, Tri};
use indexmap::IndexMap;
use model_core::Engine;
use model_derive::Model;

string_enum!(
    /// Preferred bar orientation, beside `auto`.
    Orientation { Horizontal = "horizontal", Vertical = "vertical" }
);
string_enum!(
    /// How series stack: not at all, from zero, to a share, or around the center.
    StackMode { None = "none", Zero = "zero", Normalize = "normalize", Center = "center" }
);
string_enum!(
    /// Z-order of stacked segments.
    StackOrder { Value = "value", Data = "data", Alphabetical = "alphabetical" }
);
string_enum!(
    /// A keyword for the spacing within a group of bars, beside a number.
    Overlap {
        Auto = "auto",
        None = "none",
        Flush = "flush",
        Partial = "partial",
        Full = "full",
    }
);

/// Bar-family mark overrides. Only bar and text (endpoint labels) are valid.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarChartMarksStyle {
    /// Bar mark overrides for bar charts; inherits from global.
    pub bar: BarMarkStyle,
    /// Text mark overrides for bar endpoint labels; inherits from global.
    pub text: TextMarkStyle,
}

/// The part of [`BarChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarChartMarksStyleConditional {
    /// Bar mark overrides for bar charts; inherits from global.
    #[model(absent = "the bar mark's own style")]
    pub bar: Option<BarMarkStyleConditional>,
    /// Text mark overrides for bar endpoint labels; inherits from global.
    #[model(absent = "the text mark's own style")]
    pub text: Option<TextMarkStyleConditional>,
}

/// The part of [`BarChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct BarChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Bar-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: BarChartMarksStyleConditional,
}

/// Band-scale padding for grouped (side-by-side) bars.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(range(padding_inner, ge = 0, lt = 1)),
    constraint(range(padding_outer, ge = 0, lt = 1))
)]
#[allow(clippy::duplicated_attributes)]
pub struct BarGroupedStyle {
    /// Gap between groups, as a share of the band step.
    pub padding_inner: f64,
    /// Margin at both plot edges, as a share of the band step.
    pub padding_outer: f64,
}

/// Required wrapper for bar-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct BarLayerStyle {
    /// Mark overrides for this bar layer.
    pub marks: BarChartMarksStyle,
}

/// Bar chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct BarChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: LegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Preferred bar orientation; unset behaves like 'auto', which picks horizontal for a categorical x and vertical for a continuous one (temporal, quantitative, or date-like). Never remaps x/y.
    #[model(absent = "behaves like 'auto'")]
    pub orientation: Option<Engine<Orientation, Auto>>,
    /// Default stack mode for bar charts; none renders side-by-side columns.
    pub stack: StackMode,
    /// Within-group spacing for grouped bars. Keywords: 'auto' (2 series → partial, 3+ → none), 'none' (small gap), 'flush' (bars touch), 'partial' (25% overlap), 'full' (bars coincide; an error unless each category holds one color). Or a number as a fraction of bar width: >0 overlaps, 0 touches, <0 gaps; 1 or more is 'full'. Unset uses the renderer default ('auto'). Only applies to grouped bars; setting it together with an active stack mode is an error.
    #[model(absent = "the renderer default, 'auto'")]
    pub overlap: Option<Engine<f64, Overlap>>,
    /// Z-order of stacked segments. Unset or 'value' puts the largest aggregate at baseline. 'data' stacks series in first-appearance order: SQL row order for color:, the listed order for y: [a, b, ...] (a measure null in the first rows appears later). 'alphabetical' sorts by series name. Ignored when stacking is off or there is only one series.
    #[model(absent = "ranks by value")]
    pub stack_order: Option<StackOrder>,
    /// Series names printed on stacked bars instead of in a legend.
    pub endpoint_labels: EndpointLabelsConfig,
    /// Band-scale padding for grouped bars.
    pub grouped: BarGroupedStyle,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Bar-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: BarChartMarksStyle,
    /// Rules that restyle a bar by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.bar color, opacity and border, and marks.text font color and weight. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property. Rules style the base layer, not `layers:`.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<BarChartStyleConditional>>,
}

impl BarChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list: a positional rule
    /// over an element field, which no `constraint` kind tests.
    pub fn default_position(style: &Authored<BarChartStyle>) -> Vec<Diagnostic> {
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

/// One diagnostic per `default: true` rule (`is_default[i]`) that is not last in `column`'s list.
pub(crate) fn misplaced_defaults(column: &str, is_default: &[bool]) -> Vec<Diagnostic> {
    let len = is_default.len();
    is_default
        .iter()
        .enumerate()
        .filter(|&(i, &default)| default && i + 1 != len)
        .map(|(i, _)| {
            diag::err_validation_field(
                format!("conditional.{column}"),
                format!("Value error, A rule with default: true must be the last entry in a column's rules (found at position {i} of {len})"),
            )
        })
        .collect()
}
