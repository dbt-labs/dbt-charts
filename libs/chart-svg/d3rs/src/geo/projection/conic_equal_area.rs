//! d3-geo 3.1.1 `src/projection/conicEqualArea.js`: `conicEqualAreaRaw` and the default `conicEqualArea`.

use super::conic::conic_projection;
use super::cylindrical_equal_area::CylindricalEqualArea;
use super::{Projection, RawProjection};
use crate::geo::math::{asin, EPSILON, PI};
use crate::js_math::js_sign;

#[derive(Clone)]
struct ConicEqualArea {
    n: f64,
    c: f64,
    r0: f64,
}

impl RawProjection for ConicEqualArea {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let r = libm::sqrt(self.c - 2.0 * self.n * libm::sin(y)) / self.n;
        let x = x * self.n;
        [r * libm::sin(x), self.r0 - r * libm::cos(x)]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let r0y = self.r0 - y;
        let mut l = libm::atan2(x, r0y.abs()) * js_sign(r0y);
        if r0y * self.n < 0.0 {
            l -= PI * js_sign(x) * js_sign(r0y);
        }
        Some([l / self.n, asin((self.c - (x * x + r0y * r0y) * self.n * self.n) / (2.0 * self.n))])
    }
}

/// `conicEqualAreaRaw(y0, y1)`.
pub fn conic_equal_area_raw(y0: f64, y1: f64) -> Box<dyn RawProjection> {
    let sy0 = libm::sin(y0);
    let n = (sy0 + libm::sin(y1)) / 2.0;
    if n.abs() < EPSILON {
        return Box::new(CylindricalEqualArea::new(y0));
    }
    let c = 1.0 + sy0 * (2.0 * n - sy0);
    Box::new(ConicEqualArea { n, c, r0: libm::sqrt(c) / n })
}

pub fn conic_equal_area() -> Projection {
    let mut p = conic_projection(conic_equal_area_raw);
    p.set_scale(155.424).set_center([0.0, 33.6442]);
    p
}
