//! A chart's `tooltip:` columns: the rows each adds to a mark's hover card, after the mark's own.
//!
//! [`resolve`] is production's `resolve_tooltip` (`compile/resolve/chart/_tooltip.py`): each entry's
//! label (its own, else the chart's `field_labels` entry, else the column's derived title) and format,
//! decided by the column's values: all numbers take a number format, all dates a time format, and
//! anything else prints as the text it is. [`Tooltip::rows`] is `tooltip_extra::rows_for`: one row's
//! cells painted with them.

use column_types::{ColumnType, ColumnTypes};
use d3rs::native::NULL_DISPLAY;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use serde_json::{Map, Value};

use board_model::board::Board;
use board_model::chart::tooltip::TooltipField;
use board_model::ids::QueryId;

use crate::formats::locale::NumberLocale;
use crate::formats::{column_format, is_time_format, Formats, Kind, ResolvedFormat, Tiers};
use crate::kpi::{authored_format, declared_column_format};
use crate::table_dates::{parse_temporal, strftime, TemporalError};

/// `ROLE_EXTRA` (`_tooltip.py`): the invisible code point that marks an authored row of a hover card.
pub const ROLE_EXTRA: char = '\u{200D}';
/// `SEMICOLON_ESCAPE`: what a `;` inside a value becomes, since `;` separates a hover card's rows.
pub const SEMICOLON_ESCAPE: char = '\u{E000}';

/// One authored column as its rows print it.
#[derive(Debug, Clone, PartialEq)]
struct Field {
    column: String,
    label: String,
    format: Option<ResolvedFormat>,
}

/// A chart's resolved `tooltip:` columns, in authored order.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Tooltip {
    fields: Vec<Field>,
}

/// A value kind a tooltip column is resolved for.
fn kind_of(values: &[&Value]) -> Option<Kind> {
    if values.is_empty() {
        None
    } else if values.iter().all(|v| v.is_number()) {
        Some(Kind::Number)
    } else if values.iter().all(|v| v.as_str().is_some_and(crate::display_text::is_temporal_text)) {
        Some(Kind::Time)
    } else {
        None
    }
}

/// `resolve_tooltip`: each of `entries` against the chart's `field_labels`, the query `query`'s
/// declared `formats:` on `board`, the board's format table `formats`, and the rows it draws.
pub fn resolve(
    entries: &[TooltipField],
    field_labels: &IndexMap<String, String>,
    board: &Board,
    query: &QueryId,
    rows: &[Map<String, Value>],
    formats: Formats,
) -> Result<Tooltip, Diagnostic> {
    let fields = entries
        .iter()
        .map(|entry| {
            let column = entry.column.as_str();
            let present: Vec<&Value> = rows.iter().filter_map(|row| row.get(column)).filter(|v| !v.is_null()).collect();
            let own = entry.format.as_ref().map(authored_format);
            let format = match kind_of(&present) {
                Some(kind) => {
                    let declared = declared_column_format(board, query, column, formats, kind == Kind::Time)?;
                    let values: Vec<f64> = present.iter().filter_map(|v| v.as_f64()).collect();
                    let tiers = Tiers { own: own.as_ref(), column: declared.as_ref(), ..Tiers::default() };
                    Some(column_format(kind, tiers, formats, None, None, if kind == Kind::Number { &values } else { &[] })?)
                }
                None if !present.is_empty() && (own.is_some() || declared_column_format(board, query, column, formats, false)?.is_some() || declared_column_format(board, query, column, formats, true)?.is_some()) => {
                    return Err(Diagnostic::refused(refuse!("a tooltip format on a column of text or mixed values is not supported")));
                }
                None => None,
            };
            let label = match &entry.label {
                Some(label) => label.as_str().to_string(),
                None => match field_labels.get(column) {
                    Some(label) => label.clone(),
                    None => display_text::slug_to_text(column).map_err(diag::err_internal)?,
                },
            };
            if label.contains(':') || label.contains(';') {
                return Err(Diagnostic::refused(refuse!("a tooltip label holding `:` or `;` is not supported")));
            }
            Ok(Field { column: column.to_string(), label, format })
        })
        .collect::<Result<_, Diagnostic>>()?;
    Ok(Tooltip { fields })
}

impl Tooltip {
    /// `rows_for`: the rows `row` adds to its hover card, each `ROLE_EXTRA label: value`.
    pub fn rows(&self, row: &Map<String, Value>, schema: &ColumnTypes, locale: &NumberLocale) -> Result<Vec<String>, Diagnostic> {
        self.fields.iter().map(|field| Ok(format!("{ROLE_EXTRA}{}: {}", field.label, field.value(row, schema, locale)?))).collect()
    }
}

impl Field {
    /// `TooltipField.value`: a cell printed with its column's format, `;` escaped.
    fn value(&self, row: &Map<String, Value>, schema: &ColumnTypes, locale: &NumberLocale) -> Result<String, Diagnostic> {
        let wrong = |why: String| diag::err_internal(format!("tooltip column {:?}: {why}", self.column));
        let cell = row.get(&self.column).ok_or_else(|| wrong("missing from the row".into()))?;
        let text = match (cell, &self.format) {
            (Value::Null, _) => NULL_DISPLAY.to_string(),
            (Value::Number(n), Some(format)) if !is_time_format(&format.spec) => {
                let value = n.as_f64().ok_or_else(|| wrong(format!("{n} is not a finite number")))?;
                format.paint_house(locale, value)?
            }
            (Value::String(text), Some(format)) if is_time_format(&format.spec) => {
                let declared: &ColumnType = schema.get(&self.column).map_err(wrong)?;
                match parse_temporal(text.trim(), declared) {
                    Ok(Some(moment)) => strftime(moment, &format.spec).map_err(wrong)?,
                    Ok(None) => return Err(wrong(format!("{text:?} is not a date"))),
                    Err(TemporalError::Invalid(why) | TemporalError::Unported(why)) => return Err(wrong(why)),
                }
            }
            (_, Some(format)) => return Err(wrong(format!("{cell} does not fit the format {:?}", format.spec))),
            (Value::String(text), None) => text.clone(),
            (Value::Number(_) | Value::Bool(_), None) => cell.to_string(),
            (_, None) => return Err(wrong(format!("{cell} is not a scalar"))),
        };
        if text.contains(SEMICOLON_ESCAPE) {
            return Err(Diagnostic::refused(refuse!("a tooltip value holding the character reserved to escape `;` is not supported")));
        }
        Ok(text.replace(';', &SEMICOLON_ESCAPE.to_string()))
    }
}
