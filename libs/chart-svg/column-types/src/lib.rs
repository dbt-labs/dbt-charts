//! A query column's declared type, as `dbt_charts.core.column_types.ColumnType`
//! dumps it: JSON cannot tell a date from a string, so a declared non-string
//! kind decides. A `string` column keeps shape detection (ISO dates, labeled
//! buckets, years); an undeclared column is an error.
//!
//! A [`Dataset`] pairs a query's rows with those types: the shape the host
//! sends the engine and `bind`.

use std::collections::HashMap;
use std::fmt;
use std::sync::OnceLock;

mod shared_rows;
pub use shared_rows::SharedRows;

use serde::Deserialize;
use diagnostic::{codes, Diagnostic};
use serde_json::{json, Map, Value};

/// Production's `_DATE_RE` (`core/utils.py`): ISO (`2024-03`, `2024-03-15`), US (`03/15/2024`), quarter
/// (`Q1 2024`), the short and long month-day forms and a bare year. Pattern-based, not semantic: an
/// impossible `\d{2}/\d{2}/\d{4}` still matches.
pub fn is_date_like(text: &str) -> bool {
    static DATE: OnceLock<regex::Regex> = OnceLock::new();
    DATE.get_or_init(|| {
        regex::Regex::new(
            r"^(?:\d{4}-\d{2}(?:-\d{2})?|\d{2}/\d{2}/\d{4}|Q[1-4]\s+\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}|\d{4})$",
        )
        .expect("a valid pattern")
    })
    .is_match(text.trim())
}

/// A declared column type. `Datetime` is naive wall-clock time, `DatetimeTz`
/// an instant; `List` carries its element type and `Struct` its ordered fields.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(try_from = "Wire")]
pub enum ColumnType {
    String,
    Integer,
    Float,
    Decimal,
    Boolean,
    Date,
    Datetime,
    DatetimeTz,
    Time,
    Bytes,
    Json,
    List(Box<ColumnType>),
    Struct(Vec<(String, ColumnType)>),
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum WireKind {
    String,
    Integer,
    Float,
    Decimal,
    Boolean,
    Date,
    Datetime,
    DatetimeTz,
    Time,
    Bytes,
    Json,
    List,
    Struct,
}

/// `ColumnType.model_dump()`: `kind`, plus `element` for a list and `fields`
/// for a struct (each `null` otherwise).
#[derive(Deserialize)]
struct Wire {
    kind: WireKind,
    element: Option<Box<ColumnType>>,
    fields: Option<Vec<(String, ColumnType)>>,
}

impl TryFrom<Wire> for ColumnType {
    type Error = String;

    fn try_from(wire: Wire) -> Result<Self, String> {
        let Wire { kind, element, fields } = wire;
        if matches!(kind, WireKind::List) != element.is_some() {
            return Err("exactly the list kind carries an element type".into());
        }
        if matches!(kind, WireKind::Struct) != fields.is_some() {
            return Err("exactly the struct kind carries fields".into());
        }
        Ok(match (kind, element, fields) {
            (WireKind::String, ..) => Self::String,
            (WireKind::Integer, ..) => Self::Integer,
            (WireKind::Float, ..) => Self::Float,
            (WireKind::Decimal, ..) => Self::Decimal,
            (WireKind::Boolean, ..) => Self::Boolean,
            (WireKind::Date, ..) => Self::Date,
            (WireKind::Datetime, ..) => Self::Datetime,
            (WireKind::DatetimeTz, ..) => Self::DatetimeTz,
            (WireKind::Time, ..) => Self::Time,
            (WireKind::Bytes, ..) => Self::Bytes,
            (WireKind::Json, ..) => Self::Json,
            (WireKind::List, Some(element), _) => Self::List(element),
            (WireKind::Struct, _, Some(fields)) => Self::Struct(fields),
            _ => unreachable!("the list and struct kinds are checked above"),
        })
    }
}

impl fmt::Display for ColumnType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::String => f.write_str("string"),
            Self::Integer => f.write_str("integer"),
            Self::Float => f.write_str("float"),
            Self::Decimal => f.write_str("decimal"),
            Self::Boolean => f.write_str("boolean"),
            Self::Date => f.write_str("date"),
            Self::Datetime => f.write_str("datetime"),
            Self::DatetimeTz => f.write_str("datetime_tz"),
            Self::Time => f.write_str("time"),
            Self::Bytes => f.write_str("bytes"),
            Self::Json => f.write_str("json"),
            Self::List(element) => write!(f, "list<{element}>"),
            Self::Struct(fields) => {
                f.write_str("struct<{")?;
                for (i, (name, field)) in fields.iter().enumerate() {
                    write!(f, "{}{name}: {field}", if i == 0 { "" } else { ", " })?;
                }
                f.write_str("}>")
            }
        }
    }
}

impl ColumnType {
    /// A calendar reading carried as ISO text: the only columns whose cells
    /// are ever decoded as dates.
    pub fn is_temporal(&self) -> bool {
        matches!(self, Self::Date | Self::Datetime | Self::DatetimeTz)
    }

    /// A column whose cells are JSON numbers.
    pub fn is_number(&self) -> bool {
        matches!(self, Self::Integer | Self::Float | Self::Decimal)
    }

    /// Whether `cell`, with the wire's decimal and non-finite tags decoded
    /// to numbers and nulls, is what the declaration says, at any depth. A
    /// null fits every type.
    fn holds(&self, cell: &Value) -> bool {
        match (self, cell) {
            (_, Value::Null) | (Self::Json, _) => true,
            (Self::Integer, Value::Number(n)) => n.is_i64() || n.is_u64(),
            (Self::Float | Self::Decimal, Value::Number(_)) => true,
            (Self::Boolean, Value::Bool(_)) => true,
            (Self::String | Self::Time | Self::Bytes, Value::String(_)) => true,
            (Self::Date, Value::String(text)) => iso_date(text) && text.len() == 10,
            // An instant carries its offset; wall-clock time never does.
            (Self::Datetime, Value::String(text)) => iso_date(text) && text.len() > 10 && !has_offset(text),
            (Self::DatetimeTz, Value::String(text)) => iso_date(text) && text.len() > 10 && has_offset(text),
            (Self::List(element), Value::Array(items)) => items.iter().all(|item| element.holds(item)),
            (Self::Struct(fields), Value::Object(object)) => fields
                .iter()
                .all(|(name, field)| object.get(name).is_none_or(|value| field.holds(value))),
            _ => false,
        }
    }
}

/// `YYYY-MM-DD` at the start of `text`.
fn iso_date(text: &str) -> bool {
    let b = text.as_bytes();
    b.len() >= 10
        && b[..4].iter().all(u8::is_ascii_digit)
        && b[4] == b'-'
        && b[5..7].iter().all(u8::is_ascii_digit)
        && b[7] == b'-'
        && b[8..10].iter().all(u8::is_ascii_digit)
}

/// An ISO timestamp's `Z` or `+hh:mm` / `-hh:mm` zone, which can only follow
/// the time of day.
fn has_offset(timestamp: &str) -> bool {
    let time = timestamp.get(11..).unwrap_or("");
    time.ends_with('Z') || time.contains(['+', '-'])
}

/// A cell that is not what its column's declaration says.
#[derive(Debug, PartialEq)]
pub struct CellMismatch {
    pub column: String,
    pub declared: String,
    pub value: String,
}

impl CellMismatch {
    /// `ERR-CHART-CELL-TYPE-MISMATCH` for this cell.
    pub fn into_diagnostic(self) -> Diagnostic {
        Diagnostic::new(
            &codes::ERR_CHART_CELL_TYPE_MISMATCH,
            [("column", json!(self.column)), ("declared", json!(self.declared)), ("value", json!(self.value))],
        )
    }
}

/// What the wire tagged on one cell: the Python `str()` of the value, and the
/// non-finite float a `null` stands for (`None` for a Decimal).
#[derive(Clone, Debug, PartialEq)]
pub struct CellTag {
    pub text: String,
    pub non_finite: Option<f64>,
}

/// The tags of one row's tagged cells, keyed by column.
pub type CellRow = std::collections::BTreeMap<String, CellTag>;

/// The tags of every row's tagged cells, by row.
pub type CellText = Vec<CellRow>;

/// One query result as the board payload carries it: rows plus their types. The rows hold plain
/// numbers: the wire's cell markers are decoded once, where the host's text becomes a `Dataset`,
/// and `cell_text` keeps the text they carried.
#[derive(Debug, Clone, Deserialize)]
pub struct Dataset {
    pub rows: SharedRows,
    pub schema: ColumnTypes,
    #[serde(skip)]
    pub cell_text: CellText,
}

/// One chart's datasets, keyed by query name.
pub type QueryDatasets = HashMap<String, Dataset>;

/// One dataset's declared column types, by column name.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(transparent)]
pub struct ColumnTypes(pub HashMap<String, ColumnType>);

impl ColumnTypes {
    /// The declared type of `column`. A column the schema does not declare
    /// is an error, never a guess.
    pub fn get(&self, column: &str) -> Result<&ColumnType, String> {
        self.0.get(column).ok_or_else(|| format!("the schema declares no type for column {column:?}"))
    }

    /// These types with `column` declared (or redeclared) as `declared`.
    pub fn with(mut self, column: impl Into<String>, declared: ColumnType) -> Self {
        self.0.insert(column.into(), declared);
        self
    }

    /// The first cell of a declared column that its type does not hold.
    pub fn check_rows(&self, rows: &[Map<String, Value>]) -> Result<(), CellMismatch> {
        for row in rows {
            for (column, declared) in &self.0 {
                if let Some(cell) = row.get(column).filter(|cell| !declared.holds(cell)) {
                    return Err(CellMismatch {
                        column: column.clone(),
                        declared: declared.to_string(),
                        value: cell.to_string(),
                    });
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn scalar(kind: &str) -> Value {
        json!({"kind": kind, "element": null, "fields": null})
    }

    fn types() -> ColumnTypes {
        serde_json::from_value(json!({
            "day": scalar("date"),
            "n": scalar("integer"),
            "tags": {"kind": "list", "element": scalar("string"), "fields": null},
        }))
        .expect("column types deserialize")
    }

    #[test]
    fn an_undeclared_column_is_an_error_not_a_guess() {
        let types = types();
        assert_eq!(types.get("day").unwrap(), &ColumnType::Date);
        assert!(types.get("ghost").is_err());
    }

    #[test]
    fn the_python_dump_of_a_nested_type_deserializes_and_prints_like_python() {
        let nested: ColumnType = serde_json::from_value(json!({
            "kind": "struct",
            "element": null,
            "fields": [["a", {"kind": "list", "element": scalar("datetime_tz"), "fields": null}]],
        }))
        .unwrap();
        assert_eq!(nested.to_string(), "struct<{a: list<datetime_tz>}>");
        assert!(serde_json::from_value::<ColumnType>(json!({"kind": "list", "element": null, "fields": null})).is_err());
    }

    #[test]
    fn a_naive_timestamp_contradicts_datetime_tz_and_an_aware_one_datetime() {
        let column = |kind: &str, value: &str| {
            let types: ColumnTypes = serde_json::from_value(json!({"at": scalar(kind)})).unwrap();
            types.check_rows(&[json!({"at": value}).as_object().unwrap().clone()]).is_ok()
        };
        assert!(column("datetime", "2024-01-02T03:04:05.5"));
        assert!(!column("datetime", "2024-01-02T03:04:05+00:00"));
        assert!(column("datetime_tz", "2024-01-02T03:04:05+00:00"));
        assert!(column("datetime_tz", "2024-01-02T03:04:05Z"));
        assert!(!column("datetime_tz", "2024-01-02T03:04:05"));
        assert!(column("date", "2024-01-02") && !column("date", "Jan 2, 2024"));
    }

    #[test]
    fn a_cell_that_contradicts_its_declaration_names_column_type_and_value() {
        let types = types();
        let rows = |value: Value| vec![json!({"n": value}).as_object().unwrap().clone()];
        assert_eq!(types.check_rows(&rows(json!(3))), Ok(()));
        assert_eq!(types.check_rows(&rows(Value::Null)), Ok(()));
        assert_eq!(
            types.check_rows(&rows(json!("3"))),
            Err(CellMismatch { column: "n".into(), declared: "integer".into(), value: "\"3\"".into() })
        );
    }
}
