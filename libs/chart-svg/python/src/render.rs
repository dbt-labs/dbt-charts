//! `_engine.render_board`: a compiled board drawn over its run's query results, the one entry the
//! equivalence chart-side runs through `engine::host` too.

use crate::compile::CompiledBoard;
use crate::{coded_error, json_to_py};
use engine::host;
use measure::shaped::FontBook;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::{json, Value};

/// The shaped font book of a fonts directory, loaded once by the host and passed to every
/// `render_board`: the engine keeps no process-wide font state of its own.
#[pyclass(frozen, module = "dbt_charts._engine")]
pub struct ShapedFonts(FontBook);

/// `load_shaped_fonts(fonts_dir)`: reads the served faces of the directory. The GIL is released while loading.
#[pyfunction]
fn load_shaped_fonts(py: Python<'_>, fonts_dir: &str) -> PyResult<ShapedFonts> {
    py.detach(|| FontBook::load_dir(std::path::Path::new(fonts_dir)))
        .map(ShapedFonts)
        .map_err(PyValueError::new_err)
}

/// A drawn board: its SVG, each chart's own `<svg>` (keyed as `boxes` is; a chart drawn inline has none), the report's diagnostics (`code`, `params`, `chart`), each placed chart's
/// cell (`id`, then `id#2` for a second placement) and the board's sized extent.
#[pyclass(frozen, module = "dbt_charts._engine")]
pub struct RenderedBoard {
    #[pyo3(get)]
    svg: String,
    charts: Value,
    diagnostics: Value,
    boxes: Value,
    #[pyo3(get)]
    width: f64,
    #[pyo3(get)]
    height: f64,
}

#[pymethods]
impl RenderedBoard {
    #[getter]
    fn charts<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.charts)
    }

    #[getter]
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.diagnostics)
    }

    #[getter]
    fn boxes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.boxes)
    }
}

/// `render_board(board, datasets, chart_errors, variables, overridden, fonts, rendered_at)`:
/// `datasets` is the JSON `{query id: {rows, schema}}`, `chart_errors` `{chart id: execute error}`,
/// `variables` the values `plan` returned and `overridden` the names the caller set; the formats
/// are `engine::host::run_result`'s. `rendered_at` is an RFC 3339 instant. The page's stylesheet is
/// empty: the host draws the chrome. A refusal raises `RenderDiagnosticError`. The GIL is released
/// while rendering.
#[pyfunction]
#[pyo3(signature = (board, datasets, chart_errors, variables, overridden, fonts, rendered_at))]
fn render_board(
    py: Python<'_>,
    board: &Bound<'_, CompiledBoard>,
    datasets: &str,
    chart_errors: &str,
    variables: &str,
    overridden: Vec<String>,
    fonts: &Bound<'_, ShapedFonts>,
    rendered_at: &str,
) -> PyResult<RenderedBoard> {
    let run = host::run_result(datasets, chart_errors, variables, overridden, rendered_at)
        .map_err(PyValueError::new_err)?;
    let compiled = &board.get().0;
    let book = &fonts.get().0;
    let rendered = py
        .detach(|| {
            let (board, styles, resolved, nested) = host::prepare(compiled)?;
            let mut facts = host::Facts::of(&board, styles, &resolved, nested, String::new());
            let styles = std::mem::take(&mut facts.styles);
            engine::render_board(&board, &styles, &run, book, &mut facts)
        })
        .map_err(|d| coded_error(py, &d))?;
    let diagnostics = rendered
        .report
        .diagnostics
        .iter()
        .map(|d| json!({"code": d.code, "params": d.params, "chart": d.params.get("chart_id")}))
        .collect();
    let mut placed = std::collections::BTreeMap::<String, usize>::new();
    let mut boxes = serde_json::Map::new();
    for (_, chart, r) in &rendered.report.boxes {
        let n = placed.entry(chart.as_str().to_string()).or_default();
        *n += 1;
        let key = if *n == 1 {
            chart.as_str().to_string()
        } else {
            format!("{chart}#{n}")
        };
        boxes.insert(
            key,
            json!({"x": r.x, "y": r.y, "width": r.width, "height": r.height}),
        );
    }
    Ok(RenderedBoard {
        charts: Value::Object(rendered.charts.into_iter().map(|(key, svg)| (key, Value::String(svg))).collect()),
        svg: rendered.svg,
        diagnostics: Value::Array(diagnostics),
        boxes: Value::Object(boxes),
        width: rendered.report.width,
        height: rendered.report.height,
    })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RenderedBoard>()?;
    m.add_class::<ShapedFonts>()?;
    m.add_function(wrap_pyfunction!(load_shaped_fonts, m)?)?;
    m.add_function(wrap_pyfunction!(render_board, m)?)
}
