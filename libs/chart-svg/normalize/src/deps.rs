//! The variables each chart depends on, and each variable on, as Python's normalizer settles them
//! (`expand_chart_variable_dependencies`, `compute_variable_dependencies`). A chart reads the
//! variables its display text names and those of its query (and every query that one embeds as
//! `queries.<name>`), of each layer's query, and of each query its display text reads, then (for a
//! chart of `charts:`, not an inline one) the variables those depend on in turn; a variable reads
//! those of its options query and of its `enabled:` expression, limited to declared variables. This is what `chart_focus` keeps
//! variables by.
//!
//! Python widens a chart only against the variables of the board that declares it, and reads its
//! text queries against the file that declares it. A chart widens through the variables of its
//! own board alone (the root's, for a chart of the root): a variable of another board is added by
//! name without following what it reads. A nested board reads its text queries from the root's
//! file, and a chart of another file reads its text's queries from that file.
//!
//! A display text or expression that does not parse reads no variable: rendering reports the
//! syntax error, as Python's extraction leaves it to.

use crate::sql::{query_id, QueryFacts};
use crate::tree::nested;
use crate::index::BoardIndex;
use board_model::ids::{ItemId, QueryId};
use board_model::variable::{Enabled, OptionsQuery, VariableAuthored, VariablePatch};
use model_core::Tri;
use project::ProjectPath;
use std::collections::{BTreeMap, BTreeSet};
use template::Kind;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// Variable names by chart (its hoisted key), and the variables each declared variable reads
/// directly (Python's `variable_dependencies` of a variable).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Dependencies {
    pub charts: BTreeMap<String, BTreeSet<String>>,
    pub variables: BTreeMap<String, BTreeSet<String>>,
}

/// What a variable declaration contributes: where its options come from and its `enabled:`.
struct Reads<'a> {
    options_query: Option<&'a OptionsQuery>,
    enabled: Option<&'a str>,
}

fn reads_of_patch(var: &VariablePatch) -> Reads<'_> {
    Reads {
        options_query: match &var.options {
            Tri::Value(o) => match &o.query {
                Tri::Value(q) => Some(q),
                Tri::Unset | Tri::Null => None,
            },
            Tri::Unset | Tri::Null => None,
        },
        enabled: match &var.enabled {
            Tri::Value(Enabled::Expression(e)) => Some(e),
            _ => None,
        },
    }
}

fn reads_of_authored(var: &VariableAuthored) -> Reads<'_> {
    Reads {
        options_query: match &var.options {
            Tri::Value(o) => match &o.query {
                Tri::Value(q) => Some(q),
                Tri::Unset | Tri::Null => None,
            },
            Tri::Unset | Tri::Null => None,
        },
        enabled: match &var.enabled {
            Tri::Value(Enabled::Expression(e)) => Some(e),
            _ => None,
        },
    }
}

/// The variables a template reads; text that does not parse reads none.
pub(crate) fn text_variables(text: &str, kind: Kind) -> BTreeSet<String> {
    if !text.contains("{{") && !text.contains("{%") {
        return BTreeSet::new();
    }
    template::parse(text, kind)
        .map(|info| info.variables)
        .unwrap_or_default()
}

/// An `enabled:` expression reads the variables its template names: a bare name is no template,
/// and Python reads none from it.
fn expression_variables(expr: &str) -> BTreeSet<String> {
    text_variables(expr, Kind::Label)
}

/// The variables of `id`'s SQL and of every query it embeds.
pub fn query_variables(id: &QueryId, queries: &BTreeMap<QueryId, QueryFacts>) -> BTreeSet<String> {
    let (mut seen, mut pending, mut out) = (BTreeSet::new(), vec![id.clone()], BTreeSet::new());
    while let Some(current) = pending.pop() {
        let Some(facts) = queries.get(&current) else {
            continue;
        };
        if !seen.insert(current.clone()) {
            continue;
        }
        out.extend(facts.variable_deps.iter().cloned());
        pending.extend(
            facts
                .embeds
                .iter()
                .filter_map(|name| query_id(name, current.file()).ok()),
        );
    }
    out
}

impl Dependencies {
    /// The first cycle among the variables, as the names around it with the first repeated at
    /// the end, which Python refuses.
    pub fn cycle(&self) -> Option<Vec<String>> {
        fn walk<'a>(
            node: &'a String,
            path: &mut Vec<&'a String>,
            seen: &mut BTreeSet<&'a String>,
            all: &'a BTreeMap<String, BTreeSet<String>>,
        ) -> Option<Vec<String>> {
            if let Some(at) = path.iter().position(|p| *p == node) {
                let mut around: Vec<String> =
                    path.iter().skip(at).map(|p| (*p).clone()).collect();
                around.push(node.clone());
                return Some(around);
            }
            if !seen.insert(node) {
                return None;
            }
            path.push(node);
            let found = all
                .get(node)
                .into_iter()
                .flatten()
                .find_map(|next| walk(next, path, seen, all));
            path.pop();
            found
        }
        let mut seen = BTreeSet::new();
        self.variables
            .keys()
            .find_map(|name| walk(name, &mut Vec::new(), &mut seen, &self.variables))
    }
}

/// A chart widens through the variables of the board that declares it alone: a variable declared
/// elsewhere is added by name, and what it reads is not followed (Python's
/// `get_transitive_variable_dependencies` stops at a variable outside the dict it is handed).
fn closure_within(
    seed: BTreeSet<String>,
    direct: &BTreeMap<String, BTreeSet<String>>,
    declares: impl Fn(&str) -> bool,
) -> BTreeSet<String> {
    let (mut out, mut seen) = (BTreeSet::new(), BTreeSet::new());
    let mut pending: Vec<String> = seed.into_iter().collect();
    while let Some(next) = pending.pop() {
        out.insert(next.clone());
        if !declares(&next) || !seen.insert(next.clone()) {
            continue;
        }
        pending.extend(direct.get(&next).into_iter().flatten().cloned());
    }
    out
}

/// The dependencies of every chart and declared variable of the merged root `patch`, whose
/// charts and queries are hoisted and whose queries `queries` describes, and whose nested boards
/// `index` places.
pub fn dependencies(
    patch: &Patch,
    file: &ProjectPath,
    queries: &BTreeMap<QueryId, QueryFacts>,
    index: &BoardIndex,
) -> Dependencies {
    let mut declared: Vec<(&String, Reads)> = patch
        .body
        .variables
        .iter()
        .map(|(n, v)| (n, reads_of_patch(v)))
        .collect();
    let below = patch.body.layout.as_ref().map(nested).unwrap_or_default();
    declared.extend(
        below
            .iter()
            .filter_map(|n| n.variables)
            .flat_map(|vars| vars.iter().map(|(n, v)| (n, reads_of_authored(v)))),
    );
    let names: BTreeSet<&String> = declared.iter().map(|(n, _)| *n).collect();
    let direct: BTreeMap<String, BTreeSet<String>> = declared
        .iter()
        .map(|(name, reads)| {
            let mut deps = reads
                .options_query
                .and_then(|q| {
                    let text = match q {
                        OptionsQuery::Name(n) => n.as_str(),
                        OptionsQuery::Sql(_) => return None,
                    };
                    query_id(text.strip_prefix("queries.").unwrap_or(text), file).ok()
                })
                .map(|id| query_variables(&id, queries))
                .unwrap_or_default();
            deps.extend(reads.enabled.map(expression_variables).unwrap_or_default());
            deps.retain(|d| names.contains(d) && d != *name);
            ((*name).clone(), deps)
        })
        .collect();
    let charts = patch
        .body
        .charts
        .iter()
        .filter_map(|(key, chart)| {
            let chart = chart.as_ref()?;
            let mut seed = BTreeSet::new();
            let mut ids: Vec<QueryId> = Vec::new();
            let imported = index.text_queries.get(key);
            for text in chart.display_texts() {
                seed.extend(text_variables(text, Kind::Label));
                let read = template::parse(text, Kind::Label)
                    .map(|i| i.queries)
                    .unwrap_or_default();
                ids.extend(read.iter().filter_map(|n| match imported {
                    Some(own) => own.get(n).cloned(),
                    None => query_id(n, file).ok(),
                }));
            }
            ids.extend(chart.hoisted_queries());
            for id in &ids {
                seed.extend(query_variables(id, queries));
            }
            // Python widens the charts of `charts:` and joins the inline ones after, unwidened.
            let widened = match key.starts_with('@') {
                true => seed,
                false => {
                    // The root's own variables, for a chart of the root (or an imported one).
                    let board = index.chart_boards.get(key).filter(|b| **b != ItemId::root());
                    closure_within(seed, &direct, |name| index.variable_boards.get(name) == board)
                }
            };
            Some((key.clone(), widened))
        })
        .collect();
    Dependencies {
        charts,
        variables: direct,
    }
}
