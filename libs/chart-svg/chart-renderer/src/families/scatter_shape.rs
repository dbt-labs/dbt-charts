//! Scatter's shape channel: Vega's default symbol ladder and the glyph paths
//! `vega-scenegraph` draws for each of its names, and the legend Vega draws
//! for a shape encoding that carries no legend config.

use crate::families::legend::{legend_grid, LegendGrid, LegendSymbol};
use d3rs::vega_symbol::{Extents, VegaSymbol};
use scene::{Attrs, Node, PathData, Role};
use measure::FontBook;
use crate::Payload;
use measure::baseline_shift;

/// Vega's default `range.symbol` — the named range a Vega-Lite shape scale
/// takes (`vega-lite compile/scale/range.ts:341`;
/// `vega-parser/src/config.js:232-241`).
const RANGE_SYMBOL: [&str; 8] = [
    "circle",
    "square",
    "triangle-up",
    "cross",
    "diamond",
    "triangle-right",
    "triangle-down",
    "triangle-left",
];

/// The symbol the shape scale gives the `index`-th category of its domain: a
/// d3 ordinal scale cycles its range (`range[i % range.length]`, d3-scale
/// `ordinal.js`).
pub fn symbol_for(index: usize) -> &'static str {
    RANGE_SYMBOL[index % RANGE_SYMBOL.len()]
}

/// A symbol's bounds around its origin: the half width and the reach above and
/// below it (`triangle` and `wedge` sit on their centroid, so they reach higher
/// than they fall), and whether its corners are sharp enough that Vega's miter
/// limit of 10, not the SVG default of 4, shapes a stroked tip.
#[derive(Clone, Copy)]
pub struct Bounds {
    pub x: f64,
    pub up: f64,
    pub down: f64,
    pub sharp: bool,
}

/// The area of the bounding square of a symbol of radius `r`: Vega's `size`, of which `sqrt(size) / 2` is the radius.
fn size_of(r: f64) -> f64 {
    4.0 * r * r
}

/// A Vega symbol by name; any other name would be a custom SVG path, which has no port.
fn named(symbol: &str) -> Result<VegaSymbol, String> {
    VegaSymbol::from_name(symbol).ok_or_else(|| format!("scatter: shape symbol {symbol:?} is no Vega symbol (resolver required)"))
}

/// The bounds of `symbol` drawn at radius `r` (`sqrt(size) / 2`). Only `wedge`, whose tip is a sharp corner,
/// needs the miter limit.
pub fn half_extents(symbol: &str, r: f64) -> Bounds {
    let Extents { x, up, down } = named(symbol).expect("the resolver checks a shape symbol's name").extents(size_of(r));
    Bounds { x, up, down, sharp: symbol == "wedge" }
}

/// The `stroke-miterlimit` a stroked glyph of these bounds carries.
pub fn miter_limit(bounds: Bounds) -> Option<&'static str> {
    bounds.sharp.then_some("10")
}

/// The path `vega-scenegraph` draws for `symbol` at radius `r` (`sqrt(size) / 2`), centered on the origin.
pub fn path(symbol: &str, r: f64) -> Result<PathData, String> {
    Ok(glyph(named(symbol)?, r))
}

/// [`path`] for a symbol already known to be Vega's.
pub fn glyph(symbol: VegaSymbol, r: f64) -> PathData {
    symbol.draw(size_of(r))
}

/// How a shape legend's symbols are filled (`vega-lite compile/legend/
/// encode.ts:63-74`): beside a color channel the fill is a field reference
/// and becomes `symbolBaseFillColor`'s black; with none the mark's own static
/// fill survives.
#[derive(Clone, Copy)]
pub enum SymbolFill<'a> {
    Static(&'a str),
    Field,
    /// An unfilled mark's glyph: its interior fill, under the mark's own stroke
    /// color; a color channel leaves the stroke a field ref and so unset.
    Ring { interior: &'a str, stroke: Option<&'a str> },
}

/// A shape channel's own legend when no color legend carries it: Vega-Lite
/// gives the shape encoding no legend config, so it draws with Vega's
/// defaults — orient right, `guide-title` (11px bold, `#000`), `guide-label`
/// (10px, `#000`) text (`vega-parser/src/config.js:84-95`), `titlePadding` 5
/// and `labelOffset` 4 (`:205`, `:194`) — laid out by the same symbol-legend
/// grid as every other legend, each row bounded by its own glyph.
pub struct ShapeLegend {
    title: String,
    labels: Vec<String>,
    paths: Vec<PathData>,
    family: String,
    grid: LegendGrid,
    /// The legend's ink width, ceiled, and its height.
    pub w: f64,
    pub h: f64,
}

const TITLE_SIZE: f64 = 11.0;
const LABEL_SIZE: f64 = 10.0;
const TITLE_PADDING: f64 = 5.0;
const LABEL_OFFSET: f64 = 4.0;
/// Vega's legend `symbolSize` (`config.js:198`): the glyph radius is 5.
pub(crate) const SYMBOL_RADIUS: f64 = 5.0;
/// `symbolStrokeWidth` (`config.js:200`).
const SYMBOL_STROKE_WIDTH: f64 = 1.5;
/// Vega's `black` (`config.js:11`).
const INK: &str = "#000";

impl ShapeLegend {
    /// The legend of a shape encoding on `title`'s field: one symbol per
    /// `labels` entry in `range.symbol` order. The font is `config.font`
    /// (`board_style.font`), that of Vega-Lite's `guide-title` and
    /// `guide-label` styles (`config.ts:505-512`). The title is measured at the
    /// default weight: vl-convert's text layout does not re-instance a variable
    /// font for the title's bold (`text.rs` `measure_shaped`).
    pub fn new(fonts: &FontBook, payload: &Payload, title: String, labels: Vec<String>) -> Result<Self, String> {
        let family = payload
            .board_style
            .as_ref()
            .and_then(|b| b.font.as_deref())
            .ok_or("scatter: a shape legend needs board_style.font")?;
        let symbols: Vec<&'static str> = (0..labels.len()).map(symbol_for).collect();
        let label_ws = labels
            .iter()
            .map(|l| fonts.measure(l, family, LABEL_SIZE))
            .collect::<Result<Vec<_>, _>>()?;
        let glyphs: Vec<Option<LegendSymbol>> = symbols
            .iter()
            .map(|s| {
                Some(LegendSymbol::Glyph(half_extents(s, SYMBOL_RADIUS)))
            })
            .collect();
        let paths = symbols.iter().map(|s| path(s, SYMBOL_RADIUS)).collect::<Result<Vec<_>, _>>()?;
        let grid = legend_grid(&label_ws, &glyphs, false, LABEL_SIZE, 1, LABEL_OFFSET);
        let title_w = fonts.measure_shaped(&title, family, TITLE_SIZE)?;
        let w = title_w.max(grid.width).ceil();
        let h = TITLE_SIZE + TITLE_PADDING + grid.box_h;
        Ok(Self {
            title,
            labels,
            paths,
            family: family.to_string(),
            grid,
            w,
            h,
        })
    }

    /// The legend at `(x, y)` with `opacity` the mark's own.
    pub fn nodes(&self, x: f64, y: f64, fill: SymbolFill, opacity: f64) -> Vec<Node> {
        let symbol_fill = |attrs: Attrs| match fill {
            SymbolFill::Static(color) => attrs.set("fill", color),
            SymbolFill::Field => attrs.set("fill", "black").set("fill-opacity", "1"),
            SymbolFill::Ring { interior, stroke } => {
                let attrs = attrs.set("fill", interior);
                match stroke {
                    Some(stroke) => attrs.set("stroke", stroke),
                    None => attrs,
                }
            }
        };
        let mut nodes = vec![Node::text(
            Role::Legend,
            (x, y + baseline_shift("top", TITLE_SIZE)),
            Attrs::new()
                .vega("font-family", self.family.as_str())
                .num("font-size", TITLE_SIZE)
                .set("font-weight", "bold")
                .set("fill", INK),
            self.title.as_str(),
        )];
        let entries_y = y + TITLE_SIZE + TITLE_PADDING;
        let c = self.grid.size / 2.0;
        for (i, (label, path)) in self.labels.iter().zip(&self.paths).enumerate() {
            let row_y = entries_y + self.grid.row_y[i];
            let glyph = symbol_fill(Attrs::new()).num("stroke-width", SYMBOL_STROKE_WIDTH).num("opacity", opacity);
            nodes.push(Node::path(Role::Legend, path.clone(), glyph).translate(x + c, row_y + c));
            nodes.push(Node::text(
                Role::Legend,
                (x + self.grid.label_x, row_y + c + baseline_shift("middle", LABEL_SIZE)),
                Attrs::new().vega("font-family", self.family.as_str()).num("font-size", LABEL_SIZE).set("fill", INK),
                label.as_str(),
            ));
        }
        nodes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unknown_symbol_is_refused_by_name() {
        let err = path("hexagon", 3.873).unwrap_err();
        assert!(err.contains("\"hexagon\""), "{err}");
    }

    /// A radius is the square root of the size over two, so a size's path is its radius's.
    #[test]
    fn a_radius_draws_the_symbol_of_its_size() {
        assert_eq!(path("square", 5.0).unwrap(), VegaSymbol::Square.draw(100.0));
        assert_eq!(path("circle", 3.873).unwrap(), VegaSymbol::Circle.draw(size_of(3.873)));
    }

    #[test]
    fn a_centroid_symbol_reaches_further_up_than_down() {
        let b = half_extents("wedge", 5.0);
        assert!(b.up > b.down);
        assert!(b.sharp && !half_extents("triangle", 5.0).sharp);
    }
}
