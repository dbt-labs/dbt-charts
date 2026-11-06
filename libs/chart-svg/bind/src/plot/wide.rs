//! The wide y fold: `y: [a, b]` draws one series per measure, or per (dimension value,
//! measure) with a `color:` dimension, and one point per non-null measure cell.

use std::collections::HashMap;

use diagnostic::{diag, py_str_repr, Diagnostic};
use serde_json::{Map, Value};

use crate::rows::row_num;

/// Between a dimension value and a measure in a composite series key.
pub const SEPARATOR: &str = " - ";

/// A wide chart's authored `y:` list and what crosses it.
pub struct WideY<'a> {
    pub chart: &'a str,
    pub measures: &'a [String],
    pub field_labels: &'a HashMap<String, String>,
    /// The `color:` column the measures cross with.
    pub dimension: Option<&'a str>,
}

/// One folded series.
#[derive(Debug, Clone, PartialEq)]
pub struct WideSeries {
    /// `measure`, or `<dimension value><SEPARATOR><measure>`: the series identity.
    pub key: String,
    /// The key with the measure's label in place of its name.
    pub display: String,
    /// Index into the authored measures.
    pub measure: usize,
}

/// One drawn value: source row, series index, value.
#[derive(Debug, Clone, PartialEq)]
pub struct WidePoint {
    pub row: usize,
    pub series: usize,
    pub value: f64,
}

#[derive(Debug, PartialEq)]
pub struct WideFold {
    /// Sorted by key. A measure with no non-null cell still owns its series.
    pub series: Vec<WideSeries>,
    /// Row by row, measures in authored order. A row whose dimension is null draws none.
    pub points: Vec<WidePoint>,
    /// `WARN-WIDE-MEASURE-LABEL-COLLISION`, one per label two derived labels shared.
    pub diagnostics: Vec<Diagnostic>,
}

/// Folds `rows` over `y`'s measures, each a column `column_kind` accepts as a measure. `dimension_text` is a non-null
/// dimension cell's display text, the text the series key carries.
pub fn fold_wide(
    y: &WideY,
    rows: &[Map<String, Value>],
    dimension_text: impl Fn(&Value) -> String,
) -> Result<WideFold, Diagnostic> {
    let labels = display_text::wide_measure_labels(y.measures, y.field_labels).map_err(Diagnostic::refused)?;
    let mut shared: Vec<(&str, Vec<&str>)> = Vec::new();
    for measure in y.measures {
        let label = labels.labels[measure].as_str();
        match shared.iter_mut().find(|(l, _)| *l == label) {
            Some((_, group)) if !group.contains(&measure.as_str()) => group.push(measure),
            Some(_) => {}
            None => shared.push((label, vec![measure])),
        }
    }
    if let Some((label, group)) = shared.iter().find(|(_, group)| group.len() > 1) {
        return Err(diag::err_field_labels_wide_collision(quoted(group), *label));
    }
    if y.dimension.is_some() {
        if let Some(measure) = y.measures.iter().find(|m| m.contains(SEPARATOR)) {
            return Err(diag::err_wide_measure_name_contains_separator(measure.as_str(), SEPARATOR));
        }
    }
    let present = |row: &Map<String, Value>, column: &str| row.get(column).filter(|cell| !cell.is_null()).cloned();
    let mut values: Vec<String> = Vec::new();
    if let Some(dimension) = y.dimension {
        for text in rows.iter().filter_map(|row| present(row, dimension)).map(|cell| dimension_text(&cell)) {
            if !values.contains(&text) {
                values.push(text);
            }
        }
    }
    let mut series: Vec<WideSeries> = Vec::new();
    for (measure, name) in y.measures.iter().enumerate() {
        let label = &labels.labels[name];
        let keyed: Vec<(String, String)> = match y.dimension {
            None => vec![(name.clone(), label.clone())],
            Some(_) => values
                .iter()
                .map(|v| (format!("{v}{SEPARATOR}{name}"), format!("{v}{SEPARATOR}{label}")))
                .collect(),
        };
        for (key, display) in keyed {
            if !series.iter().any(|s| s.key == key) {
                series.push(WideSeries { key, display, measure });
            }
        }
    }
    series.sort_by(|a, b| a.key.cmp(&b.key));
    let index: HashMap<&str, usize> = series.iter().enumerate().map(|(i, s)| (s.key.as_str(), i)).collect();
    let mut points = Vec::new();
    for (row_index, row) in rows.iter().enumerate() {
        let value = match y.dimension {
            None => None,
            Some(dimension) => match present(row, dimension) {
                Some(cell) => Some(dimension_text(&cell)),
                None => continue,
            },
        };
        for name in y.measures {
            // A null or non-number cell (a boolean) draws no point, as every measure reads.
            let Some(number) = row_num(row, name) else { continue };
            let key = match &value {
                None => name.clone(),
                Some(v) => format!("{v}{SEPARATOR}{name}"),
            };
            points.push(WidePoint { row: row_index, series: index[key.as_str()], value: number });
        }
    }
    let diagnostics = labels
        .collisions
        .iter()
        .map(|(label, group)| diag::warn_wide_measure_label_collision(y.chart, quoted(group), label.as_str()))
        .collect();
    Ok(WideFold { series, points, diagnostics })
}

/// Python's `", ".join(map(repr, names))`.
fn quoted<S: AsRef<str>>(names: &[S]) -> String {
    names.iter().map(|n| py_str_repr(n.as_ref())).collect::<Vec<_>>().join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rows(value: Value) -> Vec<Map<String, Value>> {
        value.as_array().unwrap().iter().map(|r| r.as_object().unwrap().clone()).collect()
    }

    fn fold(measures: &[&str], labels: &[(&str, &str)], dimension: Option<&str>, data: Value) -> Result<WideFold, Diagnostic> {
        let measures: Vec<String> = measures.iter().map(|m| m.to_string()).collect();
        let field_labels = labels.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        let y = WideY { chart: "c", measures: &measures, field_labels: &field_labels, dimension };
        fold_wide(&y, &rows(data), |cell| cell.as_str().map_or_else(|| cell.to_string(), str::to_string))
    }

    fn keys(fold: &WideFold) -> Vec<(&str, &str)> {
        fold.series.iter().map(|s| (s.key.as_str(), s.display.as_str())).collect()
    }

    #[test]
    fn measures_fold_to_sorted_series_and_a_null_or_boolean_cell_draws_no_point() {
        let data = json!([{"revenue": 3, "profit": null}, {"revenue": 4, "profit": 1}, {"revenue": true}]);
        let out = fold(&["revenue", "profit"], &[], None, data).unwrap();
        assert_eq!(keys(&out), [("profit", "Profit"), ("revenue", "Revenue")]);
        let points: Vec<(usize, usize, f64)> = out.points.iter().map(|p| (p.row, p.series, p.value)).collect();
        assert_eq!(points, [(0, 1, 3.0), (1, 1, 4.0), (1, 0, 1.0)]);
        assert!(out.diagnostics.is_empty());
    }

    #[test]
    fn a_dimension_crosses_its_first_seen_values_and_a_null_dimension_row_draws_nothing() {
        let data = json!([{"cat": "Tools", "a": 1}, {"cat": null, "a": 2}, {"cat": "Electronics", "a": 3}]);
        let out = fold(&["a"], &[("a", "Units")], Some("cat"), data).unwrap();
        assert_eq!(keys(&out), [("Electronics - a", "Electronics - Units"), ("Tools - a", "Tools - Units")]);
        assert_eq!(out.points.iter().map(|p| (p.row, p.series)).collect::<Vec<_>>(), [(0, 1), (2, 0)]);
        let separator = fold(&["a - b"], &[], Some("cat"), json!([])).unwrap_err();
        assert_eq!(separator, diag::err_wide_measure_name_contains_separator("a - b", SEPARATOR));
    }

    #[test]
    fn a_derived_label_collision_warns_and_an_authored_one_is_refused() {
        let out = fold(&["all", "fix-linked"], &[("all", "Fix linked")], None, json!([])).unwrap();
        assert_eq!(keys(&out), [("all", "Fix linked"), ("fix-linked", "fix-linked")]);
        assert_eq!(out.diagnostics, [diag::warn_wide_measure_label_collision("c", "'all', 'fix-linked'", "Fix linked")]);
        let refused = fold(&["a", "b"], &[("a", "Same"), ("b", "Same")], None, json!([])).unwrap_err();
        assert_eq!(refused, diag::err_field_labels_wide_collision("'a', 'b'", "Same"));
    }
}
