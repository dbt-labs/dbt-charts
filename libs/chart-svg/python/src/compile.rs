//! `_engine.compile`: a board file compiled in Rust, held behind an opaque handle. Python reads
//! the board's diagnostics and index; it never reads the board itself (D-16).

use crate::json_to_py;
use board_model::board::HtmlPolicy;
use normalize::{HostInputs, ProjectFiles, SourceRegistry};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use project::ProjectPath;

pyo3::create_exception!(
    dbt_charts._engine,
    CompileError,
    PyValueError,
    "A board did not compile; `.diagnostics` holds the coded findings, empty when the refusal has no code."
);

/// The host's project reader: `read(path) -> str | None`, `list(dir) -> list[str]` and
/// `exists(path) -> bool`. Each call reacquires the GIL the compile released.
struct HostFiles(Py<PyAny>);

impl HostFiles {
    fn call<T: for<'py> FromPyObject<'py, 'py>>(&self, method: &str, arg: &str) -> T
    where
        for<'py> <T as FromPyObject<'py, 'py>>::Error: Into<PyErr>,
    {
        Python::attach(|py| {
            self.0
                .call_method1(py, method, (arg,))
                .and_then(|v| v.bind(py).extract::<T>().map_err(Into::into))
                .unwrap_or_else(|e| panic!("project files `{method}({arg:?})` failed: {e}"))
        })
    }
}

impl ProjectFiles for HostFiles {
    fn read(&self, path: &ProjectPath) -> Option<String> {
        self.call("read", path.as_str())
    }
    fn list(&self, dir: &str) -> Vec<ProjectPath> {
        let names: Vec<String> = self.call("list", dir);
        names
            .into_iter()
            .map(|n| ProjectPath::try_from(n).expect("project files `list` returns project paths"))
            .collect()
    }
    fn exists(&self, path: &ProjectPath) -> bool {
        self.call("exists", path.as_str())
    }
}

/// A compiled board. Opaque: it carries no board model to Python.
#[pyclass(frozen, module = "dbt_charts._engine")]
struct CompiledBoard(normalize::CompiledBoard);

#[pymethods]
impl CompiledBoard {
    /// The non-fatal findings, as wire dicts.
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.0.structure.diagnostics)
    }

    /// The public chart ids and the charts declared below the root, as JSON text.
    fn index_json(&self) -> String {
        let index = &self.0.structure.index;
        let public_ids: serde_json::Map<String, serde_json::Value> =
            index.public_ids.iter().map(|(k, v)| (k.to_string(), v.as_str().into())).collect();
        serde_json::json!({ "public_ids": public_ids, "declared_below": index.declared_below }).to_string()
    }
}

fn refusal(py: Python<'_>, error: &normalize::CompileError) -> PyErr {
    let diagnostics = error.diagnostics();
    let build = || -> PyResult<PyErr> {
        let err = CompileError::new_err(error.to_string());
        err.value(py).setattr("diagnostics", json_to_py(py, &diagnostics)?)?;
        Ok(err)
    };
    build().unwrap_or_else(|failure| failure)
}

/// `compile(files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources)`.
///
/// `today` is `YYYY-MM-DD`. SQL queries are refused until the host supplies an analyzer. The GIL
/// is released while compiling.
#[pyfunction]
#[pyo3(signature = (files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources))]
#[allow(clippy::too_many_arguments)]
fn compile(
    py: Python<'_>,
    files: Py<PyAny>,
    path: &str,
    boards_root: &str,
    today: &str,
    default_theme: &str,
    html_policy_ceiling: Option<&str>,
    sources: Vec<String>,
) -> PyResult<CompiledBoard> {
    let path = ProjectPath::try_from(path).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let today = migrate::parse_date(today).ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
    let html_policy_ceiling = html_policy_ceiling
        .map(|p| HtmlPolicy::try_from(p).map_err(PyValueError::new_err))
        .transpose()?;
    let sources = SourceRegistry::declared(sources);
    let files = HostFiles(files);
    let compiled = py.detach(|| {
        let host = HostInputs {
            boards_root,
            today,
            default_theme,
            html_policy_ceiling,
            sql: &board_model::sql_analysis::Refused,
            sources: &sources,
        };
        normalize::compile(&files, &path, &host)
    });
    compiled.map(CompiledBoard).map_err(|e| refusal(py, &e))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<CompiledBoard>()?;
    m.add_function(wrap_pyfunction!(compile, m)?)?;
    m.add("CompileError", m.py().get_type::<CompileError>())?;
    Ok(())
}
