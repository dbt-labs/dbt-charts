//! PyO3 bindings exposing the chart renderer's `render_chart` to Python as
//! `dbt_charts._engine`. Build/distribution notes:
//! `libs/chart-svg/docs/laws-compile.md`'s Wheel section.

use chart_renderer::diagnostic::Diagnostic;
use chart_renderer::families::table::width::{measure_natural_width, MeasurePayload};
use chart_renderer::{render_measured, Payload};
use measure::FontBook;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

static FONT_BOOKS: Mutex<Option<HashMap<String, Arc<FontBook>>>> = Mutex::new(None);
static FONT_LOADS: AtomicUsize = AtomicUsize::new(0);

/// The process-wide `FontBook` for `fonts_dir`, loaded on first use. The lock
/// is held across the load so two threads never load the same directory twice.
fn fonts_for(fonts_dir: Option<&str>) -> Result<Arc<FontBook>, String> {
    let Some(dir) = fonts_dir else {
        return Ok(Arc::new(FontBook::empty()));
    };
    let mut guard = FONT_BOOKS.lock().expect("font cache mutex poisoned");
    let books = guard.get_or_insert_with(HashMap::new);
    if let Some(book) = books.get(dir) {
        return Ok(book.clone());
    }
    let book = Arc::new(FontBook::load_dir(dir)?);
    FONT_LOADS.fetch_add(1, Ordering::SeqCst);
    books.insert(dir.to_string(), book.clone());
    Ok(book)
}

pyo3::create_exception!(
    dbt_charts._engine,
    RenderDiagnosticError,
    PyValueError,
    "A render refused with a registered diagnostic; `.code` and `.params` carry it."
);

/// `json.loads` of a serialized value, so `params` arrive as plain Python data.
fn json_to_py<'py>(py: Python<'py>, value: &impl serde::Serialize) -> PyResult<Bound<'py, PyAny>> {
    let text = serde_json::to_string(value).expect("diagnostics serialize");
    py.import("json")?.call_method1("loads", (text,))
}

/// The `RenderDiagnosticError` for a refusal, `.code` and `.params` set. A
/// failure to build it is the error raised instead: nothing is swallowed.
fn coded_error(py: Python<'_>, diagnostic: &Diagnostic) -> PyErr {
    let build = || -> PyResult<PyErr> {
        let err = RenderDiagnosticError::new_err(diagnostic.to_string());
        let value = err.value(py);
        value.setattr("code", diagnostic.code)?;
        value.setattr("params", json_to_py(py, &diagnostic.params)?)?;
        Ok(err)
    };
    build().unwrap_or_else(|failure| failure)
}

/// Render one chart payload (the `chart-renderer payload.json out.svg` JSON)
/// to `(svg, warnings, natural_height)`, the SVG without the CLI's trailing
/// newline, each warning a `{"code", "params"}` dict, and
/// `RenderOutput.natural_height`. `fonts_dir` is used as given, so it
/// must be absolute or relative to the process cwd. A refusal raises
/// `RenderDiagnosticError` (a `ValueError`) with `.code` and `.params`; an
/// engine gap carries code `ERR-ENGINE-REFUSED`. The GIL is released
/// while rendering.
#[pyfunction]
fn render_chart<'py>(
    py: Python<'py>,
    payload_json: &str,
) -> PyResult<(String, Bound<'py, PyAny>, Option<f64>)> {
    // `Payload` holds a `RefCell` report, so it is built inside the detached
    // closure rather than moved across the GIL release.
    let output = py.detach(|| {
        let payload: Payload = serde_json::from_str(payload_json)
            .map_err(|e| Diagnostic::from(format!("parse payload: {e}")))?;
        let fonts = fonts_for(payload.fonts_dir.as_deref())?;
        render_measured(&payload, &fonts)
    });
    let output = output.map_err(|diagnostic| coded_error(py, &diagnostic))?;
    Ok((output.svg, json_to_py(py, &output.warnings)?, output.natural_height))
}

/// The width a table needs to show every column, columns and outer padding
/// only (`measure_natural_width`): `None` with nothing to hug. The payload is
/// the table's resolved chart, its rows and `fonts_dir`. A table the engine
/// cannot measure raises `RenderDiagnosticError` like `render_chart`.
#[pyfunction]
fn measure_table_natural_width(py: Python<'_>, payload_json: &str) -> PyResult<Option<f64>> {
    let measured = py.detach(|| {
        let payload: MeasurePayload = serde_json::from_str(payload_json)
            .map_err(|e| Diagnostic::from(format!("parse payload: {e}")))?;
        let fonts = fonts_for(payload.fonts_dir.as_deref())?;
        measure_natural_width(&payload, &fonts).map_err(Diagnostic::from)
    });
    measured.map_err(|diagnostic| coded_error(py, &diagnostic))
}

/// What `table_title_geometry` reads: the table's resolved chart, the width its
/// title wraps in, `fonts_dir`, and the board's `language` (its casing rule).
#[derive(serde::Deserialize)]
struct TitleGeometryPayload {
    resolved: serde_json::Value,
    width: f64,
    fonts_dir: Option<String>,
    #[serde(default)]
    language: display_text::language::Language,
}

/// A table's title band as the engine lays it out (`title_geometry`): the
/// first baseline's distance below the card's inset top, and the height from
/// that baseline down to the header. A table the engine cannot lay out raises
/// `RenderDiagnosticError` like `render_chart`.
#[pyfunction]
fn table_title_geometry(py: Python<'_>, payload_json: &str) -> PyResult<(f64, f64)> {
    let geometry = py.detach(|| {
        let payload: TitleGeometryPayload = serde_json::from_str(payload_json)
            .map_err(|e| Diagnostic::from(format!("parse payload: {e}")))?;
        let fonts = fonts_for(payload.fonts_dir.as_deref())?;
        display_text::language::with_language(payload.language, || {
            chart_renderer::families::table::layout::title_geometry(&payload.resolved, payload.width, &fonts)
        })
        .map_err(Diagnostic::from)
    });
    geometry.map_err(|diagnostic| coded_error(py, &diagnostic))
}

/// Parse a custom chart template in the engine that renders it; the error
/// message names the line. Compile runs this so a template the engine cannot
/// parse fails at compile, not at render.
#[pyfunction]
fn check_template(source: &str) -> PyResult<()> {
    chart_renderer::families::custom::check_template(source).map_err(PyValueError::new_err)
}

/// How many distinct `fonts_dir` loads this process has done.
#[pyfunction]
fn font_loads() -> usize {
    FONT_LOADS.load(Ordering::SeqCst)
}

#[pyfunction]
fn languages() -> Vec<&'static str> {
    display_text::language::LANGUAGES.to_vec()
}

/// The language a d3 time locale name implies (`de-DE` -> `de`); `None` (a
/// custom definition) or a locale with no catalog is `en`.
#[pyfunction]
fn language_for_time_locale(name: Option<&str>) -> &'static str {
    display_text::language::language_of_time_locale(name)
}

/// Message `id` in `lang`: `n` picks a plural form and fills `{n}`, `args`
/// fill the other placeholders. An unknown language or id, a missing or unused
/// arg, or a plural message with no `n` raises `ValueError`.
#[pyfunction]
#[pyo3(signature = (lang, id, n=None, args=None))]
fn message(
    lang: &str,
    id: &str,
    n: Option<u64>,
    args: Option<HashMap<String, String>>,
) -> PyResult<String> {
    let args = args.unwrap_or_default();
    let pairs: Vec<(&str, &str)> = args.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    display_text::language::message(lang, id, n, &pairs).map_err(PyValueError::new_err)
}

/// A Vega expression over the numeric expression `x` evaluating to the CLDR
/// plural category `lang` gives that (possibly fractional) count.
#[pyfunction]
fn plural_expr(lang: &str, x: &str) -> PyResult<String> {
    display_text::language::plural_expr(lang, x).map_err(PyValueError::new_err)
}

/// The forms of plural message `id` in `lang`, by CLDR category, in category
/// order so the specs built from them are stable across processes.
#[pyfunction]
fn message_forms(lang: &str, id: &str) -> PyResult<BTreeMap<String, String>> {
    display_text::language::forms(lang, id)
        .cloned()
        .map_err(PyValueError::new_err)
}

/// Letter-case `text` per `case` in `lang`'s title rules (`title` follows the
/// catalog's `case.title_style`).
#[pyfunction]
fn apply_case(lang: &str, text: &str, case: &str) -> PyResult<String> {
    display_text::case::apply_case_in(lang, text, case).map_err(PyValueError::new_err)
}

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_chart, m)?)?;
    m.add_function(wrap_pyfunction!(measure_table_natural_width, m)?)?;
    m.add_function(wrap_pyfunction!(table_title_geometry, m)?)?;
    m.add_function(wrap_pyfunction!(font_loads, m)?)?;
    m.add_function(wrap_pyfunction!(check_template, m)?)?;
    m.add_function(wrap_pyfunction!(languages, m)?)?;
    m.add_function(wrap_pyfunction!(language_for_time_locale, m)?)?;
    m.add_function(wrap_pyfunction!(message, m)?)?;
    m.add_function(wrap_pyfunction!(plural_expr, m)?)?;
    m.add_function(wrap_pyfunction!(message_forms, m)?)?;
    m.add_function(wrap_pyfunction!(apply_case, m)?)?;
    m.add("RenderDiagnosticError", m.py().get_type::<RenderDiagnosticError>())?;
    Ok(())
}
