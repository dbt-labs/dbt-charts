//! d3-geo 3.1.1 `src/projection/azimuthal.js`: `azimuthalRaw` and `azimuthalInvert`, the forward and inverse
//! the azimuthal projections are built from. Each takes the projection's own `scale` or `angle` function.

use crate::geo::math::asin;
use crate::js_math::js_truthy;

/// `azimuthalRaw(scale)(x, y)`; where `scale` is infinite (the antipode) d3 returns [2, 0].
pub fn azimuthal_forward(scale: fn(f64) -> f64, x: f64, y: f64) -> [f64; 2] {
    let cx = libm::cos(x);
    let cy = libm::cos(y);
    let k = scale(cx * cy);
    if k == f64::INFINITY {
        return [2.0, 0.0];
    }
    [k * cy * libm::sin(x), k * libm::sin(y)]
}

/// `azimuthalInvert(angle)(x, y)`; at the center (`z` falsy) the latitude is `asin(z)`.
pub fn azimuthal_invert(angle: fn(f64) -> f64, x: f64, y: f64) -> [f64; 2] {
    let z = libm::sqrt(x * x + y * y);
    let c = angle(z);
    let sc = libm::sin(c);
    let cc = libm::cos(c);
    [libm::atan2(x * sc, z * cc), asin(if js_truthy(z) { y * sc / z } else { z })]
}
