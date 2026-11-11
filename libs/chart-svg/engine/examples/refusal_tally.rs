//! Tallies the corpus boards `normalize::compile` builds whole by what `engine::render_board`
//! does with them, under the `clarity` theme and default variables, and writes the uncommitted `sweep/local/engine_refusal_tally.json`.
//!
//! Run from the repo root: `cargo run --manifest-path libs/chart-svg/Cargo.toml -p engine --example refusal_tally`
//! (`just chart_svg_dev refusal-tally`). The list is `normalize/tests/fixtures/accepted_boards.txt`.
//!
//! No query results are fed: a board that reaches a chart's data before any refusal is
//! `needs-data`, never drawn from invented rows. So a `refused:` row is a refusal the engine
//! makes before it looks at data, and `needs-data` hides whatever refusal would follow. This is
//! the report all CI could produce: it has no recordings. Neither report is committed.
//!
//! Each board's outcome is `drawn`, `refused: <first refusal's message>`, `error: <code>`,
//! `compile-failed` or `needs-data` (the engine asked for a query's result and
//! `ERR-ENGINE-NEEDS-DATA` was raised). The report has a count per outcome, descending, and one
//! sorted row per board per line.
//!
//! With `--with-data` (`just chart_svg_dev refusal-tally-data`) each board is fed the query
//! results the equivalence sweep recorded for it (`Sidecar`) and the report goes to the
//! uncommitted `sweep/local/engine_refusal_tally_data.json`. A board with no sidecar, or whose
//! sidecar lacks a query the engine asked for, is `no-recording` (the missing query in the row's
//! `detail`).
//!
//! The sidecar is the `datasets.json` the sweep writes beside a board's recording
//! (`sweep/local/equivalence/recordings/<board>/`, default variables only, gitignored;
//! `Recording.datasets` in `_replay.py`): per query, keyed by the name the compile gives it
//! (`revenue`, `@charts.c.query`), `{rows, schema}` in `rows_wire`'s shape, `{error}` or
//! `{ambiguous}`.

#[path = "../tests/it/inputs.rs"]
mod inputs;
#[path = "../tests/it/recorded.rs"]
mod recorded;

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

use recorded::{render, Project, Sidecar};
use serde_json::Value;

struct Row {
    group: String,
    board: String,
    outcome: String,
    /// Why a board did not compile; the outcome stays `compile-failed` so the rows group.
    detail: Option<String>,
}

/// Counts by outcome, most boards first, ties by outcome text.
fn summarize<'a>(outcomes: impl IntoIterator<Item = &'a str>) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for outcome in outcomes {
        *counts.entry(outcome).or_default() += 1;
    }
    let mut counts: Vec<_> = counts
        .into_iter()
        .map(|(outcome, n)| (outcome.to_string(), n))
        .collect();
    counts.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    counts
}

/// The report: a summary and the per-board rows sorted by board, one object per line.
fn report(rows: &mut [Row]) -> String {
    rows.sort_by(|a, b| a.board.cmp(&b.board));
    let mut out = String::from("{\n");
    writeln!(out, "  \"total\": {},", rows.len()).unwrap();
    out.push_str("  \"summary\": [\n");
    let summary = summarize(rows.iter().map(|r| r.outcome.as_str()));
    for (i, (outcome, boards)) in summary.iter().enumerate() {
        let comma = if i + 1 < summary.len() { "," } else { "" };
        writeln!(
            out,
            "    {}{comma}",
            serde_json::json!({"outcome": outcome, "boards": boards})
        )
        .unwrap();
    }
    out.push_str("  ],\n  \"boards\": [\n");
    for (i, row) in rows.iter().enumerate() {
        let mut value =
            serde_json::json!({"board": row.board, "group": row.group, "outcome": row.outcome});
        if let Some(detail) = &row.detail {
            value["detail"] = Value::String(detail.clone());
        }
        let comma = if i + 1 < rows.len() { "," } else { "" };
        writeln!(out, "    {value}{comma}").unwrap();
    }
    out.push_str("  ]\n}\n");
    out
}

/// The outcome of `board`'s `text`. `recordings` is the directory of the sidecars when the
/// tally feeds them.
fn tally(board: &str, text: &str, recordings: Option<&Path>) -> (String, Option<String>) {
    let sidecar = || recordings.map(|dir| Sidecar::read(dir, board, text)).transpose();
    match render(board, &Project::single(text), sidecar) {
        Ok(_) => ("drawn".into(), None),
        Err(outcome) => outcome,
    }
}


fn main() {
    let with_data = match std::env::args().skip(1).collect::<Vec<_>>().as_slice() {
        [] => false,
        [flag] if flag == "--with-data" => true,
        other => panic!("usage: refusal_tally [--with-data], not {other:?}"),
    };
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let recordings = root.join("sweep/local/equivalence/recordings");
    let list =
        std::fs::read_to_string(root.join("normalize/tests/fixtures/accepted_boards.txt")).unwrap();
    let corpus: Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("board-model/tests/fixtures/corpus.json")).unwrap(),
    )
    .unwrap();
    let mut rows = Vec::new();
    for line in list.lines().filter(|l| !l.is_empty()) {
        let (group, board) = line
            .split_once('\t')
            .unwrap_or_else(|| panic!("accepted_boards.txt: no tab in {line:?}"));
        let text = corpus[board]
            .as_str()
            .unwrap_or_else(|| panic!("accepted_boards.txt: {board} is not in corpus.json"));
        let (outcome, detail) = tally(board, text, with_data.then_some(recordings.as_path()));
        rows.push(Row {
            group: group.into(),
            board: board.into(),
            outcome,
            detail,
        });
    }
    // Neither report is committed: a snapshot of every board, rewritten by any PR that changes a
    // refusal, would conflict every parallel one.
    let out = root.join(if with_data {
        "sweep/local/engine_refusal_tally_data.json"
    } else {
        "sweep/local/engine_refusal_tally.json"
    });
    std::fs::create_dir_all(out.parent().unwrap()).unwrap();
    std::fs::write(&out, report(&mut rows)).unwrap();
    for (outcome, boards) in summarize(rows.iter().map(|r| r.outcome.as_str()))
        .iter()
        .take(10)
    {
        println!("{boards:>6}  {outcome}");
    }
    println!("wrote {} ({} boards)", out.display(), rows.len());
}

#[cfg(test)]
mod tests {
    use super::*;
    use diagnostic::Diagnostic;
    use project::ProjectPath;
    use recorded::{outcome_of, NEEDS_DATA, NO_RECORDING};
    use serde_json::json;

    fn row(board: &str, outcome: &str) -> Row {
        Row {
            group: "g".into(),
            board: board.into(),
            outcome: outcome.into(),
            detail: None,
        }
    }

    fn file() -> ProjectPath {
        ProjectPath::try_from("b.yaml").unwrap()
    }

    fn sidecar(queries: Value) -> Sidecar {
        Sidecar::parse(
            &json!({"source": "text", "queries": queries}).to_string(),
            "text",
        )
        .unwrap()
    }

    #[test]
    fn outcomes_are_named_by_what_the_engine_said() {
        let outcome = |rendered| outcome_of(rendered, None).0;
        assert_eq!(outcome(Ok(())), "drawn");
        assert_eq!(
            outcome(Err(Diagnostic::refused("a thing is not supported"))),
            "refused: a thing is not supported"
        );
        assert_eq!(
            outcome(Err(diagnostic::diag::err_engine_needs_data("b.yaml#q"))),
            NEEDS_DATA
        );
        assert_eq!(
            outcome(Err(diagnostic::diag::err_internal("boom"))),
            "error: ERR-INTERNAL"
        );
    }

    #[test]
    fn a_query_the_recording_lacks_is_no_recording_naming_it() {
        let recorded = sidecar(
            json!({"other": {"ambiguous": 2}, "failed": {"error": {"code": "ERR-QUERY-INTERRUPTED"}}}),
        );
        let asked = |query: &str| {
            outcome_of(
                Err(diagnostic::diag::err_engine_needs_data(query)),
                Some((&recorded, &file())),
            )
        };
        assert_eq!(
            asked("b.yaml#q"),
            (
                NO_RECORDING.into(),
                Some("the sidecar holds no result for b.yaml#q".into())
            )
        );
        assert_eq!(
            asked("shared.yml#other").0,
            NO_RECORDING,
            "a query of another file is not the sidecar's"
        );
        assert_eq!(
            asked("b.yaml#other"),
            (
                NO_RECORDING.into(),
                Some("b.yaml#other was recorded 2 times: no one result stands for it".into())
            )
        );
        assert_eq!(
            asked("b.yaml#failed").0,
            "query-error: ERR-QUERY-INTERRUPTED"
        );
    }

    #[test]
    fn a_sidecar_must_come_from_the_boards_own_text() {
        let raw = json!({"source": "old", "queries": {}}).to_string();
        assert!(Sidecar::parse(&raw, "old").is_ok());
        assert!(Sidecar::parse(&raw, "new")
            .unwrap_err()
            .contains("other board text"));
    }

    #[test]
    fn a_board_is_found_under_the_name_the_sweep_quotes() {
        assert_eq!(
            Sidecar::dir_name("ladder/variables/02-select_static.v~1"),
            "ladder%2Fvariables%2F02-select_static.v~1"
        );
        assert_eq!(Sidecar::dir_name("a b&c"), "a%20b%26c");
    }

    const KPI: &str = "queries:\n  q:\n    type: values\n    columns: [v]\n    values:\n      - [1]\ncharts:\n  a:\n    query: q\n    type: kpi\n    value: v\nrows: [a]\n";

    /// A recordings directory holding the one sidecar `document` for the board `KPI`.
    fn recordings(name: &str, document: Option<Value>) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("refusal_tally_{name}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        if let Some(document) = document {
            std::fs::create_dir_all(dir.join("kpi")).unwrap();
            std::fs::write(dir.join("kpi/datasets.json"), document.to_string()).unwrap();
        }
        dir
    }

    fn tally_kpi(name: &str, document: Option<Value>) -> (String, Option<String>) {
        let dir = recordings(name, document);
        let outcome = tally("kpi", KPI, Some(&dir));
        let _ = std::fs::remove_dir_all(&dir);
        outcome
    }

    #[test]
    fn a_board_is_tallied_before_data_and_over_its_recorded_results() {
        assert_eq!(tally("kpi", KPI, None).0, NEEDS_DATA);
        let int = json!({"kind": "integer", "element": null, "fields": null});
        let rows = json!({"rows": [{"v": 7}], "schema": {"v": int}});
        assert_eq!(
            tally_kpi("rows", Some(json!({"source": KPI, "queries": {"q": rows}}))),
            ("drawn".into(), None)
        );
    }

    #[test]
    fn a_recorded_error_is_the_charts_error_card() {
        let error = json!({"query_name": "q", "code": "ERR-QUERY-INTERRUPTED", "fields": {}, "message": "interrupted"});
        assert_eq!(
            tally_kpi(
                "error",
                Some(json!({"source": KPI, "queries": {"q": {"error": error}}}))
            ),
            ("drawn".into(), None)
        );
    }

    #[test]
    fn a_board_with_no_usable_recording_is_no_recording() {
        let (outcome, detail) = tally_kpi("absent", None);
        assert_eq!(outcome, NO_RECORDING);
        assert!(detail.unwrap().starts_with("no sidecar"));
        let (outcome, detail) = tally_kpi("stale", Some(json!({"source": "other", "queries": {}})));
        assert_eq!(
            (
                outcome.as_str(),
                detail.unwrap().contains("other board text")
            ),
            (NO_RECORDING, true)
        );
        let (outcome, detail) = tally_kpi("lacking", Some(json!({"source": KPI, "queries": {}})));
        assert_eq!(
            (outcome.as_str(), detail.as_deref()),
            (
                NO_RECORDING,
                Some("the sidecar holds no result for b.yaml#q")
            )
        );
    }

    #[test]
    fn summary_is_by_count_descending_then_text() {
        let got = summarize(["b", "a", "b", "c", "a", "b", "drawn"]);
        assert_eq!(
            got,
            [
                ("b".to_string(), 3),
                ("a".to_string(), 2),
                ("c".to_string(), 1),
                ("drawn".to_string(), 1)
            ]
        );
    }

    #[test]
    fn report_is_one_sorted_row_per_line() {
        let mut rows = vec![
            row("z", "drawn"),
            row("a", "refused: x"),
            row("m", "refused: x"),
        ];
        let text = report(&mut rows);
        let parsed: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed["total"], 3);
        assert_eq!(
            parsed["summary"][0],
            serde_json::json!({"outcome": "refused: x", "boards": 2})
        );
        let boards: Vec<&str> = parsed["boards"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["board"].as_str().unwrap())
            .collect();
        assert_eq!(boards, ["a", "m", "z"]);
        assert_eq!(text.lines().filter(|l| l.contains("\"board\"")).count(), 3);
    }
}
