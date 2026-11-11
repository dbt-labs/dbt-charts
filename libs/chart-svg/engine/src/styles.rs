//! The engine's style seam from a compiled board: the one place `bind::board::ChartStyles` is
//! assembled from what normalize resolved: each placement's total style, own tier and rhythm slot
//! (`CompiledBoard.placed`, `placed_own` and `LayoutFacts.chart_facts`) and each chart's public id
//! (`Structure.index.public_ids`), and each placement's placing scope, the root's style or the
//! innermost nested board's from `CompiledBoard.nested`. It lives here because `bind` cannot depend on `normalize`, so
//! `engine` depends on it.

use bind::board::{ChartStyles, DetailsFacts, TabsFacts};
use diagnostic::{diag, Diagnostic};
use normalize::CompiledBoard;

/// Each placement's total family style, own tier, scope, rhythm slot and public chart ids, keyed by position, and each
/// tabs layout's and details board's facts.
pub fn chart_styles(compiled: &CompiledBoard) -> Result<ChartStyles, Diagnostic> {
    let layout = &compiled.structure.layout;
    let missing = |what: &str, at: &str| diag::err_internal(format!("the compiled board holds no {what} for the placement {at}"));
    let mut styles = ChartStyles { public_ids: compiled.structure.index.public_ids.clone(), ..ChartStyles::default() };
    for at in layout.charts.keys() {
        let placed = compiled.placed.get(at.as_str()).ok_or_else(|| missing("placed style", at.as_str()))?;
        let own = compiled.placed_own.get(at.as_str()).ok_or_else(|| missing("own style", at.as_str()))?;
        styles.placed.insert(at.clone(), *placed.total.clone());
        styles.own.insert(at.clone(), own.clone());
        let nested = compiled.nested.iter().filter(|(key, _)| at.as_str().strip_prefix(key.as_str()).is_some_and(|rest| rest.starts_with('.')));
        let scope = nested.max_by_key(|(key, _)| key.len()).map_or(&compiled.style, |(_, scope)| scope);
        styles.scope.insert(at.clone(), scope.clone());
    }
    for (at, facts) in &layout.chart_facts {
        if let Some(slot) = facts.rhythm_slot {
            styles.rhythm.insert(at.clone(), slot);
        }
    }
    styles.tabs = layout.tabs.iter().map(|(at, t)| (at.clone(), TabsFacts { variable: t.variable.clone(), slugs: t.slugs.clone(), default_tab: t.default_tab })).collect();
    styles.details = layout.details.iter().map(|(at, d)| (at.clone(), DetailsFacts { variable: d.variable.clone(), expanded: d.expanded })).collect();
    Ok(styles)
}
