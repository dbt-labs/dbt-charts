//! The engine's style seam from a compiled board: the one place `bind::board::ChartStyles` is
//! assembled from what normalize resolved: each placement's total style, own tier and rhythm slot
//! (`CompiledBoard.placed`, `placed_own` and `LayoutFacts.chart_facts`) and each chart's public id
//! (`Structure.index.public_ids`), and each placement's placing scope, the root's style or the
//! innermost nested board's from `CompiledBoard.nested`. It lives here because `bind` cannot depend on `normalize`, so
//! `engine` depends on it.

use bind::board::{ChartStyles, DetailsFacts, TabsFacts};
use diagnostic::{diag, Diagnostic};
use board_model::ids::ChartId;
use normalize::CompiledBoard;
use std::collections::BTreeMap;
use std::sync::Arc;
use board_model::style::style::Style;
use model_core::Resolved;

/// Each placement's total family style, own tier, scope, rhythm slot and public chart ids, keyed by position, and each
/// tabs layout's and details board's facts.
pub fn chart_styles(compiled: &CompiledBoard) -> Result<ChartStyles, Diagnostic> {
    let layout = &compiled.structure.layout;
    let missing = |what: &str, at: &str| diag::err_internal(format!("the compiled board holds no {what} for the placement {at}"));
    let chart_boards = compiled.structure.index.chart_boards.iter().filter(|(key, _)| compiled.structure.index.declared_below.contains(*key)).map(|(key, board)| Ok((ChartId::try_from(key.clone()).map_err(|e| diag::err_internal(format!("{e:?}")))?, board.clone()))).collect::<Result<_, Diagnostic>>()?;
    let chart_variables = compiled
        .structure
        .index
        .public_ids
        .keys()
        .map(|id| {
            let names = compiled.structure.deps.charts.get(id.as_str()).ok_or_else(|| diag::err_internal(format!("the chart {id} has no variable dependencies")))?;
            Ok((id.clone(), names.clone()))
        })
        .collect::<Result<_, Diagnostic>>()?;
    let mut styles = ChartStyles { chart_variables, public_ids: compiled.structure.index.public_ids.clone(), chart_boards, ..ChartStyles::default() };
    // Every placement in one scope shares one copy of its style.
    let mut scopes: BTreeMap<Option<&str>, Arc<Resolved<Style>>> = BTreeMap::new();
    for at in layout.charts.keys() {
        let placed = compiled.placed.get(at.as_str()).ok_or_else(|| missing("placed style", at.as_str()))?;
        let own = compiled.placed_own.get(at.as_str()).ok_or_else(|| missing("own style", at.as_str()))?;
        styles.placed.insert(at.clone(), (*placed.total).clone());
        styles.own.insert(at.clone(), own.clone());
        let nested = compiled.nested.iter().filter(|(key, _)| at.as_str().strip_prefix(key.as_str()).is_some_and(|rest| rest.starts_with('.')));
        let scope = nested.max_by_key(|(key, _)| key.len()).map(|(key, _)| key.as_str());
        let shared = scopes.entry(scope).or_insert_with(|| Arc::new(scope.map_or(&compiled.style, |key| &compiled.nested[key]).clone()));
        styles.scope.insert(at.clone(), shared.clone());
    }
    for (at, facts) in &layout.chart_facts {
        if let Some(slot) = facts.rhythm_slot {
            styles.rhythm.insert(at.clone(), slot);
        }
    }
    styles.tabs = layout.tabs.iter().map(|(at, t)| (at.clone(), TabsFacts { variable: t.variable.clone(), slugs: t.slugs.clone(), default_tab: t.default_tab })).collect();
    for key in compiled.structure.policies.keys() {
        let id = normalize::sql::query_id(key, &compiled.structure.file).map_err(|e| diag::err_internal(format!("the root's query {key}: {e:?}")))?;
        styles.root_queries.insert(key.clone(), id);
    }
    for (key, read) in &compiled.structure.index.text_queries {
        let id = ChartId::try_from(key.clone()).map_err(|e| diag::err_internal(format!("{e:?}")))?;
        styles.text_queries.insert(id, read.clone());
    }
    styles.details = layout.details.iter().map(|(at, d)| (at.clone(), DetailsFacts { variable: d.variable.clone(), expanded: d.expanded })).collect();
    Ok(styles)
}
