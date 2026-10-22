//! d3-geo 3.1.1 `src/path/bounds.js`: the extent of the points a projected stream delivers (`fit` reads it).

use crate::geo::stream::GeoStream;

/// The extent of the points streamed so far.
pub struct Bounds {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
}

impl Default for Bounds {
    fn default() -> Self {
        Self { x0: f64::INFINITY, y0: f64::INFINITY, x1: f64::NEG_INFINITY, y1: f64::NEG_INFINITY }
    }
}

impl Bounds {
    /// `[[x0, y0], [x1, y1]]`, and the sink starts over.
    pub fn result(&mut self) -> [[f64; 2]; 2] {
        let bounds = [[self.x0, self.y0], [self.x1, self.y1]];
        *self = Self::default();
        bounds
    }
}

impl GeoStream for Bounds {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        if x < self.x0 {
            self.x0 = x;
        }
        if x > self.x1 {
            self.x1 = x;
        }
        if y < self.y0 {
            self.y0 = y;
        }
        if y > self.y1 {
            self.y1 = y;
        }
    }

    fn line_start(&mut self) {}
    fn line_end(&mut self) {}
    fn polygon_start(&mut self) {}
    fn polygon_end(&mut self) {}

    fn sphere(&mut self) {
        unreachable!("d3's boundsStream has no sphere: the pre-clip stage consumes it");
    }
}
