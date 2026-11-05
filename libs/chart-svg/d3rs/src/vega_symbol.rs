//! Port of vega-scenegraph 5.1.0's `src/path/symbols.js`: the symbol shapes a Vega mark draws. Vega's `symbol`
//! mark runs `d3.symbol().type(vegaType).size(size)` with no context, so each shape is drawn onto a path
//! as typed path data, exactly as [`crate::symbol`] draws d3-shape's own. Vega's shapes are not
//! d3-shape's: `size` is the area of the bounding square (`circle` has radius `sqrt(size) / 2`, not
//! `sqrt(size / PI)`), and the triangles and wedge are Vega's.
//! References: https://github.com/vega/vega/blob/v6.2.0/packages/vega-scenegraph/src/path/symbols.js

use crate::path::Path;
use crate::path::Command;

/// `HalfSqrt3` of `vega-scenegraph/src/util/constants.js`.
const HALF_SQRT_3: f64 = 0.866_025_403_784_438_6;
/// `Tan30` of `symbols.js`.
const TAN_30: f64 = 0.577_350_269_189_625_7;

/// The built-in symbol names `symbols(name)` knows; any other name is a custom SVG path, which has no port.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VegaSymbol {
    Circle,
    Cross,
    Diamond,
    Square,
    Arrow,
    Wedge,
    Triangle,
    TriangleUp,
    TriangleDown,
    TriangleRight,
    TriangleLeft,
    Stroke,
}

/// How far a symbol reaches from its origin: the half width, and the reach above and below it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Extents {
    pub x: f64,
    pub up: f64,
    pub down: f64,
}

/// What a symbol's `draw(context, size)` calls.
enum Outline {
    /// `moveTo(r, 0); arc(0, 0, r, 0, Tau)`.
    Circle(f64),
    /// `rect(x, x, w, w)`.
    Square(f64),
    /// `moveTo` the first point, `lineTo` the rest, `closePath` when closed.
    Polygon { points: Vec<(f64, f64)>, closed: bool },
}

impl VegaSymbol {
    /// The name `symbols(name)` registers it under.
    pub fn from_name(name: &str) -> Option<VegaSymbol> {
        Some(match name {
            "circle" => VegaSymbol::Circle,
            "cross" => VegaSymbol::Cross,
            "diamond" => VegaSymbol::Diamond,
            "square" => VegaSymbol::Square,
            "arrow" => VegaSymbol::Arrow,
            "wedge" => VegaSymbol::Wedge,
            "triangle" => VegaSymbol::Triangle,
            "triangle-up" => VegaSymbol::TriangleUp,
            "triangle-down" => VegaSymbol::TriangleDown,
            "triangle-right" => VegaSymbol::TriangleRight,
            "triangle-left" => VegaSymbol::TriangleLeft,
            "stroke" => VegaSymbol::Stroke,
            _ => return None,
        })
    }

    fn outline(self, size: f64) -> Outline {
        let r = size.sqrt() / 2.0;
        let h = HALF_SQRT_3 * r;
        let closed = |points: Vec<(f64, f64)>| Outline::Polygon { points, closed: true };
        match self {
            VegaSymbol::Circle => Outline::Circle(r),
            VegaSymbol::Cross => {
                let s = r / 2.5;
                closed(vec![(-r, -s), (-r, s), (-s, s), (-s, r), (s, r), (s, s), (r, s), (r, -s), (s, -s), (s, -r), (-s, -r), (-s, -s)])
            }
            VegaSymbol::Diamond => closed(vec![(-r, 0.0), (0.0, -r), (r, 0.0), (0.0, r)]),
            VegaSymbol::Square => Outline::Square(size.sqrt()),
            VegaSymbol::Arrow => {
                let (s, t, v) = (r / 7.0, r / 2.5, r / 8.0);
                closed(vec![(-s, r), (s, r), (s, -v), (t, -v), (0.0, -r), (-t, -v), (-s, -v)])
            }
            VegaSymbol::Wedge => {
                let o = h - r * TAN_30;
                let b = r / 4.0;
                closed(vec![(0.0, -h - o), (-b, h - o), (b, h - o)])
            }
            VegaSymbol::Triangle => {
                let o = h - r * TAN_30;
                closed(vec![(0.0, -h - o), (-r, h - o), (r, h - o)])
            }
            VegaSymbol::TriangleUp => closed(vec![(0.0, -h), (-r, h), (r, h)]),
            VegaSymbol::TriangleDown => closed(vec![(0.0, h), (-r, -h), (r, -h)]),
            VegaSymbol::TriangleRight => closed(vec![(h, 0.0), (-h, -r), (-h, r)]),
            VegaSymbol::TriangleLeft => closed(vec![(-h, 0.0), (h, -r), (h, r)]),
            VegaSymbol::Stroke => Outline::Polygon { points: vec![(-r, 0.0), (r, 0.0)], closed: false },
        }
    }

    /// `symbol().type(this).size(size)()`: the path data, centered on the origin.
    pub fn draw(self, size: f64) -> Vec<Command> {
        let mut path = Path::new();
        match self.outline(size) {
            // Vega's circle is d3's two half arcs, as written: `Path::arc` splits a sweep at quarter turns.
            // d3-path's `arc` draws nothing of a radius of 0, leaving the lone `moveTo`.
            Outline::Circle(r) => {
                if r == 0.0 {
                    return vec![Command::MoveTo(0.0, 0.0)];
                }
                let half = |x| Command::SvgArc { rx: r, ry: r, rot: 0.0, large: true, sweep: true, x, y: 0.0 };
                return vec![Command::MoveTo(r, 0.0), half(-r), half(r)];
            }
            Outline::Square(w) => {
                let x = -w / 2.0;
                path.rect(x, x, w, w);
            }
            Outline::Polygon { points, closed } => {
                let (first, rest) = points.split_first().expect("a polygon has points");
                path.move_to(first.0, first.1);
                for &(x, y) in rest {
                    path.line_to(x, y);
                }
                if closed {
                    path.close_path();
                }
            }
        }
        path.into_commands()
    }

    /// The bounds Vega's `boundContext` collects from [`draw`](Self::draw): every point drawn, an arc as its box.
    pub fn extents(self, size: f64) -> Extents {
        let (mut x, mut min_y, mut max_y) = (0.0_f64, f64::INFINITY, f64::NEG_INFINITY);
        let mut reach = |px: f64, py: f64| {
            x = x.max(px.abs());
            min_y = min_y.min(py);
            max_y = max_y.max(py);
        };
        match self.outline(size) {
            Outline::Circle(r) => {
                reach(r, 0.0);
                reach(-r, -r);
                reach(r, r);
            }
            Outline::Square(w) => {
                let origin = -w / 2.0;
                reach(origin, origin);
                reach(origin + w, origin + w);
            }
            Outline::Polygon { points, .. } => points.into_iter().for_each(|(px, py)| reach(px, py)),
        }
        // Math.max and Math.min put +0 above -0; `0 - min` and `max + 0` end a -0 as 0.
        Extents { x, up: 0.0 - min_y, down: max_y + 0.0 }
    }
}
