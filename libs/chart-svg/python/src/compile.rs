//! `_engine.compile`: a board file compiled in Rust, held behind an opaque handle. Python reads
//! the board's diagnostics and index; it never reads the board itself (D-16).

use crate::json_to_py;
use crate::escape::Escaped;
use crate::sql::PySqlAnalysis;
use board_model::board::HtmlPolicy;
use board_model::cache::{CacheLayer, CacheTtl};
use board_model::sql_analysis::{Refused, SqlAnalysis};
use normalize::{HostInputs, ProjectFiles, ReadError, SourceRegistry};
use project::ProjectPath;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::Value;
use std::collections::BTreeMap;

pyo3::create_exception!(
    dbt_charts._engine,
    CompileError,
    PyValueError,
    "A board did not compile; `.diagnostics` holds its coded findings, never empty."
);

/// The host's project reader: `read(path) -> str | None` (`None` only for a file that is not
/// there), `list(dir) -> list[str]` and `exists(path) -> bool`. Each call reacquires the GIL the
/// compile released. An exception the host raises is the refusal `ERR-PROJECT-FILE-UNREADABLE`,
/// naming the file and the exception.
struct HostFiles<'a>(Py<PyAny>, &'a Escaped);

impl HostFiles<'_> {
    fn call<T: for<'py> FromPyObject<'py, 'py>>(
        &self,
        method: &str,
        arg: &str,
    ) -> Result<T, ReadError>
    where
        for<'py> <T as FromPyObject<'py, 'py>>::Error: Into<PyErr>,
    {
        Python::attach(|py| {
            self.0
                .call_method1(py, method, (arg,))
                .and_then(|v| v.bind(py).extract::<T>().map_err(Into::into))
                .map_err(|e| ReadError::new(arg, self.1.reason(py, e)))
        })
    }
}

impl ProjectFiles for HostFiles<'_> {
    fn read(&self, path: &ProjectPath) -> Result<Option<String>, ReadError> {
        self.call("read", path.as_str())
    }
    fn list(&self, dir: &str) -> Result<Vec<ProjectPath>, ReadError> {
        let names: Vec<String> = self.call("list", dir)?;
        names
            .into_iter()
            .map(|n| ProjectPath::try_from(n).map_err(|e| ReadError::new(dir, e.to_string())))
            .collect()
    }
    fn exists(&self, path: &ProjectPath) -> Result<bool, ReadError> {
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
    fn host_facts_json(&self, py: Python<'_>) -> PyResult<String> {
        let facts = self.0.structure.host_facts().map_err(|d| crate::coded_error(py, &d))?;
        Ok(serde_json::to_string(&facts).expect("host facts are plain data"))
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
    let public_ids: serde_json::Map<String, serde_json::Value> = index
        .public_ids
        .iter()
        .map(|(k, v)| (k.to_string(), v.as_str().into()))
        .collect();
    let queries: Vec<_> = facts
        .queries
        .iter()
        .map(|q| serde_json::json!({ "name": q.name, "sql": q.sql }))
        .collect();
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

/// A refusal as the Python exception: its diagnostics and the warnings gathered before it, both
/// in the wire shape.
fn refusal(
    py: Python<'_>,
    error: &normalize::CompileError,
    (diagnostics, warnings): (Vec<Value>, Vec<Value>),
) -> PyErr {
    let build = || -> PyResult<PyErr> {
        let err = CompileError::new_err(error.to_string());
        err.value(py)
            .setattr("diagnostics", json_to_py(py, &diagnostics)?)?;
        err.value(py)
            .setattr("warnings", json_to_py(py, &warnings)?)?;
        Ok(err)
    };
    build().unwrap_or_else(|failure| failure)
}

/// A cache layer as the host sends it: `(enabled, ttl, prefetch)`, each `None` when the scope
/// does not author it. The host validated the ttl, so it is not read again.
type CacheWire = (Option<bool>, Option<String>, Option<bool>);

fn cache_layer((enabled, ttl, prefetch): CacheWire) -> CacheLayer {
    CacheLayer {
        enabled,
        ttl: ttl.map(|t| match t.as_str() {
            "forever" => CacheTtl::Forever,
            _ => CacheTtl::Duration(t),
        }),
        prefetch,
        ..CacheLayer::default()
    }
}

/// What a compile is of: the board at a project path, or board text with no project location.
enum Board<'a> {
    At(ProjectPath),
    Text(&'a str),
}

/// `compile(files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources, implicit_source, cache_root, source_caches, sql, named, text)`.
///
/// `path` is `None` for a board with no project location, whose `text` is then required (and
/// refused beside a path): it inherits no `meta.yml` and resolves no relative reference.
///
/// `cache_root` is the project's `cache:` layer and `source_caches` each declared source's; both
/// are required, since a compile with no root has no cache policy to resolve.
///
/// `sources` maps each project source name to its declaration as JSON text.
///
/// `today` is `YYYY-MM-DD`. `sql` is the host's analyzer (`analyze_all(requests) -> outcomes`);
/// without one a SQL query is refused. The GIL is released while compiling.
#[pyfunction]
#[pyo3(signature = (files, path, *, boards_root, today, default_theme, html_policy_ceiling, sources, implicit_source, cache_root, source_caches, sql=None, named=false, text=None))]
#[allow(clippy::too_many_arguments)]
fn compile(
    py: Python<'_>,
    files: Py<PyAny>,
    path: Option<&str>,
    boards_root: &str,
    today: &str,
    default_theme: &str,
    html_policy_ceiling: Option<&str>,
    sources: BTreeMap<String, String>,
    implicit_source: Option<String>,
    cache_root: CacheWire,
    source_caches: BTreeMap<String, CacheWire>,
    sql: Option<Py<PyAny>>,
    named: bool,
    text: Option<&str>,
) -> PyResult<CompiledBoard> {
    let board = match (path, text) {
        (Some(_), Some(_)) => {
            return Err(PyValueError::new_err("text is the board of a compile with no path; the board at a path is read from files"))
        }
        (None, None) => return Err(PyValueError::new_err("a compile with no path needs the board's text")),
        (Some(path), None) => Board::At(ProjectPath::try_from(path).map_err(|e| PyValueError::new_err(e.to_string()))?),
        (None, Some(text)) => Board::Text(text),
    };
    let today = migrate::parse_date(today)
        .ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
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
    let cache_root = cache_layer(cache_root);
    let source_caches: BTreeMap<String, CacheLayer> = source_caches
        .into_iter()
        .map(|(name, layer)| (name, cache_layer(layer)))
        .collect();
    let escaped = Escaped::default();
    let files = HostFiles(files, &escaped);
    let analyzer = sql.map(|a| PySqlAnalysis(a, &escaped));
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
            cache_root: &cache_root,
            source_caches: &source_caches,
            named,
        };
        match &board {
            Board::At(path) => normalize::compile_or_refuse(&files, path, &host),
            Board::Text(text) => normalize::compile_text_or_refuse(&files, text, &host),
        }
    });
    if let Some(interrupt) = escaped.take() {
        return Err(interrupt);
    }
    compiled.map(CompiledBoard).map_err(|r| {
        let wire = match &board {
            Board::At(path) => Ok((
                r.error.wire_diagnostics(&files, path, today),
                normalize::wire_located(r.warnings.clone(), &files, path, today),
            )),
            Board::Text(text) => normalize::pathless_wire_refusal(&r, text, today),
        };
        match wire {
            Ok(wire) => refusal(py, &r.error, wire),
            Err(e) => PyValueError::new_err(e.to_string()),
        }
    })
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
    let today = migrate::parse_date(today)
        .ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
    // The index reads only which names the project declares, never their declarations.
    let sources = SourceRegistry::declared(
        sources
            .into_iter()
            .map(|name| (name, serde_json::Value::Null)),
    )
    .with_implicit(implicit_source);
    let escaped = Escaped::default();
    let files = HostFiles(files, &escaped);
    // An index resolves no query's cache policy, so these layers are never read.
    let (no_cache, no_source_caches) = (CacheLayer::default(), BTreeMap::new());
    let indexed = py.detach(|| {
        let host = HostInputs {
            boards_root,
            today,
            default_theme: "",
            html_policy_ceiling: None,
            sql: &Refused,
            sources: &sources,
            cache_root: &no_cache,
            source_caches: &no_source_caches,
            named: false,
        };
        normalize::compile_index(&files, &path, &host)
    });
    if let Some(interrupt) = escaped.take() {
        return Err(interrupt);
    }
    indexed
        .map(|(index, read)| index_text(&index, read.iter()))
        .map_err(|e| {
            let diagnostics = e.wire_diagnostics(&files, &path, today);
            refusal(py, &e, (diagnostics, Vec::new()))
        })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(index, m)?)?;
    m.add_class::<CompiledBoard>()?;
    m.add_function(wrap_pyfunction!(compile, m)?)?;
    m.add("CompileError", m.py().get_type::<CompileError>())?;
    Ok(())
}
