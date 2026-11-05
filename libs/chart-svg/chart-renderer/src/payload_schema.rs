//! The declared type of each column of a payload's rows: the source declares
//! it (`dbt_charts.core.column_types`) and sends it beside the rows. A declared
//! non-string kind decides; a `string` column keeps shape detection.

use std::collections::HashMap;

use column_types::{CellMismatch, ColumnType, ColumnTypes, Dataset, QueryDatasets};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use diagnostic::codes;
use diagnostic::Diagnostic;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::Payload;

/// The declared types of a chart's `rows` and of each entry of its `datasets`.
#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
pub struct PayloadSchema {
    pub rows: ColumnTypes,
    pub datasets: HashMap<String, ColumnTypes>,
}

impl PayloadSchema {
    /// The schema of a chart drawn from `query_name`'s dataset, with every
    /// other dataset beside it for the layers that read them.
    pub fn of_datasets(datasets: &QueryDatasets, query_name: &str) -> Result<Self, String> {
        let rows = datasets
            .get(query_name)
            .ok_or_else(|| format!("dataset {query_name} not forwarded"))?
            .schema
            .clone();
        let datasets = datasets.iter().map(|(name, d)| (name.clone(), d.schema.clone())).collect();
        Ok(Self { rows, datasets })
    }

    /// The declared type of `column` in the named dataset, or in `rows` for
    /// `None`. An undeclared column or dataset is an error, never a guess.
    pub fn get(&self, dataset: Option<&str>, column: &str) -> Result<&ColumnType, Diagnostic> {
        let types = match dataset {
            None => &self.rows,
            Some(name) => self
                .datasets
                .get(name)
                .ok_or_else(|| Diagnostic::refused(format!("the payload declares no types for dataset {name:?}")))?,
        };
        types.get(column).map_err(Diagnostic::refused)
    }

    /// `ERR-CHART-CELL-TYPE-MISMATCH` for the first cell, in `rows` or any
    /// dataset, that its column's declared type does not hold.
    pub fn check(
        &self,
        rows: &[Map<String, Value>],
        datasets: Option<&HashMap<String, Vec<Map<String, Value>>>>,
    ) -> Result<(), Diagnostic> {
        let check = |types: &ColumnTypes, rows: &[Map<String, Value>]| types.check_rows(rows).map_err(CellMismatch::into_diagnostic);
        check(&self.rows, rows)?;
        for (name, rows) in datasets.into_iter().flatten() {
            let types = self.datasets.get(name).ok_or_else(|| {
                Diagnostic::refused(format!("the payload declares no types for dataset {name:?}"))
            })?;
            check(types, rows)?;
        }
        Ok(())
    }
}

/// The refusal for a cell its column's declared type does not hold.
/// `ERR-CHART-MEASURE-IS-TEXT` for the first measure channel of `chart_type`
/// whose column is declared `string`. Text is never read as a number, however
/// its cells look; date-shaped text keeps its date reading.
pub fn refuse_text_measures(payload: &Payload, chart_type: &str) -> Result<(), Diagnostic> {
    let resolved = &payload.resolved;
    let field = |key: &str| resolved.get(key).and_then(Value::as_str).filter(|f| !f.is_empty());
    let mut measures: Vec<(Option<&str>, &str)> = Vec::new();
    match chart_type {
        "bar" | "line" | "area" | "scatter" | "heatmap" => {
            let gradient = resolved.pointer("/resolved_channels/color");
            let gradient_field = gradient
                .filter(|c| c["mode"] == "gradient")
                .and_then(|c| c["data_field"].as_str());
            // A heatmap's y and a scatter's x and y can be category axes; a wide chart
            // plots its folded columns, not the fold's own value column.
            let wide = resolved.get("wide_measures").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str);
            let own: &[&str] = match chart_type {
                "heatmap" => &[],
                "scatter" => &["size"],
                _ if wide.clone().next().is_some() => &[],
                _ => &["y", "y_start"],
            };
            let own = own.iter().copied().filter_map(field);
            measures.extend(own.chain(wide).chain(gradient_field).map(|f| (None, f)));
            let base_query = field("query_name");
            for layer in resolved.get("layers").and_then(Value::as_array).into_iter().flatten() {
                let dataset = layer["query_name"].as_str().filter(|name| Some(*name) != base_query);
                measures.extend(layer["y"].as_str().map(|y| (dataset, y)));
            }
        }
        "pie" => measures.extend(field("theta").map(|f| (None, f))),
        "point_map" => measures.extend(field("size").map(|f| (None, f))),
        _ => {}
    }
    for (dataset, column) in measures {
        if !matches!(payload.schema.get(dataset, column)?, ColumnType::String) {
            continue;
        }
        let rows = match dataset {
            None => &payload.rows,
            // A layer whose dataset carries no rows plots nothing.
            Some(name) => match payload.datasets.as_ref().and_then(|d| d.get(name)) {
                Some(rows) => rows,
                None => continue,
            },
        };
        if rows.is_empty() {
            continue;
        }
        let types = match dataset {
            None => &payload.schema.rows,
            Some(name) => &payload.schema.datasets[name],
        };
        if infer_vega_type(types, rows, column)? == VegaType::Nominal {
            return Err(Diagnostic::new(&codes::ERR_CHART_MEASURE_IS_TEXT, [("column", json!(column))]));
        }
    }
    Ok(())
}

/// The rows of every dataset, keyed by query name: `Payload::datasets`.
pub fn rows_by_query(datasets: &QueryDatasets) -> HashMap<String, Vec<Map<String, Value>>> {
    datasets.iter().map(|(name, d)| (name.clone(), d.rows.clone())).collect()
}

/// The types a unit test's inline rows would be declared with: a unit test
/// writes its rows as JSON, where a date is text, so ISO dates and timestamps
/// are declared as the dates they stand for. Production never reads a type off
/// a cell; a column of mixed or nested cells is `json`.
#[cfg(test)]
pub(crate) fn test_schema_json(rows: &[Map<String, Value>]) -> Value {
    let columns: std::collections::BTreeSet<&String> = rows.iter().flat_map(Map::keys).collect();
    let kind_of = |cells: &[&Value]| -> &'static str {
        let text = |f: &dyn Fn(&str) -> bool| cells.iter().all(|c| c.as_str().is_some_and(f));
        let date = |s: &str| s.len() == 10 && s.as_bytes()[4] == b'-' && s[..4].bytes().all(|b| b.is_ascii_digit());
        let stamp = |s: &str| s.len() > 10 && date(&s[..10]) && matches!(s.as_bytes()[10], b'T' | b' ');
        let aware = |s: &str| s.ends_with('Z') || s[11..].contains(['+', '-']);
        match cells {
            [] => "string",
            _ if cells.iter().all(|c| c.is_boolean()) => "boolean",
            _ if cells.iter().all(|c| c.is_i64() || c.is_u64()) => "integer",
            _ if cells.iter().all(|c| c.is_number()) => "float",
            _ if text(&date) => "date",
            _ if text(&|s| stamp(s) && aware(s)) => "datetime_tz",
            _ if text(&stamp) => "datetime",
            _ if cells.iter().all(|c| c.is_string()) => "string",
            _ => "json",
        }
    };
    let declared: Map<String, Value> = columns
        .into_iter()
        .map(|column| {
            let cells: Vec<&Value> = rows.iter().filter_map(|r| r.get(column)).filter(|v| !v.is_null()).collect();
            (column.clone(), json!({"kind": kind_of(&cells), "element": null, "fields": null}))
        })
        .collect();
    Value::Object(declared)
}

/// `test_schema_json` for a payload's `rows` and each of its `datasets`.
#[cfg(test)]
pub(crate) fn test_payload_schema_json(
    rows: &[Map<String, Value>],
    datasets: Option<&HashMap<String, Vec<Map<String, Value>>>>,
) -> Value {
    let datasets: Map<String, Value> =
        datasets.into_iter().flatten().map(|(name, rows)| (name.clone(), test_schema_json(rows))).collect();
    json!({"rows": test_schema_json(rows), "datasets": datasets})
}

/// `payload`'s schema as `with_test_schema` infers it, with `columns` declared
/// as the given kinds: a column that is null in every inline row has no cell to
/// read a type from, as production's declared result would have.
#[cfg(test)]
pub(crate) fn test_declare(payload: &mut Value, columns: &[(&str, &str)]) {
    let mut schema = crate::with_test_schema(payload.clone())["schema"].clone();
    for (column, kind) in columns {
        schema["rows"][*column] = json!({"kind": kind, "element": null, "fields": null});
    }
    payload["schema"] = schema;
}

/// A payload's schema for inline rows a unit test assigns after parsing it.
#[cfg(test)]
pub(crate) fn test_schema_for(rows: &[Map<String, Value>]) -> PayloadSchema {
    serde_json::from_value(test_payload_schema_json(rows, None)).expect("test schemas are valid kinds")
}

/// The types a unit test's inline rows would be declared with.
#[cfg(test)]
pub(crate) fn test_schema_of(rows: &[Map<String, Value>]) -> ColumnTypes {
    serde_json::from_value(test_schema_json(rows)).expect("test schemas are valid kinds")
}

/// The JSON of `columns`, each declared `kind`.
#[cfg(test)]
pub(crate) fn test_types_json(columns: &[&str], kind: &str) -> Value {
    let declared: Map<String, Value> = columns
        .iter()
        .map(|column| ((*column).to_string(), json!({"kind": kind, "element": null, "fields": null})))
        .collect();
    Value::Object(declared)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> PayloadSchema {
        serde_json::from_value(json!({
            "rows": {"day": {"kind": "date", "element": null, "fields": null}},
            "datasets": {"other": {"n": {"kind": "integer", "element": null, "fields": null}}},
        }))
        .expect("schema deserializes")
    }

    #[test]
    fn a_column_is_typed_in_rows_or_in_a_named_dataset_and_anything_else_is_an_error() {
        let schema = schema();
        assert_eq!(schema.get(None, "day").unwrap(), &ColumnType::Date);
        assert_eq!(schema.get(Some("other"), "n").unwrap(), &ColumnType::Integer);
        assert!(schema.get(None, "ghost").is_err());
        assert!(schema.get(Some("missing"), "n").is_err());
        assert!(PayloadSchema::default().get(None, "day").is_err());
    }

    #[test]
    fn a_payload_without_a_schema_is_refused() {
        let payload = json!({"resolved": {}, "rows": [], "width": 1.0, "height": 1.0});
        // The inherent deserializer, ahead of the unit-test hook that declares inline rows.
        let err = crate::Payload::deserialize(payload).err().expect("a schema is required");
        assert!(err.to_string().contains("missing field `schema`"), "{err}");
    }

    #[test]
    fn a_cell_contradicting_its_declared_type_is_a_data_shape_diagnostic() {
        let schema = schema();
        let row = |key: &str, value: Value| vec![json!({ key: value }).as_object().unwrap().clone()];
        let datasets = HashMap::from([("other".to_string(), row("n", json!("3")))]);
        assert!(schema.check(&row("day", json!("2024-01-02")), None).is_ok());
        let err = schema.check(&row("day", json!(5)), None).unwrap_err();
        assert_eq!(err.code, "ERR-CHART-CELL-TYPE-MISMATCH");
        assert_eq!(err.params["column"], json!("day"));
        assert_eq!(err.params["declared"], json!("date"));
        assert_eq!(err.params["value"], json!("5"));
        let err = schema.check(&[], Some(&datasets)).unwrap_err();
        assert_eq!(err.params["column"], json!("n"));
    }
}
