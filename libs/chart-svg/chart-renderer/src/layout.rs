//! What a family's `layout` hands back. `bind` is the family's own (its `Bound`
//! holds what the data decides); `layout` fits that to a cell and returns
//! nodes, the box the data occupies, and the findings it raised.

use crate::diagnostic::Diagnostic;
use scene::Node;

/// An axis-aligned box in canvas pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// How far the chrome around the data box reaches into the cell on each side.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Insets {
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
    pub left: f64,
}

impl Rect {
    /// The insets between `self`, a cell, and `inner`, the box inside it.
    pub fn insets_of(&self, inner: &Rect) -> Insets {
        Insets {
            top: inner.y - self.y,
            right: (self.x + self.width) - (inner.x + inner.width),
            bottom: (self.y + self.height) - (inner.y + inner.height),
            left: inner.x - self.x,
        }
    }
}

/// The widths a family can be hugged to: the narrowest it draws and the one it
/// asks for.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct IntrinsicWidth {
    pub min: f64,
    pub preferred: f64,
}

/// A family's laid-out cell.
#[derive(Debug)]
pub struct Layout {
    /// The box the data is drawn in, in the cell's coordinates.
    pub data_box: Rect,
    /// The chrome (title, legend) between the cell's edges and `data_box`.
    pub insets: Insets,
    pub nodes: Vec<Node>,
    /// Findings the layout raised, in the order it raised them.
    pub diagnostics: Vec<Diagnostic>,
    /// Where the title group sits in `data_box` coordinates, for the caller that
    /// paints it (`FrameTitle` writes a string, not nodes); `None` when untitled.
    pub title_pos: Option<(f64, f64)>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn insets_are_the_cell_edges_to_the_inner_box() {
        let cell = Rect { x: 0.0, y: 0.0, width: 600.0, height: 400.0 };
        let inner = Rect { x: 17.0, y: 44.0, width: 500.0, height: 340.0 };
        assert_eq!(cell.insets_of(&inner), Insets { top: 44.0, right: 83.0, bottom: 16.0, left: 17.0 });
    }
}
