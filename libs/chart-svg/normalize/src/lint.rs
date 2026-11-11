//! Python's board warnings (`compile/validate/board_warnings.py`), read off the merged root: the
//! findings that do not refuse the board. Each detector keeps Python's order and wording; the
//! live gate (`tests/test_engine_compile_live.py`) pins every code listed in its `PORTED`.
//!
//! Python runs them on the compiled board, after `chart_focus` has narrowed it. The two
//! unreferenced detectors read what the board was before (Python records it in `board.meta`), so
//! they judge the board as written; the others judge what focus left, which for a title is none.

use crate::board_warnings;
use crate::sql::QueryFacts;
use crate::tree::{nested, Nested};
use board_model::board::value::VisibleAuthored;
use board_model::ids::{ItemId, QueryId};
use board_model::variable::{Enabled, OptionsQuery, VariableAuthored, VariablePatch};
use diagnostic::{diag, Diagnostic};
use model_core::Tri;
use project::ProjectPath;
use std::collections::{BTreeMap, BTreeSet};
use template::Kind;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// A query the way Python's registry keys it: its name in the board's own file, else
/// `<file>#<name>`.
fn key(id: &QueryId, file: &ProjectPath) -> String {
    match id.file() == file {
        true => id.name().as_str().to_string(),
        false => id.to_string(),
    }
}

fn bare(name: &str) -> &str {
    name.strip_prefix("queries.").unwrap_or(name)
}

/// The queries a markdown or display text reads as `queries.<name>`; text that does not parse
/// reads none.
fn text_queries(text: &str, kind: Kind) -> BTreeSet<String> {
    match text.contains("queries") {
        true => template::parse(text, kind)
            .map(|info| info.queries)
            .unwrap_or_default(),
        false => BTreeSet::new(),
    }
}

/// The names of the queries one variable declaration reads: its options and its `enabled:` probe.
fn variable_reads(options: Option<&OptionsQuery>, enabled: Option<&Enabled>) -> Vec<String> {
    let options = match options {
        Some(OptionsQuery::Name(q)) => Some(bare(q.as_str()).to_string()),
        Some(OptionsQuery::Sql(_)) | None => None,
    };
    let probe = match enabled {
        Some(Enabled::Probe(p)) => Some(bare(&p.query).to_string()),
        _ => None,
    };
    options.into_iter().chain(probe).collect()
}

fn patch_variable_reads(var: &VariablePatch) -> Vec<String> {
    let options = match &var.options {
        Tri::Value(o) => match &o.query {
            Tri::Value(q) => Some(q),
            _ => None,
        },
        _ => None,
    };
    variable_reads(options, enabled(&var.enabled))
}

fn authored_variable_reads(var: &VariableAuthored) -> Vec<String> {
    let options = match &var.options {
        Tri::Value(o) => match &o.query {
            Tri::Value(q) => Some(q),
            _ => None,
        },
        _ => None,
    };
    variable_reads(options, enabled(&var.enabled))
}

fn enabled(slot: &Tri<Enabled>) -> Option<&Enabled> {
    match slot {
        Tri::Value(e) => Some(e),
        _ => None,
    }
}

fn probe_query(visible: Option<&VisibleAuthored>) -> Option<&str> {
    match visible {
        Some(VisibleAuthored::Probe(p)) => p.query.as_deref(),
        _ => None,
    }
}

fn filled(text: Option<&str>) -> Option<&str> {
    text.filter(|t| !t.is_empty())
}

fn root_text(slot: &Tri<board_model::facet::Markdown>) -> Option<&str> {
    match slot {
        Tri::Value(t) => Some(t.as_str()),
        _ => None,
    }
}

/// The queries nothing reads, as Python's `referenced_query_names` counts a read: a chart, a
/// layer, a variable's options or `enabled:` probe, a layout item's `visible:` probe, a board's
/// title or text (tab panes included), another query's `{{ queries.<name> }}`.
fn referenced_queries(
    patch: &Patch,
    file: &ProjectPath,
    placed: &BTreeSet<&str>,
    below: &[Nested<'_>],
    queries: &BTreeMap<QueryId, QueryFacts>,
) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let root = [
        match &patch.body.title {
            Tri::Value(t) => Some(t.as_str()),
            _ => None,
        },
        root_text(&patch.body.text),
    ];
    let said = below.iter().flat_map(|n| [n.title, n.text]);
    for text in root.into_iter().chain(said).flatten() {
        names.extend(text_queries(text, Kind::Markdown));
    }
    for (name, chart) in &patch.body.charts {
        let Some(chart) = chart else { continue };
        names.extend(chart.hoisted_queries().iter().map(|id| key(id, file)));
        for text in chart.display_texts() {
            names.extend(text_queries(text, Kind::Label));
        }
        // A chart's `visible:` is its placement's, so an unplaced chart has none.
        if placed.contains(name.as_str()) {
            let visible = chart.base().and_then(|b| match &b.visible {
                Tri::Value(v) => Some(v),
                _ => None,
            });
            names.extend(probe_query(visible).map(|q| bare(q).to_string()));
        }
    }
    names.extend(
        below
            .iter()
            .filter_map(|n| probe_query(n.visible))
            .map(|q| bare(q).to_string()),
    );
    names.extend(patch.body.variables.values().flat_map(patch_variable_reads));
    names.extend(
        below
            .iter()
            .filter_map(|n| n.variables)
            .flat_map(|vars| vars.values().flat_map(authored_variable_reads)),
    );
    for facts in queries.values() {
        names.extend(facts.embeds.iter().cloned());
    }
    names
}

/// A board whose whole content is one chart that titles itself too: the board's title and the
/// chart's say the same thing twice. Root only; prose or a titled section anywhere in the tree
/// separates the two, and a KPI or callout heads itself otherwise.
fn single_chart_redundant_title(
    patch: &Patch,
    placed: &BTreeMap<ItemId, String>,
    below: &[Nested<'_>],
) -> Option<Diagnostic> {
    let title = filled(match &patch.body.title {
        Tri::Value(t) => Some(t.as_str()),
        _ => None,
    })?;
    let [chart] = placed.values().collect::<Vec<_>>()[..] else {
        return None;
    };
    let prose = filled(root_text(&patch.body.text)).is_some()
        || below.iter().any(|n| filled(n.text).is_some());
    if prose || below.iter().any(|n| filled(n.title).is_some()) {
        return None;
    }
    let chart = patch.body.charts.get(chart)?.as_ref()?;
    let chart_title = filled(chart.title())?;
    Some(
        diag::warn_single_chart_redundant_title(title, chart_title)
            .at_path(board_warnings::path(&["title"])),
    )
}

/// Every warning, in Python's order. `focused` is whether `chart_focus` narrowed the board, which
/// leaves it no title; `placed` is where the charts sat as written.
pub fn lint(
    patch: &Patch,
    file: &ProjectPath,
    placed: &BTreeMap<ItemId, String>,
    prose_imports: &BTreeSet<ItemId>,
    focused: bool,
    queries: &BTreeMap<QueryId, QueryFacts>,
    facts: &board_warnings::Facts,
) -> Vec<Diagnostic> {
    let below = patch.body.layout.as_ref().map(nested).unwrap_or_default();
    let mut out = Vec::new();
    let double = board_warnings::double_header(patch);
    let in_slot = double.is_some() && filled(root_text(&patch.body.text)).is_none();
    out.extend(double);
    out.extend(board_warnings::untitled_sections(patch, in_slot));
    if !focused {
        out.extend(single_chart_redundant_title(patch, placed, &below));
    }
    out.extend(board_warnings::adjacent_text_rows(patch, prose_imports));
    let placed: BTreeSet<&str> = placed.values().map(String::as_str).collect();
    let mut orphans: Vec<&str> = patch
        .body
        .charts
        .keys()
        .map(String::as_str)
        .filter(|id| !placed.contains(id))
        .collect();
    orphans.sort_unstable();
    out.extend(orphans.into_iter().map(diag::warn_unreferenced_chart));
    let referenced = referenced_queries(patch, file, &placed, &below, queries);
    let mut unread: Vec<&str> = patch
        .body
        .queries
        .keys()
        .map(String::as_str)
        .filter(|name| {
            !name.starts_with('_') && !name.starts_with('@') && !referenced.contains(*name)
        })
        .collect();
    unread.sort_unstable();
    out.extend(unread.into_iter().map(|name| {
        diag::warn_unreferenced_query(name).at_path(board_warnings::path(&["queries", name]))
    }));
    let leaves = board_warnings::Leaves::of(patch);
    out.extend(board_warnings::retired_ignore_codes(
        &leaves,
        facts.public_ids,
    ));
    let theme = board_warnings::theme_leaves(facts.theme);
    out.extend(board_warnings::axis_align_discarded(
        patch,
        &leaves,
        &theme,
        facts.public_ids,
    ));
    out.extend(board_warnings::unused_format_names(
        &facts.own_format_names,
        &leaves,
    ));
    out
}
