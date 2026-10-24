//! The `email` / `url` text formats on a table column: the value prints
//! unchanged, and the link the name implies is already baked into the column
//! (`compile/resolve/chart/_table.py::_text_link_field`). A text name has no
//! spec for the numeric or date lanes, so the column lowers to a plain text
//! column before any lane is chosen, and a blank cell becomes null: it prints
//! the null dash and never links (a blank string would link a bare `mailto:`).

use serde_json::{Map, Value};

use crate::common::is_text_format;
use d3rs::py_str::is_py_space;

type Row = Map<String, Value>;

/// The spec a column's resolved `format` slot names: a bare string or an
/// object's `spec`.
fn spec(format: &Value) -> Option<&str> {
    format.as_str().or_else(|| format.get("spec")?.as_str())
}

/// `resolved` with every text-formatted column's `format` dropped and `rows`
/// with those columns' blank cells null, or `None` when no column is
/// text-formatted. A number under a text name is the kind mismatch resolve
/// raises.
pub(super) fn lower(
    resolved: &Value,
    rows: &[Row],
) -> Option<(Value, Vec<Row>)> {
    let Some(columns) = resolved.get("columns").and_then(Value::as_object) else {
        return None;
    };
    let text_columns: Vec<(&str, &str)> = columns
        .iter()
        .filter_map(|(name, column)| {
            let spec = spec(column.get("format")?).filter(|s| is_text_format(s))?;
            Some((name.as_str(), spec))
        })
        .collect();
    if text_columns.is_empty() {
        return None;
    }
    let mut lowered = resolved.clone();
    for (name, _) in &text_columns {
        lowered["columns"][*name]
            .as_object_mut()
            .expect("a column carrying a format is an object")
            .remove("format");
    }
    let blank = |value: &Value| value.as_str().is_some_and(|s| s.chars().all(is_py_space));
    let rows = rows
        .iter()
        .map(|row| {
            let mut row = row.clone();
            for (name, _) in &text_columns {
                if row.get(*name).is_some_and(blank) {
                    row.insert((*name).to_string(), Value::Null);
                }
            }
            row
        })
        .collect();
    Some((lowered, rows))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn rows(values: Value) -> Vec<Row> {
        serde_json::from_value(values).unwrap()
    }

    #[test]
    fn a_text_named_column_drops_its_format_and_nulls_its_blank_cells() {
        let resolved = json!({"columns": {
            "mail": {"format": {"spec": "email", "raw": "email"}, "link": "mailto:{{ mail | urlencode }}"},
            "site": {"format": "url"},
            "amount": {"format": {"spec": "$,.0f", "raw": "currency_whole"}},
            "name": {"format": null},
        }});
        let rows = rows(json!([
            {"mail": "a@x.co", "site": "", "amount": 5, "name": ""},
            {"mail": " \t\u{3000}", "site": "https://a.example", "amount": 6, "name": "b"},
        ]));
        let (lowered, lowered_rows) = lower(&resolved, &rows).unwrap();
        assert_eq!(
            lowered,
            json!({"columns": {
                "mail": {"link": "mailto:{{ mail | urlencode }}"},
                "site": {},
                "amount": {"format": {"spec": "$,.0f", "raw": "currency_whole"}},
                "name": {"format": null},
            }})
        );
        assert_eq!(
            lowered_rows,
            self::rows(json!([
                {"mail": "a@x.co", "site": null, "amount": 5, "name": ""},
                {"mail": null, "site": "https://a.example", "amount": 6, "name": "b"},
            ]))
        );
    }

    #[test]
    fn a_table_with_no_text_format_is_left_alone() {
        let resolved = json!({"columns": {"amount": {"format": "integer"}, "n": {}}});
        assert!(lower(&resolved, &rows(json!([{"amount": 1, "n": ""}]))).is_none());
        assert!(lower(&json!({"columns": null}), &[]).is_none());
    }
}
