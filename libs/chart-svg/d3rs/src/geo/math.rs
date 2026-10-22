//! d3-geo 3.1.1 `src/math.js`: the constants and clamped inverse trig the geo ports share.

pub const EPSILON: f64 = 1e-6;
pub const EPSILON2: f64 = 1e-12;
pub const PI: f64 = std::f64::consts::PI;
pub const HALF_PI: f64 = std::f64::consts::FRAC_PI_2;
pub const QUARTER_PI: f64 = std::f64::consts::FRAC_PI_4;
pub const TAU: f64 = std::f64::consts::TAU;
pub const DEGREES: f64 = 180.0 / PI;
pub const RADIANS: f64 = PI / 180.0;

pub fn acos(x: f64) -> f64 {
    if x > 1.0 {
        0.0
    } else if x < -1.0 {
        PI
    } else {
        libm::acos(x)
    }
}

pub fn asin(x: f64) -> f64 {
    if x > 1.0 {
        HALF_PI
    } else if x < -1.0 {
        -HALF_PI
    } else {
        libm::asin(x)
    }
}
