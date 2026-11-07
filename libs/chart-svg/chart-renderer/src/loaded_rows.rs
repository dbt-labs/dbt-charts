//! A query result parsed once: the host loads it, then every engine call that
//! draws from it shares the parsed rows instead of re-reading them.

use std::collections::HashMap;
use std::sync::Arc;

use ::bind::rows::{decode_cells, CellText};
use column_types::{ColumnTypes, SharedRows};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::families::table::width::MeasurePayload;
use crate::payload_schema::PayloadSchema;
use crate::Payload;

type Row = Map<String, Value>;

/// One result's rows, decoded from the wire's cell tags, with its declared types.
#[derive(Debug)]
pub struct LoadedRows {
    pub rows: SharedRows,
    pub schema: ColumnTypes,
    pub cell_text: Arc<CellText>,
}

#[derive(Deserialize)]
struct Wire {
    rows: Vec<Row>,
    schema: ColumnTypes,
}

impl LoadedRows {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let Wire { mut rows, schema } = serde_json::from_str(text).map_err(|e| format!("parse rows: {e}"))?;
        let cell_text = Arc::new(decode_cells(&mut rows)?);
        Ok(Self { rows: rows.into(), schema, cell_text })
    }
}

fn layout_fields(layout: &str) -> Result<Map<String, Value>, String> {
    let Value::Object(fields) = serde_json::from_str(layout).map_err(|e| format!("parse payload: {e}"))? else {
        return Err("parse payload: a layout is an object".to_string());
    };
    for key in ["rows", "datasets", "schema"] {
        if fields.contains_key(key) {
            return Err(format!("parse payload: the layout carries {key:?}; rows arrive as loaded handles"));
        }
    }
    Ok(fields)
}

impl Payload {
    /// The payload `layout` describes over `rows` and the `datasets` beside them.
    pub fn from_layout(
        layout: &str,
        rows: &LoadedRows,
        datasets: &HashMap<String, Arc<LoadedRows>>,
    ) -> Result<Payload, String> {
        let mut fields = layout_fields(layout)?;
        fields.insert("rows".into(), json!([]));
        fields.insert("schema".into(), json!({"rows": {}, "datasets": {}}));
        let mut payload = Payload::deserialize(Value::Object(fields)).map_err(|e| format!("parse payload: {e}"))?;
        payload.rows = rows.rows.clone();
        payload.cell_text = rows.cell_text.clone();
        payload.datasets = Some(datasets.iter().map(|(name, d)| (name.clone(), d.rows.clone())).collect());
        payload.schema = PayloadSchema {
            rows: rows.schema.clone(),
            datasets: datasets.iter().map(|(name, d)| (name.clone(), d.schema.clone())).collect(),
        };
        Ok(payload)
    }
}

impl MeasurePayload {
    /// The measure `layout` describes over `rows`.
    pub fn from_layout(layout: &str, rows: &LoadedRows) -> Result<MeasurePayload, String> {
        let mut fields = layout_fields(layout)?;
        fields.insert("rows".into(), json!([]));
        fields.insert("schema".into(), json!({}));
        let mut payload = MeasurePayload::deserialize(Value::Object(fields)).map_err(|e| format!("parse payload: {e}"))?;
        payload.rows = rows.rows.clone();
        payload.cell_text = rows.cell_text.clone();
        payload.schema = rows.schema.clone();
        Ok(payload)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn loaded(text: &str) -> Arc<LoadedRows> {
        Arc::new(LoadedRows::from_json(text).expect("rows load"))
    }

    #[test]
    fn loading_decodes_each_cell_tag_once() {
        let rows = loaded(r#"{"rows": [{"v": {"decimal": "1.50"}}, {"v": {"non_finite": "nan", "text": "NaN"}}], "schema": {"v": {"kind": "decimal"}}}"#);
        assert_eq!(rows.rows[0]["v"], serde_json::json!(1.5));
        assert_eq!(rows.rows[1]["v"], Value::Null);
        assert_eq!(rows.cell_text[0]["v"].text, "1.50");
        assert_eq!(rows.cell_text[1]["v"].text, "NaN");
    }

    #[test]
    fn a_payload_over_a_handle_shares_its_rows() {
        let rows = loaded(r#"{"rows": [{"a": 1}], "schema": {"a": {"kind": "integer"}}}"#);
        let layout = r#"{"resolved": {"id": "c", "chart_type": "table"}, "width": 10.0, "height": 10.0}"#;
        let datasets = HashMap::from([("q".to_string(), rows.clone())]);
        let payload = Payload::from_layout(layout, &rows, &datasets).expect("layout parses");
        assert!(payload.rows.ptr_eq(&rows.rows));
        assert!(Arc::ptr_eq(&payload.cell_text, &rows.cell_text));
        assert!(payload.datasets.as_ref().unwrap()["q"].ptr_eq(&rows.rows));
        assert_eq!(payload.schema.datasets["q"], rows.schema);
    }

    #[test]
    fn a_layout_that_carries_rows_is_refused() {
        let rows = loaded(r#"{"rows": [], "schema": {}}"#);
        let layout = r#"{"resolved": {}, "rows": [], "width": 1.0, "height": 1.0}"#;
        let err = Payload::from_layout(layout, &rows, &HashMap::new()).err().expect("refused");
        assert!(err.contains("carries \"rows\""), "{err}");
    }
}
