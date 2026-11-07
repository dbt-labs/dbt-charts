//! `chart/authored/table.py`: a flat table, or a pivot cross-tab.

use super::{ConditionalFormattingField, SharedChartFields};
use crate::facet::Channel;
use crate::style::table::TableChartStyle;
use model_core::{Cx, FromNode, Leaf, Merge, Node, ReadScalar, TypeExpr};
use model_derive::Model;

crate::style::axis::non_empty_list!(
    /// The columns a spanner covers: at least one.
    SpannerColumns,
    String
);

/// One spanner: a label drawn over a run of adjacent columns.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct TableSpanner {
    /// Group label for the listed columns.
    pub label: String,
    /// Query columns the spanner covers. Must be adjacent in the query's column order and unique; a column may sit in at most one spanner.
    pub columns: SpannerColumns,
}

/// Table chart.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(forbids(
        rows,
        columns,
        values,
        when(set(spanners)),
        nonempty(rows, columns, values)
    )),
    constraint(unique(rows, columns, values)),
    constraint(unique("spanners[*].columns"))
)]
pub struct TableChart {
    #[flatten]
    pub formatting: ConditionalFormattingField,
    #[flatten]
    pub shared: SharedChartFields,
    /// Appearance overrides for this chart alone.
    #[model(fill = fill_style, nullable)]
    pub style: TableChartStyle,
    /// Fields whose distinct values form the row dimension of a pivot cross-tab. Each string is a column name from the query result. Omit for flat (non-pivot) tables.
    #[model(absent = "a flat table")]
    pub rows: Option<Vec<Channel>>,
    /// Fields whose distinct values become column headers in a pivot cross-tab. Multiple fields create a nested multi-dimension pivot (outer → inner). Omit for flat tables.
    #[model(absent = "a flat table")]
    pub columns: Option<Vec<Channel>>,
    /// Measure fields that fill pivot cells. Each string is a column name from the query result. When omitted, all query columns not claimed by rows or columns are used.
    #[model(absent = "every column not claimed by rows or columns")]
    pub values: Option<Vec<Channel>>,
    /// Query column whose value marks each row's role: `total`, `summary`, or anything else for a plain body row. The column is never drawn. Totals and summaries pin to the bottom, stay out of row numbers and the page count, and take `style.row.roles`.
    #[model(absent = "every row is a body row")]
    pub row_role: Option<String>,
    /// Labels drawn in an extra header row above runs of adjacent columns of a flat table. Not combinable with the pivot rows/columns/values channels.
    #[model(absent = "no spanners")]
    pub spanners: Option<Vec<TableSpanner>>,
}
