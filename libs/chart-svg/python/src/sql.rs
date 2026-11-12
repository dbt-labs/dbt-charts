//! The Python host's SQL analyzer behind `SqlAnalysis` (D-18): one callback per compile,
//! `analyze_all(requests: list[dict]) -> list[dict]`, in the wire form of `board_model::sql_wire`.

use board_model::sql_analysis::{SqlAnalysis, SqlOutcome, SqlRequest};
use board_model::sql_wire;
use crate::escape::Escaped;
use pyo3::prelude::*;

pub struct PySqlAnalysis<'a>(pub Py<PyAny>, pub &'a Escaped);

impl PySqlAnalysis<'_> {
    fn ask(&self, requests: &[SqlRequest]) -> Result<Vec<SqlOutcome>, String> {
        let wire =
            serde_json::Value::Array(requests.iter().map(sql_wire::request).collect()).to_string();
        let answered: String = Python::attach(|py| {
            let json = py.import("json")?;
            let asked = json.call_method1("loads", (wire,))?;
            let answers = self.0.call_method1(py, "analyze_all", (asked,))?;
            json.call_method1("dumps", (answers,))?.extract()
        })
        .map_err(|e| format!("the SQL analyzer `analyze_all` failed: {}", Python::attach(|py| self.1.reason(py, e))))?;
        let outcomes: Vec<serde_json::Value> =
            serde_json::from_str(&answered).map_err(|e| e.to_string())?;
        if outcomes.len() != requests.len() {
            return Err(format!(
                "the SQL analyzer answered {} of {} requests",
                outcomes.len(),
                requests.len()
            ));
        }
        outcomes
            .iter()
            .map(|v| sql_wire::outcome(v).map_err(|e| format!("the SQL analyzer's outcome: {e}")))
            .collect()
    }
}

/// A host failure refuses every request, naming it.
impl SqlAnalysis for PySqlAnalysis<'_> {
    fn analyze(&self, request: &SqlRequest) -> SqlOutcome {
        let [outcome] = <[SqlOutcome; 1]>::try_from(self.analyze_all(std::slice::from_ref(request)))
            .unwrap_or_else(|_| [SqlOutcome::Refused("the SQL analyzer answered nothing".to_string())]);
        outcome
    }

    fn analyze_all(&self, requests: &[SqlRequest]) -> Vec<SqlOutcome> {
        self.ask(requests).unwrap_or_else(|why| {
            requests
                .iter()
                .map(|_| SqlOutcome::Refused(why.clone()))
                .collect()
        })
    }
}
