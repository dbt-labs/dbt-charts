//! `_engine.plan`: what a run of a compiled board reads and binds, as the QueryPlan wire JSON
//! (D-35). Python reads it through one hand-written `TypedDict`, never a class from here.

use crate::compile::CompiledBoard;
use crate::coded_error;
use indexmap::IndexMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

/// `plan(board, given, now_epoch, time_zone, all_queries) -> str`: `given` is the JSON object of the variables
/// the caller set; the clock is this call's own (D-38). `all_queries` also plans the SQL queries
/// nothing reads. The GIL is released while planning.
#[pyfunction]
fn plan(py: Python<'_>, board: &Bound<'_, CompiledBoard>, given: &str, now_epoch: i64, time_zone: &str, all_queries: bool) -> PyResult<String> {
    let given: IndexMap<String, Value> =
        serde_json::from_str(given).map_err(|e| PyValueError::new_err(format!("given is not a JSON object: {e}")))?;
    let board = &board.get().0;
    py.detach(|| engine::plan(board, &given, now_epoch, time_zone, all_queries)).map_err(|d| coded_error(py, &d))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(plan, m)?)
}
