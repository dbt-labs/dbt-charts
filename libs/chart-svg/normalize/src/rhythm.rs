//! The single-series rhythm slot (`normalize/single_series_allocation.py`): the theme's
//! single-series palette has several stops, and the charts that paint one ink walk them in
//! document order across the root's whole tree.

use crate::index::ChartFacts;
use board_model::chart::{CartesianChartFieldsAuthored, ChartAuthored, Columns};
use board_model::ids::ItemId;
use model_core::Tri;
use std::collections::BTreeMap;

/// The channel fields of the families that draw marks of one ink.
pub fn cartesian(chart: &ChartAuthored) -> Option<&CartesianChartFieldsAuthored> {
    match chart {
        ChartAuthored::Bar(c) | ChartAuthored::Histogram(c) => Some(&c.cartesian),
        ChartAuthored::Line(c) => Some(&c.cartesian),
        ChartAuthored::Area(c) => Some(&c.cartesian),
        ChartAuthored::Scatter(c) => Some(&c.cartesian),
        _ => None,
    }
}

/// A chart paints one ink when it has no `color:` channel and not several `y` measures, and is
/// a family that draws marks of one ink.
pub fn single_ink(chart: &ChartAuthored) -> bool {
    cartesian(chart).is_some_and(ink)
}

/// One entry per chart placement, as `(board, place, chart name)` in the order the layout walk
/// meets them. A board holds one copy of each chart it places, so a chart placed twice in one board
/// takes a slot each time and keeps the later; a nested board (a tab pane, a column group) holds
/// its own copy.
pub fn allocate(
    order: &[(String, ItemId, String)],
    single_ink: &dyn Fn(&str) -> bool,
) -> BTreeMap<ItemId, ChartFacts> {
    let mut slot = 0;
    let mut held: BTreeMap<(&str, &str), u32> = BTreeMap::new();
    for (board, _, name) in order {
        if single_ink(name) {
            held.insert((board, name), slot);
            slot += 1;
        }
    }
    order
        .iter()
        .map(|(board, pos, name)| {
            let rhythm_slot = held.get(&(board.as_str(), name.as_str())).copied();
            (
                pos.clone(),
                ChartFacts {
                    rhythm_slot,
                    ..ChartFacts::default()
                },
            )
        })
        .collect()
}

fn ink(c: &CartesianChartFieldsAuthored) -> bool {
    let several = matches!(&c.y, Tri::Value(Columns::Many(y)) if y.len() > 1);
    !matches!(c.color, Tri::Value(_)) && !several
}
