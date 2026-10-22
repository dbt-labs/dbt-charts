//! d3-geo 3.1.1 `src/projection/gnomonic.js`: `gnomonicRaw` and the default `gnomonic`.

use super::azimuthal::azimuthal_invert;
use super::{projection, Projection, RawProjection};

#[derive(Clone)]
pub struct Gnomonic;

impl RawProjection for Gnomonic {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        let cy = libm::cos(y);
        let k = libm::cos(x) * cy;
        [cy * libm::sin(x) / k, libm::sin(y) / k]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some(azimuthal_invert(libm::atan, x, y))
    }
}

pub fn gnomonic() -> Projection {
    let mut p = projection(Gnomonic);
    p.set_scale(144.049).set_clip_angle(60.0);
    p
}
