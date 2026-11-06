//! Engine features no ported module uses yet (`rewrite`, `conflicts_with`,
//! `expansion`, `chart_type`) on synthetic moves against real frozen grammars.

use std::sync::Arc;

use serde_json::json;

use crate::apply::{apply_conditional_move, apply_move, Applied, MigrationError};
use crate::catalog::grammar;
use crate::decl::{path, ConditionalMove, Grammars, Mapped, Move, Rewritten};

fn doc(text: &str) -> yaml::Node {
    yaml::parse_node(text).expect("parses")
}

fn grammars(source: &'static str, target: &'static str) -> Grammars {
    Grammars {
        source,
        target,
        source_schema: grammar(source).expect("source"),
        target_schema: grammar(target).expect("target"),
    }
}

fn html_policy_move() -> Move {
    let g = grammars("0.4.0", "0.5.0");
    g.suffix_rename_moves(&[(&["html_policy"], &["allow_html"])], None, None)
        .into_iter()
        .next()
        .expect("allow_html is declared in both grammars")
}

#[test]
fn rewrite_rebuilds_the_value_and_notes_a_change() {
    let mut m = html_policy_move();
    m.rewrite = Some(Arc::new(|v| match v {
        serde_json::Value::Bool(true) => Rewritten::Value(json!("trusted-raw")),
        _ => Rewritten::Drop,
    }));
    m.rewrite_note = Some("html widened");
    let mut yes = doc("title: T\ntext: hi\nallow_html: true\n");
    let mut out = Applied::default();
    apply_move(&mut yes, &m, &mut out).expect("applies");
    assert_eq!(yes.to_json()["html_policy"], json!("trusted-raw"));
    assert_eq!(out.notices, ["html widened"]);
}

#[test]
fn a_rewrite_keeps_a_timestamp_it_hands_back_unchanged() {
    let mut m = html_policy_move();
    m.rewrite = Some(Arc::new(|v| {
        let mut out = v.clone();
        out["n"] = json!(2);
        Rewritten::Value(out)
    }));
    let mut node = doc("title: T\ntext: hi\nallow_html:\n  at: 2020-01-02\n  n: 1\n");
    apply_move(&mut node, &m, &mut Applied::default()).expect("applies");
    let at = crate::doc::at(&node, &["html_policy".to_string(), "at".to_string()]).expect("kept");
    assert!(matches!(&at.kind, yaml::Kind::Scalar(yaml::Value::Timestamp(t)) if t == "2020-01-02"));
    assert_eq!(node.to_json()["html_policy"]["n"], json!(2));
}

#[test]
fn rewrite_drop_strikes_the_source_and_leaves_nothing() {
    let mut m = html_policy_move();
    m.rewrite = Some(Arc::new(|_| Rewritten::Drop));
    let mut no = doc("title: T\ntext: hi\nallow_html: false\n");
    apply_move(&mut no, &m, &mut Applied::default()).expect("applies");
    assert_eq!(no.to_json(), json!({"title": "T", "text": "hi"}));
}

#[test]
fn conflicts_with_refuses_a_competing_spelling() {
    let mut m = html_policy_move();
    m.conflicts_with = vec![path(&["title"])];
    let mut d = doc("title: T\ntext: hi\nallow_html: true\n");
    let err = apply_move(&mut d, &m, &mut Applied::default()).expect_err("conflict");
    assert!(matches!(err, MigrationError::Conflict(m) if m.contains("'title' is also set")));
}

#[test]
fn chart_type_gates_a_move_to_its_family() {
    let g = grammars("0.4.0", "0.5.0");
    let mut moves = g.suffix_rename_moves(&[(&["max_number"], &["number"])], None, Some("kpi"));
    assert!(!moves.is_empty());
    let m = moves.remove(0);
    let board = "style:\n  text:\n    column:\n      number: 2\ntext: hi\n";
    let mut d = doc(board);
    apply_move(&mut d, &m, &mut Applied::default()).expect("applies");
    // A board outside any chart holds no `kpi` type, so the gated move leaves it alone.
    assert_eq!(d.to_json(), doc(board).to_json());
    let mut outside = m.clone();
    outside.chart_type = Some(String::new());
    apply_move(&mut d, &outside, &mut Applied::default()).expect("applies");
    assert_eq!(
        d.to_json()["style"]["text"]["column"],
        json!({"max_number": 2})
    );
}

#[test]
fn expansion_turns_a_scalar_into_the_mapping_its_leaves_declare() {
    let g = grammars("0.9.0", "0.10.0");
    let mut moves = g.suffix_rename_moves(
        &[(&["legend", "position"], &["legend", "position"])],
        None,
        None,
    );
    let mut m = moves.remove(0);
    m.expansion = Some(Arc::new(
        [(
            "top-left".to_string(),
            vec![
                ("edge", Mapped::Str("top".into())),
                ("overlay", Mapped::Bool(true)),
            ],
        )]
        .into_iter()
        .collect(),
    ));
    let board = chart_with_legend_position();
    let mut d = doc(&board);
    apply_move(&mut d, &m, &mut Applied::default()).expect("applies");
    let legend = &d.to_json()["charts"]["c"]["style"]["legend"]["position"];
    assert_eq!(legend, &json!({"edge": "top", "overlay": true}));
}

fn chart_with_legend_position() -> String {
    "queries:\n  q:\n    sql: select 1\ncharts:\n  c:\n    type: bar\n    query: q\n    x: m\n    y: v\n    style:\n      legend:\n        position: top-left\n".to_string()
}

mod reader {
    use crate::catalog::current_grammar;
    use crate::validate::{first_rejection, is_valid};

    fn current() -> &'static crate::schema::Schema {
        current_grammar().expect("current grammar")
    }

    #[test]
    fn if_then_picks_a_chart_family_by_its_type() {
        let g = current();
        let board = |ty: &str| {
            super::doc(&format!(
                "queries:\n  q:\n    sql: select 1\ncharts:\n  c:\n    type: {ty}\n    query: q\n    value: v\n"
            ))
        };
        assert!(is_valid(g.root(), g.root(), &board("kpi")));
        assert!(!is_valid(g.root(), g.root(), &board("bar")));
    }

    #[test]
    fn additional_properties_false_names_the_unexpected_keys() {
        let g = current();
        let d = super::doc("title: T\ntext: hi\nbogus: 1\nalso_bogus: 2\n");
        assert_eq!(
            first_rejection(g.root(), &d).as_deref(),
            Some("Additional properties are not allowed ('also_bogus', 'bogus' were unexpected)")
        );
    }

    #[test]
    fn a_bare_dollar_matches_before_a_trailing_newline_as_pythons_does() {
        let ok = |pattern: &str, text: &str| {
            let schema = serde_json::json!({ "pattern": pattern });
            let d = super::doc(&format!(
                "k: {}\n",
                serde_json::to_string(text).expect("json")
            ));
            is_valid(&schema, &schema, crate::doc::child(&d, "k").expect("k"))
        };
        assert!(ok("^red$", "red\n") && !ok("^red$", "red\n\n"));
        assert!(!ok("^(?!auto$)[a-z\n]*$", "auto\n") && ok("^(?:[^#]|$)", "\n"));
        assert!(ok("^a\\$$", "a$") && ok("^[$]$", "$") && !ok("^[$]$", "$\n\n"));
    }

    #[test]
    fn a_pattern_with_lookahead_is_read() {
        let schema = serde_json::json!({"pattern": "^(?!auto$)[a-z]*$"});
        let ok = |t: &str| {
            let d = super::doc(&format!("k: {t}\n"));
            is_valid(&schema, &schema, crate::doc::child(&d, "k").expect("k"))
        };
        assert!(ok("blue") && !ok("auto"));
    }
}

fn kpi_tone_move() -> ConditionalMove {
    ConditionalMove {
        source: "0.4.0",
        target: "0.5.0",
        chart_type: "kpi",
        old_tail: path(&["style", "tone"]),
        new_tail: path(&["support", "tone"]),
        sibling_tail: path(&["support"]),
        drop_warning: "dropped {chart!r}".into(),
    }
}

const KPI: &str = "charts:\n  k:\n    type: kpi\n    query: q\n    value: v\n";

#[test]
fn a_conditional_move_lands_in_its_existing_sibling() {
    let mut d = doc(&format!(
        "{KPI}    support:\n      value: w\n    style:\n      tone: good\n"
    ));
    let mut out = Applied::default();
    apply_conditional_move(&mut d, &kpi_tone_move(), &mut out).expect("applies");
    let kpi = &d.to_json()["charts"]["k"];
    assert_eq!(kpi["support"], json!({"value": "w", "tone": "good"}));
    assert!(
        kpi.get("style").is_none(),
        "the emptied style block goes with it"
    );
    assert!(out.notices.is_empty());
}

#[test]
fn a_conditional_move_onto_an_authored_destination_conflicts() {
    let mut d = doc(&format!(
        "{KPI}    support:\n      tone: bad\n    style:\n      tone: good\n"
    ));
    let err = apply_conditional_move(&mut d, &kpi_tone_move(), &mut Applied::default())
        .expect_err("conflict");
    assert!(matches!(err, MigrationError::Conflict(m) if m.contains("in chart 'k'")));
}

mod catalog {
    use crate::catalog::{entries, grammar};
    use crate::registry::Registry;

    #[test]
    fn every_released_version_is_embedded_and_every_module_contributes() {
        for e in entries() {
            let g = grammar(&e.version).unwrap_or_else(|| panic!("{} not embedded", e.version));
            assert!(
                g.root().get("properties").is_some(),
                "{} did not parse",
                e.version
            );
        }
        let reg = Registry::get();
        for (v, _) in crate::MODULES {
            let n = |s: &str| reg.moves_from(s).len() + reg.deletions_from(s).len();
            let source = entries()
                .iter()
                .find(|e| e.version == *v)
                .and_then(|e| e.predecessor.clone());
            assert!(
                n(source.as_deref().unwrap_or("")) > 0,
                "module {v} registered nothing"
            );
        }
    }

    #[test]
    fn every_pattern_in_the_frozen_files_compiles() {
        fn walk(v: &serde_json::Value, bad: &mut Vec<String>) {
            match v {
                serde_json::Value::Object(m) => {
                    if let Some(p) = m.get("pattern").and_then(|p| p.as_str()) {
                        if !crate::validate::pattern_compiles(p) {
                            bad.push(p.to_string());
                        }
                    }
                    m.values().for_each(|x| walk(x, bad));
                }
                serde_json::Value::Array(a) => a.iter().for_each(|x| walk(x, bad)),
                _ => {}
            }
        }
        let mut bad = Vec::new();
        for e in entries() {
            walk(grammar(&e.version).expect("embedded").root(), &mut bad);
        }
        assert!(bad.is_empty(), "{bad:?}");
    }
}
