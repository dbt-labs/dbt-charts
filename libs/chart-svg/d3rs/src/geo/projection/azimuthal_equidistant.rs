//! d3-geo 3.1.1 `src/projection/azimuthalEquidistant.js`: `azimuthalEquidistantRaw` and the default
//! `azimuthalEquidistant`.

use super::azimuthal::{azimuthal_forward, azimuthal_invert};
use super::{projection, Projection, RawProjection};
use crate::geo::math::acos;
use crate::js_math::js_truthy;

#[derive(Clone)]
pub struct AzimuthalEquidistant;

impl RawProjection for AzimuthalEquidistant {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        azimuthal_forward(
            |c| {
                let c = acos(c);
                if js_truthy(c) {
                    c / libm::sin(c)
                } else {
                    c
                }
            },
            x,
            y,
        )
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some(azimuthal_invert(|z| z, x, y))
    }
}

pub fn azimuthal_equidistant() -> Projection {
    let mut p = projection(AzimuthalEquidistant);
    p.set_scale(79.4188).set_clip_angle(180.0 - 1e-3);
    p
}
