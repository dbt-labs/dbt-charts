//! Stacking by cell (D-29): a board advances by the cell sizing gave an item, never by what the
//! item drew, so layout is authoritative and nothing grows after it. A card sits in its cell: one
//! that draws to its content in a taller cell (a table given spare height) leaves the rest blank,
//! and one that would draw taller than its cell (a KPI under an authored `height:` below its
//! natural height, a squeezed card) is refused by name, never allowed to overflow.

use diagnostic::{refuse, Diagnostic};

/// How far a card may exceed its cell: the rounding of a measured height.
const TOLERANCE: f64 = 1e-6;

/// What a row advances by for an item sized to `cell` that drew `drawn` tall.
pub fn row_advance(cell: f64, drawn: f64) -> Result<f64, Diagnostic> {
    if drawn > cell + TOLERANCE {
        return Err(Diagnostic::refused(refuse!("a card drawn taller than its cell is not supported")));
    }
    Ok(cell)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_row_advances_by_its_cell_and_a_card_taller_than_it_is_refused() {
        assert_eq!(row_advance(80.7, 80.7).unwrap(), 80.7);
        assert_eq!(row_advance(408.0, 402.0).unwrap(), 408.0);
        assert!(row_advance(80.0, 80.7).is_err());
    }
}
