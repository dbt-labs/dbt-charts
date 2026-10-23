//! d3-geo 3.1.1 `src/path/centroid.js`: the planar centroid of a projected stream, by the highest dimension
//! present (polygon area, else line length, else points).

use crate::geo::stream::GeoStream;
use crate::js_math::js_truthy;

#[derive(Clone, Copy)]
enum Point {
    Isolated,
    FirstLine,
    Line,
    FirstRing,
    Ring,
}

/// d3 declares `x00`, `y00`, `x0`, `y0` without a value, so an empty ring reads `undefined`: NaN here.
/// `x{0,1,2}_sum`/`z{0,1,2}` are d3's `X0..Z2`: points, line lengths, ring areas.
pub struct PathCentroid {
    x0_sum: f64,
    y0_sum: f64,
    z0: f64,
    x1_sum: f64,
    y1_sum: f64,
    z1: f64,
    x2_sum: f64,
    y2_sum: f64,
    z2: f64,
    in_polygon: bool,
    point: Point,
    x00: f64,
    y00: f64,
    x0: f64,
    y0: f64,
}

impl Default for PathCentroid {
    fn default() -> Self {
        Self {
            x0_sum: 0.0,
            y0_sum: 0.0,
            z0: 0.0,
            x1_sum: 0.0,
            y1_sum: 0.0,
            z1: 0.0,
            x2_sum: 0.0,
            y2_sum: 0.0,
            z2: 0.0,
            in_polygon: false,
            point: Point::Isolated,
            x00: f64::NAN,
            y00: f64::NAN,
            x0: f64::NAN,
            y0: f64::NAN,
        }
    }
}

impl PathCentroid {
    /// `[x, y]`, or `[NaN, NaN]` for an empty stream; the sink starts over.
    pub fn result(&mut self) -> [f64; 2] {
        let centroid = if js_truthy(self.z2) {
            [self.x2_sum / self.z2, self.y2_sum / self.z2]
        } else if js_truthy(self.z1) {
            [self.x1_sum / self.z1, self.y1_sum / self.z1]
        } else if js_truthy(self.z0) {
            [self.x0_sum / self.z0, self.y0_sum / self.z0]
        } else {
            [f64::NAN, f64::NAN]
        };
        *self = Self::default();
        centroid
    }

    fn add_point(&mut self, x: f64, y: f64) {
        self.x0_sum += x;
        self.y0_sum += y;
        self.z0 += 1.0;
    }

    /// The edge from the previous point, weighted by its length.
    fn add_edge(&mut self, x: f64, y: f64) {
        let (dx, dy) = (x - self.x0, y - self.y0);
        let z = libm::sqrt(dx * dx + dy * dy);
        self.x1_sum += z * (self.x0 + x) / 2.0;
        self.y1_sum += z * (self.y0 + y) / 2.0;
        self.z1 += z;
    }

    fn add_ring_point(&mut self, x: f64, y: f64) {
        self.add_edge(x, y);
        let z = self.y0 * x - self.x0 * y;
        self.x2_sum += z * (self.x0 + x);
        self.y2_sum += z * (self.y0 + y);
        self.z2 += z * 3.0;
        self.x0 = x;
        self.y0 = y;
        self.add_point(x, y);
    }
}

impl GeoStream for PathCentroid {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        match self.point {
            Point::Isolated => self.add_point(x, y),
            Point::FirstLine => {
                self.point = Point::Line;
                self.x0 = x;
                self.y0 = y;
                self.add_point(x, y);
            }
            Point::Line => {
                self.add_edge(x, y);
                self.x0 = x;
                self.y0 = y;
                self.add_point(x, y);
            }
            Point::FirstRing => {
                self.point = Point::Ring;
                self.x00 = x;
                self.x0 = x;
                self.y00 = y;
                self.y0 = y;
                self.add_point(x, y);
            }
            Point::Ring => self.add_ring_point(x, y),
        }
    }

    fn line_start(&mut self) {
        self.point = if self.in_polygon { Point::FirstRing } else { Point::FirstLine };
    }

    fn line_end(&mut self) {
        if self.in_polygon {
            self.add_ring_point(self.x00, self.y00);
        } else {
            self.point = Point::Isolated;
        }
    }

    fn polygon_start(&mut self) {
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        self.in_polygon = false;
        self.point = Point::Isolated;
    }

    fn sphere(&mut self) {
        unreachable!("d3's centroid stream has no sphere: a projection's pre-clip consumes it, and geoPath rejects it otherwise");
    }
}
