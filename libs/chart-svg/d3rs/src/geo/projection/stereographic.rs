//! d3-geo 3.1.1 `src/projection/stereographic.js`: `stereographicRaw` and the default `stereographic`.

use super::azimuthal::azimuthal_invert;
use super::{projection, Projection, RawProjection};

#[derive(Clone)]
pub struct Stereographic;

impl RawProjection for Stereographic {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let cy = libm::cos(y);
        let k = 1.0 + libm::cos(x) * cy;
        [cy * libm::sin(x) / k, libm::sin(y) / k]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some(azimuthal_invert(|z| 2.0 * libm::atan(z), x, y))
    }
}

pub fn stereographic() -> Projection {
    let mut p = projection(Stereographic);
    p.set_scale(250.0).set_clip_angle(142.0);
    p
}
