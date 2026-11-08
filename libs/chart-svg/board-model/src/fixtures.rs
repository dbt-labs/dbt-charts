//! Builders for the spine's types, for tests that need a value without a board to parse.
//! Each builds through the field types' `TryFrom`, the way normalize does, so a fixture never
//! holds a value normalize could not produce. Behind the `fixtures` feature.

mod board;
pub use board::*;
mod sql;
pub use sql::{Opaque, Recorded};

use crate::chart::callout::CalloutChart;
use crate::chart::ChartQuery;
use crate::chart::{Chart, ChartBase, TooltipField};
use crate::facet::{Channel, DisplayText};
use crate::ids::QueryId;
use crate::query::Query;
use crate::style::callout::CalloutChartStyle;
use crate::style::font::FontStylePatch;
use crate::style::style::Style;
use crate::variable::Variable;
use model_core::{parse, Build, FromNode, Leaf, Node};
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
        visible: None,
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

/// A variable's render value from its wire form, as `plan` returns it.
pub fn variable_value(wire: &Value) -> Result<crate::variable_value::VariableValue, FixtureError> {
    crate::variable_value::VariableValue::from_json(wire).map_err(|e| FixtureError(vec![e]))
}

fn resolved_theme(
    theme: &str,
) -> Result<model_core::Resolved<crate::style::style::Style>, FixtureError> {
    let folded = crate::board::theme::built_in_theme(theme)
        .map_err(|e| FixtureError(vec![e.to_string()]))?;
    let model_core::Tri::Value(style) = &folded.style else {
        return Err(FixtureError(vec![format!("theme {theme} has no style")]));
    };
    model_core::Resolved::base(&crate::cascade::TABLES, (**style).clone())
        .map_err(|e| FixtureError(vec![e.to_string()]))
}

/// The total `Style` of a built-in theme: its folded `style:` patch through the generated inherit
/// and fill, then built. Nothing is written by hand, so it is what normalize would resolve.
pub fn resolved_style(theme: &str) -> Result<Box<crate::style::style::Style>, FixtureError> {
    resolved_theme(theme).map(|r| r.total)
}

/// [`resolved_style`] with `style`, the authored form of a board's own `style:` block, laid over the
/// theme as the board's scope is.
pub fn resolved_style_over(
    theme: &str,
    style: &Value,
) -> Result<Box<crate::style::style::Style>, FixtureError> {
    let text = serde_json::to_string(style).map_err(|e| FixtureError(vec![e.to_string()]))?;
    let node: Node = yaml::parse_node(&text)
        .map_err(|ds| FixtureError(ds.iter().map(|d| d.message().unwrap_or_default()).collect()))?;
    let parsed = model_core::parse_patch::<crate::style::style::Style>(&node, None);
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
    resolved_theme(theme)?
        .apply(&parsed.twin)
        .map(|r| r.total)
        .map_err(|e| FixtureError(vec![e.to_string()]))
}

/// Where Rust resolves a leaf Python leaves `None`, Python's expected patch is projected to the
/// effective value before the comparison. One rule per leaf:
///
/// - a chart family's `color.categorical.single_series_palette` (pie included): every Python
///   consumer reads the family's own when set and `charts.color.categorical.single_series_palette`
///   otherwise (shared.py's `effective_single_series_palette`).
/// - a map family's chart-level `font` (`geoshape`, `point_map`): no Python consumer reads the
///   family-root font's own leaves, and the chart draws in `charts.font`, so an unset leaf is that
///   font's.
/// - the bar value labels' `font` (global, then each bar family): a label with no font of its own
///   paints in the text mark's (`charts.marks.text.font`). `family`, `color`, `size` and `weight`
///   are what Vega-Lite's text config reads from it (vega_lite/mapping.py:128-145,
///   resolve/chart/bar.py:1409); `style`, `decoration`, `case` and `line_height` are unread by
///   Python's bar-label consumers, and follow the same font for consistency.
pub fn project_effective_tiers(want: &mut <Style as Leaf>::Patch) {
    project_single_series_ink(want);
    project_bar_label_font(want);
    project_map_font(want);
}

fn project_map_font(want: &mut <Style as Leaf>::Patch) {
    // The leaves the family sets keep their value over the enclosing font's.
    let enclosing = want.charts.font.clone();
    for font in [
        &mut want.charts.geoshape.geo.card.font,
        &mut want.charts.point_map.geo.card.font,
    ] {
        let mut merged = enclosing.clone();
        merged.merge(font.clone());
        *font = merged;
    }
}

fn project_bar_label_font(want: &mut <Style as Leaf>::Patch) {
    use model_core::Tri;
    // An unset font takes the lower tier whole; a set one keeps its own leaves over it.
    fn under(font: &mut Tri<FontStylePatch>, lower: &Tri<FontStylePatch>) {
        match (&mut *font, lower) {
            (Tri::Unset, Tri::Value(low)) => *font = Tri::Value(low.clone()),
            (Tri::Value(own), Tri::Value(low)) => {
                let mut merged = low.clone();
                merged.merge(own.clone());
                *own = merged;
            }
            _ => {}
        }
    }
    let text = Tri::Value(want.charts.marks.text.font.clone());
    under(&mut want.charts.marks.bar.labels.labels.font, &text);
    let global = want.charts.marks.bar.labels.labels.font.clone();
    under(&mut want.charts.bar.marks.bar.labels.labels.font, &global);
    under(
        &mut want.charts.histogram.marks.bar.labels.labels.font,
        &global,
    );
}

fn project_single_series_ink(want: &mut <Style as Leaf>::Patch) {
    use model_core::Tri;
    let Tri::Value(root) = &want.charts.color.categorical else {
        return;
    };
    let (ink, whole) = (root.single_series_palette.clone(), root.clone());
    let mut families = [
        &mut want.charts.bar.cartesian.painted.color,
        &mut want.charts.line.cartesian.painted.color,
        &mut want.charts.area.cartesian.painted.color,
        &mut want.charts.scatter.cartesian.painted.color,
        &mut want.charts.histogram.cartesian.painted.color,
        &mut want.charts.heatmap.cartesian.painted.color,
        &mut want.charts.pie.color,
    ];
    for color in &mut families {
        match &mut color.categorical {
            Tri::Value(c) if c.single_series_palette.is_none() => {
                c.single_series_palette = ink.clone();
            }
            Tri::Unset => color.categorical = Tri::Value(whole.clone()),
            _ => {}
        }
    }
}
