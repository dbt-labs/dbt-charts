//! The chart title slot's priority list: `chart.title`, `family.title`, then the board's
//! total `charts.title`; `merge_chart_style`'s walk
//! (`resolve/style/chart_style.py`). The subtitle is a leaf group of the same slot
//! (`title.subtitle`), so one ladder serves both.

use board_model::style::board::ChartTitleStyle;

use model_core::Tri;

use super::Tiers;
use crate::walk::{sparse, Ladder, Rung};

pub fn title_ladder<'a>(tiers: &Tiers<'a>) -> Ladder<Rung<'a, ChartTitleStyle>> {
    Rung::ladder([tiers.own.title, tiers.family.title], &tiers.charts.title)
}

/// The board's total title with the leaves this slot walks (`min_height`, `subtitle.max_lines`)
/// taken from the walk.
pub fn walked_title(tiers: &Tiers<'_>) -> Option<ChartTitleStyle> {
    let ladder = title_ladder(tiers);
    let mut title = tiers.charts.title.clone();
    title.base.min_height = *ladder.leaf(|p| sparse(&p.base.min_height), |t| Tri::Value(&t.base.min_height))?;
    title.base.subtitle.max_lines = ladder
        .leaf(|p| sparse(&p.base.subtitle.max_lines), |t| Tri::Value(&t.base.subtitle.max_lines))
        .cloned()
        ?;
    Some(title)
}
