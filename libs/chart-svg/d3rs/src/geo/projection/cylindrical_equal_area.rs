//! d3-geo 3.1.1 `src/projection/cylindricalEqualArea.js`: `cylindricalEqualAreaRaw`, what `conicEqualArea`
//! becomes when its parallels are symmetrical around the equator. d3-geo exports no projection built on it.

use super::RawProjection;
use crate::geo::math::asin;

#[derive(Clone)]
pub struct CylindricalEqualArea {
    cos_phi0: f64,
}

impl CylindricalEqualArea {
    pub fn new(phi0: f64) -> Self {
        Self { cos_phi0: libm::cos(phi0) }
    }
}

impl RawProjection for CylindricalEqualArea {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [lambda * self.cos_phi0, libm::sin(phi) / self.cos_phi0]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some([x / self.cos_phi0, asin(y * self.cos_phi0)])
    }
}
