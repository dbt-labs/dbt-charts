//! A spark_bar's data: the label and magnitude of each row within `max_bars`, the scale they share
//! and the count text. `chart-renderer/src/families/spark_bar.rs::bind` decides the same facts;
//! what this slice does not model is refused by name.

use std::collections::BTreeSet;

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::SparkBarChart;
use board_model::ids::ChartId;
use board_model::style::spark_bar::SparkBarChartStyle;
use column_types::ColumnType;
use diagnostic::{diag, refuse, Diagnostic};
use serde_json::{json, Map, Value};

use crate::formats::locale::NumberLocale;
use crate::bound::{Family, Failure, SparkBar, SparkBarRow};
use crate::run::RunResult;

/// Production's `auto_detect_fields` name preferences, tried in order on the first row's keys.
const LABEL_NAMES: [&str; 4] = ["value", "label", "category", "name"];
const COUNT_NAMES: [&str; 5] = ["frequency", "count", "freq", "n", "total"];

pub fn bind(id: &ChartId, chart: &SparkBarChart, style: &SparkBarChartStyle, board: &Board, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    let unmodeled = [
        (chart.shared.title.is_some() || chart.shared.subtitle.is_some(), "a spark_bar `title` or `subtitle`"),
        (chart.shared.base.link.is_some(), "a spark_bar `link`"),
        (board.regional.is_some(), "a number locale other than en-US"),
    ];
    if let Some((_, what)) = unmodeled.iter().find(|(set, _)| *set) {
        return Err(Diagnostic::refused(refuse!("{what} is not supported")));
    }
    let Some(ChartQuery::Hoisted(query)) = &chart.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset = run.dataset(query, id)?;
    let failure = |diagnostic: Diagnostic| Ok(Err(Failure { chart: id.clone(), diagnostic: diagnostic.with_sink_param("source_path", json!(format!("charts.{id}"))) }));
    if let Err(mismatch) = dataset.schema.check_rows(&dataset.rows) {
        return failure(mismatch.into_diagnostic().with_sink_param("chart_id", json!(id.as_str())));
    }
    let rows = &dataset.rows;
    let Some(first) = rows.first() else {
        return Err(Diagnostic::refused(refuse!("a spark_bar whose query returned no rows is not supported")));
    };
    let numeric = |column: &str| matches!(dataset.schema.0.get(column), Some(ColumnType::Integer | ColumnType::Float));
    let named = |names: &[&str]| first.keys().find(|k| names.contains(&k.to_lowercase().as_str())).cloned();
    let x = match chart.x.as_ref() {
        Some(x) => Some(x.as_str().to_string()),
        None => named(&COUNT_NAMES).or_else(|| first.keys().find(|k| numeric(k)).cloned()),
    };
    let y = match chart.y.as_ref() {
        Some(y) => Some(y.0.as_str().to_string()),
        None => named(&LABEL_NAMES).or_else(|| first.iter().find(|(_, v)| v.is_string()).map(|(k, _)| k.clone())),
    };
    let not_numeric = |field: &str, reason: &str| failure(diag::err_spark_bar_value_not_numeric(id.as_str(), field, reason));
    let Some(x) = x else {
        return not_numeric("<none>", "no x is authored and no numeric column could be auto-detected");
    };
    if !rows.iter().any(|row| row.contains_key(&x)) {
        let available: BTreeSet<&String> = rows.iter().flat_map(|row| row.keys()).collect();
        return failure(diag::err_spark_bar_value_field_not_found(id.as_str(), x.as_str(), json!(available)));
    }
    if !numeric(&x) {
        return Err(Diagnostic::refused(refuse!("a spark_bar magnitude column that is not an integer or float is not supported")));
    }

    let visible = &rows[..rows.len().min(style.max_bars as usize)];
    let mut bound = Vec::with_capacity(visible.len());
    for row in visible {
        let label = label(row, y.as_deref())?;
        let (value, count) = match row.get(&x) {
            Some(Value::Number(n)) => (n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?, count_text(n)?),
            _ => return Err(Diagnostic::refused(refuse!("a spark_bar magnitude that is not a number is not supported"))),
        };
        bound.push(SparkBarRow { label, value, count: style.count.visible.then_some(count) });
    }
    let signed = bound.iter().any(|row| row.value < 0.0);
    let max = bound.iter().fold(0.0_f64, |m, row| m.max(row.value.abs()));
    Ok(Ok(Family::SparkBar(SparkBar { rows: bound, max_value: if max == 0.0 { 1.0 } else { max }, signed, more: rows.len() - visible.len() })))
}

/// The row's label: a text cell as is. Production's `str()` of any other cell, and its numbered
/// placeholder for a chart with no label column, are not modeled.
fn label(row: &Map<String, Value>, y: Option<&str>) -> Result<String, Diagnostic> {
    match y.and_then(|y| row.get(y)) {
        Some(Value::String(text)) => Ok(text.clone()),
        _ => Err(Diagnostic::refused(refuse!("a spark_bar label that is not text is not supported"))),
    }
}

/// Production keys on the Python type, which the JSON number keeps: an integral float prints
/// `str(int(v))`, any other float `,.1f`, an int `,d`.
fn count_text(n: &serde_json::Number) -> Result<String, Diagnostic> {
    let locale = &NumberLocale::en_us().locale;
    let text = if n.is_i64() || n.is_u64() {
        d3rs::format::format(locale, ",d", n.as_f64().unwrap_or(0.0))
    } else {
        let f = n.as_f64().unwrap_or(0.0);
        if f.fract() == 0.0 { Ok(format!("{}", f as i64)) } else { d3rs::format::format(locale, ",.1f", f) }
    };
    text.map_err(diag::err_internal)
}
