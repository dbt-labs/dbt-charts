//! `_engine.compile`: a board file compiled in Rust, held behind an opaque handle. Python reads
//! the board's diagnostics and index; it never reads the board itself (D-16).

use crate::json_to_py;
use crate::sql::PySqlAnalysis;
use board_model::board::HtmlPolicy;
use board_model::sql_analysis::{Refused, SqlAnalysis};
use std::collections::BTreeMap;
use normalize::{HostInputs, ProjectFiles, SourceRegistry};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use project::ProjectPath;

pyo3::create_exception!(
    dbt_charts._engine,
    CompileError,
    PyValueError,
    "A board did not compile; `.diagnostics` holds its coded findings, never empty."
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
pub struct CompiledBoard(pub(crate) normalize::CompiledBoard);

#[pymethods]
impl CompiledBoard {
    /// The non-fatal findings, as wire dicts: `code`, `params`, `level`, and when known `path`,
    /// `range` (`file`, `start_line`, `end_line`, `columns`), `hint` and `related`. A
    /// `source_needle` rides in `params`.
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.0.structure.wire_diagnostics())
    }

    /// What validate's host checks read (queries, column refs, unbound charts, links, suppressed
    /// warnings), as versioned JSON text.
    fn host_facts_json(&self) -> String {
        serde_json::to_string(&self.0.structure.host_facts())
            .expect("host facts are plain data")
    }

    /// The board's index as versioned JSON text (see [`index_text`]).
    fn index_json(&self) -> String {
        index_text(&self.0.structure.index, self.0.structure.files.iter())
    }
}

/// An index as versioned JSON text: the public chart ids, the charts declared below the root, what
/// search and listings read of the root board, and the files read to get there.
fn index_text<'a>(
    index: &normalize::BoardIndex,
    files: impl Iterator<Item = &'a ProjectPath>,
) -> String {
    let facts = &index.facts;
    let public_ids: serde_json::Map<String, serde_json::Value> =
        index.public_ids.iter().map(|(k, v)| (k.to_string(), v.as_str().into())).collect();
    let queries: Vec<_> = facts.queries.iter().map(|q| serde_json::json!({ "name": q.name, "sql": q.sql })).collect();
    let charts: Vec<_> = facts
        .charts
        .iter()
        .map(|c| serde_json::json!({ "id": c.id, "title": c.title, "query": c.query }))
        .collect();
    serde_json::json!({
        "version": normalize::INDEX_VERSION,
        "public_ids": public_ids,
        "declared_below": index.declared_below,
        "title": facts.title,
        "notes": facts.notes,
        "tags": facts.tags,
        "queries": queries,
        "charts": charts,
        "files": files.map(|f| f.as_str()).collect::<Vec<_>>(),
    })
    .to_string()
}

fn refusal(
    py: Python<'_>,
    error: &normalize::CompileError,
    warnings: &[diagnostic::Diagnostic],
    files: &HostFiles,
    path: &ProjectPath,
    today: migrate::Date,
) -> PyErr {
    let diagnostics = error.wire_diagnostics(files, path, today);
    let warnings = normalize::wire_located(warnings.to_vec(), files, path, today);
    let build = || -> PyResult<PyErr> {
        let err = CompileError::new_err(error.to_string());
        err.value(py).setattr("diagnostics", json_to_py(py, &diagnostics)?)?;
        err.value(py).setattr("warnings", json_to_py(py, &warnings)?)?;
        Ok(err)
    };
    build().unwrap_or_else(|failure| failure)
}

/// `compile(files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources, sql, named)`.
///
/// `sources` maps each project source name to its declaration as JSON text.
///
/// `today` is `YYYY-MM-DD`. `sql` is the host's analyzer (`analyze_all(requests) -> outcomes`);
/// without one a SQL query is refused. The GIL is released while compiling.
#[pyfunction]
#[pyo3(signature = (files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources, implicit_source, sql=None, named=false))]
#[allow(clippy::too_many_arguments)]
fn compile(
    py: Python<'_>,
    files: Py<PyAny>,
    path: &str,
    boards_root: &str,
    today: &str,
    default_theme: &str,
    html_policy_ceiling: Option<&str>,
    sources: BTreeMap<String, String>,
    implicit_source: Option<String>,
    sql: Option<Py<PyAny>>,
    named: bool,
) -> PyResult<CompiledBoard> {
    let path = ProjectPath::try_from(path).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let today = migrate::parse_date(today).ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
    let html_policy_ceiling = html_policy_ceiling
        .map(|p| HtmlPolicy::try_from(p).map_err(PyValueError::new_err))
        .transpose()?;
    let sources = SourceRegistry::declared(
        sources
            .into_iter()
            .map(|(name, text)| {
                serde_json::from_str(&text)
                    .map(|declaration| (name.clone(), declaration))
                    .map_err(|e| PyValueError::new_err(format!("source {name}: {e}")))
            })
            .collect::<PyResult<Vec<_>>>()?,
    )
    .with_implicit(implicit_source);
    let files = HostFiles(files);
    let analyzer = sql.map(PySqlAnalysis);
    let sql: &(dyn SqlAnalysis + Sync) = match &analyzer {
        Some(a) => a,
        None => &Refused,
    };
    let compiled = py.detach(|| {
        let host = HostInputs {
            boards_root,
            today,
            default_theme,
            html_policy_ceiling,
            sql,
            sources: &sources,
            named,
        };
        normalize::compile_or_refuse(&files, &path, &host)
    });
    compiled
        .map(CompiledBoard)
        .map_err(|r| refusal(py, &r.error, &r.warnings, &files, &path, today))
}

/// `index(files, path, *, boards_root, today, sources, implicit_source)`: the board's index as
/// JSON text, from the fold alone: no SQL analysis, no style, none of the lints. A board whose SQL
/// does not parse is indexed; `compile` is what refuses it. The GIL is released while folding.
#[pyfunction]
#[pyo3(signature = (files, path, *, boards_root, today, sources, implicit_source))]
fn index(
    py: Python<'_>,
    files: Py<PyAny>,
    path: &str,
    boards_root: &str,
    today: &str,
    sources: Vec<String>,
    implicit_source: Option<String>,
) -> PyResult<String> {
    let path = ProjectPath::try_from(path).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let today = migrate::parse_date(today).ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
    // The index reads only which names the project declares, never their declarations.
    let sources = SourceRegistry::declared(sources.into_iter().map(|name| (name, serde_json::Value::Null)))
        .with_implicit(implicit_source);
    let files = HostFiles(files);
    let indexed = py.detach(|| {
        let host = HostInputs {
            boards_root,
            today,
            default_theme: "",
            html_policy_ceiling: None,
            sql: &Refused,
            sources: &sources,
            named: false,
        };
        normalize::compile_index(&files, &path, &host)
    });
    indexed
        .map(|(index, read)| index_text(&index, read.iter()))
        .map_err(|e| refusal(py, &e, &[], &files, &path, today))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(index, m)?)?;
    m.add_class::<CompiledBoard>()?;
    m.add_function(wrap_pyfunction!(compile, m)?)?;
    m.add("CompileError", m.py().get_type::<CompileError>())?;
    Ok(())
}
