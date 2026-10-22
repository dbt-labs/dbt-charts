//! Scatter's shape channel: Vega's default symbol ladder and the glyph paths
//! `vega-scenegraph` draws for each of its names, and the legend Vega draws
//! for a shape encoding that carries no legend config.

use std::fmt::Write as _;

use crate::families::legend::{legend_grid, LegendGrid, LegendSymbol};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::Payload;
use crate::measure::baseline_shift;

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

/// `HalfSqrt3` of `vega-scenegraph/src/util/constants.js:5`.
const HALF_SQRT_3: f64 = 0.866_025_403_784_438_6;

/// The symbol the shape scale gives the `index`-th category of its domain: a
/// d3 ordinal scale cycles its range (`range[i % range.length]`, d3-scale
/// `ordinal.js`).
pub fn symbol_for(index: usize) -> &'static str {
    RANGE_SYMBOL[index % RANGE_SYMBOL.len()]
}

/// A number the way `vega-scenegraph` prints a path coordinate: three
/// decimals, trailing zeros trimmed.
fn num(v: f64) -> String {
    let s = format!("{v:.3}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s == "-0" {
        "0".into()
    } else {
        s.into()
    }
}

/// `tan(30deg)`, the offset that lifts `triangle` and `wedge` onto their
/// centroid (`vega-scenegraph/src/path/symbols.js`).
const TAN_30: f64 = 0.577_350_269_189_625_7;

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

/// The bounds of `symbol` drawn at radius `r` (`sqrt(size) / 2`).
pub fn half_extents(symbol: &str, r: f64) -> Bounds {
    let h = HALF_SQRT_3 * r;
    let o = h - r * TAN_30;
    let (x, up, down) = match symbol {
        "triangle-up" | "triangle-down" => (r, h, h),
        "triangle-right" | "triangle-left" => (h, r, r),
        "triangle" => (r, h + o, h - o),
        "wedge" => (r / 4.0, h + o, h - o),
        "arrow" => (r / 2.5, r, r),
        "stroke" => (r, 0.0, 0.0),
        _ => (r, r, r),
    };
    Bounds { x, up, down, sharp: symbol == "wedge" }
}

/// The attribute a stroked glyph of these bounds carries.
pub fn miter_attr(bounds: Bounds) -> &'static str {
    if bounds.sharp { " stroke-miterlimit=\"10\"" } else { "" }
}

/// The SVG path `vega-scenegraph/src/path/symbols.js` draws for `symbol` at
/// radius `r` (`sqrt(size) / 2`), centered on the origin.
pub fn path(symbol: &str, r: f64) -> Result<String, String> {
    let h = HALF_SQRT_3 * r;
    let o = h - r * TAN_30;
    let poly = |pts: &[(f64, f64)]| {
        let mut d = String::new();
        for (i, (x, y)) in pts.iter().enumerate() {
            d.push(if i == 0 { 'M' } else { 'L' });
            d.push_str(&format!("{},{}", num(*x), num(*y)));
        }
        d.push('Z');
        d
    };
    Ok(match symbol {
        "circle" => {
            let r = num(r);
            format!("M{r},0A{r},{r},0,1,1,-{r},0A{r},{r},0,1,1,{r},0")
        }
        "square" => {
            let w = num(2.0 * r);
            format!("M{},{}h{w}v{w}h-{w}Z", num(-r), num(-r))
        }
        "cross" => {
            let s = r / 2.5;
            poly(&[
                (-r, -s),
                (-r, s),
                (-s, s),
                (-s, r),
                (s, r),
                (s, s),
                (r, s),
                (r, -s),
                (s, -s),
                (s, -r),
                (-s, -r),
                (-s, -s),
            ])
        }
        "diamond" => poly(&[(-r, 0.0), (0.0, -r), (r, 0.0), (0.0, r)]),
        "triangle-up" => poly(&[(0.0, -h), (-r, h), (r, h)]),
        "triangle-down" => poly(&[(0.0, h), (-r, -h), (r, -h)]),
        "triangle-right" => poly(&[(h, 0.0), (-h, -r), (-h, r)]),
        "triangle-left" => poly(&[(-h, 0.0), (h, -r), (h, r)]),
        "triangle" => poly(&[(0.0, -h - o), (-r, h - o), (r, h - o)]),
        "wedge" => poly(&[(0.0, -h - o), (-r / 4.0, h - o), (r / 4.0, h - o)]),
        "arrow" => {
            let (s, t, v) = (r / 7.0, r / 2.5, r / 8.0);
            poly(&[(-s, r), (s, r), (s, -v), (t, -v), (0.0, -r), (-t, -v), (-s, -v)])
        }
        "stroke" => format!("M{},0L{},0", num(-r), num(r)),
        other => return Err(format!("scatter: shape symbol {other:?} is no Vega symbol (resolver required)")),
    })
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
    paths: Vec<String>,
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
const SYMBOL_RADIUS: f64 = 5.0;
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

    /// Paints the legend at `(x, y)` with `opacity` the mark's own.
    pub fn paint(
        &self,
        svg: &mut String,
        x: f64,
        y: f64,
        fill: SymbolFill,
        opacity: f64,
    ) {
        let fill_attrs = match fill {
            SymbolFill::Static(color) => format!("fill=\"{}\"", xml_escape_attr(color)),
            SymbolFill::Field => "fill=\"black\" fill-opacity=\"1\"".to_string(),
            SymbolFill::Ring { interior, stroke } => format!(
                "fill=\"{}\"{}",
                xml_escape_attr(interior),
                stroke.map(|s| format!(" stroke=\"{}\"", xml_escape_attr(s))).unwrap_or_default()
            ),
        };
        let family = vega_escape_attr(&self.family);
        let _ = write!(
            svg,
            "<text x=\"{x:.2}\" y=\"{:.2}\" font-family=\"{family}\" font-size=\"{TITLE_SIZE}\" \
             font-weight=\"bold\" fill=\"{INK}\">{}</text>",
            y + baseline_shift("top", TITLE_SIZE),
            xml_escape(&self.title)
        );
        let entries_y = y + TITLE_SIZE + TITLE_PADDING;
        let c = self.grid.size / 2.0;
        for (i, (label, path)) in self.labels.iter().zip(&self.paths).enumerate() {
            let row_y = entries_y + self.grid.row_y[i];
            let _ = write!(
                svg,
                "<path transform=\"translate({:.2},{:.2})\" d=\"{}\" {fill_attrs} \
                 stroke-width=\"{SYMBOL_STROKE_WIDTH}\" opacity=\"{opacity}\"/>\
                 <text x=\"{:.2}\" y=\"{:.2}\" font-family=\"{family}\" \
                 font-size=\"{LABEL_SIZE}\" fill=\"{INK}\">{}</text>",
                x + c,
                row_y + c,
                path,
                x + self.grid.label_x,
                row_y + c + baseline_shift("middle", LABEL_SIZE),
                xml_escape(label)
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_match_the_vega_scenegraph_output() {
        assert_eq!(
            path("square", 3.873).unwrap(),
            "M-3.873,-3.873h7.746v7.746h-7.746Z"
        );
        assert_eq!(
            path("triangle-up", 3.873).unwrap(),
            "M0,-3.354L-3.873,3.354L3.873,3.354Z"
        );
        assert_eq!(
            path("triangle-up", 5.0).unwrap(),
            "M0,-4.33L-5,4.33L5,4.33Z"
        );
    }

    #[test]
    fn the_centroid_symbols_and_the_arrow_match_the_vega_scenegraph_output() {
        assert_eq!(path("arrow", 3.873).unwrap(), "M-0.553,3.873L0.553,3.873L0.553,-0.484L1.549,-0.484L0,-3.873L-1.549,-0.484L-0.553,-0.484Z");
        assert_eq!(path("wedge", 3.873).unwrap(), "M0,-4.472L-0.968,2.236L0.968,2.236Z");
        assert_eq!(path("triangle", 3.873).unwrap(), "M0,-4.472L-3.873,2.236L3.873,2.236Z");
        assert_eq!(path("stroke", 3.873).unwrap(), "M-3.873,0L3.873,0");
        assert!(path("hexagon", 3.873).is_err());
    }

    #[test]
    fn a_centroid_symbol_reaches_further_up_than_down() {
        let b = half_extents("wedge", 5.0);
        assert!((b.up - b.down - 2.0 * (HALF_SQRT_3 * 5.0 - 5.0 * TAN_30)).abs() < 1e-9);
        assert!(b.sharp && !half_extents("triangle", 5.0).sharp);
    }
}
