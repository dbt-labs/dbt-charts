//! d3-geo 3.1.1 `src/projection/conicEquidistant.js`: `conicEquidistantRaw` and the default
//! `conicEquidistant`. With no cone (n near 0) the raw projection is equirectangular's.

use super::conic::conic_projection;
use super::equirectangular::Equirectangular;
use super::{Projection, RawProjection};
use crate::geo::math::{EPSILON, PI};
use crate::js_math::js_sign;

#[derive(Clone)]
struct ConicEquidistant {
    n: f64,
    g: f64,
}

impl RawProjection for ConicEquidistant {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let gy = self.g - y;
        let nx = self.n * x;
        [gy * libm::sin(nx), self.g - gy * libm::cos(nx)]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let gy = self.g - y;
        let mut l = libm::atan2(x, gy.abs()) * js_sign(gy);
        if gy * self.n < 0.0 {
            l -= PI * js_sign(x) * js_sign(gy);
        }
        Some([l / self.n, self.g - js_sign(self.n) * libm::sqrt(x * x + gy * gy)])
    }
}

/// `conicEquidistantRaw(y0, y1)`.
pub fn conic_equidistant_raw(y0: f64, y1: f64) -> Box<dyn RawProjection> {
    let cy0 = libm::cos(y0);
    let n = if y0 == y1 { libm::sin(y0) } else { (cy0 - libm::cos(y1)) / (y1 - y0) };
    let g = cy0 / n + y0;
    if n.abs() < EPSILON {
        return Box::new(Equirectangular);
    }
    Box::new(ConicEquidistant { n, g })
}

pub fn conic_equidistant() -> Projection {
    let mut p = conic_projection(conic_equidistant_raw);
    p.set_scale(131.154).set_center([0.0, 13.9389]);
    p
}
