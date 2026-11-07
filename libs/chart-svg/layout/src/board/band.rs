//! The cols-row title band: the first baseline and the body top every titled
//! cell of a `cols` row shares (SL A1, D-30).

use crate::title::TitleMetrics;

/// How far a cell moves its title and its body down so its row's titles share
/// one baseline and its bodies one top. `body_dy >= title_dy`; the cell is
/// `body_dy` taller.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct TitleShift {
    pub title_dy: f64,
    pub body_dy: f64,
}

/// A row's shifts, one per cell. The titles share the largest ascent, and the
/// bodies sit below the tallest title block once it is on that baseline. A cell
/// with no title is not in the row's cells and takes no shift.
pub fn row_shifts(cells: &[TitleMetrics]) -> Vec<TitleShift> {
    let baseline = cells.iter().map(|m| m.ascent).fold(0.0, f64::max);
    let body_top = baseline + cells.iter().map(|m| m.tail).fold(0.0, f64::max);
    cells
        .iter()
        .map(|m| TitleShift {
            title_dy: baseline - m.ascent,
            body_dy: body_top - m.ascent - m.tail,
        })
        .collect()
}
