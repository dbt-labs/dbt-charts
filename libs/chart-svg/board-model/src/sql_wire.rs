//! The SQL seam's JSON wire form between a host and compile: a request out, an outcome back.
//! Positions are SQL-local. `Skeleton.placeholders`, `OutputColumn.sources`/`format` and
//! `Analysis.scope` are not on the wire; a host that computes them needs a wire version.

use crate::sql_analysis::*;
use serde_json::{json, Value};

pub fn request(r: &SqlRequest) -> Value {
    json!({
        "sql": r.sql,
        "setup_sql": r.setup_sql,
        "source": r.source.map(|s| s.as_str()),
        "dialect": r.dialect.map(|d| d.as_str()),
    })
}

fn str_of(v: &Value, key: &str) -> Result<String, String> {
    v[key]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| format!("`{key}` is not a string"))
}

fn num_of(v: &Value, key: &str) -> Result<usize, String> {
    v[key]
        .as_u64()
        .map(|n| n as usize)
        .ok_or_else(|| format!("`{key}` is not a count"))
}

fn guard(v: &Value) -> Result<Guard, String> {
    match v["kind"].as_str() {
        Some("allowed") => Ok(Guard::Allowed),
        Some("undetermined") => Ok(Guard::Undetermined),
        Some("mutating") => Ok(Guard::Mutating {
            node_kind: str_of(v, "node_kind")?,
            fragment_preview: str_of(v, "fragment_preview")?,
        }),
        other => Err(format!("unknown guard kind {other:?}")),
    }
}

fn lints(v: &Value) -> Result<Vec<Lint>, String> {
    let items = v.as_array().ok_or("`lints` is not a list")?;
    items
        .iter()
        .map(|l| match l["kind"].as_str() {
            Some("literal_newlines") => Ok(Lint::LiteralNewlines),
            Some("date_literal_variable") => Ok(Lint::DateLiteralVariable {
                keyword: str_of(l, "keyword")?,
                variable: str_of(l, "variable")?,
            }),
            other => Err(format!("unknown lint kind {other:?}")),
        })
        .collect()
}

fn findings(v: &Value) -> Result<Vec<QueryFinding>, String> {
    let items = v.as_array().ok_or("`findings` is not a list")?;
    items
        .iter()
        .map(|f| {
            let kind = match f["kind"].as_str() {
                Some("missing_join_predicate") => FindingKind::MissingJoinPredicate,
                Some("reaggregation") => FindingKind::Reaggregation,
                Some("date_as_text") => FindingKind::DateAsText {
                    column: str_of(f, "column")?,
                },
                other => return Err(format!("unknown finding kind {other:?}")),
            };
            let text = |key: &str| f[key].as_str().map(str::to_string);
            Ok(QueryFinding {
                kind,
                severity: str_of(f, "severity")?,
                message: str_of(f, "message")?,
                detail: text("detail"),
                recommendation: text("recommendation"),
                confidence: f["confidence"].as_f64(),
                evidence: f["evidence"]
                    .as_array()
                    .ok_or("`evidence` is not a list")?
                    .iter()
                    .map(|e| e.as_str().map(str::to_string).ok_or("evidence is not text"))
                    .collect::<Result<_, _>>()?,
            })
        })
        .collect()
}

/// One outcome a host answered: `{kind: "parse_error", message, position}` or
/// `{kind: "analyzed", analysis}`.
pub fn outcome(v: &Value) -> Result<SqlOutcome, String> {
    if v["kind"] == "parse_error" {
        let p = &v["position"];
        return Ok(SqlOutcome::ParseError(ParseError {
            message: str_of(v, "message")?,
            position: ErrorPosition {
                line: num_of(p, "line")?,
                start_col: num_of(p, "start_col")?,
                end_col: num_of(p, "end_col")?,
                line_text: str_of(p, "line_text")?,
            },
        }));
    }
    if v["kind"] != "analyzed" {
        return Err(format!("unknown outcome kind {}", v["kind"]));
    }
    let a = &v["analysis"];
    let setup = match a.get("setup").filter(|s| !s.is_null()) {
        Some(s) => Some(SetupAnalysis {
            guard: guard(&s["guard"])?,
            lints: lints(&s["lints"])?,
        }),
        None => None,
    };
    let outputs = a["outputs"]
        .as_array()
        .ok_or("`outputs` is not a list")?
        .iter()
        .map(|n| {
            n.as_str()
                .map(|name| OutputColumn {
                    name: name.to_string(),
                    sources: Vec::new(),
                    format: None,
                })
                .ok_or_else(|| "an output name is not a string".to_string())
        })
        .collect::<Result<_, _>>()?;
    Ok(SqlOutcome::Analyzed(Analysis {
        skeleton: Skeleton {
            sql: str_of(a, "skeleton")?,
            placeholders: Vec::new(),
        },
        select_only: guard(&a["select_only"])?,
        lints: lints(&a["lints"])?,
        findings: findings(&a["findings"])?,
        setup,
        outputs,
        outputs_complete: a["outputs_complete"]
            .as_bool()
            .ok_or("`outputs_complete` is not a bool")?,
        scope: Vec::new(),
    }))
}
