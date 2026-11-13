//! The engine side of chart mode for an inline family (`equivalence_sweep.py::engine_chart_side`): one
//! chart drawn in the cell production gave it, through `engine::host::render_chart_in_cell`, the entry
//! production's host runs, so a chart-mode case proves what production will run.
//!
//! Run as `chart_cell <datasets.json> <project.json> <placement> <width> <height|-> <left> <right>
//! <top> <bottom>`: the sidecar and project `chart_side` takes, the placement (`id`, `id#2`) and the
//! cell as `render_native` takes it (`-` is the family's own height). One JSON object goes to stdout:
//!
//! - `{"svg", "height", "diagnostics"}`: the chart's own `<svg>` at the cell's width, its height and its
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

use engine::host::Cell;
use recorded::{draw, Project, Sidecar, NEEDS_DATA, NO_RECORDING};
use serde_json::{json, Value};

/// The answer for `placement` drawn in `cell` over the results in the sidecar `raw`, or the bug the
/// render hit.
fn answer(raw: &str, project: &Project, placement: &str, cell: Cell) -> Result<Value, String> {
    let drawn = draw(
        "board",
        project,
        || {
            let sidecar = Sidecar::read_unchecked(raw)?;
            sidecar.page_stylesheet()?;
            Ok(Some(sidecar))
        },
        |board, styles, run, _| engine::host::render_chart_in_cell(board, styles, run, inputs::book(), placement, cell),
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
    let [datasets, project, placement, width, height, left, right, top, bottom] = args.as_slice() else {
        panic!("usage: chart_cell <datasets.json> <project.json> <placement> <width> <height|-> <left> <right> <top> <bottom>, not {args:?}");
    };
    let number = |name: &str, text: &String| text.parse::<f64>().unwrap_or_else(|e| panic!("{name} {text}: {e}"));
    let cell = Cell {
        width: number("width", width),
        height: (height != "-").then(|| number("height", height)),
        inset: (number("left", left), number("right", right), number("top", top), number("bottom", bottom)),
    };
    let read = |path: &String| std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let project = Project::parse(&read(project)).unwrap_or_else(|e| panic!("{e}"));
    match answer(&read(datasets), &project, placement, cell) {
        Ok(answer) => println!("{answer}"),
        Err(bug) => panic!("{bug}"),
    }
}
