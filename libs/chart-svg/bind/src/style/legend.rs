//! The legend slot's priority list: `chart.legend`, `family.legend`, then the board's
//! total `charts.legend`; `merge_chart_style`'s `_FAMILY_THEN_LOCAL_FIELDS` walk
//! (`resolve/style/chart_context.py`). Every family's legend shares `LegendStyleBase`;
//! its placement differs per family (`position`) and, with `direction`, `visible` and
//! `title.visible`, is decided per chart (`without_engine_decided`), so none of those is walked.

use board_model::style::legend::LegendStyleBase;

use super::Tiers;
use crate::walk::{Ladder, Rung};

pub fn legend_ladder<'a>(tiers: &Tiers<'a>) -> Ladder<Rung<'a, LegendStyleBase>> {
    Rung::ladder(
        [tiers.own.legend, tiers.family.legend],
        &tiers.charts.legend.base,
    )
}
