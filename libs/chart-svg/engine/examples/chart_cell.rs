//! The engine side of chart mode's cell lane (`equivalence_sweep.py::engine_cell_side`): one chart
//! drawn in the cell production's own render gave it, through `engine::host::render_chart_in_cell`,
//! the entry production's host runs, so a chart-mode case proves what production would run.
//!
//! Run as `chart_cell <datasets.json> <project.json> <placement> <cell.json>`: the sidecar and project
//! `chart_side` takes, the placement (`id`, `id#2`), and the cell as production's
//! `engine_cell.cell_args` builds it from the placement's `render_svg_family` call
//! (`{"width", "height", "inset", "title_shift", "empty_filters", "automatic_link", "images"}`, `height` null for the family's
//! own). One JSON object goes to stdout:
//!
//! - `{"svg", "height", "diagnostics"}`: the chart's own `<svg>`, the height it drew and its
//!   diagnostics (`code`, `params`, `chart`).
//! - `{"refused"}`: why the engine does not draw the chart (a refusal it makes by name, or the
//!   recording lacking what it asked for).
//! - `{"uncompiled"}`: the board did not compile in the project it was given.
//!
//! Anything else (a panic, an engine error that is not a refusal) is a bug: the tool exits non-zero.
//!
//! Build with `just chart_svg_dev chart-side-build`, which links it where the sweep looks
//! (`target/release/chart_cell`).

#[path = "../tests/it/inputs.rs"]
mod inputs;
#[path = "../tests/it/recorded.rs"]
mod recorded;

use engine::host::{Cell, TitleShift};
use indexmap::IndexMap;
use recorded::{draw, Project, Sidecar, NEEDS_DATA, NO_RECORDING};
use serde::Deserialize;
use serde_json::{json, Value};

/// `engine_cell.CellArgs` as JSON.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CellArgs {
    empty_filters: Vec<(String, String)>,
    automatic_link: Option<String>,
    width: f64,
    height: Option<f64>,
    inset: (f64, f64, f64, f64),
    title_shift: (f64, f64),
    images: engine::host::Images,
}

/// The answer for `placement` drawn in `args`' cell over the results in the sidecar `raw`, or the bug
/// the render hit.
fn answer(raw: &str, project: &Project, placement: &str, args: CellArgs) -> Result<Value, String> {
    let (title_dy, body_dy) = args.title_shift;
    let cell = Cell { width: args.width, height: args.height, inset: args.inset, title_shift: TitleShift { title_dy, body_dy } };
    let images = args.images;
    let drawn = draw(
        "board",
        project,
        || {
            let sidecar = Sidecar::read_unchecked(raw)?;
            sidecar.page_stylesheet()?;
            Ok(Some(sidecar))
        },
        |board, styles, run, _| {
            let id = placement.split_once('#').map_or(placement, |(id, _)| id);
            let mut run = run.clone();
            run.empty_filters = IndexMap::from([(id.to_string(), args.empty_filters)]);
            run.automatic_links = args.automatic_link.into_iter().map(|link| (id.to_string(), link)).collect();
            engine::host::render_chart_in_cell(board, styles, &run, inputs::book(), placement, cell, &images)
        },
    );
    match drawn {
        Ok(cell) => {
            let diagnostics: Vec<Value> = cell.diagnostics.iter().map(|d| json!({"code": d.code, "params": d.params, "chart": d.params.get("chart_id")})).collect();
            Ok(json!({"svg": cell.svg, "height": cell.height, "diagnostics": diagnostics}))
        }
        Err((outcome, detail)) => {
            let why = detail.map_or(outcome.clone(), |d| format!("{outcome}: {d}"));
            match outcome.as_str() {
                "compile-failed" => Ok(json!({"uncompiled": why})),
                o if o.starts_with("refused:") || o == NEEDS_DATA || o == NO_RECORDING || o.starts_with("query-error:") => Ok(json!({"refused": why})),
                _ => Err(why),
            }
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [datasets, project, placement, cell] = args.as_slice() else {
        panic!("usage: chart_cell <datasets.json> <project.json> <placement> <cell.json>, not {args:?}");
    };
    let read = |path: &String| std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let project = Project::parse(&read(project)).unwrap_or_else(|e| panic!("{e}"));
    let cell: CellArgs = serde_json::from_str(&read(cell)).unwrap_or_else(|e| panic!("{cell}: {e}"));
    match answer(&read(datasets), &project, placement, cell) {
        Ok(answer) => println!("{answer}"),
        Err(bug) => panic!("{bug}"),
    }
}
