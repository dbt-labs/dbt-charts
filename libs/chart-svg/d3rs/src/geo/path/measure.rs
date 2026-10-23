//! d3-geo 3.1.1 `src/path/measure.js`: the length of the lines (and polygon rings) a projected stream delivers.

use crate::geo::stream::GeoStream;
use crate::geo::Adder;

#[derive(Clone, Copy)]
enum Point {
    /// d3's `noop`.
    Ignored,
    First,
    Next,
}

/// d3 declares `x00`, `y00`, `x0`, `y0` without a value, so an empty ring reads `undefined`: NaN here.
pub struct PathMeasure {
    sum: Adder,
    ring: bool,
    point: Point,
    x00: f64,
    y00: f64,
    x0: f64,
    y0: f64,
}

impl Default for PathMeasure {
    fn default() -> Self {
        Self {
            sum: Adder::default(),
            ring: false,
            point: Point::Ignored,
            x00: f64::NAN,
            y00: f64::NAN,
            x0: f64::NAN,
            y0: f64::NAN,
        }
    }
}

impl PathMeasure {
    /// The length, and the sink starts over.
    pub fn result(&mut self) -> f64 {
        let length = self.sum.value();
        self.sum = Adder::default();
        length
    }

    fn add_point(&mut self, x: f64, y: f64) {
        self.x0 -= x;
        self.y0 -= y;
        self.sum.add(libm::sqrt(self.x0 * self.x0 + self.y0 * self.y0));
        self.x0 = x;
        self.y0 = y;
    }
}

impl GeoStream for PathMeasure {
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
        self.point = Point::First;
    }

    fn line_end(&mut self) {
        if self.ring {
            self.add_point(self.x00, self.y00);
        }
        self.point = Point::Ignored;
    }

    fn polygon_start(&mut self) {
        self.ring = true;
    }

    fn polygon_end(&mut self) {
        self.ring = false;
    }

    fn sphere(&mut self) {
        unreachable!("d3's measure stream has no sphere: a projection's pre-clip consumes it, and geoPath rejects it otherwise");
    }
}
