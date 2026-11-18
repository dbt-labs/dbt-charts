//! A spark_bar's data: the label and magnitude of each row within `max_bars`, the scale they share
//! and the count text. `chart-renderer/src/families/spark_bar.rs::bind` decides the same facts.

use std::collections::BTreeSet;

use board_model::chart::query::ChartQuery;
use board_model::chart::SparkBarChart;
use board_model::ids::ChartId;
use board_model::style::spark_bar::SparkBarChartStyle;
use column_types::ColumnType;
use diagnostic::{diag, Diagnostic};
use serde_json::{json, Map, Value};

use crate::formats::locale::d3_format;
use crate::bound::{Family, Failure, SparkBar, SparkBarRow};
use crate::run::RunResult;

/// Production's `auto_detect_fields` name preferences, tried in order on the first row's keys.
const LABEL_NAMES: [&str; 4] = ["value", "label", "category", "name"];
const COUNT_NAMES: [&str; 5] = ["frequency", "count", "freq", "n", "total"];

pub fn bind(id: &ChartId, chart: &SparkBarChart, style: &SparkBarChartStyle, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    // A spark_bar draws no link, authored or automatic: production hands its renderer none
    // (engine.py:`build_chart_layout`), so its `link:` is never read here.
    let Some(ChartQuery::Hoisted(query)) = &chart.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset = run.dataset(query, id)?;
    let failure = |diagnostic: Diagnostic| Ok(Err(Failure { chart: id.clone(), diagnostic: diagnostic.with_sink_param("source_path", json!(format!("charts.{id}"))) }));
    if let Err(mismatch) = dataset.schema.check_rows(&dataset.rows) {
        return failure(mismatch.into_diagnostic().with_sink_param("chart_id", json!(id.as_str())));
    }
    let rows = &dataset.rows;
    let authored = |text: &Option<board_model::facet::DisplayText>| text.as_ref().map(|t| t.as_str().to_string()).filter(|t| !t.is_empty());
    let (title, subtitle) = (authored(&chart.shared.title), authored(&chart.shared.subtitle));
    // Production validates no field of an empty result and draws no row.
    let Some(first) = rows.first() else {
        return Ok(Ok(Family::SparkBar(SparkBar { rows: Vec::new(), max_value: 1.0, signed: false, more: 0, title, subtitle })));
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
    // spark_bar.rs:`validate_value_field`, over every row of the result, never only the drawn ones:
    // a null is sparse data, any other non-number fails the chart, and a column of nulls alone holds
    // nothing to draw.
    let mut has_numeric = false;
    for row in rows {
        match row.get(&x) {
            None | Some(Value::Null) => {}
            Some(Value::Number(_)) => has_numeric = true,
            Some(other) => return not_numeric(&x, &format!("row value {} is not numeric", diagnostic::py_repr(other))),
        }
    }
    if !has_numeric {
        return not_numeric(&x, "the column holds no numeric values");
    }

    let visible = &rows[..rows.len().min(style.max_bars as usize)];
    let datetime = y.as_ref().is_some_and(|y| matches!(dataset.schema.0.get(y), Some(ColumnType::Datetime | ColumnType::DatetimeTz)));
    let mut bound = Vec::with_capacity(visible.len());
    for (i, row) in visible.iter().enumerate() {
        let label = label(row, y.as_deref(), datetime, i + 1)?;
        let (value, count) = match row.get(&x) {
            Some(Value::Number(n)) => (n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?, count_text(n)?),
            // A null magnitude draws no bar and counts `0` (spark_bar.rs:`format_count`).
            None | Some(Value::Null) => (0.0, "0".to_string()),
            Some(other) => return Err(diag::err_internal(format!("the validated magnitude {other} is not a number"))),
        };
        bound.push(SparkBarRow { label, value, count: style.count.visible.then_some(count) });
    }
    let signed = bound.iter().any(|row| row.value < 0.0);
    let max = bound.iter().fold(0.0_f64, |m, row| m.max(row.value.abs()));
    Ok(Ok(Family::SparkBar(SparkBar { rows: bound, max_value: if max == 0.0 { 1.0 } else { max }, signed, more: rows.len() - visible.len(), title, subtitle })))
}

/// The row's label, as production's `str(row.get(y_field, ""))` prints it (spark_bar.rs:`bind`): a
/// text cell as is, a null `None`, a boolean `True`/`False`, a number as Python prints it, and a row
/// without the column empty; with no label column, the `n`th row is "Item n". A datetime travels
/// as `isoformat()` and Python prints `isoformat(" ")`.
fn label(row: &Map<String, Value>, y: Option<&str>, datetime: bool, n: usize) -> Result<String, Diagnostic> {
    let Some(y) = y else {
        return display_text::language::tr("spark.item", None, &[("n", &n.to_string())]).map_err(diag::err_internal);
    };
    Ok(match row.get(y) {
        None => String::new(),
        Some(Value::String(text)) if datetime => {
            let (date, time) = text.split_once('T').ok_or_else(|| diag::err_internal(format!("the datetime {text} has no `T`")))?;
            format!("{date} {time}")
        }
        Some(Value::String(text)) => text.clone(),
        Some(Value::Null) => "None".to_string(),
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.to_string(),
        Some(Value::Number(n)) if n.is_f64() => d3rs::py_float::float_repr(n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))?),
        Some(Value::Number(n)) => n.to_string(),
        Some(other) => return Err(diag::err_internal(format!("a spark_bar label {other} is neither text, a number nor a boolean"))),
    })
}

/// Production keys on the Python type, which the JSON number keeps: an integral float prints
/// `str(int(v))`, any other float `,.1f`, an int `,d`, each `d3_format` in the board's number locale.
fn count_text(n: &serde_json::Number) -> Result<String, Diagnostic> {
    let text = if n.is_i64() || n.is_u64() {
        d3_format(",d", n.as_f64().unwrap_or(0.0))
    } else {
        let f = n.as_f64().unwrap_or(0.0);
        if f.fract() == 0.0 { Ok(format!("{}", f as i64)) } else { d3_format(",.1f", f) }
    };
    text.map_err(diag::err_internal)
}
