//! The variables each chart depends on, and each variable on, as Python's normalizer settles them
//! (`expand_chart_variable_dependencies`, `compute_variable_dependencies`). A chart reads the
//! variables its display text names and those of its query (and every query that one embeds as
//! `queries.<name>`), of each layer's query, and of each query its display text reads, then (for a
//! chart of `charts:`, not an inline one) the variables those depend on in turn; a variable reads those of its options query and of its
//! `enabled:` expression, limited to declared variables. This is what `chart_focus` keeps
//! variables by.
//!
//! Python widens a chart only against the variables of the board that declares it, and reads its
//! text queries against the file that declares it. This does both against the root board and
//! file, which is Python's for a chart the root declares and not for one declared below it or in
//! another file: `chart_focus` refuses those by name (`BoardIndex::declared_below`) rather than
//! approximate them.
//!
//! A display text or expression that does not parse reads no variable: rendering reports the
//! syntax error, as Python's extraction leaves it to.

use crate::sql::{query_id, QueryFacts};
use crate::tree::nested;
use board_model::ids::QueryId;
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
    /// The variables declared below the root board, which Python also lists on the root.
    pub nested: BTreeSet<String>,
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
fn text_variables(text: &str, kind: Kind) -> BTreeSet<String> {
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
fn query_variables(id: &QueryId, queries: &BTreeMap<QueryId, QueryFacts>) -> BTreeSet<String> {
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

/// `seed` and every variable those depend on, through `direct`.
fn closure(
    seed: BTreeSet<String>,
    direct: &BTreeMap<String, BTreeSet<String>>,
) -> BTreeSet<String> {
    let (mut out, mut pending) = (BTreeSet::new(), seed.into_iter().collect::<Vec<_>>());
    while let Some(name) = pending.pop() {
        if !out.insert(name.clone()) {
            continue;
        }
        pending.extend(direct.get(&name).into_iter().flatten().cloned());
    }
    out
}

impl Dependencies {
    /// A variable that depends on itself through others, which Python refuses.
    pub fn cycle(&self) -> Option<&String> {
        fn reaches<'a>(
            from: &'a String,
            to: &String,
            all: &'a BTreeMap<String, BTreeSet<String>>,
            seen: &mut BTreeSet<&'a String>,
        ) -> bool {
            all.get(from)
                .into_iter()
                .flatten()
                .any(|next| next == to || (seen.insert(next) && reaches(next, to, all, seen)))
        }
        self.variables
            .keys()
            .find(|name| reaches(name, name, &self.variables, &mut BTreeSet::new()))
    }
}

/// The dependencies of every chart and declared variable of the merged root `patch`, whose
/// charts and queries are hoisted and whose queries `queries` describes.
pub fn dependencies(
    patch: &Patch,
    file: &ProjectPath,
    queries: &BTreeMap<QueryId, QueryFacts>,
) -> Dependencies {
    let mut declared: Vec<(&String, Reads)> = patch
        .body
        .variables
        .iter()
        .map(|(n, v)| (n, reads_of_patch(v)))
        .collect();
    let at_root = declared.len();
    let below = patch.body.layout.as_ref().map(nested).unwrap_or_default();
    declared.extend(
        below
            .iter()
            .filter_map(|n| n.variables)
            .flat_map(|vars| vars.iter().map(|(n, v)| (n, reads_of_authored(v)))),
    );
    let nested = declared
        .iter()
        .skip(at_root)
        .map(|(name, _)| (*name).clone())
        .collect();
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
            for text in chart.display_texts() {
                seed.extend(text_variables(text, Kind::Label));
                ids.extend(
                    template::parse(text, Kind::Label)
                        .map(|i| i.queries)
                        .unwrap_or_default()
                        .iter()
                        .filter_map(|n| query_id(n, file).ok()),
                );
            }
            ids.extend(chart.hoisted_queries());
            for id in &ids {
                seed.extend(query_variables(id, queries));
            }
            // Python widens the charts of `charts:` and joins the inline ones after, unwidened.
            let widened = match key.starts_with('@') {
                true => seed,
                false => closure(seed, &direct),
            };
            Some((key.clone(), widened))
        })
        .collect();
    Dependencies {
        charts,
        variables: direct,
        nested,
    }
}
