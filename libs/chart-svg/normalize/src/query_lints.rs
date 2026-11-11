//! Python's query lints (`validate_compiled_queries`): the host's [`QueryFinding`]s of each
//! authored SQL query, as the query warnings `dct validate` reports. The findings need a parse
//! tree and come from the host; what compile owns is which queries are linted and, for a date
//! turned into text, which columns some chart uses as its x. A query's own suppression set, its
//! `ignore` list and the codes its SQL names in `-- dct:ignore` comments, filters them here.

use crate::extends::MetaLint;
use crate::sql::{query_id, QueryFacts, SqlFacts};
use board_model::chart::ChartAuthored;
use board_model::ids::{ItemId, QueryId};
use board_model::query::QueryAuthored;
use board_model::sql_analysis::{FindingKind, QueryFinding};
use diagnostic::{codes, diag, Diagnostic, ModelPath};
use model_core::Spell;
use project::ProjectPath;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// The channels a family binds by position, as `positional.py`'s `POSITIONAL_CHANNELS`: the x
/// channel's place among the projections, by projection count.
fn positional_x(family: &str, columns: usize) -> Option<usize> {
    match (family, columns) {
        ("bar" | "line" | "area", 2 | 3) => Some(0),
        ("scatter", 2) => Some(0),
        ("scatter", 3) => Some(1),
        ("heatmap", 3) => Some(0),
        ("histogram", 1) => Some(0),
        _ => None,
    }
}

/// The fields whose being named means a chart chose its columns (`CHANNELS` and `PINNED_FIELDS`
/// in `compile/resolve/chart/positional.py`).
fn named_fields(family: &str) -> &'static [&'static str] {
    match family {
        "bar" => &[
            "x",
            "y",
            "color",
            "pattern",
            "y_start",
            "layers",
            "multiples",
            "tooltip",
        ],
        "line" => &[
            "x",
            "y",
            "color",
            "connect",
            "layers",
            "multiples",
            "tooltip",
        ],
        "area" => &[
            "x",
            "y",
            "color",
            "pattern",
            "layers",
            "multiples",
            "tooltip",
        ],
        "scatter" => &[
            "x",
            "y",
            "color",
            "size",
            "shape",
            "layers",
            "multiples",
            "tooltip",
        ],
        "heatmap" => &["x", "y", "color", "pattern", "multiples"],
        _ => &["x"],
    }
}

fn family(chart: &ChartAuthored) -> Option<&'static str> {
    Some(match chart {
        ChartAuthored::Bar(_) => "bar",
        ChartAuthored::Histogram(_) => "histogram",
        ChartAuthored::Line(_) => "line",
        ChartAuthored::Area(_) => "area",
        ChartAuthored::Scatter(_) => "scatter",
        ChartAuthored::Heatmap(_) => "heatmap",
        _ => return None,
    })
}

fn named(v: Option<&Value>) -> bool {
    !matches!(v, None | Some(Value::Null))
        && v != Some(&Value::String(String::new()))
        && v != Some(&Value::Array(Vec::new()))
}

fn text<'a>(map: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    map.get(key).and_then(Value::as_str)
}

/// `_x_columns_by_query`: the lowercased columns each query supplies as the x of a placed chart
/// or layer, by the query's id as printed.
fn x_columns(
    patch: &Patch,
    placed: &BTreeMap<ItemId, String>,
    queries: &BTreeMap<QueryId, QueryFacts>,
) -> BTreeMap<String, BTreeSet<String>> {
    let mut by_query: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let by_id: BTreeMap<String, &QueryFacts> =
        queries.iter().map(|(id, f)| (id.to_string(), f)).collect();
    for name in placed.values() {
        let Some(Some(chart)) = patch.body.charts.get(name) else {
            continue;
        };
        let Some(family) = family(chart) else {
            continue;
        };
        let Value::Object(fields) = chart.spell() else {
            continue;
        };
        let query = text(&fields, "query");
        let mut x = text(&fields, "x").map(str::to_string);
        if let Some(q) = query {
            if x.is_none() && named_fields(family).iter().all(|f| !named(fields.get(*f))) {
                let outputs = match by_id.get(q).map(|f| &f.sql) {
                    Some(SqlFacts::Analyzed(a)) if a.outputs_complete => Some(&a.outputs),
                    _ => None,
                };
                x = outputs
                    .and_then(|o| positional_x(family, o.len()).and_then(|i| o.get(i)))
                    .map(|o| o.name.clone());
            }
            if let Some(x) = &x {
                by_query
                    .entry(q.to_string())
                    .or_default()
                    .insert(x.to_lowercase());
            }
        }
        let layers = fields
            .get("layers")
            .and_then(Value::as_array)
            .into_iter()
            .flatten();
        for layer in layers.filter_map(Value::as_object) {
            let (Some(q), Some(x)) = (
                text(layer, "query").or(query),
                text(layer, "x").or(x.as_deref()),
            ) else {
                continue;
            };
            by_query
                .entry(q.to_string())
                .or_default()
                .insert(x.to_lowercase());
        }
    }
    by_query
}

/// Python's `_is_space`: what `str.split` and `\s` take as a separator.
fn space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.splitlines` line breaks.
fn line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

/// `parse_inline_suppressions`: the codes named by every `-- dct:ignore` in the authored SQL,
/// `*` for a comment naming none. A plain line scan, as Python's: the comment may sit anywhere
/// on any line, in a string literal too, and covers the whole query.
fn inline_suppressions(sql: &str) -> BTreeSet<String> {
    const WORD: &str = "dct:ignore";
    let mut codes = BTreeSet::new();
    for line in sql.split(line_break) {
        let found = line
            .char_indices()
            .filter(|(at, _)| line[*at..].starts_with("--"))
            .find_map(|(at, _)| {
                let rest = line[at + 2..].trim_start_matches(space);
                let head = rest
                    .get(..WORD.len())
                    .filter(|h| h.eq_ignore_ascii_case(WORD))?;
                let after = &rest[head.len()..];
                let word = |c: char| c.is_alphanumeric() || c == '_';
                // `\b` after the last word character of `ignore`.
                (!after.starts_with(word)).then_some(after)
            });
        if let Some(rest) = found {
            let named: Vec<&str> = rest.split(space).filter(|c| !c.is_empty()).collect();
            if named.is_empty() {
                codes.insert("*".to_string());
            }
            codes.extend(named.into_iter().map(str::to_string));
        }
    }
    codes
}

/// The codes the query `name` suppresses, one set: its `ignore` list, its comments' codes, the
/// `meta.yml` chain's `lint.ignore` and the chain's `lint.ignore_queries` for it.
fn suppressed(name: &str, query: Option<&QueryAuthored>, meta: &MetaLint) -> BTreeSet<String> {
    let mut codes = meta.ignore.clone();
    codes.extend(meta.ignore_queries.get(name).into_iter().flatten().cloned());
    codes.extend(query.map(authored_suppressions).unwrap_or_default());
    codes
}

/// A query's own `ignore` list and its comments' codes.
fn authored_suppressions(query: &QueryAuthored) -> BTreeSet<String> {
    let spelled = query.spell();
    let mut codes = spelled
        .get("sql")
        .and_then(Value::as_str)
        .map(inline_suppressions)
        .unwrap_or_default();
    let listed = spelled.get("ignore").and_then(Value::as_array);
    codes.extend(
        listed
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_string),
    );
    codes
}

fn warning(name: &str, f: &QueryFinding) -> Diagnostic {
    let build = match f.kind {
        FindingKind::MissingJoinPredicate => diag::warn_missing_join_predicate,
        FindingKind::Reaggregation => diag::warn_reaggregation,
        FindingKind::DateAsText { .. } => diag::warn_date_as_text,
    };
    let mut d = build(name, f.message.as_str())
        .at_path(ModelPath(vec![
            "queries".to_string(),
            name.to_string(),
            "sql".to_string(),
        ]))
        .with_sink_param("severity", Value::from(f.severity.as_str()))
        .with_sink_param("confidence", f.confidence.map_or(Value::Null, Value::from))
        .with_sink_param(
            "detail",
            f.detail.as_deref().map_or(Value::Null, Value::from),
        )
        .with_sink_param("evidence", Value::from(f.evidence.clone()));
    if let Some(fix) = &f.recommendation {
        d = d.with_sink_param("fix", Value::from(fix.as_str()));
    }
    d
}

/// The query warnings of every authored SQL query the lints reach, in declaration order: a query
/// that did not parse or reads a variable (its SQL is a template until render) and a synthetic
/// one are skipped, as is a finding below warning severity.
pub fn query_lints(
    patch: &Patch,
    file: &ProjectPath,
    placed: &BTreeMap<ItemId, String>,
    queries: &BTreeMap<QueryId, QueryFacts>,
    meta: &MetaLint,
) -> Vec<Diagnostic> {
    let x = x_columns(patch, placed, queries);
    let mut out = Vec::new();
    for key in patch.body.queries.keys() {
        if key.starts_with('_') || key.starts_with('@') {
            continue;
        }
        let Ok(id) = query_id(key, file) else {
            continue;
        };
        let Some(QueryFacts {
            sql: SqlFacts::Analyzed(a),
            variable_deps,
            ..
        }) = queries.get(&id)
        else {
            continue;
        };
        if !variable_deps.is_empty() {
            continue;
        }
        let wanted = x.get(&id.to_string());
        let authored = patch.body.queries.get(key).and_then(Option::as_ref);
        let ignored = suppressed(key, authored, meta);
        for f in &a.findings {
            if let FindingKind::DateAsText { column } = &f.kind {
                if !wanted.is_some_and(|w| w.contains(&column.to_lowercase())) {
                    continue;
                }
            }
            let code = match f.kind {
                FindingKind::MissingJoinPredicate => codes::WARN_MISSING_JOIN_PREDICATE.id,
                FindingKind::Reaggregation => codes::WARN_REAGGREGATION.id,
                FindingKind::DateAsText { .. } => codes::WARN_DATE_AS_TEXT.id,
            };
            if ignored.contains("*") || ignored.contains(code) {
                continue;
            }
            if matches!(f.severity.as_str(), "error" | "warning") {
                out.push(warning(key, f));
            }
        }
    }
    out
}
