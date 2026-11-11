//! A table's data: the text of each cell of a flat table of text columns, and the header each
//! column heads with: a text column's humanized header, cased by the header font. `chart-renderer/src/families/table/bind.rs` (`visible_columns`,
//! `build_cell_data`) and `compile/resolve/chart/_table.py` decide the same facts; what this slice
//! does not model is refused by name.

use board_model::board::Board;
use board_model::chart::query::ChartQuery;
use board_model::chart::TableChart;
use board_model::ids::ChartId;
use board_model::style::table::{CellAlign, TableChartStyle, TableChartStylePatch, TableColumnConfig};
use column_types::{is_date_like, ColumnType, Dataset};
use diagnostic::{diag, refuse, Diagnostic};
use serde_json::Value;

use crate::kpi::declared_formats;
use crate::bound::{Failure, Family, Table, TableColumn};
use crate::run::RunResult;
use crate::table_dates::parse_temporal;

/// Binds `chart` to its query's rows, with the `style` of the placement it is bound at.
pub fn bind_table(id: &ChartId, chart: &TableChart, style: &TableChartStyle, own: &TableChartStylePatch, board: &Board, run: &RunResult) -> Result<Result<Family, Failure>, Diagnostic> {
    refuse_unmodeled(chart, style, board, run)?;
    let Some(ChartQuery::Hoisted(query)) = &chart.shared.base.query else {
        return Err(diag::err_internal(format!("chart {id} has no hoisted query")));
    };
    let dataset: &Dataset = run.dataset(query, id)?;
    if board.body.queries.get(query.name().as_str()).is_some_and(|q| !declared_formats(q).is_empty()) {
        return Err(Diagnostic::refused(refuse!("a table whose query declares column formats is not supported")));
    }
    let rows = dataset.rows.as_slice();
    let Some(first) = rows.first() else {
        return Err(Diagnostic::refused(refuse!("a table whose query returned no rows is not supported")));
    };
    let case = style.header.font.case.as_str();
    // `style.columns` styles the query's columns and hides none but those it marks `visible: false`.
    // Production resolves it from the chart's own `style:` alone and reads no board or family tier's.
    let authored = &style.columns;
    if authored.len() != own.columns.len() || authored.keys().any(|name| !own.columns.contains_key(name)) {
        return Err(Diagnostic::refused(refuse!("a `style.columns` set on a board or family tier is not supported")));
    }
    if let Some(unknown) = authored.keys().find(|name| !first.contains_key(name.as_str())) {
        return Err(Diagnostic::refused(refuse!("a `style.columns` entry for a column the query does not return ({unknown}) is not supported")));
    }
    let mut columns = Vec::with_capacity(first.len());
    for name in first.keys() {
        let config = authored.get(name);
        if let Some(config) = config {
            refuse_unmodeled_column(config)?;
            if config.visible == Some(false) {
                continue;
            }
        }
        if dataset.schema.get(name).map_err(diag::err_internal)? != &ColumnType::String {
            return Err(Diagnostic::refused(refuse!("a table column that is not text is not supported")));
        }
        let cells = rows.iter().map(|row| text_cell(row.get(name))).collect::<Result<Vec<_>, _>>()?;
        let label = match config.and_then(|c| c.label.as_ref()) {
            Some(label) => column_label(label.as_str())?,
            None => display_text::slug_to_text(name).map_err(diag::err_internal)?,
        };
        let header = display_text::case::apply_case(&label, case).map_err(diag::err_internal)?;
        let align = config.and_then(|c| c.align).unwrap_or(CellAlign::Left);
        columns.push(TableColumn { header, cells, align });
    }
    if columns.is_empty() {
        return Err(Diagnostic::refused(refuse!("a table whose columns are all hidden is not supported")));
    }
    Ok(Ok(Family::Table(Table { columns })))
}

/// Every chart field, style and board fact that changes what the table says and is not modeled.
fn refuse_unmodeled(chart: &TableChart, style: &TableChartStyle, board: &Board, run: &RunResult) -> Result<(), Diagnostic> {
    let (base, defaults) = (&chart.shared.base, &style.column_defaults);
    let unmodeled = [
        (chart.shared.subtitle.is_some(), "a `subtitle`"),
        (base.notes.is_some(), "`notes`"),
        (base.link.is_some(), "a `link`"),
        (chart.rows.is_some() || chart.columns.is_some() || chart.values.is_some(), "a pivot (`rows`, `columns` or `values`)"),
        (chart.row_role.is_some(), "a `row_role`"),
        (chart.spanners.is_some(), "`spanners`"),
        (!chart.formatting.conditional_formatting.is_empty(), "`conditional_formatting`"),
        (defaults.width.is_some() || defaults.align.is_some() || defaults.format.is_some() || defaults.background.is_some() || defaults.font.is_some(), "`style.column_defaults`"),
        (style.transpose, "`style.transpose`"),
        (board.regional.is_some() || run.env.locale != "en-US", "a locale other than en-US"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table with {what} is not supported"))),
        None => Ok(()),
    }
}

/// A cell as drawn: the text of a plain string. Production paints a blank or null cell as the null
/// dash, a date-shaped string in the numeral face and a temporal one through a date format.
fn text_cell(cell: Option<&Value>) -> Result<String, Diagnostic> {
    match cell {
        Some(Value::String(text)) if !text.trim().is_empty() && !is_date_like(text) && matches!(parse_temporal(text.trim(), &ColumnType::String), Ok(None)) => {
            Ok(text.clone())
        }
        _ => Err(Diagnostic::refused(refuse!("a table cell that is not plain text is not supported"))),
    }
}

/// A column's label as drawn. Template syntax needs the board's variables and queries; an empty
/// label heads the column with nothing, which this slice does not draw.
fn column_label(label: &str) -> Result<String, Diagnostic> {
    if label.is_empty() {
        return Err(Diagnostic::refused(refuse!("a table column with a blank `label` is not supported")));
    }
    if label.contains("{{") || label.contains("{%") {
        return Err(Diagnostic::refused(refuse!("a table column `label` with template syntax is not supported")));
    }
    Ok(label.to_string())
}

/// Every `style.columns` key that changes what a cell says or how its column is sized and is not
/// modeled; `visible`, `label` and `align` are.
fn refuse_unmodeled_column(config: &TableColumnConfig) -> Result<(), Diagnostic> {
    let unmodeled = [
        (config.format.is_some(), "`format`"),
        (config.spark.is_some(), "`spark`"),
        (config.swatch.is_some(), "`swatch`"),
        (config.width.is_some(), "`width`"),
        (config.max_width.is_some(), "`max_width`"),
        (config.header_overflow.is_some(), "`header_overflow`"),
        (config.header_link.is_some(), "`header_link`"),
        (config.link.is_some(), "`link`"),
        (config.background.is_some(), "`background`"),
        (config.font.is_some(), "`font`"),
        (config.scale.is_some(), "`scale`"),
        (config.glyph.is_some(), "`glyph`"),
        (config.glyph_color.is_some(), "`glyph_color`"),
    ];
    match unmodeled.iter().find(|(set, _)| *set) {
        Some((_, what)) => Err(Diagnostic::refused(refuse!("a table column with {what} is not supported"))),
        None => Ok(()),
    }
}
