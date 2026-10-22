//! d3-geo 3.1.1 `src/projection/transverseMercator.js`: `transverseMercatorRaw` and the default
//! `transverseMercator`, a mercator rotated 90° about its pole. Its `center` and `rotate` overrides are the
//! transverse arms of [`Projection::set_center`] and [`Projection::set_rotate`].

use super::mercator::mercator_projection;
use super::{Projection, RawProjection};
use crate::geo::math::HALF_PI;

#[derive(Clone)]
pub struct TransverseMercator;

impl RawProjection for TransverseMercator {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [libm::log(libm::tan((HALF_PI + phi) / 2.0)), -lambda]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some([-y, 2.0 * libm::atan(libm::exp(x)) - HALF_PI])
    }
}

pub fn transverse_mercator() -> Projection {
    let mut p = mercator_projection(TransverseMercator, true);
    p.set_rotate([0.0, 0.0, 0.0]);
    p.set_scale(159.155);
    p
}
