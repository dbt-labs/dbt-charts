//! A column's authored `type:` (`style.columns.<col>.type`): `text`, `number`,
//! `date` or `datetime`, whatever the query declares. It is presentation only:
//! the column's cells are rewritten once, before any lane is chosen, so the
//! lanes read a typed column as they read a query that declared it.
//!
//! A non-blank cell that does not cast is `ERR-TABLE-CELL-CAST`, never printed as
//! written. A declared boolean, json, list or struct column takes no type.

use ::bind::rows::coerce_numeric_cell;
use ::bind::table_dates::parse_iso;
use ::bind::time_zone::all_aware;
use column_types::{CellRow, ColumnType, ColumnTypes};
use diagnostic::{diag, py_str, Diagnostic};
use serde::Deserialize;
use serde_json::{Map, Number, Value};

use super::bind::board_alias;
use crate::common::{is_text_format, FormatSlot};

type Row = Map<String, Value>;

#[derive(Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "lowercase")]
pub(super) enum CellType {
    Text,
    Number,
    Date,
    Datetime,
}

#[derive(PartialEq, Eq)]
enum FormatKind {
    Text,
    Number,
    Time,
}

impl CellType {
    fn name(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Number => "number",
            Self::Date => "date",
            Self::Datetime => "datetime",
        }
    }

    fn format_kind(self) -> FormatKind {
        match self {
            Self::Text => FormatKind::Text,
            Self::Number => FormatKind::Number,
            Self::Date | Self::Datetime => FormatKind::Time,
        }
    }

    fn expected(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Number => "a number",
            Self::Date => "an ISO date (YYYY-MM-DD)",
            Self::Datetime => "an ISO date or timestamp",
        }
    }
}

/// Whether `format` writes the kind of value a column of `column_type` holds. An inherited
/// format of another kind says nothing about a typed column, so it does not apply.
pub(super) fn format_fits(column_type: Option<CellType>, format: &FormatSlot) -> Result<bool, String> {
    let Some(column_type) = column_type else {
        return Ok(true);
    };
    let spec = board_alias(&format.spec)?.unwrap_or_else(|| format.spec.clone());
    let kind = if is_text_format(&spec) {
        FormatKind::Text
    } else if d3rs::format::predefined_time_spec(&spec).is_some() || d3rs::format_spec::is_time_format(&spec) {
        FormatKind::Time
    } else {
        FormatKind::Number
    };
    Ok(kind == column_type.format_kind())
}

/// `rows` and `types` with every typed column of `resolved` cast, or `None` when none is typed.
pub(super) fn lower(
    resolved: &Value,
    rows: &[Row],
    types: &ColumnTypes,
    cell_text: &[CellRow],
) -> Result<Option<(Vec<Row>, ColumnTypes)>, Diagnostic> {
    let Some(columns) = resolved.get("columns").and_then(Value::as_object) else {
        return Ok(None);
    };
    let mut typed = Vec::new();
    for (name, column) in columns {
        let Some(declared) = column.get("type").filter(|t| !t.is_null()) else {
            continue;
        };
        let column_type = CellType::deserialize(declared)
            .map_err(|e| Diagnostic::refused(format!("table: column '{name}' type: {e}")))?;
        if !types.0.contains_key(name) && !rows.is_empty() {
            return Err(Diagnostic::refused(format!(
                "table: column '{name}' sets `type: {}`, but the query returns no such column; a pivoted column takes no type, so type the measure it fans out from",
                column_type.name()
            )));
        }
        typed.push((name.as_str(), column_type));
    }
    if typed.is_empty() {
        return Ok(None);
    }
    let mut rows = rows.to_vec();
    let mut types = types.clone();
    for (name, column_type) in typed {
        let Ok(declared) = types.get(name).cloned() else {
            continue;
        };
        if matches!(declared, ColumnType::Boolean | ColumnType::Json | ColumnType::List(_) | ColumnType::Struct(_)) {
            return Err(Diagnostic::refused(format!(
                "table: column '{name}' is declared {declared}, which takes no `type: {}`",
                column_type.name()
            )));
        }
        let cast = cast_column(&mut rows, cell_text, name, column_type, declared)?;
        types = types.with(name, cast);
    }
    Ok(Some((rows, types)))
}

/// Cast column `name` of `rows` in place; the kind its cells are now.
fn cast_column(
    rows: &mut [Row],
    cell_text: &[CellRow],
    name: &str,
    column_type: CellType,
    declared: ColumnType,
) -> Result<ColumnType, Diagnostic> {
    if column_type == CellType::Number && declared.is_number() {
        return Ok(declared);
    }
    let mut all_whole = true;
    let mut stamps: Vec<String> = Vec::new();
    for (at, row) in rows.iter_mut().enumerate() {
        let Some(cell) = row.get_mut(name) else { continue };
        let blank = match cell {
            Value::Null => true,
            Value::String(text) => text.trim().is_empty(),
            _ => false,
        };
        if blank {
            *cell = Value::Null;
            continue;
        }
        let original = cell.clone();
        let fail = || diag::err_table_cell_cast(name, column_type.name(), original.clone(), column_type.expected());
        match column_type {
            CellType::Text => {
                // A tagged cell (a decimal) prints the spelling the source wrote.
                let tag = cell_text.get(at).and_then(|tags| tags.get(name));
                if let Some(tag) = tag {
                    *cell = Value::String(tag.text.clone());
                } else if !cell.is_string() {
                    *cell = Value::String(py_str(cell));
                }
            }
            CellType::Number => {
                let number = coerce_numeric_cell(Some(cell)).ok_or_else(fail)?;
                all_whole &= number.fract() == 0.0 && number.abs() < 9e15;
                if cell.is_string() {
                    *cell = if number.fract() == 0.0 && number.abs() < 9e15 {
                        Value::from(number as i64)
                    } else {
                        Number::from_f64(number).map(Value::Number).ok_or_else(fail)?
                    };
                }
            }
            CellType::Date | CellType::Datetime => {
                let Value::String(text) = &*cell else {
                    return Err(fail());
                };
                let text = text.trim();
                // A date column keeps every spelling a string column's date lane read
                // (`03/15/2024`, `Mar 15`, `Q1 2024`) as written; an ISO-shaped day must
                // be a real one (`2024-13-40` is refused).
                let iso_day = text.len() == 10
                    && text.bytes().enumerate().all(|(i, b)| if i == 4 || i == 7 { b == b'-' } else { b.is_ascii_digit() });
                let reads = match column_type {
                    CellType::Datetime => matches!(parse_iso(text), Ok(Some(_))),
                    _ if iso_day => matches!(parse_iso(text), Ok(Some(_))),
                    _ => column_types::is_date_like(text),
                };
                if !reads {
                    return Err(fail());
                }
                stamps.push(text.to_string());
                *cell = Value::String(text.to_string());
            }
        }
    }
    Ok(match column_type {
        CellType::Text => ColumnType::String,
        CellType::Number if all_whole => ColumnType::Integer,
        CellType::Number => ColumnType::Float,
        CellType::Date => ColumnType::Date,
        CellType::Datetime if all_aware(stamps.iter().map(String::as_str)) => ColumnType::DatetimeTz,
        CellType::Datetime => ColumnType::Datetime,
    })
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn text_prints_a_decimal_as_the_source_spelled_it() {
        let resolved = json!({"columns": {"v": {"type": "text"}}});
        let rows: Vec<Row> = serde_json::from_value(json!([{"v": 2.5}, {"v": 7.5}])).unwrap();
        let tags: Vec<CellRow> = vec![
            CellRow::from([("v".to_string(), column_types::CellTag { text: "2.50".into(), non_finite: None })]),
            CellRow::new(),
        ];
        let types = ColumnTypes::default().with("v", ColumnType::Decimal);
        let (lowered, _) = lower(&resolved, &rows, &types, &tags).unwrap().unwrap();
        assert_eq!(lowered[0]["v"], json!("2.50"));
        assert_eq!(lowered[1]["v"], json!("7.5"));
    }

    #[test]
    fn a_type_on_a_column_the_query_does_not_return_refuses_by_name() {
        let resolved = json!({"columns": {"ghost": {"type": "number"}}});
        let rows: Vec<Row> = serde_json::from_value(json!([{"v": 1}])).unwrap();
        let types = ColumnTypes::default().with("v", ColumnType::Integer);
        let err = lower(&resolved, &rows, &types, &[]).unwrap_err().to_string();
        assert!(err.contains("'ghost'") && err.contains("pivoted"), "{err}");
    }
}
