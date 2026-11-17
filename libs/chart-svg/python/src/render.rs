//! `_engine.render_board`: a compiled board drawn over its run's query results, the one entry the
//! equivalence chart-side runs through `engine::host` too.

use crate::compile::CompiledBoard;
use crate::{coded_error, json_to_py, Rows};
use column_types::Dataset;
use indexmap::IndexMap;
use std::collections::HashMap;
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

/// `render_board(board, datasets, chart_errors, empty_filters, automatic_links, variables, presets, overridden, fonts, rendered_at, link_context)`:
/// `datasets` is the JSON `{plan key: {rows, schema}}`, `chart_errors` `{chart id: execute error}`, `automatic_links` `{chart id: link template}`,
/// `variables` the values `plan` returned, `presets` its `variables.presets` and `overridden` the names the caller set; the formats
/// are `engine::host::run_result`'s. `rendered_at` is an RFC 3339 instant; `link_context` the JSON `{root, current_board_slug, origin}` of a host that serves boards. The page's stylesheet is
/// empty: the host draws the chrome. A refusal raises `RenderDiagnosticError`. The GIL is released
/// while rendering.
#[pyfunction]
#[pyo3(signature = (board, datasets, chart_errors, empty_filters, automatic_links, variables, presets, overridden, fonts, rendered_at, link_context))]
fn render_board(
    py: Python<'_>,
    board: &Bound<'_, CompiledBoard>,
    datasets: &str,
    chart_errors: &str,
    empty_filters: &str,
    automatic_links: &str,
    variables: &str,
    presets: &str,
    overridden: Vec<String>,
    fonts: &Bound<'_, ShapedFonts>,
    rendered_at: &str,
    link_context: Option<&str>,
) -> PyResult<RenderedBoard> {
    let compiled = &board.get().0;
    let book = &fonts.get().0;
    let (board, styles, resolved, nested) = py.detach(|| host::prepare(compiled)).map_err(|d| coded_error(py, &d))?;
    let run = host::run_result(compiled, &board, datasets, chart_errors, empty_filters, automatic_links, variables, presets, overridden, rendered_at, link_context)
        .map_err(PyValueError::new_err)?;
    let rendered = py
        .detach(|| {
            host::check_presets(&board, &run)?;
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

/// One chart drawn in a cell: its own `<svg>`, that `<svg>`'s height and the chart's diagnostics
/// (`code`, `params`, `chart`).
#[pyclass(frozen, module = "dbt_charts._engine")]
pub struct RenderedCell {
    #[pyo3(get)]
    svg: String,
    #[pyo3(get)]
    height: f64,
    diagnostics: Value,
}

#[pymethods]
impl RenderedCell {
    #[getter]
    fn diagnostics<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        json_to_py(py, &self.diagnostics)
    }
}

/// `PreparedBoard(board, datasets, chart_errors, variables, presets, overridden, fonts, rendered_at,
/// link_context)`: one render's board and run, prepared once for every chart the host draws in a cell.
/// The arguments are `render_board`'s, less `empty_filters` and `automatic_links`, which each draw carries for its own chart,
/// except `datasets`, which is `{plan key: Rows}`: the results `load_rows` parsed: their rows are shared, not copied.
/// A refusal of the board raises `RenderDiagnosticError`; a wire text that is not `render_board`'s, a
/// `ValueError`. The GIL is released while preparing.
#[pyclass(frozen, module = "dbt_charts._engine")]
pub struct PreparedBoard {
    board: std::sync::Mutex<host::CellBoard>,
    fonts: Py<ShapedFonts>,
}

#[pymethods]
impl PreparedBoard {
    #[new]
    #[pyo3(signature = (board, datasets, chart_errors, variables, presets, overridden, fonts, rendered_at, link_context, geo))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        board: &Bound<'_, CompiledBoard>,
        datasets: HashMap<String, Bound<'_, Rows>>,
        chart_errors: &str,
        variables: &str,
        presets: &str,
        overridden: Vec<String>,
        fonts: Py<ShapedFonts>,
        rendered_at: &str,
        link_context: Option<&str>,
        geo: &str,
    ) -> PyResult<Self> {
        let (compiled, derived) = (&board.get().0, &board.get().1);
        let datasets: IndexMap<String, Dataset> = datasets
            .into_iter()
            .map(|(key, rows)| {
                let loaded = &rows.get().0;
                (key, Dataset { rows: loaded.rows.clone(), schema: loaded.schema.clone(), cell_text: loaded.cell_text.as_ref().clone() })
            })
            .collect();
        // `Ok` is the engine's diagnostic, `Err` a wire text that is the host's bug.
        let prepared = py.detach(|| -> Result<host::CellBoard, Result<diagnostic::Diagnostic, String>> {
            let (board, styles) = (derived.board(compiled).map_err(Ok)?, derived.styles(compiled).map_err(Ok)?);
            let board = host::CellBoard::new(compiled, board, styles, datasets, chart_errors, variables, presets, overridden, rendered_at, link_context, geo)
                .map_err(Err)?;
            board.check_presets().map_err(Ok)?;
            Ok(board)
        });
        let board = prepared.map_err(|e| match e {
            Ok(d) => coded_error(py, &d),
            Err(text) => PyValueError::new_err(text),
        })?;
        Ok(Self { board: std::sync::Mutex::new(board), fonts })
    }

    /// `draw(placement, empty_filters, automatic_link, width, height, inset, title_shift, images)`: the chart placed as
    /// `placement` (`id`, then `id#2`, as `RenderedBoard.boxes` is keyed) drawn in the host's cell, as
    /// `render_native` takes it: `width` and `height` inside the padding `inset` (left, right, top,
    /// bottom) the slot was shrunk by, `height` `None` for the chart's own, under the cols-row
    /// `title_shift` (`title_dy`, `body_dy`). `empty_filters` are the `(label, text)`
    /// filters the chart names when it has nothing to show, `automatic_link` the link the host synthesized for it, and `images` the `data:` URI of each project
    /// image its text writes, by that reference. A refusal raises `RenderDiagnosticError`;
    /// no other part of the board is laid out. The GIL is released while drawing.
    #[pyo3(signature = (placement, empty_filters, automatic_link, width, height, inset, title_shift, images))]
    fn draw(
        &self,
        py: Python<'_>,
        placement: &str,
        empty_filters: Vec<(String, String)>,
        automatic_link: Option<String>,
        width: f64,
        height: Option<f64>,
        inset: (f64, f64, f64, f64),
        title_shift: (f64, f64),
        images: host::Images,
    ) -> PyResult<RenderedCell> {
        let book = &self.fonts.get().0;
        let (title_dy, body_dy) = title_shift;
        let cell = host::Cell { width, height, inset, title_shift: host::TitleShift { title_dy, body_dy } };
        let drawn = py.detach(|| {
            let mut board = self.board.lock().map_err(|_| "a draw of this board panicked".to_string())?;
            board.draw(book, placement, empty_filters, automatic_link, cell, &images)
        });
        let drawn = drawn.map_err(PyValueError::new_err)?.map_err(|d| coded_error(py, &d))?;
        let diagnostics = drawn
            .diagnostics
            .iter()
            .map(|d| json!({"code": d.code, "params": d.params, "chart": d.params.get("chart_id")}))
            .collect();
        Ok(RenderedCell { svg: drawn.svg, height: drawn.height, diagnostics: Value::Array(diagnostics) })
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RenderedBoard>()?;
    m.add_class::<RenderedCell>()?;
    m.add_class::<PreparedBoard>()?;
    m.add_class::<ShapedFonts>()?;
    m.add_function(wrap_pyfunction!(load_shaped_fonts, m)?)?;
    m.add_function(wrap_pyfunction!(render_board, m)?)
}
