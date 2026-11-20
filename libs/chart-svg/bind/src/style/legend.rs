//! The legend slot's priority list: `chart.key.legend`, `family.key.legend`, then the board's
//! total `charts.key.legend`; `merge_chart_style`'s walk (`resolve/style/chart_style.py`). Every
//! cartesian family's legend shares `LegendStyleBase`; its placement (`position`) and, with
//! `direction` and `title.visible`, is decided per chart (`without_engine_decided`), so none of
//! those is walked.

use board_model::style::legend::LegendStyleBase;
use model_core::Tri;

use super::Tiers;
use crate::walk::{absent, sparse, Ladder, Rung};

pub fn legend_ladder<'a>(tiers: &Tiers<'a>) -> Ladder<Rung<'a, LegendStyleBase>> {
    Rung::ladder(
        [tiers.own.legend, tiers.family.legend],
        &tiers.charts.key.legend.base,
    )
}

/// The board's total legend with every leaf this slot walks (`symbol_limit`, and the leaves the
/// engine decides per chart: `direction`, `title.visible`) taken from the walk.
pub fn walked_legend(tiers: &Tiers<'_>) -> Option<LegendStyleBase> {
    let ladder = legend_ladder(tiers);
    let mut base = tiers.charts.key.legend.base.clone();
    base.symbol_limit = ladder.leaf(|p| p.symbol_limit.as_ref(), |t| absent(&t.symbol_limit)).copied();
    base.title.visible = ladder.leaf(|p| sparse(&p.title.visible), |t| Tri::Value(&t.title.visible))?.clone();
    base.direction = ladder.leaf(|p| sparse(&p.direction), |t| Tri::Value(&t.direction))?.clone();
    Some(base)
}

/// Whether the key is shown at all: `key.visible`, the chart's own tier first, then its family's,
/// then the board's.
pub fn walked_key_visible(tiers: &Tiers<'_>) -> bool {
    tiers.own.visible.or(tiers.family.visible).unwrap_or(tiers.charts.key.visible)
}
