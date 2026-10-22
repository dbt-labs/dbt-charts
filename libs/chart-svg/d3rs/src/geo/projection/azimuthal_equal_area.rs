//! d3-geo 3.1.1 `src/projection/azimuthalEqualArea.js`: `azimuthalEqualAreaRaw` and the default
//! `azimuthalEqualArea`.

use super::azimuthal::{azimuthal_forward, azimuthal_invert};
use super::{projection, Projection, RawProjection};
use crate::geo::math::asin;

#[derive(Clone)]
pub struct AzimuthalEqualArea;

impl RawProjection for AzimuthalEqualArea {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        azimuthal_forward(|cxcy| libm::sqrt(2.0 / (1.0 + cxcy)), x, y)
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some(azimuthal_invert(|z| 2.0 * asin(z / 2.0), x, y))
    }
}

pub fn azimuthal_equal_area() -> Projection {
    let mut p = projection(AzimuthalEqualArea);
    p.set_scale(124.75).set_clip_angle(180.0 - 1e-3);
    p
}
