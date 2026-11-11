//! Query composition (`{{ queries.X }}` and `{{ queries.X.cache }}` in a query's SQL): the names a
//! SQL text composes, and the pass that refuses what Python's `detect_query_dependencies` refuses:
//! a composed name that cannot be a table alias (a SQL reserved word) and a composition cycle.
//!
//! Python reads these references with two regexes over the raw text, so the cycle is judged on the
//! names as written. An imported chart's queries are the exception: Python rewrites their
//! references to the registry keys of the file's queries before it checks, so [`refuse_cycles`]
//! is told which keys those are and reads their references as the file's own.

use crate::hoist::HoistError;
use crate::slug::{is_space, is_word};
use board_model::query::QueryAuthored;
use diagnostic::diag;
use indexmap::IndexMap;
use model_core::Tri;
use std::collections::{BTreeMap, BTreeSet};

/// SQL words that cannot be a table alias on every engine (Python's `_SQL_RESERVED_WORDS`),
/// sorted.
const RESERVED: &[&str] = &[
    "all",
    "and",
    "any",
    "array",
    "as",
    "asc",
    "between",
    "by",
    "case",
    "cast",
    "check",
    "collate",
    "column",
    "constraint",
    "create",
    "cross",
    "current",
    "default",
    "delete",
    "desc",
    "distinct",
    "drop",
    "else",
    "end",
    "except",
    "exists",
    "false",
    "fetch",
    "for",
    "foreign",
    "from",
    "full",
    "group",
    "having",
    "in",
    "inner",
    "insert",
    "intersect",
    "into",
    "is",
    "join",
    "left",
    "like",
    "limit",
    "natural",
    "not",
    "null",
    "offset",
    "on",
    "or",
    "order",
    "outer",
    "primary",
    "qualify",
    "references",
    "right",
    "select",
    "set",
    "some",
    "table",
    "then",
    "to",
    "true",
    "union",
    "unique",
    "update",
    "using",
    "values",
    "when",
    "where",
    "window",
    "with",
];

/// The queries a SQL text composes, in the order Python's dependency scan finds them:
/// `{{ queries.NAME }}` first, then `{{ queries.NAME.cache }}` (`_QUERY_REF_RE`, `_CACHE_REF_RE`).
pub(crate) fn composed_queries(sql: &str) -> Vec<String> {
    let mut plain = Vec::new();
    let mut cached = Vec::new();
    // Every `{{` opens a candidate, as the regexes try each position (`{{{ queries.a }}` composes).
    for (at, _) in sql.match_indices('{') {
        let Some(tag) = sql.get(at..).and_then(|t| t.strip_prefix("{{")) else {
            continue;
        };
        let Some((inside, _)) = tag.split_once("}}") else {
            continue;
        };
        let Some(rest) = inside.trim_matches(is_space).strip_prefix("queries.") else {
            continue;
        };
        let end = rest.find(|c: char| !is_word(c)).unwrap_or(rest.len());
        let (name, tail) = rest.split_at(end);
        let tail = tail.trim_start_matches(is_space);
        if name.is_empty() {
            continue;
        }
        match tail.strip_prefix('.').map(|t| t.trim_matches(is_space)) {
            None if tail.is_empty() => plain.push(name.to_string()),
            Some("cache") => cached.push(name.to_string()),
            _ => {}
        }
    }
    plain.extend(cached);
    plain
}

/// Python's `_validate_query_ref_name`: a composed name is a table alias.
fn reserved(name: &str) -> Result<(), HoistError> {
    match RESERVED.binary_search(&name.to_lowercase().as_str()) {
        Ok(_) => Err(HoistError::refused(diag::err_query_name_reserved_word(name))),
        Err(_) => Ok(()),
    }
}

fn cycle(path: &[String]) -> HoistError {
    HoistError::Cycle(Box::new(diag::err_jinja_error(format!(
        "Circular query dependency detected: {}",
        path.join(" -> ")
    ))))
}

/// The check Python's walk over an imported file makes of each query it reads, alone: the names it
/// composes are aliases, and none is the query itself.
pub(crate) fn check_composed(name: &str, composed: &[String]) -> Result<(), HoistError> {
    composed.iter().try_for_each(|c| reserved(c))?;
    match composed.iter().any(|c| c == name) {
        true => Err(cycle(&[name.to_string(), name.to_string()])),
        false => Ok(()),
    }
}

/// Python's registry key for a query another file declares (`_cross_board_query_key`); a plain
/// name is its own key.
pub(crate) fn registry_key(key: &str) -> String {
    match key.split_once('#') {
        Some((file, name)) => {
            let file: String = file
                .chars()
                .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
                .collect();
            format!("_ximport_{file}_{name}")
        }
        None => key.to_string(),
    }
}

/// The name Python registers a hoisted query under (`synthetic_query_name`): an inline chart
/// query is `_inline_query_<chart>`, a layer's `_inline_query_layer_<chart>_<i>`, a variable's
/// options query `_var_options_<variable>`. A named query keeps its key. `None` for an inline
/// query of a chart the layout places: Python names it after a title-derived chart id.
fn python_name(key: &str) -> Option<String> {
    if !key.starts_with('@') {
        return Some(key.to_string());
    }
    if let Some((_, var)) = key
        .strip_suffix(".options.query")
        .and_then(|k| k.rsplit_once("variables."))
    {
        return Some(format!("_var_options_{var}"));
    }
    let chart = key
        .strip_prefix("@charts.")
        .filter(|c| !c.starts_with('@'))?;
    match chart.rsplit_once(".layers[") {
        Some((chart, index)) => Some(format!(
            "_inline_query_layer_{chart}_{}",
            index.strip_suffix("].query")?
        )),
        None => Some(format!("_inline_query_{}", chart.strip_suffix(".query")?)),
    }
}

/// Python refuses a `setup_sql` that composes a query (`normalize_query`, before any dependency
/// check): it runs untranslated, ahead of every query.
fn setup_sql_composes(key: &str, query: &Option<QueryAuthored>) -> Result<(), HoistError> {
    match query {
        Some(QueryAuthored::Sql(q)) if matches!(&q.setup_sql, Tri::Value(s) if s.contains("{{ queries.")) =>
        {
            let name = registry_key(key);
            Err(HoistError::refused(diag::err_setup_sql_composes_query(name)))
        }
        _ => Ok(()),
    }
}

fn not_yet(key: &str, composed: &str) -> HoistError {
    HoistError::NotYet(format!(
        "query '{key}' composes '{composed}', which may be the generated name of an inline \
         chart the layout places; those names are not ported yet"
    ))
}

/// Refuses the first composition problem among `queries` (Python's `detect_query_dependencies`):
/// a `setup_sql` that composes, every composed name a usable alias, then no query reaching itself
/// through the queries it composes. Queries are known by the name Python registers them under, so
/// a chart's inline query that composes its own `_inline_query_<chart>` is the self-cycle it is
/// there. Only queries that exist are followed. `scoped` holds the keys of the queries an imported
/// chart gathered: they compose the queries of their own file (`<file>#<name>`), the rest a query
/// by its name. Python's registry holds the scoped ones first, so a cycle among them is the one
/// it reports when several exist. `slot` says the layout places another board file, at any depth:
/// Python then checks the inline and option queries as nodes of the same graph.
pub(crate) fn refuse_cycles(
    queries: &IndexMap<String, Option<QueryAuthored>>,
    scoped: &BTreeSet<String>,
    slot: bool,
) -> Result<(), HoistError> {
    queries
        .iter()
        .try_for_each(|(key, query)| setup_sql_composes(key, query))?;
    let order: Vec<&String> = queries
        .keys()
        .filter(|k| scoped.contains(*k))
        .chain(queries.keys().filter(|k| !scoped.contains(*k)))
        .collect();
    let mut deps: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut names: Vec<String> = Vec::new();
    let mut alone: Vec<String> = Vec::new();
    let mut promoted: Vec<(String, Vec<String>)> = Vec::new();
    // The names Python generates for the inline and option queries it can name.
    let generated: BTreeSet<String> = queries
        .keys()
        .filter(|k| k.starts_with('@'))
        .filter_map(|k| python_name(k))
        .collect();
    let unnamed = queries.keys().any(|k| python_name(k).is_none());
    for key in order {
        let composed = match queries.get(key) {
            Some(Some(QueryAuthored::Sql(sql))) => {
                composed_queries(sql.sql.as_deref().unwrap_or_default())
            }
            _ => Vec::new(),
        };
        composed.iter().try_for_each(|c| reserved(c))?;
        let Some(name) = python_name(key) else {
            // Its name is unknown, so a composed `_inline_query_*` may be itself, declared or not.
            if let Some(c) = composed.iter().find(|c| c.starts_with("_inline_query_")) {
                return Err(not_yet(key, c));
            }
            continue;
        };
        // With a chart named after its title in the layout and another board file placed, a
        // query composing a generated name nothing here accounts for may close a loop through
        // that chart's inline query.
        if unnamed && slot {
            if let Some(c) = composed
                .iter()
                .find(|c| c.starts_with("_inline_query_") && !generated.contains(*c))
            {
                return Err(not_yet(key, c));
            }
        }
        // Python checks the declared registry before it promotes an inline or option query. Then,
        // without another board file, it checks each promoted query alone: only a reference to its
        // own generated name is a cycle, and it shares no node with a declared query of that name.
        // With one, it checks the registry again, a promoted query replacing a declared one.
        if key.starts_with('@') {
            if slot {
                promoted.push((name, composed));
            } else if composed.contains(&name) {
                alone.push(name);
            }
            continue;
        }
        let file = key.split_once('#').filter(|_| scoped.contains(key));
        deps.insert(
            name.clone(),
            composed
                .into_iter()
                .map(|c| match file {
                    Some((file, _)) => format!("{file}#{c}"),
                    None => c,
                })
                .collect(),
        );
        names.push(name);
    }
    let mut done: BTreeSet<&str> = BTreeSet::new();
    for name in &names {
        visit(name, &deps, &mut Vec::new(), &mut done)?;
    }
    for (name, composed) in promoted {
        if deps.insert(name.clone(), composed).is_none() {
            names.push(name);
        }
    }
    let mut done: BTreeSet<&str> = BTreeSet::new();
    for name in &names {
        visit(name, &deps, &mut Vec::new(), &mut done)?;
    }
    match alone.first() {
        Some(name) => Err(cycle(&[name.clone(), name.clone()])),
        None => Ok(()),
    }
}

/// Python's depth-first `_detect_circular`: `path` is the queries being walked, `done` the ones
/// walked.
fn visit<'a>(
    node: &'a str,
    deps: &'a BTreeMap<String, Vec<String>>,
    path: &mut Vec<&'a str>,
    done: &mut BTreeSet<&'a str>,
) -> Result<(), HoistError> {
    if let Some(start) = path.iter().position(|p| *p == node) {
        let named: Vec<String> = path
            .iter()
            .skip(start)
            .chain([&node])
            .map(|p| registry_key(p))
            .collect();
        return Err(cycle(&named));
    }
    if !done.insert(node) {
        return Ok(());
    }
    path.push(node);
    for dep in deps.get(node).into_iter().flatten() {
        if let Some((known, _)) = deps.get_key_value(dep.as_str()) {
            visit(known, deps, path, done)?;
        }
    }
    path.pop();
    Ok(())
}
