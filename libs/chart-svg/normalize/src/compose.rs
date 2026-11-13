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
use crate::sql::query_id;
use board_model::ids::{QueryId, QueryName};
use board_model::query::QueryAuthored;
use diagnostic::diag;
use indexmap::IndexMap;
use model_core::Tri;
use project::ProjectPath;
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
    let (mut plain, cached) = references(sql);
    plain.extend(cached);
    plain
}

/// The names of `{{ queries.NAME.cache }}` in `sql`.
fn cache_refs(sql: &str) -> Vec<String> {
    references(sql).1
}

/// The plain `{{ queries.NAME }}` names and the `{{ queries.NAME.cache }}` names of `sql`.
fn references(sql: &str) -> (Vec<String>, Vec<String>) {
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
    (plain, cached)
}

/// Python's `_validate_query_ref_name`: a composed name is a table alias.
fn reserved(name: &str) -> Result<(), HoistError> {
    match RESERVED.binary_search(&name.to_lowercase().as_str()) {
        Ok(_) => Err(HoistError::refused(diag::err_query_name_reserved_word(
            name,
        ))),
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

/// A `setup_sql` that composes a query is refused (`normalize_query`, before any dependency
/// check): it runs untranslated, ahead of every query.
fn setup_sql_composes(
    own: &ProjectPath,
    id: &QueryId,
    query: &Option<QueryAuthored>,
) -> Result<(), HoistError> {
    match query {
        Some(QueryAuthored::Sql(q)) if matches!(&q.setup_sql, Tri::Value(s) if s.contains("{{ queries.")) =>
        {
            let name = id.label(own);
            Err(HoistError::refused(diag::err_setup_sql_composes_query(
                name,
            )))
        }
        _ => Ok(()),
    }
}

/// Refuses the first composition problem among `queries`: a `setup_sql` that composes, every
/// composed name a usable alias, every `.cache` reference naming a defined query, then no query
/// reaching itself through the queries it composes,
/// each checked in `QueryId` order. A query is `own`'s or another file's; `scoped` holds the keys
/// of the queries an imported chart gathered, which compose the queries of their own file, the
/// rest those of `own` by name. Only queries that exist are followed, and an inline or option
/// query (an `@` position no author can write) is composed by none, so it closes no cycle.
pub(crate) fn refuse_cycles(
    own: &ProjectPath,
    queries: &IndexMap<String, Option<QueryAuthored>>,
    scoped: &BTreeSet<String>,
) -> Result<(), HoistError> {
    let mut sorted: BTreeMap<QueryId, (&String, &Option<QueryAuthored>)> = BTreeMap::new();
    for (key, query) in queries {
        sorted.insert(query_id(key, own)?, (key, query));
    }
    for (id, (_, query)) in &sorted {
        setup_sql_composes(own, id, query)?;
    }
    let mut deps: BTreeMap<&QueryId, Vec<QueryId>> = BTreeMap::new();
    for (id, (key, query)) in &sorted {
        let composed = match query {
            Some(QueryAuthored::Sql(sql)) => {
                composed_queries(sql.sql.as_deref().unwrap_or_default())
            }
            _ => Vec::new(),
        };
        composed.iter().try_for_each(|c| reserved(c))?;
        let home = if scoped.contains(*key) {
            id.file()
        } else {
            own
        };
        if let Some(QueryAuthored::Sql(sql)) = query {
            for name in cache_refs(sql.sql.as_deref().unwrap_or_default()) {
                let known = QueryName::try_from(name.clone())
                    .ok()
                    .and_then(|n| QueryId::new(home.clone(), n).ok())
                    .is_some_and(|c| sorted.contains_key(&c));
                if !known {
                    return Err(unknown_cache_ref(own, home, id, key, &name, &sorted));
                }
            }
        }
        if id.name().is_inline() {
            continue;
        }
        let found = composed
            .into_iter()
            .filter_map(|c| QueryId::new(home.clone(), QueryName::try_from(c).ok()?).ok())
            .collect();
        deps.insert(id, found);
    }
    let mut done: BTreeSet<&QueryId> = BTreeSet::new();
    for id in deps.keys() {
        visit(own, id, &deps, &mut Vec::new(), &mut done)?;
    }
    Ok(())
}

/// A `queries.NAME.cache` reference to no query: it names the holder, and lists the queries of
/// `home`, the file the reference resolves in. A query hoisted out of a chart has no `queries.<key>`
/// node to point at, so its refusal carries no path.
fn unknown_cache_ref(
    own: &ProjectPath,
    home: &ProjectPath,
    holder: &QueryId,
    key: &str,
    name: &str,
    queries: &BTreeMap<QueryId, (&String, &Option<QueryAuthored>)>,
) -> HoistError {
    let available: Vec<String> = queries
        .keys()
        .filter(|id| !id.name().is_inline() && id.file() == home)
        .map(|id| id.label(own))
        .collect();
    let refusal =
        diag::err_query_cache_ref_unknown(holder.label(own), name, available.join(", "));
    HoistError::refused(match holder.name().is_inline() {
        true => refusal,
        false => refusal.at_path(diagnostic::ModelPath(
            ["queries", key, "sql"].map(str::to_string).to_vec(),
        )),
    })
}

/// A depth-first walk: `path` is the queries being walked, `done` the ones walked.
fn visit<'a>(
    own: &ProjectPath,
    node: &'a QueryId,
    deps: &'a BTreeMap<&'a QueryId, Vec<QueryId>>,
    path: &mut Vec<&'a QueryId>,
    done: &mut BTreeSet<&'a QueryId>,
) -> Result<(), HoistError> {
    if let Some(start) = path.iter().position(|p| *p == node) {
        let named: Vec<String> = path
            .iter()
            .skip(start)
            .chain([&node])
            .map(|p| p.label(own))
            .collect();
        return Err(cycle(&named));
    }
    if !done.insert(node) {
        return Ok(());
    }
    path.push(node);
    for dep in deps.get(node).into_iter().flatten() {
        if let Some((known, _)) = deps.get_key_value(dep) {
            visit(own, known, deps, path, done)?;
        }
    }
    path.pop();
    Ok(())
}
