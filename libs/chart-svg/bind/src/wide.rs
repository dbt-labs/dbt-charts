//! The wide-measures fold: a `y: [a, b]` chart unfolds into one row per (source row, measure),
//! production's VL `fold` transform computed host-side so every family reads the same long rows.

use column_types::{ColumnType, ColumnTypes};
use serde_json::Value;

use crate::rows::row_str;

/// Field the VL fold transform emits for the folded numeric value — mirrors
/// `WIDE_VALUE_FIELD`. A wide payload's own `y` is already this constant
/// (baked by the resolver), so no family needs to set it itself.
pub const WIDE_VALUE_FIELD: &str = "__dbt_charts_wide_value__";

/// The key under which a row whose date x was floored to its time bucket keeps
/// the date it carried, which production's tooltip and link read.
pub const RAW_X: &str = "\u{1d}raw_x";

/// The raw measure name the fold keys each row by — mirrors `WIDE_KEY_FIELD`,
/// which a link's `{{ color }}` reads.
pub const WIDE_KEY_FIELD: &str = "__dbt_charts_wide_key__";

/// Human-readable series label the fold's color field carries — mirrors
/// `WIDE_LABEL_FIELD`. Substituted for the chart's own `color` field once,
/// at the top of each wide-capable family's render entry point.
pub const WIDE_LABEL_FIELD: &str = "__dbt_charts_wide_label__";

/// Separator between a `color:` dimension's value and the measure name in a
/// composite series label; mirrors `WIDE_SERIES_SEPARATOR`.
pub const WIDE_SERIES_SEPARATOR: &str = " - ";

/// Display string for a wide chart's `color:` dimension value — the ONE
/// place production builds a series label host-side with Python `str()`
/// (`unfold_wide_rows`, `_wide.py::_label_expression`'s
/// `json.dumps(str(value))`), rather than letting Vega draw the raw datum:
/// a bool renders `"True"`/`"False"`, disagreeing with `row_str`'s
/// JSON-native `"true"`/`"false"` deliberately — everywhere else in this
/// crate a bool cell reaches Vega's own `toString`, never Python's.
fn wide_dimension_str(row: &serde_json::Map<String, Value>, field: &str) -> String {
    match row.get(field) {
        Some(Value::Bool(b)) => if *b { "True" } else { "False" }.to_string(),
        _ => row_str(row, field),
    }
}

/// Pre-fold wide rows into long form — the Rust mirror of `unfold_wide_rows`.
/// A row whose `dimension` cell is null names no series (the whole row is
/// dropped); a row whose MEASURE cell is null contributes no row for that
/// measure alone. Every other original column rides along unchanged on each
/// emitted row (`row.clone()` first, fold fields added after), so a
/// per-row lookup against a folded row still sees the same source values.
pub fn unfold_wide_rows(
    rows: &[serde_json::Map<String, Value>],
    measures: &[String],
    dimension: Option<&str>,
) -> Vec<serde_json::Map<String, Value>> {
    let mut out = Vec::new();
    for row in rows {
        if let Some(dim) = dimension {
            if row.get(dim).map(Value::is_null).unwrap_or(true) {
                continue;
            }
        }
        for measure in measures {
            let Some(value) = row.get(measure) else { continue };
            if value.is_null() {
                continue;
            }
            let mut folded = row.clone();
            let label = match dimension {
                None => measure.clone(),
                Some(dim) => format!("{}{WIDE_SERIES_SEPARATOR}{measure}", wide_dimension_str(row, dim)),
            };
            folded.insert(WIDE_LABEL_FIELD.to_string(), Value::String(label));
            folded.insert(WIDE_KEY_FIELD.to_string(), Value::String(measure.clone()));
            folded.insert(WIDE_VALUE_FIELD.to_string(), value.clone());
            out.push(folded);
        }
    }
    out
}

/// The declared types of `unfold_wide_rows`' output: every source column rides
/// along, the label and key fields are text, and the value field takes the
/// measures' own type (numbers of differing kinds fold to `float`).
pub fn unfold_wide_types(types: &ColumnTypes, measures: &[String]) -> Result<ColumnTypes, String> {
    let mut declared = measures.iter().map(|m| types.get(m)).collect::<Result<Vec<_>, _>>()?;
    declared.dedup();
    let value = match declared.as_slice() {
        [only] => (*only).clone(),
        many if many.iter().all(|t| t.is_number()) => ColumnType::Float,
        many => return Err(format!("wide measures {measures:?} mix the types {many:?}")),
    };
    Ok(types
        .clone()
        .with(WIDE_LABEL_FIELD, ColumnType::String)
        .with(WIDE_KEY_FIELD, ColumnType::String)
        .with(WIDE_VALUE_FIELD, value))
}

/// A scatter's wide fold as the validators and `bind` see it: rows folded in sorted measure order,
/// with the types that declare the fold's synthesized fields.
pub fn fold_wide(
    types: &ColumnTypes,
    rows: &[serde_json::Map<String, Value>],
    measures: &[String],
    dimension: Option<&str>,
) -> Result<(ColumnTypes, Vec<serde_json::Map<String, Value>>), String> {
    let mut order = measures.to_vec();
    order.sort();
    Ok((unfold_wide_types(types, measures)?, unfold_wide_rows(rows, &order, dimension)))
}

/// Distinct non-null values of `dimension`, in first-seen order, deduped by
/// their `wide_dimension_str` display form — mirrors `wide_dimension_values`.
/// Every composite label (`unfold_wide_rows` above) already keys on
/// `wide_dimension_str`, so dedup can't desync from the string the label
/// itself carries.
pub fn wide_dimension_values(
    rows: &[serde_json::Map<String, Value>],
    dimension: &str,
) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for row in rows {
        if row.get(dimension).map(Value::is_null).unwrap_or(true) {
            continue;
        }
        let s = wide_dimension_str(row, dimension);
        if seen.insert(s.clone()) {
            out.push(s);
        }
    }
    out
}

/// Sorted color-scale domain of a wide chart, RAW identity (production's
/// `raw_wide_series_names`): the authored measures (from the resolved chart,
/// not the rows — a measure null on every row still owns a domain/palette
/// slot), crossed with every observed `dimension` value when one is authored.
pub fn wide_series_names(
    measures: &[String],
    dimension: Option<&str>,
    rows: &[serde_json::Map<String, Value>],
) -> Vec<String> {
    let mut names: Vec<String> = match dimension {
        None => measures.to_vec(),
        Some(dim) => wide_dimension_values(rows, dim)
            .iter()
            .flat_map(|v| measures.iter().map(move |m| format!("{v}{WIDE_SERIES_SEPARATOR}{m}")))
            .collect(),
    };
    names.sort();
    names
}

/// `_wide_fields.py::humanize_wide_series_name`: a wide series' painted
/// label. A bare measure name maps to its label; a `<value><sep><measure>`
/// composite keeps the dimension value verbatim and labels the measure half;
/// anything else (not a wide series) is its own display text.
pub fn wide_series_display(name: &str, wide_labels: &std::collections::HashMap<String, String>) -> String {
    if let Some(label) = wide_labels.get(name) {
        return label.clone();
    }
    match name.rsplit_once(WIDE_SERIES_SEPARATOR) {
        Some((value, measure)) => match wide_labels.get(measure) {
            Some(label) => format!("{value}{WIDE_SERIES_SEPARATOR}{label}"),
            None => name.to_string(),
        },
        None => name.to_string(),
    }
}

/// The value a wide series' marks link by: the dimension value of a `<value><sep><measure>`
/// composite, else the series name itself (a fold with no dimension is keyed by its measure).
pub fn wide_link_key(series: &str, wide: bool, from_measures: bool) -> &str {
    match series.rsplit_once(WIDE_SERIES_SEPARATOR) {
        Some((value, _)) if wide && !from_measures => value,
        _ => series,
    }
}

#[cfg(test)]
mod wide_fields_tests {
    use super::*;
    use serde_json::json;

    fn row(v: serde_json::Value) -> serde_json::Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn unfold_drops_a_null_measure_cell() {
        let rows = vec![row(json!({"month": "Jan", "a": 1.0, "b": Value::Null}))];
        let measures = vec!["a".to_string(), "b".to_string()];
        let out = unfold_wide_rows(&rows, &measures, None);
        assert_eq!(out.len(), 1, "only measure a's row survives: {out:?}");
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("a"));
        assert_eq!(out[0][WIDE_VALUE_FIELD], json!(1.0));
    }

    #[test]
    fn unfold_drops_a_null_dimension_row_entirely() {
        let rows = vec![
            row(json!({"month": "Jan", "cat": Value::Null, "a": 1.0})),
            row(json!({"month": "Feb", "cat": "Tools", "a": 2.0})),
        ];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("cat"));
        assert_eq!(out.len(), 1, "the null-dimension row names no series: {out:?}");
        assert_eq!(out[0]["month"], json!("Feb"));
    }

    #[test]
    fn unfold_composes_the_dimension_value_and_measure_into_the_label() {
        let rows = vec![row(json!({"month": "Jan", "cat": "Tools", "a": 1.0}))];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("cat"));
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("Tools - a"));
    }

    /// A bool `color:` dimension composes Python's `str()` (`"True"`), not
    /// JSON `toString` (`"true"`) — production builds this label host-side,
    /// unlike every other bool cell in this crate, which Vega draws raw.
    #[test]
    fn unfold_composes_a_bool_dimension_value_as_pythons_str() {
        let rows = vec![row(json!({"month": "Jan", "active": true, "a": 1.0}))];
        let measures = vec!["a".to_string()];
        let out = unfold_wide_rows(&rows, &measures, Some("active"));
        assert_eq!(out[0][WIDE_LABEL_FIELD], json!("True - a"));
    }

    #[test]
    fn unfold_keeps_the_original_columns_on_every_folded_row() {
        let rows = vec![row(json!({"month": "Jan", "a": 1.0, "b": 2.0}))];
        let measures = vec!["a".to_string(), "b".to_string()];
        let out = unfold_wide_rows(&rows, &measures, None);
        assert_eq!(out.len(), 2);
        assert!(out.iter().all(|r| r["month"] == json!("Jan")));
    }

    #[test]
    fn wide_series_names_sorts_the_plain_measure_list() {
        let measures = vec!["revenue".to_string(), "profit".to_string()];
        assert_eq!(wide_series_names(&measures, None, &[]), vec!["profit", "revenue"]);
    }

    #[test]
    fn wide_series_names_sorts_the_dimension_x_measure_cross() {
        let rows = vec![
            row(json!({"cat": "Tools", "a": 1.0})),
            row(json!({"cat": "Electronics", "a": 2.0})),
        ];
        let measures = vec!["largest_order".to_string(), "avg_order_value".to_string()];
        assert_eq!(
            wide_series_names(&measures, Some("cat"), &rows),
            vec![
                "Electronics - avg_order_value",
                "Electronics - largest_order",
                "Tools - avg_order_value",
                "Tools - largest_order",
            ]
        );
    }

    #[test]
    fn wide_series_display_labels_the_measure_half_only() {
        let labels = std::collections::HashMap::from([
            ("units_sold".to_string(), "units sold".to_string()),
        ]);
        assert_eq!(wide_series_display("units_sold", &labels), "units sold");
        assert_eq!(wide_series_display("EU - units_sold", &labels), "EU - units sold");
        assert_eq!(wide_series_display("EU", &labels), "EU");
    }
}
