//! Python's `validate_variable_references` (normalize step 10): a variable no board declares,
//! named by a chart's `title`, `subtitle` or a KPI's `label`, an inline chart query's SQL, a
//! named query's SQL, or a variable's own inline options query. One `ERR-UNKNOWN-VARIABLE` per
//! undeclared name and owner, names sorted, charts first, then queries, then variables.
//!
//! What a template reads is [`crate::deps`]' (`jinja2.meta.find_undeclared_variables` less the
//! engine's helpers). A `text:` row, a callout's message, a KPI's support label and a layer's
//! label are not scanned, as in Python. The declared set is every variable of the root and of
//! each nested board, with the tab and details variables layout generates.

use crate::compile::CompileError;
use crate::deps::text_variables;
use crate::index::{BoardIndex, LayoutFacts};
use crate::sql::QueryFacts;
use board_model::chart::{ChartAuthored, ChartQuery};
use board_model::ids::QueryId;
use diagnostic::{diag, py_str_repr, Diagnostic};
use model_core::{first_set, Tri};
use std::collections::{BTreeMap, BTreeSet};
use template::Kind;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

const REFERENCE_URL: &str = "https://docs.dbtcharts.com/reference/yaml-reference/";

/// The texts Python's `_chart_direct_deps` reads: `title` and `subtitle`, a KPI's `label`.
fn direct_texts(chart: &ChartAuthored) -> Vec<&str> {
    use board_model::facet::DisplayText;
    fn text(t: Tri<&DisplayText>) -> Option<&str> {
        first_set([t]).map(DisplayText::as_str)
    }
    match chart {
        ChartAuthored::Kpi(c) => text(c.label.as_ref()).into_iter().collect(),
        ChartAuthored::Callout(c) => text(c.title.as_ref()).into_iter().collect(),
        _ => chart.shared().map_or_else(Vec::new, |s| {
            [text(s.title.as_ref()), text(s.subtitle.as_ref())]
                .into_iter()
                .flatten()
                .collect()
        }),
    }
}

fn own_query(chart: &ChartAuthored) -> Option<QueryId> {
    match chart.clone().query_mut() {
        Some(Tri::Value(ChartQuery::Hoisted(id))) => Some(id.clone()),
        _ => None,
    }
}

fn finding(
    surface: &str,
    owner: &str,
    var: &str,
    declared: &BTreeSet<String>,
    query_names: &[String],
) -> Diagnostic {
    let pool: Vec<String> = declared
        .iter()
        .cloned()
        .chain(query_names.iter().map(|n| format!("queries.{n}")))
        .collect();
    let hint = match model_core::nearest(var, &pool) {
        Some(s) => format!("Did you mean {}? See: {REFERENCE_URL}", py_str_repr(&s)),
        None => format!("See: {REFERENCE_URL}"),
    };
    diag::err_unknown_variable(surface, owner, var).with_hint(hint)
}

/// Where a hoisted variable query was written: the variable's name and the slot (`options` or
/// the variable's own `query`), and whether the root board declares it. `None` for any other
/// position (a chart's inline query).
fn variable_slot(name: &str) -> Option<(bool, &str, &'static str)> {
    let (root, rest) = match name.strip_prefix("@variables.") {
        Some(rest) => (true, rest),
        None => (false, name.split_once(".variables.")?.1),
    };
    let (var, slot) = rest.split_once('.')?;
    match slot {
        "options.query" => Some((root, var, "var_options")),
        "query" => Some((root, var, "var_query")),
        _ => None,
    }
}

/// The refusals of `patch`, none when every reference resolves. Python names a root variable's
/// own query under the variable; below a nested board it has no registry yet, so the query is
/// named as its synthetic `_var_options_<name>` / `_var_query_<name>` and reported as a query.
pub(crate) fn check(
    patch: &Patch,
    queries: &BTreeMap<QueryId, QueryFacts>,
    index: &BoardIndex,
    declared: &BTreeSet<String>,
    layout: &LayoutFacts,
) -> Result<Vec<Diagnostic>, CompileError> {
    let mut declared = declared.clone();
    declared.extend(layout.tabs.values().map(|t| t.variable.clone()));
    declared.extend(layout.details.values().map(|d| d.variable.clone()));
    let query_names: Vec<String> = queries
        .keys()
        .map(|id| id.name().as_str())
        .filter(|n| !n.starts_with('@'))
        .map(str::to_string)
        .collect();
    let unknown = |deps: &BTreeSet<String>| -> Vec<String> {
        deps.iter()
            .filter(|d| !declared.contains(*d))
            .cloned()
            .collect()
    };
    let mut out = Vec::new();
    for (key, chart) in &patch.body.charts {
        let Some(chart) = chart else { continue };
        let owner = crate::compile::public_id(index, key)?;
        let mut deps: BTreeSet<String> = direct_texts(chart)
            .into_iter()
            .flat_map(|t| text_variables(t, Kind::Label))
            .collect();
        if let Some(id) = own_query(chart).filter(|id| id.name().is_inline()) {
            // A values or schema query has no SQL facts: it reads no variable.
            if let Some(facts) = queries.get(&id) {
                deps.extend(facts.variable_deps.iter().cloned());
            }
        }
        out.extend(
            unknown(&deps)
                .iter()
                .map(|v| finding("Chart", &owner, v, &declared, &query_names)),
        );
    }
    for (id, facts) in queries {
        let name = id.name().as_str();
        let (surface, owner) = match (name.starts_with('@'), variable_slot(name)) {
            (false, _) => ("Query", name.to_string()),
            (true, Some((true, var, _))) => ("Variable", var.to_string()),
            (true, Some((false, var, slot))) => ("Query", format!("_{slot}_{var}")),
            (true, None) => continue,
        };
        out.extend(
            unknown(&facts.variable_deps)
                .iter()
                .map(|v| finding(surface, &owner, v, &declared, &query_names)),
        );
    }
    Ok(out)
}
