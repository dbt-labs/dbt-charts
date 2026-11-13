//! `chart/authored/bar.py`: bars, and the histogram that bins x. Both tags read one model
//! (Python's `BarChart`), so the struct cannot see which tag it was read under. A rule that
//! depends on it names the tag (`read_under = "type"`): the histogram's tooltip, y_start,
//! annotations, pattern, layers, support_table and legend-edge rules are declared constraints, in
//! the order Python's `BarChart.__constraints__` states them: the first a board breaks is the
//! message it gets.

use super::annotation::Annotation;
use super::layer::CartesianLayer;
use super::{CartesianChartFields, NonBlankColumn, PatternChannelField, TooltipFields};
use crate::style::bar::BarChartStyle;
use model_derive::Model;

/// Bar or histogram chart; a histogram adds automatic x binning.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(
    read_under = "type",
    constraint(forbids(support_table, when(is_in(type, "histogram")),
        message = "chart.support_table is not supported for chart type 'histogram' in v1. Supported chart types: area, bar, funnel, line."
    )),
    constraint(not_null(y_start,
        message = "y_start names no column; remove it for bars that start at zero."
    )),
    constraint(forbids(layers, when(is_in(type, "histogram")), nonempty(layers),
        message = "chart.layers is not supported for chart type 'histogram'. Supported chart types: area, bar, line, scatter."
    )),
    constraint(forbids(pattern, "style.pattern", when(is_in(type, "histogram")),
        message = "pattern is not supported on type: histogram: its bars are bins of rows, so a column has no single value to pattern by."
    )),
    constraint(forbids("style.legend.position.edge", when(is_in(type, "histogram"), is_in("style.legend.position.edge", "endpoints")),
        message = "legend position `endpoints` is not supported on type: histogram: it has one series, so there are no endpoint labels to print. Set the edge to `top`, `bottom`, `left` or `right`, or remove it."
    )),
    constraint(forbids(annotations, when(is_in(type, "histogram")), nonempty(annotations),
        message = "annotations are not supported on type: histogram: a histogram bins x, so an anchor has no category or date slot to name."
    )),
    constraint(forbids(tooltip, when(set(layers)),
        message = "tooltip is not supported with layers: an overlay mark's card describes the layer, not a column of the base query."
    )),
    constraint(forbids(tooltip, when(is_in(type, "histogram")),
        message = "tooltip is not supported on type: histogram: a bar there is a bin of rows, not one row, so a column has no single value to show."
    )),
    constraint(forbids(tooltip, when(set(y_start)),
        message = "tooltip is not supported with y_start: a ranged bar's card shows its start, end and difference."
    )),
    constraint(forbids(y_start, when(is_in(type, "histogram")),
        message = "y_start is not supported on type: histogram — a histogram's bars count from zero, so it takes no y_start."
    )),
    constraint(forbids(y_start, when(is_in(type, "funnel")),
        message = "y_start is not supported on type: funnel: a funnel centers each bar, so a bar has no start of its own. Use type: bar for ranged bars."
    )),
    constraint(forbids(y_start, when(is_in("style.stack", "zero", "normalize", "center")),
        message = "y_start sets each bar's start, so it can't be stacked; remove style.stack or y_start."
    )),
    constraint(forbids(y_start, when(list(y)),
        message = "y_start sets one start per bar, so y must name a single column, not a list."
    )),
    constraint(forbids(layers, when(is_in(type, "bar", "funnel"), list(y)), nonempty(layers),
        message = "Bar chart: layers are not supported with multi-metric (y: [...]) charts. Use a long-form query with one y field."
    ))
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
    #[model(fill = fill_style, nullable)]
    pub style: BarChartStyle,
    /// Extra marks on one shared x, each with its own type. Not supported when type: histogram: a histogram bins x and aggregates to a count, so there is no shared y measure for an overlay to plot against.
    #[model(fill = fill_empty, nullable)]
    pub layers: Vec<CartesianLayer>,
    /// Rules, bands, notes and trends drawn on the chart. Not supported when type: histogram.
    #[model(fill = fill_empty, nullable)]
    pub annotations: Vec<Annotation>,
}
