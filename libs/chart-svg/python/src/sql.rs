//! The Python host's SQL analyzer behind `SqlAnalysis` (D-18): one callback per compile,
//! `analyze_all(requests: list[dict]) -> list[dict]`, in the wire form of `board_model::sql_wire`.

use board_model::sql_analysis::{SqlAnalysis, SqlOutcome, SqlRequest};
use board_model::sql_wire;
use pyo3::prelude::*;

pub struct PySqlAnalysis(pub Py<PyAny>);

impl SqlAnalysis for PySqlAnalysis {
    fn analyze(&self, request: &SqlRequest) -> SqlOutcome {
        self.analyze_all(std::slice::from_ref(request)).remove(0)
    }

    fn analyze_all(&self, requests: &[SqlRequest]) -> Vec<SqlOutcome> {
        let wire = serde_json::Value::Array(requests.iter().map(sql_wire::request).collect()).to_string();
        let answered: String = Python::attach(|py| {
            let json = py.import("json")?;
            let asked = json.call_method1("loads", (wire,))?;
            let answers = self.0.call_method1(py, "analyze_all", (asked,))?;
            json.call_method1("dumps", (answers,))?.extract()
        })
        .unwrap_or_else(|e| panic!("SQL analyzer `analyze_all` failed: {e}"));
        let outcomes: Vec<serde_json::Value> =
            serde_json::from_str(&answered).expect("json.dumps wrote JSON");
        assert_eq!(outcomes.len(), requests.len(), "SQL analyzer answered a different number of requests");
        outcomes
            .iter()
            .map(|v| sql_wire::outcome(v).unwrap_or_else(|e| panic!("SQL analyzer outcome: {e}")))
            .collect()
    }
}
