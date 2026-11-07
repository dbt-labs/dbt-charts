//! `chart/authored/bar.py`: bars, and the histogram that bins x. Both tags read one model
//! (Python's `BarChart`), so the struct cannot see which tag it was read under; the rules that
//! depend on it are [`BarChart::type_rules`], called with the tag.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{
    CartesianChartFields, ChartAuthored, Columns, NonBlankColumn, PatternChannelField,
    TooltipFields,
};
use crate::style::bar::BarChartStyle;
use diagnostic::diag::err_validation_field as err;
use diagnostic::Diagnostic;
use model_core::{Authored, Tri};
use model_derive::Model;

/// Bar or histogram chart; a histogram adds automatic x binning.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    constraint(forbids(tooltip, when(set(layers)))),
    constraint(forbids(tooltip, when(set(y_start)))),
    constraint(forbids(y_start, when(is_in("style.stack", "zero", "normalize", "center")))),
    constraint(forbids(y_start, when(list(y))))
)]
#[allow(clippy::duplicated_attributes)]
pub struct BarChart {
    #[flatten]
    pub tooltip: TooltipFields,
    #[flatten]
    pub cartesian: CartesianChartFields,
    #[flatten]
    pub pattern: PatternChannelField,
    /// Column each bar starts from instead of zero (waterfall, range, Gantt, lollipop). Same kind as y: both numeric, or both dates. Not with stacking.
    #[model(absent = "bars start at zero")]
    pub y_start: Option<NonBlankColumn>,
    /// Appearance overrides for this chart alone.
    #[model(nullable)]
    pub style: BarChartStyle,
    /// Extra marks on one shared x, each with its own type. Not supported when type: histogram: a histogram bins x and aggregates to a count, so there is no shared y measure for an overlay to plot against.
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart. Not supported when type: histogram.
    pub annotations: Vec<Annotation>,
}

fn set<T>(t: &Tri<T>) -> bool {
    matches!(t, Tri::Value(_))
}

impl BarChart {
    /// The rules Python's `BarChart.__constraints__` states on `type:`. A histogram bins x and
    /// counts, so it refuses what needs a mark per row or a measure; a bar refuses a multi-measure
    /// `y:` that has no x or carries layers, and an `x:` with no `y:`.
    pub fn type_rules(chart: &Authored<BarChart>, histogram: bool) -> Vec<Diagnostic> {
        let c = &chart.cartesian;
        let multi = matches!(&c.y, Tri::Value(Columns::Many(_)));
        let layers = chart.layers.as_ref().is_some_and(|l| !l.is_empty());
        let mut out = Vec::new();
        if matches!(chart.y_start, Tri::Null) {
            out.push(err(
                "y_start",
                "Value error, y_start names no column; remove it for bars that start at zero.",
            ));
        }
        let mut refuse =
            |field: &str, message: &str| out.push(err(field, format!("Value error, {message}")));
        if histogram {
            if layers {
                refuse("layers", "chart.layers is not supported for chart type 'histogram'. Supported chart types: area, bar, line, scatter.");
            }
            if set(&chart.pattern.pattern) || chart.style.pattern != Default::default() {
                refuse("pattern", "pattern is not supported on type: histogram: its bars are bins of rows, so a column has no single value to pattern by.");
            }
            if set(&c.support_table) {
                refuse("support_table", "chart.support_table is not supported for chart type 'histogram' in v1. Supported chart types: area, bar, line.");
            }
            if chart.annotations.as_ref().is_some_and(|a| !a.is_empty()) {
                refuse("annotations", "annotations are not supported on type: histogram: a histogram bins x, so an anchor has no category or date slot to name.");
            }
            if chart.tooltip.tooltip.is_some() {
                refuse("tooltip", "tooltip is not supported on type: histogram: a bar there is a bin of rows, not one row, so a column has no single value to show.");
            }
            if set(&chart.y_start) {
                refuse("y_start", "y_start is not supported on type: histogram — a histogram's bars count from zero, so it takes no y_start.");
            }
        } else {
            if multi && layers {
                refuse("layers", "Bar chart: layers are not supported with multi-metric (y: [...]) charts. Use a long-form query with one y field.");
            }
            if multi && !set(&c.x) {
                refuse(
                    "x",
                    "Bar chart: multi-metric (y: [...]) charts require an x field.",
                );
            }
            if set(&c.x) && !matches!(&c.y, Tri::Value(y) if !y.is_blank()) {
                refuse("y", "Bar chart: x requires a y field — a bar with categories but no measure has nothing to draw.");
            }
        }
        out
    }
}

impl ChartAuthored {
    /// [`BarChart::type_rules`] under the tag this chart was read with; no other family has a rule
    /// that reads its tag.
    pub fn type_rules(&self) -> Vec<Diagnostic> {
        match self {
            Self::Bar(b) => BarChart::type_rules(b, false),
            Self::Histogram(b) => BarChart::type_rules(b, true),
            _ => Vec::new(),
        }
    }
}
