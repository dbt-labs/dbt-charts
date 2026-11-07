//! Builders for the spine's types, for tests that need a value without a board to parse.
//! Each builds through the field types' `TryFrom`, the way normalize does, so a fixture never
//! holds a value normalize could not produce. Behind the `fixtures` feature.

mod board;
pub use board::*;

use crate::chart::callout::CalloutChart;
use crate::chart::ChartQuery;
use crate::chart::{Chart, ChartBase, TooltipField};
use crate::facet::{Channel, DisplayText};
use crate::ids::QueryId;
use crate::query::Query;
use crate::style::callout::CalloutChartStyle;
use crate::variable::Variable;
use model_core::{parse, Build, FromNode, Node};
use serde_json::{json, Value};
use std::fmt;

/// A callout chart with `message` and the total `style`; no title, no ignored warnings.
pub fn callout_chart(message: &str, style: CalloutChartStyle) -> Result<Chart, String> {
    Ok(Chart::Callout(CalloutChart {
        message: DisplayText::try_from(message.to_string())?,
        title: None,
        style,
        warnings_ignore: Vec::new(),
    }))
}

/// The shared chart fields of a chart that reads `query`: no notes, link or ignored warnings.
/// A family builder takes this as its `base`.
pub fn chart_base_with_query(query: &str) -> Result<ChartBase, String> {
    Ok(ChartBase {
        notes: None,
        query: Some(ChartQuery::Hoisted(QueryId::try_from(query.to_string())?)),
        link: None,
        warnings_ignore: Vec::new(),
    })
}

/// A tooltip row for `column`, with the default label and format.
pub fn tooltip_field(column: &str) -> Result<TooltipField, String> {
    Ok(TooltipField {
        column: Channel::try_from(column.to_string())?,
        label: None,
        format: None,
    })
}

// Builders for queries and variables: they take authored spellings and build through
// parse then `Build`, so an unknown key, a refused value or an unset leaf is an `Err`.
// Cross-field constraints are not checked by either, so a builder keeps to the shape
// normalize leaves.

/// Why a fixture could not be built: every parse error or unset leaf, by authored path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FixtureError(pub Vec<String>);

impl fmt::Display for FixtureError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0.join("; "))
    }
}

impl std::error::Error for FixtureError {}

/// Parse `value` as the authored form of `T` and build the total value.
pub fn build<T>(value: &Value) -> Result<T, FixtureError>
where
    T: FromNode + Build,
{
    let text = serde_json::to_string(value).map_err(|e| FixtureError(vec![e.to_string()]))?;
    let node: Node = yaml::parse_node(&text)
        .map_err(|ds| FixtureError(ds.iter().map(|d| d.message().unwrap_or_default()).collect()))?;
    let parsed = parse::<T>(&node, None);
    if !parsed.errors.is_empty() {
        let msgs = parsed.errors.iter().map(|e| {
            format!(
                "{}: {}",
                e.path(),
                e.diagnostic.message().unwrap_or_default()
            )
        });
        return Err(FixtureError(msgs.collect()));
    }
    T::build_authored(parsed.twin, "").map_err(|u| FixtureError(u.paths))
}

fn formats(formats: &[(&str, &str)]) -> Value {
    formats
        .iter()
        .map(|(c, f)| (c.to_string(), json!(f)))
        .collect()
}

/// A SQL query on a named source, with a format declared per column (`(column, spec)`).
pub fn sql_query(
    sql: &str,
    source: &str,
    formats_by_column: &[(&str, &str)],
) -> Result<Query, FixtureError> {
    build(&json!({
        "type": "sql", "sql": sql, "source": source,
        "formats": formats(formats_by_column),
    }))
}

/// A values query: rows as authored, with its column names declared in order.
pub fn values_query(columns: &[&str], rows: &[Vec<Value>]) -> Result<Query, FixtureError> {
    if let Some((i, row)) = rows
        .iter()
        .enumerate()
        .find(|(_, r)| r.len() != columns.len())
    {
        return Err(FixtureError(vec![format!(
            "Values row {i} has {} items, expected {} (columns: {columns:?})",
            row.len(),
            columns.len()
        )]));
    }
    let rows: Vec<Value> = rows
        .iter()
        .map(|cells| {
            columns
                .iter()
                .zip(cells)
                .map(|(c, v)| (c.to_string(), v.clone()))
                .collect()
        })
        .collect();
    build(&json!({"type": "values", "columns": columns, "rows": rows}))
}

/// A variable with the given control (`"auto"` or an input type's spelling) and a caption.
pub fn variable(input: &str, label: &str) -> Result<Variable, FixtureError> {
    build(&json!({"input": input, "label": label, "required": false, "visible": true}))
}

/// A select over a column of a named query.
pub fn select_variable(label: &str, query: &str, column: &str) -> Result<Variable, FixtureError> {
    build(&json!({
        "input": "select", "label": label, "required": false, "visible": true,
        "options": {"query": query, "column": column},
    }))
}

/// A daterange with a rolling default and its own preset menu.
pub fn daterange_variable(
    label: &str,
    default: &str,
    presets: &[&str],
) -> Result<Variable, FixtureError> {
    build(&json!({
        "input": "daterange", "label": label, "default": default, "presets": presets,
        "required": false, "visible": true,
    }))
}

/// One variable per control kind, `auto` first, in declaration order.
pub fn variable_of_every_kind() -> Result<Vec<Variable>, FixtureError> {
    let kinds = ["auto"]
        .into_iter()
        .chain(crate::variable::InputType::VALUES.iter().copied());
    kinds.map(|kind| variable(kind, kind)).collect()
}
