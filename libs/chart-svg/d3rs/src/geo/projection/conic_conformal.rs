//! d3-geo 3.1.1 `src/projection/conicConformal.js`: `conicConformalRaw` and the default `conicConformal`.
//! With no cone (n is 0) the raw projection is mercator's.

use super::conic::conic_projection;
use super::mercator::Mercator;
use super::{Projection, RawProjection};
use crate::geo::math::{EPSILON, HALF_PI, PI};
use crate::js_math::{js_sign, js_truthy};

fn tany(y: f64) -> f64 {
    libm::tan((HALF_PI + y) / 2.0)
}

#[derive(Clone)]
struct ConicConformal {
    n: f64,
    f: f64,
}

impl RawProjection for ConicConformal {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let y = if self.f > 0.0 {
            if y < -HALF_PI + EPSILON {
                -HALF_PI + EPSILON
            } else {
                y
            }
        } else if y > HALF_PI - EPSILON {
            HALF_PI - EPSILON
        } else {
            y
        };
        let r = self.f / libm::pow(tany(y), self.n);
        [r * libm::sin(self.n * x), self.f - r * libm::cos(self.n * x)]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let fy = self.f - y;
        let r = js_sign(self.n) * libm::sqrt(x * x + fy * fy);
        let mut l = libm::atan2(x, fy.abs()) * js_sign(fy);
        if fy * self.n < 0.0 {
            l -= PI * js_sign(x) * js_sign(fy);
        }
        Some([l / self.n, 2.0 * libm::atan(libm::pow(self.f / r, 1.0 / self.n)) - HALF_PI])
    }
}

/// `conicConformalRaw(y0, y1)`.
pub fn conic_conformal_raw(y0: f64, y1: f64) -> Box<dyn RawProjection> {
    let cy0 = libm::cos(y0);
    let n = if y0 == y1 { libm::sin(y0) } else { libm::log(cy0 / libm::cos(y1)) / libm::log(tany(y1) / tany(y0)) };
    let f = cy0 * libm::pow(tany(y0), n) / n;
    if !js_truthy(n) {
        return Box::new(Mercator);
    }
    Box::new(ConicConformal { n, f })
}

pub fn conic_conformal() -> Projection {
    let mut p = conic_projection(conic_conformal_raw);
    p.set_scale(109.5).set_parallels([30.0, 30.0]);
    p
}
