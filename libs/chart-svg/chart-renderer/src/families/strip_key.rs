//! A stepped key drawn as one horizontal strip above a chart (the ink theme's `strip_key`): square
//! swatches, one per ramp step, side by side, the strip's two ends labeled and the value named
//! after the last (`0 ▮▮▮▮▮ 74 signups`). A strip wider than its card is refused.

use measure::FontBook;
use scene::{Attrs, Node, Role};

use crate::common::Font;

/// A swatch's side, and the distance from one swatch's start to the next's in a run.
const SWATCH: f64 = 12.0;
const SWATCH_PITCH: f64 = 14.0;
/// The space between a label and the swatch beside it.
const LABEL_GAP: f64 = 5.0;
/// The space between the strip and the chart under it.
const STRIP_GAP: f64 = 12.0;

enum Item {
    Swatch { x: f64, fill: String },
    Label { x: f64, text: String },
}

/// A strip laid out from its left edge.
pub struct StripKey {
    items: Vec<Item>,
    font: Font,
    pub width: f64,
    /// The strip's row and the gap under it.
    pub height: f64,
}

impl StripKey {
    /// `fills` are the steps' colors, `ends` the labels of the strip's two ends, and `title`
    /// what the values count, printed after the last value.
    pub fn new(fills: &[String], ends: [String; 2], title: &str, font: &Font, fonts: &FontBook) -> Result<Self, String> {
        let size = font.size_or(11.0);
        let measure = |t: &str| font.measure_weighted(fonts, t, size);
        let [first, last] = ends;
        // One value is named once, after its swatch.
        let single = first == last;
        let last = if title.is_empty() { last } else { format!("{last} {title}") };
        let (mut x, mut items) = if single { (0.0, Vec::new()) } else { (measure(&first)? + LABEL_GAP, vec![Item::Label { x: 0.0, text: first }]) };
        for fill in fills {
            items.push(Item::Swatch { x, fill: fill.clone() });
            x += SWATCH_PITCH;
        }
        x += LABEL_GAP - (SWATCH_PITCH - SWATCH);
        let width = x + measure(&last)?;
        items.push(Item::Label { x, text: last });
        Ok(StripKey { items, font: font.clone(), width, height: SWATCH.max(size) + STRIP_GAP })
    }

    /// The strip with its top-left at `(x, y)`.
    pub fn nodes(&self, (x, y): (f64, f64)) -> Vec<Node> {
        let size = self.font.size_or(11.0);
        let row = SWATCH.max(size);
        let baseline = y + (row + size * 0.7) / 2.0;
        self.items
            .iter()
            .map(|item| match item {
                Item::Swatch { x: sx, fill } => Node::rect(Role::Legend, (x + sx, y + (row - SWATCH) / 2.0, SWATCH, SWATCH), Attrs::new().set("fill", fill.as_str())),
                Item::Label { x: lx, text } => {
                    let attrs = Attrs::new()
                        .vega("font-family", self.font.family_or("sans-serif"))
                        .num("font-size", size)
                        .set("font-weight", self.font.weight_str())
                        .set("fill", self.font.color.as_deref().unwrap_or("#000"));
                    Node::text(Role::Legend, (x + lx, baseline), attrs, text.as_str())
                }
            })
            .collect()
    }

    /// Refuses a strip wider than the `avail` px its card gives it.
    pub fn fits(self, avail: f64) -> Result<Self, String> {
        if self.width > avail {
            return Err(diagnostic::refuse!("a strip key wider than its card ({:.0} px of {avail:.0}) is not supported", self.width));
        }
        Ok(self)
    }
}
