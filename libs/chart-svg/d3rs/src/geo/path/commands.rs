//! d3-geo 3.1.1 `src/path/string.js`, ported to emit path commands: `PathCommands`, the sink `geoPath` draws
//! through. d3 and Vega 6.2.0 print the commands as a `d` string rounded to `digits` (3); here the sink rounds
//! the same way and hands over the commands, and `print` writes them. A point is a circle of the path's point
//! radius, as d3 draws it, with its relative moves and arcs resolved to absolute commands.

use crate::geo::stream::GeoStream;
use crate::js_math::js_round;
use scene::{PathCommand, PathData};

#[derive(Clone, Copy)]
enum Point {
    /// Outside a line: `point` is an isolated point, drawn as a circle.
    Isolated,
    First,
    Rest,
}

pub struct PathCommands {
    /// `10 ** digits` when coordinates are rounded; d3 skips rounding for `digits` null or above 15.
    rounding: Option<f64>,
    radius: f64,
    out: Vec<PathCommand>,
    in_polygon: bool,
    point: Point,
}

impl PathCommands {
    pub fn new(digits: Option<u32>, radius: f64) -> Self {
        Self {
            rounding: digits.filter(|d| *d <= 15).map(|d| 10f64.powi(d as i32)),
            radius,
            out: Vec::new(),
            in_polygon: false,
            point: Point::Isolated,
        }
    }

    /// The path, or `None` for an empty one (d3 returns null); the sink starts over.
    pub fn result(&mut self) -> Option<PathData> {
        let commands = std::mem::take(&mut self.out);
        (!commands.is_empty()).then_some(PathData(commands))
    }

    fn round(&self, v: f64) -> f64 {
        self.rounding.map_or(v, |k| js_round(v * k) / k)
    }

    /// The point's circle: d3's `m0,r a r,r 0 1,1 0,-2r a r,r 0 1,1 0,2r z` from `(x, y)`, in absolute commands.
    fn circle(&mut self, x: f64, y: f64) {
        let r = self.round(self.radius);
        let (x, y) = (self.round(x), self.round(y));
        let top = y + r;
        let bottom = top - self.round(2.0 * self.radius);
        let arc = |y: f64| PathCommand::A { rx: r, ry: r, rot: 0.0, large: true, sweep: true, x, y };
        self.out.extend([PathCommand::M(x, y), PathCommand::M(x, top), arc(bottom), arc(top), PathCommand::Z]);
    }
}

impl GeoStream for PathCommands {
    fn polygon_start(&mut self) {
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        self.in_polygon = false;
    }

    fn line_start(&mut self) {
        self.point = Point::First;
    }

    fn line_end(&mut self) {
        if self.in_polygon {
            self.out.push(PathCommand::Z);
        }
        self.point = Point::Isolated;
    }

    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        match self.point {
            Point::First => {
                self.out.push(PathCommand::M(self.round(x), self.round(y)));
                self.point = Point::Rest;
            }
            Point::Rest => self.out.push(PathCommand::L(self.round(x), self.round(y))),
            Point::Isolated => self.circle(x, y),
        }
    }

    fn sphere(&mut self) {
        unreachable!("d3's PathCommands has no sphere: a projection's pre-clip consumes it, and geoPath rejects it otherwise");
    }
}
