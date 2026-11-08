//! A KPI card's data: the headline cell of its one row, cut into the lanes it draws in, and its
//! label. `chart-renderer/src/families/kpi.rs::bind` and `compile/resolve/chart/simple.py::_resolve_kpi`
//! decide the same facts; what this slice does not model is refused by name.

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::KpiChart;
use board_model::ids::ChartId;
use board_model::facet::FormatName;
use board_model::query::Query;
use column_types::{ColumnType, Dataset};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use serde_json::{json, Value};

use crate::formats::locale::NumberLocale;
use crate::formats::{column_format, Formats, Kind, Notation, Tiers};
use crate::model::{Body, Failure, Kpi};
use crate::run::RunResult;

/// Binds `chart` to its query's one row. A row count or a column production fails on is that
/// chart's error card; a query that returned no rows is refused, since production also reports a
/// zero-rows warning this stage does not raise.
pub fn bind_kpi(id: &ChartId, chart: &KpiChart, board: &Board, run: &RunResult) -> Result<Body, Diagnostic> {
    refuse_unmodeled(chart, board, run)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset: &Dataset = run.datasets.get(query).ok_or_else(|| diag::err_internal(format!("query {query} has no dataset")))?;
    let Some(value) = &chart.value else {
        return Err(Diagnostic::refused(refuse!("a KPI that names no `value` is not supported")));
    };
    let column = value.as_str();
    let failure = |diagnostic: Diagnostic| {
        Ok(Body::Failed(Failure { chart: id.clone(), diagnostic: diagnostic.with_sink_param("source_path", json!(format!("charts.{id}"))) }))
    };
    if let Err(mismatch) = dataset.schema.check_rows(&dataset.rows) {
        // The sink stamps the chart a data error belongs to (`stamp_chart_diagnostic`); the KPI
        // codes carry it in their own template.
        return failure(mismatch.into_diagnostic().with_sink_param("chart_id", json!(id.as_str())));
    }
    if board.body.queries.get(query.name().as_str()).is_some_and(|q| declared_formats(q).contains_key(column)) {
        return Err(Diagnostic::refused(refuse!("a query's `formats:` for a KPI value is not supported")));
    }
    let row = match dataset.rows.as_slice() {
        [] => return Err(Diagnostic::refused(refuse!("a KPI whose query returned no rows is not supported"))),
        [row] => row,
        rows => return failure(diag::err_kpi_multirow(id.as_str(), rows.len())),
    };
    let Some(cell) = row.get(column) else {
        return failure(diag::err_kpi_value_column_not_found(id.as_str(), column));
    };
    let value = number(cell, dataset.schema.get(column).map_err(Diagnostic::from)?)?;
    let compact = &chart.style.value.compact;
    let compacted = if value.abs() >= compact.threshold { compact.spec.as_str() } else { "" };
    let format = column_format(Kind::Number, Tiers::default(), Formats::new(None), Some(compacted), Some(Notation::Narrative), &[value])?;
    let lanes = format.paint_lanes(&NumberLocale::en_us(), value)?;
    Ok(Body::Kpi(Kpi { number: lanes.number, suffix: lanes.suffix, label: label(chart)? }))
}

fn declared_formats(query: &Query) -> &IndexMap<String, FormatName> {
    match query {
        Query::Sql(q) => &q.base.formats,
        Query::Values(q) => &q.base.formats,
        Query::Schema(q) => &q.base.formats,
    }
}

/// Every chart field, style and board fact that changes what the card says and is not modeled.
fn refuse_unmodeled(chart: &KpiChart, board: &Board, run: &RunResult) -> Result<(), Diagnostic> {
    let unmodeled = [
        (chart.glyph.is_some(), "a KPI `glyph`"),
        (chart.good.is_some(), "a KPI `good`"),
        (chart.support.is_some(), "a KPI `support` block"),
        (chart.background.is_some(), "a KPI `background` channel"),
        (!chart.formatting.conditional_formatting.is_empty(), "a KPI `conditional_formatting`"),
        (chart.base.link.is_some(), "a KPI `link`"),
        (chart.style.value.format.is_some(), "a KPI `style.value.format`"),
        (board.regional.is_some() || run.env.locale != "en-US", "a number locale other than en-US"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("{what} is not supported"))),
        None => Ok(()),
    }
}

/// The headline as a number: production reads any other cell (text, a moment, a null) another way.
fn number(cell: &Value, declared: &ColumnType) -> Result<f64, Diagnostic> {
    match (cell, declared) {
        (Value::Number(n), ColumnType::Integer | ColumnType::Float) => {
            n.as_f64().ok_or_else(|| diag::err_internal(format!("the number {n} is not a double")))
        }
        _ => Err(Diagnostic::refused(refuse!("a KPI value that is not an integer or float number is not supported"))),
    }
}

/// The label as drawn; an empty one is no label. Template syntax needs the board's variables and
/// queries.
fn label(chart: &KpiChart) -> Result<Option<String>, Diagnostic> {
    let Some(text) = chart.label.as_ref().map(|label| label.as_str()).filter(|text| !text.is_empty()) else {
        return Ok(None);
    };
    if text.contains("{{") || text.contains("{%") {
        return Err(Diagnostic::refused(refuse!("a KPI label with template syntax is not supported")));
    }
    Ok(Some(text.to_string()))
}
