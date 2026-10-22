//! d3-geo 3.1.1 `src/cartesian.js`: unit-sphere vectors.

use super::math::asin;

pub fn spherical(c: [f64; 3]) -> [f64; 2] {
    [libm::atan2(c[1], c[0]), asin(c[2])]
}

pub fn cartesian(lambda: f64, phi: f64) -> [f64; 3] {
    let cos_phi = libm::cos(phi);
    [cos_phi * libm::cos(lambda), cos_phi * libm::sin(lambda), libm::sin(phi)]
}

pub fn cartesian_dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub fn cartesian_cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

pub fn cartesian_add_in_place(a: &mut [f64; 3], b: [f64; 3]) {
    a[0] += b[0];
    a[1] += b[1];
    a[2] += b[2];
}

pub fn cartesian_scale(vector: [f64; 3], k: f64) -> [f64; 3] {
    [vector[0] * k, vector[1] * k, vector[2] * k]
}

pub fn cartesian_normalize_in_place(d: &mut [f64; 3]) {
    let l = libm::sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    d[0] /= l;
    d[1] /= l;
    d[2] /= l;
}
