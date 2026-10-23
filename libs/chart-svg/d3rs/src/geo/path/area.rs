//! d3-geo 3.1.1 `src/path/area.js`: the planar area of the polygons a projected stream delivers.

use crate::geo::stream::GeoStream;
use crate::geo::Adder;

#[derive(Clone, Copy)]
enum Point {
    /// d3's `noop`: outside a polygon's rings.
    Ignored,
    First,
    Next,
}

/// d3 declares `x00`, `y00`, `x0`, `y0` without a value, so an empty ring reads `undefined`: NaN here.
pub struct PathArea {
    sum: Adder,
    ring_sum: Adder,
    in_polygon: bool,
    point: Point,
    x00: f64,
    y00: f64,
    x0: f64,
    y0: f64,
}

impl Default for PathArea {
    fn default() -> Self {
        Self {
            sum: Adder::default(),
            ring_sum: Adder::default(),
            in_polygon: false,
            point: Point::Ignored,
            x00: f64::NAN,
            y00: f64::NAN,
            x0: f64::NAN,
            y0: f64::NAN,
        }
    }
}

impl PathArea {
    /// The area, and the sink starts over.
    pub fn result(&mut self) -> f64 {
        let area = self.sum.value() / 2.0;
        self.sum = Adder::default();
        area
    }

    fn add_point(&mut self, x: f64, y: f64) {
        self.ring_sum.add(self.y0 * x - self.x0 * y);
        self.x0 = x;
        self.y0 = y;
    }
}

impl GeoStream for PathArea {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        match self.point {
            Point::Ignored => {}
            Point::First => {
                self.point = Point::Next;
                self.x00 = x;
                self.x0 = x;
                self.y00 = y;
                self.y0 = y;
            }
            Point::Next => self.add_point(x, y),
        }
    }

    fn line_start(&mut self) {
        if self.in_polygon {
            self.point = Point::First;
        }
    }

    fn line_end(&mut self) {
        if self.in_polygon {
            self.add_point(self.x00, self.y00);
        }
    }

    fn polygon_start(&mut self) {
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        self.in_polygon = false;
        self.point = Point::Ignored;
        self.sum.add(self.ring_sum.value().abs());
        self.ring_sum = Adder::default();
    }

    fn sphere(&mut self) {
        unreachable!("d3's area stream has no sphere: a projection's pre-clip consumes it, and geoPath rejects it otherwise");
    }
}
