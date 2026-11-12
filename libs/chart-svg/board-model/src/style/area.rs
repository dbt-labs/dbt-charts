//! `style/theme/area.py`: the area chart family.

use crate::primitives::{PatternStyle, StrokeStyle};
use crate::style::bar::{StackMode, StackOrder};
use crate::style::chart_base::{
    AnnotatedChartStyleMixin, CartesianChartStyle, LegendedChartStyleBase,
    QuantitativeAxisChartStyleMixin,
};
use crate::style::endpoint_labels::EndpointLabelsConfig;
use crate::chart::conditional_formatting::Predicate;
use crate::style::bar::misplaced_defaults;
use crate::style::marks::{AreaLineStyleConditional, AreaMarkStyle, AreaMarkStyleConditional, PointLabelsStyle, PointMarkStyle, PointMarkStyleConditional};
use diagnostic::Diagnostic;
use indexmap::IndexMap;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Stroke/halo/labels for an area chart's top-edge line and value labels.
///
/// The edge is a separate line mark, so its stroke geometry belongs here and not on
/// `AreaMarkStyle` (fill only). Deliberately not the full `LineMarkStyle`: no `curve` or
/// `connect`. Curve is one shared value driving both the fill and this edge (they trace one
/// silhouette) and lives solely on `AreaMarkStyle.curve`; a second `curve` here would silently
/// do nothing.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaLineStyle {
    /// Area top-edge line stroke style.
    #[model(
        fill_at(to = "color", value = "auto"),
        fill_at(to = "width", value = "auto")
    )]
    pub stroke: StrokeStyle,
    /// Halo stroke width multiplier relative to stroke.width; 0 disables the halo.
    pub halo_multiplier: f64,
    /// Value label style for the area's plotted points; inherits from global.
    pub labels: PointLabelsStyle,
}

/// Area-family mark overrides.
///
/// `area` owns fill (opacity, curve, stacked recipe). `line` owns the top-edge line's
/// stroke/halo/labels. `point` overlays real point markers at each plotted value (size 0 by
/// default: invisible unless authored). No `text`: no text-mark authored surface exists for area.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaChartMarksStyle {
    /// Area fill mark overrides; inherits from global.
    pub area: AreaMarkStyle,
    /// Top-edge line stroke/halo/label overrides; inherits from global.
    pub line: AreaLineStyle,
    /// Point-overlay mark overrides; inherits from global.
    pub point: PointMarkStyle,
}

/// The part of [`AreaChartMarksStyle`] a conditional rule may set.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaChartMarksStyleConditional {
    /// Area fill mark overrides; inherits from global.
    #[model(absent = "the area mark's own style")]
    pub area: Option<AreaMarkStyleConditional>,
    /// Top-edge line overrides; inherits from global.
    #[model(absent = "the edge line's own style")]
    pub line: Option<AreaLineStyleConditional>,
    /// Point-overlay mark overrides; inherits from global.
    #[model(absent = "the point mark's own style")]
    pub point: Option<PointMarkStyleConditional>,
}

/// The part of [`AreaChartStyle`] a conditional rule may set: one predicate plus the mark style
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
pub struct AreaChartStyleConditional {
    #[flatten]
    pub predicate: Predicate,
    /// Area-family mark overrides; a rule that restyles nothing is a mistake, so they are required.
    pub marks: AreaChartMarksStyleConditional,
}

/// Required wrapper for area-layer mark overrides.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(complete)]
pub struct AreaLayerStyle {
    /// Mark overrides for this area layer.
    pub marks: AreaChartMarksStyle,
}

/// Area chart style: chart-level fields + marks sub-block.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct AreaChartStyle {
    #[flatten]
    pub annotated: AnnotatedChartStyleMixin,
    #[flatten]
    pub quantitative: QuantitativeAxisChartStyleMixin,
    #[flatten]
    pub legended: LegendedChartStyleBase,
    #[flatten]
    pub cartesian: CartesianChartStyle,
    /// Default stack mode for area charts: 'none', 'zero', 'normalize', or 'center'.
    pub stack: StackMode,
    /// Z-order of stacked segments. Unset or 'value' puts the largest aggregate at baseline. 'data' stacks series in first-appearance order: SQL row order for color:, the listed order for y: [a, b, ...] (a measure null in the first rows appears later). 'alphabetical' sorts by series name. Ignored when stacking is off or there is only one series.
    #[model(absent = "ranks by value")]
    pub stack_order: Option<StackOrder>,
    /// Series names printed on the bands instead of in a legend.
    pub endpoint_labels: EndpointLabelsConfig,
    /// How the `pattern:` channel paints fills.
    pub pattern: PatternStyle,
    /// Area-family mark overrides.
    #[model(inherit_slot = "Style.charts.marks")]
    pub marks: AreaChartMarksStyle,
    /// Rules that restyle a point by its row's value, keyed by column name. Each rule is one condition (eq, ne, lt, lte, gt, gte, between, in, is_null, or default) plus the style it applies: marks.point color, fill, filled, size, shape, opacity and stroke_width, and marks.area color and opacity, and marks.line.stroke color, width and dasharray (the top edge). An area segment takes a fill or top-edge key only when both of its end points resolve the same value for it, so a run of matching points restyles the fill and edge between them, and the segment stepping into the run keeps the base style; include the boundary point in the condition to draw it. Curve, stacked and the other whole-series properties cannot vary per row. Every matching rule applies; where two set the same key the later one wins, so list the more specific rule last. A `default: true` rule applies only when no other rule matched. Two columns cannot patch the same property. Rules style the base layer, not `layers:`.
    #[model(fill = fill_empty)]
    pub conditional: IndexMap<String, Vec<AreaChartStyleConditional>>,
}

impl AreaChartStyle {
    /// A `default: true` rule is allowed only last in a column's rule list.
    pub fn default_position(style: &Authored<AreaChartStyle>) -> Vec<Diagnostic> {
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
