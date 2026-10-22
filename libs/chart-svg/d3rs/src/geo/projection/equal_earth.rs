//! d3-geo 3.1.1 `src/projection/equalEarth.js`: `equalEarthRaw` and the default `equalEarth`.

use super::{projection, Projection, RawProjection};
use crate::geo::math::{asin, EPSILON2};

const A1: f64 = 1.340264;
const A2: f64 = -0.081106;
const A3: f64 = 0.000893;
const A4: f64 = 0.003796;
const ITERATIONS: usize = 12;

fn m() -> f64 {
    libm::sqrt(3.0) / 2.0
}

#[derive(Clone)]
pub struct EqualEarth;

impl RawProjection for EqualEarth {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        let m = m();
        let l = asin(m * libm::sin(phi));
        let l2 = l * l;
        let l6 = l2 * l2 * l2;
        [
            lambda * libm::cos(l) / (m * (A1 + 3.0 * A2 * l2 + l6 * (7.0 * A3 + 9.0 * A4 * l2))),
            l * (A1 + A2 * l2 + l6 * (A3 + A4 * l2)),
        ]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        let m = m();
        let mut l = y;
        let mut l2 = l * l;
        let mut l6 = l2 * l2 * l2;
        for _ in 0..ITERATIONS {
            let fy = l * (A1 + A2 * l2 + l6 * (A3 + A4 * l2)) - y;
            let fpy = A1 + 3.0 * A2 * l2 + l6 * (7.0 * A3 + 9.0 * A4 * l2);
            let delta = fy / fpy;
            l -= delta;
            l2 = l * l;
            l6 = l2 * l2 * l2;
            if delta.abs() < EPSILON2 {
                break;
            }
        }
        Some([
            m * x * (A1 + 3.0 * A2 * l2 + l6 * (7.0 * A3 + 9.0 * A4 * l2)) / libm::cos(l),
            asin(libm::sin(l) / m),
        ])
    }
}

pub fn equal_earth() -> Projection {
    let mut p = projection(EqualEarth);
    p.set_scale(177.158);
    p
}
