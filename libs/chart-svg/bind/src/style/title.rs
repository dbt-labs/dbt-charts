//! The chart title slot's priority list: `chart.title`, `family.title`, then the board's
//! total `charts.title`; `merge_chart_style`'s `_FAMILY_THEN_LOCAL_FIELDS` walk
//! (`resolve/style/chart_context.py`). The subtitle is a leaf group of the same slot
//! (`title.subtitle`), so one ladder serves both.

use board_model::style::board::ChartTitleStyle;

use super::Tiers;
use crate::walk::{Ladder, Rung};

pub fn title_ladder<'a>(tiers: &Tiers<'a>) -> Ladder<Rung<'a, ChartTitleStyle>> {
    Rung::ladder([tiers.own.title, tiers.family.title], &tiers.charts.title)
}
