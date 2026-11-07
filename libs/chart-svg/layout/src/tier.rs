//! The card width tier: the band of widths a card's title and labels size by.
//!
//! The bounds come from the default board's 24-column grid: 1128 px of content
//! (1200 − 2 × 36 margin) over 24 columns is a 47 px column. A tier opens half a
//! column early so a card that loses its gap to a neighbor keeps its tier.

const COLUMN: f64 = 1128.0 / 24.0;
const NARROW_MIN: f64 = 7.5 * COLUMN;
const MEDIUM_MIN: f64 = 11.5 * COLUMN;
const WIDE_MIN: f64 = 23.5 * COLUMN;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WidthTier {
    Tiny,
    Narrow,
    Medium,
    Wide,
}

impl WidthTier {
    /// The tier of a card's outer width.
    pub fn of(width: f64) -> Self {
        if width >= WIDE_MIN {
            WidthTier::Wide
        } else if width >= MEDIUM_MIN {
            WidthTier::Medium
        } else if width >= NARROW_MIN {
            WidthTier::Narrow
        } else {
            WidthTier::Tiny
        }
    }
}
