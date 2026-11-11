//! Decode (spec § 5.1, pass 1), the first board-wide pass: a dataset's cells against its declared schema, once, into
//! one [`Key`] variant per column. The declared type picks the variant, never the cell. A
//! number or date key keeps its wire text, so `2` and `2.0` are two keys, as production's
//! canonical-JSON partition key keeps them.
//!
//! A date or timestamp is read by the time axis's one parser; an aware timestamp's
//! calendar date is read in the active board zone (`crate::time_zone`). Nested columns
//! (`json`, `list`, `struct`) have no key: a law that asks for one is refused by name.

use std::collections::HashMap;

use column_types::{CellMismatch, ColumnType, Dataset};
use diagnostic::Diagnostic;
use serde_json::{Map, Value};

use crate::bound::Key;
use crate::time_axis::{Grain, TimeAxis};

/// A dataset's scalar columns as keys, column-major, one key per row.
#[derive(Debug)]
pub struct Decoded {
    columns: HashMap<String, Vec<Key>>,
    nested: HashMap<String, String>,
}

impl Decoded {
    /// The keys of `column`, one per row. A column the schema does not declare, or one
    /// whose declared type is nested, is refused by name.
    pub fn keys(&self, column: &str) -> Result<&[Key], Diagnostic> {
        if let Some(keys) = self.columns.get(column) {
            return Ok(keys);
        }
        Err(Diagnostic::refused(match self.nested.get(column) {
            Some(declared) => format!("column {column:?} is declared {declared}, which has no key"),
            None => format!("the schema declares no type for column {column:?}"),
        }))
    }
}

/// Decodes every declared scalar column of `dataset`. A cell its declaration does not hold
/// is `ERR-CHART-CELL-TYPE-MISMATCH`; a missing cell is [`Key::Null`].
pub fn decode(dataset: &Dataset) -> Result<Decoded, Diagnostic> {
    dataset.schema.check_rows(&dataset.rows).map_err(CellMismatch::into_diagnostic)?;
    let mut axis = TimeAxis::new(Grain::default(), HashMap::new());
    let (mut columns, mut nested) = (HashMap::new(), HashMap::new());
    for (column, declared) in &dataset.schema.0 {
        if matches!(declared, ColumnType::Json | ColumnType::List(_) | ColumnType::Struct(_)) {
            nested.insert(column.clone(), declared.to_string());
            continue;
        }
        let keys = dataset
            .rows
            .iter()
            .map(|row| key(row, column, declared, &mut axis))
            .collect::<Result<_, _>>()?;
        columns.insert(column.clone(), keys);
    }
    Ok(Decoded { columns, nested })
}

fn key(row: &Map<String, Value>, column: &str, declared: &ColumnType, axis: &mut TimeAxis) -> Result<Key, Diagnostic> {
    let cell = match row.get(column) {
        None | Some(Value::Null) => return Ok(Key::Null),
        Some(cell) => cell,
    };
    let mismatch = || {
        CellMismatch { column: column.to_string(), declared: declared.to_string(), value: cell.to_string() }
            .into_diagnostic()
    };
    Ok(match (declared, cell) {
        (ColumnType::Integer, Value::Number(n)) => match n.as_i64() {
            Some(i) => Key::Int(i),
            None => return Err(Diagnostic::refused(format!("integer {n} in column {column:?} exceeds the 64-bit signed range"))),
        },
        (ColumnType::Float | ColumnType::Decimal, Value::Number(n)) => {
            Key::Num { value: n.as_f64().ok_or_else(mismatch)?, text: n.to_string() }
        }
        (ColumnType::Boolean, Value::Bool(b)) => Key::Bool(*b),
        (ColumnType::String | ColumnType::Time | ColumnType::Bytes, Value::String(text)) => Key::Text(text.clone()),
        (ColumnType::Date | ColumnType::Datetime | ColumnType::DatetimeTz, Value::String(text)) => {
            Key::Date { instant: axis.read(text).map_err(|_| Diagnostic::refused(format!("date {text:?} in column {column:?} lies outside the years the time axis reads")))?.clone(), text: text.clone() }
        }
        _ => return Err(mismatch()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn dataset(schema: Value, rows: Value) -> Dataset {
        serde_json::from_value(json!({"schema": schema, "rows": rows})).unwrap()
    }

    fn scalar(kind: &str) -> Value {
        json!({"kind": kind, "element": null, "fields": null})
    }

    #[test]
    fn the_declared_type_picks_one_variant_per_column_and_a_number_keeps_its_text() {
        let data = dataset(
            json!({"d": scalar("decimal"), "n": scalar("integer"), "b": scalar("boolean"), "s": scalar("string")}),
            json!([{"d": 2, "n": 2, "b": true, "s": "2"}, {"d": 2.0, "n": null, "b": false}]),
        );
        let decoded = decode(&data).unwrap();
        let d = decoded.keys("d").unwrap();
        assert!(matches!(d, [Key::Num { value: 2.0, .. }, Key::Num { value: 2.0, .. }]) && d[0] != d[1], "{d:?}");
        assert!(matches!(decoded.keys("n").unwrap(), [Key::Int(2), Key::Null]));
        assert!(matches!(decoded.keys("b").unwrap(), [Key::Bool(true), Key::Bool(false)]));
        assert!(matches!(decoded.keys("s").unwrap(), [Key::Text(t), Key::Null] if t == "2"));
    }

    #[test]
    fn an_aware_timestamp_reads_its_date_in_the_board_zone_and_orders_by_instant() {
        let _zone = crate::time_zone::zone_scope("America/New_York");
        let data = dataset(
            json!({"at": scalar("datetime_tz")}),
            json!([{"at": "2024-03-01T02:00:00Z"}, {"at": "2024-02-29T23:00:00-05:00"}]),
        );
        let decoded = decode(&data).unwrap();
        let [Key::Date { instant: first, .. }, Key::Date { instant: second, .. }] = decoded.keys("at").unwrap() else {
            panic!("two dates")
        };
        assert_eq!(first.date, (2024, 2, 29));
        assert!(decoded.keys("at").unwrap()[0] < decoded.keys("at").unwrap()[1], "{first:?} {second:?}");
    }

    #[test]
    fn a_mismatched_cell_and_a_nested_column_are_refused_by_name() {
        let bad = dataset(json!({"n": scalar("integer")}), json!([{"n": "3"}]));
        assert_eq!(
            decode(&bad).unwrap_err(),
            CellMismatch { column: "n".into(), declared: "integer".into(), value: "\"3\"".into() }.into_diagnostic()
        );
        let list = json!({"kind": "list", "element": scalar("integer"), "fields": null});
        let decoded = decode(&dataset(json!({"xs": list}), json!([{"xs": [1, 2]}]))).unwrap();
        assert!(decoded.keys("xs").unwrap_err().to_string().contains("list<integer>"));
        assert!(decoded.keys("ghost").is_err());
        let huge = dataset(json!({"n": scalar("integer")}), json!([{"n": 18446744073709551615u64}]));
        assert!(decode(&huge).unwrap_err().to_string().contains("exceeds the 64-bit signed range"));
    }

    #[test]
    fn two_spellings_of_one_instant_are_two_keys_ordered_by_instant() {
        let data = dataset(
            json!({"at": scalar("datetime_tz"), "day": scalar("date")}),
            json!([
                {"at": "2024-01-01T00:00:00+00:00", "day": "2024-01-02"},
                {"at": "2024-01-01T00:00:00Z", "day": "2024-01-02"},
                {"at": "2023-12-31T23:00:00Z"},
            ]),
        );
        let decoded = decode(&data).unwrap();
        let at = decoded.keys("at").unwrap();
        assert!(at[0] != at[1] && at[2] < at[0] && at[2] < at[1], "{at:?}");
        let day = decoded.keys("day").unwrap();
        assert_eq!(day[0], day[1]);
    }
}
