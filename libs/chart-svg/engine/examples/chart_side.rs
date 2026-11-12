//! The engine side of the equivalence harness's chart mode (`equivalence_sweep.py::engine_chart_side`).
//!
//! Run as `chart_side <datasets.json> <project.json>`: the sidecar the sweep writes beside a board's
//! recordings (`chart_svg_dev._replay.Recording.write_datasets`), which holds the board's query
//! results, and the project to render over them (`recorded::Project`: the board's path, the
//! project's text files, the board's own with its root width forced as the case has it, and the
//! source names the project declares; the caller pairs them). The board is compiled and rendered whole, as
//! `refusal_tally --with-data` does, and one JSON object goes to stdout:
//!
//! - `{"svg", "charts", "diagnostics", "boxes", "extent"}`: the board's SVG, each chart's own `<svg>`
//!   (`charts`, keyed as `boxes` is: a chart the board draws inline has none), and the report as the comparator reads its meta: diagnostics (`code`, `params`,
//!   `chart`), each placement's sized box keyed `id`, `id#2`, ... and the board's extent.
//! - `{"refused"}`: why the engine does not draw the board: a refusal it makes by name, or the
//!   recording lacking what the engine asked for. A refusal covers every chart of the board.
//! - `{"uncompiled"}`: the board did not compile in the project it was given: a gap of this
//!   harness (or a board production refuses too), never a refusal of the engine.
//!
//! Anything else (a panic, an engine error that is not a refusal) is a bug: the tool exits
//! non-zero with the reason on stderr.
//!
//! The engine renders a board under its default variables, so the sweep runs only those cases here.
//! The stylesheet is left empty: chart mode takes the card chrome from production and splices in
//! only the chart's own `<svg>`.
//!
//! Build with the release binaries: `cargo build --release --manifest-path libs/chart-svg/Cargo.toml
//! -p engine --example chart_side`, then `just chart_svg_dev chart-side-build` links it where the
//! sweep looks (`target/release/chart_side`).

#[path = "../tests/it/inputs.rs"]
mod inputs;
#[path = "../tests/it/recorded.rs"]
mod recorded;

use std::collections::BTreeMap;

use recorded::{render, Project, Sidecar, NEEDS_DATA, NO_RECORDING};
use serde_json::{json, Value};

/// The answer for the board of `project` rendered over the results in the sidecar `raw`, or the bug
/// the render hit.
fn answer(raw: &str, project: &Project) -> Result<Value, String> {
    match render("board", project, || {
        let sidecar = Sidecar::read_unchecked(raw)?;
        sidecar.page_stylesheet()?;
        Ok(Some(sidecar))
    }) {
        Ok(rendered) => {
            let diagnostics: Vec<Value> = rendered
                .report
                .diagnostics
                .iter()
                .map(|d| json!({"code": d.code, "params": d.params, "chart": d.params.get("chart_id")}))
                .collect();
            let mut placed: BTreeMap<String, usize> = BTreeMap::new();
            let mut boxes = serde_json::Map::new();
            for (_, chart, r) in &rendered.report.boxes {
                let n = placed.entry(chart.as_str().to_string()).or_default();
                *n += 1;
                let key = if *n == 1 { chart.as_str().to_string() } else { format!("{chart}#{n}") };
                boxes.insert(key, json!({"x": r.x, "y": r.y, "width": r.width, "height": r.height}));
            }
            let extent = json!({"width": rendered.report.width, "height": rendered.report.height});
            let charts: serde_json::Map<String, Value> = rendered.charts.iter().map(|(key, svg)| (key.clone(), json!(svg))).collect();
            Ok(json!({"svg": rendered.svg, "charts": charts, "diagnostics": diagnostics, "boxes": boxes, "extent": extent}))
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
    let [datasets, project] = args.as_slice() else {
        panic!("usage: chart_side <datasets.json> <project.json>, not {args:?}");
    };
    let read = |path: &String| std::fs::read_to_string(path).unwrap_or_else(|e| panic!("{path}: {e}"));
    let project = Project::parse(&read(project)).unwrap_or_else(|e| panic!("{e}"));
    match answer(&read(datasets), &project) {
        Ok(answer) => println!("{answer}"),
        Err(bug) => panic!("{bug}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PIE: &str = "width: 400\nqueries:\n  q:\n    type: values\n    columns: [k, v]\n    values:\n      - [a, 1]\n      - [b, 2]\ncharts:\n  p:\n    query: q\n    type: pie\n    theta: v\n    color: k\nrows: [p]\n";
    const SCATTER: &str = "queries:\n  q:\n    type: values\n    columns: [x, y]\n    values:\n      - [1, 2]\ncharts:\n  s:\n    query: q\n    type: scatter\n    x: x\n    y: y\nrows: [s]\n";

    fn sidecar(queries: Value) -> String {
        json!({"source": "recorded text", "queries": queries, "rendered_at": "2026-01-15T12:00:00Z", "stylesheet": "svg text { font-variant-emoji: text; }"}).to_string()
    }

    fn rows() -> Value {
        let text = json!({"kind": "string", "element": null, "fields": null});
        let int = json!({"kind": "integer", "element": null, "fields": null});
        json!({"q": {"rows": [{"k": "a", "v": 1}, {"k": "b", "v": 2}], "schema": {"k": text, "v": int}}})
    }

    #[test]
    fn a_drawn_board_answers_with_its_svg_and_diagnostics() {
        let answer = answer(&sidecar(rows()), &Project::single(PIE)).unwrap();
        assert!(answer["svg"].as_str().is_some_and(|s| s.contains("data-chart-id=\"p\"")), "{answer}");
        assert_eq!(answer["diagnostics"], json!([]));
        assert!(answer["boxes"]["p"]["width"].as_f64().unwrap() > 0.0, "{answer}");
        assert!(answer["extent"]["height"].as_f64().unwrap() > 0.0, "{answer}");
    }

    #[test]
    fn a_board_renders_at_the_sidecars_clock() {
        let answer = answer(&sidecar(rows()), &Project::single(PIE)).unwrap();
        assert!(answer["svg"].as_str().is_some_and(|s| s.contains("data-rendered-at=\"2026-01-15T12:00:00Z\"")), "{answer}");
    }

    #[test]
    fn a_board_renders_with_the_sidecars_stylesheet() {
        let answer = answer(&sidecar(rows()), &Project::single(PIE)).unwrap();
        assert!(answer["svg"].as_str().is_some_and(|s| s.contains("svg text { font-variant-emoji: text; }")), "{answer}");
    }

    #[test]
    fn a_sidecar_with_no_stylesheet_is_refused() {
        let raw = json!({"source": "recorded text", "queries": rows(), "rendered_at": "2026-01-15T12:00:00Z"}).to_string();
        let answer = answer(&raw, &Project::single(PIE)).unwrap();
        assert!(answer["refused"].as_str().unwrap().starts_with("no-recording: the sidecar has no stylesheet"), "{answer}");
    }

    #[test]
    fn a_sidecar_with_no_clock_is_refused() {
        let raw = json!({"source": "recorded text", "queries": rows(), "stylesheet": ""}).to_string();
        let answer = answer(&raw, &Project::single(PIE)).unwrap();
        assert!(answer["refused"].as_str().unwrap().starts_with("no-recording: the sidecar has no rendered_at"), "{answer}");
    }

    #[test]
    fn a_board_the_engine_does_not_draw_is_refused_by_name() {
        let answer = answer(&sidecar(rows()), &Project::single(SCATTER)).unwrap();
        assert!(answer["refused"].as_str().unwrap().starts_with("refused: "), "{answer}");
    }

    #[test]
    fn a_recording_lacking_a_query_is_refused() {
        let answer = answer(&sidecar(json!({})), &Project::single(PIE)).unwrap();
        assert_eq!(answer["refused"], "no-recording: the sidecar holds no result for b.yaml#q");
    }

    #[test]
    fn a_board_that_does_not_compile_here_is_uncompiled_not_refused() {
        let sql = "queries:\n  q:\n    sql: select 1 as v\ncharts:\n  p:\n    query: q\n    type: kpi\n    value: v\nrows: [p]\n";
        let answer = answer(&sidecar(rows()), &Project::single(sql)).unwrap();
        assert!(answer["uncompiled"].as_str().unwrap().contains("must have a source"), "{answer}");
        assert!(answer.get("refused").is_none());
    }

    #[test]
    fn a_board_that_does_not_parse_is_uncompiled() {
        let answer = answer(&sidecar(rows()), &Project::single("this: [is not a board")).unwrap();
        assert!(answer.get("uncompiled").is_some(), "{answer}");
    }

    fn project(board: &str, files: &[(&str, &str)], sources: &[&str]) -> Project {
        Project {
            board: board.into(),
            files: files.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect(),
            sources: sources.iter().map(|s| s.to_string()).collect(),
        }
    }

    const SQL: &str = "queries:\n  q:\n    sql: select 1 as v\ncharts:\n  p:\n    query: q\n    type: kpi\n    value: v\nrows: [p]\n";

    #[test]
    fn a_folders_meta_source_reaches_the_compile() {
        let files = [("charts/b.yml", SQL), ("charts/meta.yml", "source: warehouse\n")];
        let answer = answer(&sidecar(json!({})), &project("charts/b.yml", &files, &["warehouse"])).unwrap();
        // It compiled: the engine went on to ask for the query's result, which the sidecar lacks.
        assert!(answer["refused"].as_str().unwrap().starts_with("no-recording"), "{answer}");
    }

    #[test]
    fn a_source_the_project_does_not_declare_is_uncompiled() {
        let files = [("charts/b.yml", SQL), ("charts/meta.yml", "source: warehouse\n")];
        let answer = answer(&sidecar(json!({})), &project("charts/b.yml", &files, &["other"])).unwrap();
        assert!(answer["uncompiled"].is_string(), "{answer}");
    }

    #[test]
    fn a_project_is_read_from_the_sweeps_json() {
        let raw = json!({"board": "charts/b.yml", "files": {"charts/b.yml": "x"}, "sources": ["w"]}).to_string();
        let parsed = Project::parse(&raw).unwrap();
        assert_eq!((parsed.board.as_str(), parsed.sources.as_slice()), ("charts/b.yml", ["w".to_string()].as_slice()));
        assert!(Project::parse("{}").is_err());
    }
}
