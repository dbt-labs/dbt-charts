//! d3-geo 3.1.1 `src/projection/orthographic.js`: `orthographicRaw` and the default `orthographic`.

use super::azimuthal::azimuthal_invert;
use super::{projection, Projection, RawProjection};
use crate::geo::math::{asin, EPSILON};

#[derive(Clone)]
pub struct Orthographic;

impl RawProjection for Orthographic {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        [libm::cos(y) * libm::sin(x), libm::sin(y)]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some(azimuthal_invert(asin, x, y))
    }
}

pub fn orthographic() -> Projection {
    let mut p = projection(Orthographic);
    p.set_scale(249.5).set_clip_angle(90.0 + EPSILON);
    p
}
