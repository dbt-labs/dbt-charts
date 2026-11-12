//! `_engine.plan`: what a run of a compiled board reads and binds, as the QueryPlan wire JSON
//! (D-35). Python reads it through one hand-written `TypedDict`, never a class from here.

use crate::compile::CompiledBoard;
use crate::coded_error;
use indexmap::IndexMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;

/// `plan(board, given, builtins, now_epoch, all_queries, active_tab_only, strict, only_charts) -> str`: `given`
/// and `builtins` are JSON objects (the variables the caller set; host values echoed undeclared).
/// The clock is this call's own, in the board's own time zone (D-38). `all_queries` also plans the
/// SQL queries nothing reads; `active_tab_only` plans only the open tabs; the two do not combine.
/// `strict` refuses a given name the board does not declare, else ignores it. `only_charts` (`None`
/// for the whole board) focuses the plan on the named charts. The GIL is released while planning; the
/// plan reads only the compiled board, so no host callback re-attaches it.
#[pyfunction]
#[pyo3(signature = (board, given, builtins, now_epoch, all_queries, active_tab_only, strict, only_charts))]
#[allow(clippy::too_many_arguments)]
fn plan(
    py: Python<'_>,
    board: &Bound<'_, CompiledBoard>,
    given: &str,
    builtins: &str,
    now_epoch: i64,
    all_queries: bool,
    active_tab_only: bool,
    strict: bool,
    only_charts: Option<Vec<String>>,
) -> PyResult<String> {
    let object = |name: &str, text: &str| -> PyResult<IndexMap<String, Value>> {
        serde_json::from_str(text).map_err(|e| PyValueError::new_err(format!("{name} is not a JSON object: {e}")))
    };
    let given = object("given", given)?;
    let builtins = object("builtins", builtins)?;
    let board = &board.get().0;
    py.detach(|| engine::plan(board, &given, &builtins, now_epoch, all_queries, active_tab_only, strict, only_charts.as_deref()))
        .map_err(|d| coded_error(py, &d))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(plan, m)?)
}
