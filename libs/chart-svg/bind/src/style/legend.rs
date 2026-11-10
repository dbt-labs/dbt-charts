//! The legend slot's priority list: `chart.legend`, `family.legend`, then the board's
//! total `charts.legend`; `merge_chart_style`'s `_FAMILY_THEN_LOCAL_FIELDS` walk
//! (`resolve/style/chart_context.py`). Every family's legend shares `LegendStyleBase`;
//! its placement differs per family (`position`) and, with `direction`, `visible` and
//! `title.visible`, is decided per chart (`without_engine_decided`), so none of those is walked.

use board_model::style::legend::LegendStyleBase;
use model_core::Tri;

use super::Tiers;
use crate::walk::{absent, sparse, Ladder, Rung};

pub fn legend_ladder<'a>(tiers: &Tiers<'a>) -> Ladder<Rung<'a, LegendStyleBase>> {
    Rung::ladder(
        [tiers.own.legend, tiers.family.legend],
        &tiers.charts.legend.base,
    )
}

/// The board's total legend with every leaf this slot walks (`symbol_limit`, and the leaves the
/// engine decides per chart: `direction`, `visible`, `title.visible`) taken from the walk. `None`
/// when a walk finds no rung, which the total ends, so never for a total style.
pub fn walked_legend(tiers: &Tiers<'_>) -> Option<LegendStyleBase> {
    let ladder = legend_ladder(tiers);
    let mut base = tiers.charts.legend.base.clone();
    base.symbol_limit = ladder.leaf(|p| p.symbol_limit.as_ref(), |t| absent(&t.symbol_limit)).copied();
    base.visible = ladder.leaf(|p| sparse(&p.visible), |t| Tri::Value(&t.visible))?.clone();
    base.title.visible = ladder.leaf(|p| sparse(&p.title.visible), |t| Tri::Value(&t.title.visible))?.clone();
    base.direction = ladder.leaf(|p| sparse(&p.direction), |t| Tri::Value(&t.direction))?.clone();
    Some(base)
}
