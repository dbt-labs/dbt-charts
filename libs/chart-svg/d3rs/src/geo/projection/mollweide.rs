//! d3-geo-projection 4.0.0 `src/mollweide.js`: `mollweideBromleyTheta`, `mollweideBromleyRaw` and the
//! `mollweideRaw` and default `mollweide` built from them. The Newton iteration stops when the step is at
//! most ε = 1e-6 or after 30 steps.

use super::{projection, Projection, RawProjection};
use crate::geo::math::{asin, EPSILON, HALF_PI, PI};

const ITERATIONS: usize = 30;

/// `mollweideBromleyTheta(cp, phi)`.
fn theta(cp: f64, mut phi: f64) -> f64 {
    let cpsin_phi = cp * libm::sin(phi);
    let mut i = ITERATIONS;
    loop {
        let delta = (phi + libm::sin(phi) - cpsin_phi) / (1.0 + libm::cos(phi));
        phi -= delta;
        i -= 1;
        if !(delta.abs() > EPSILON && i > 0) {
            break;
        }
    }
    phi / 2.0
}

/// `mollweideBromleyRaw(cx, cy, cp)`.
#[derive(Clone)]
pub struct MollweideBromley {
    cx: f64,
    cy: f64,
    cp: f64,
}

impl RawProjection for MollweideBromley {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        let phi = theta(self.cp, phi);
        [self.cx * lambda * libm::cos(phi), self.cy * libm::sin(phi)]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let y = asin(y / self.cy);
        Some([x / (self.cx * libm::cos(y)), asin((2.0 * y + libm::sin(2.0 * y)) / self.cp)])
    }
}

/// `mollweideRaw = mollweideBromleyRaw(sqrt2 / halfPi, sqrt2, pi)`.
pub fn mollweide_raw() -> MollweideBromley {
    let sqrt2 = libm::sqrt(2.0);
    MollweideBromley { cx: sqrt2 / HALF_PI, cy: sqrt2, cp: PI }
}

pub fn mollweide() -> Projection {
    let mut p = projection(mollweide_raw());
    p.set_scale(169.529);
    p
}
