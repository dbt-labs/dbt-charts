//! d3-geo 3.1.1 `src/pointEqual.js`.

use super::math::EPSILON;

pub fn point_equal(a: [f64; 2], b: [f64; 2]) -> bool {
    (a[0] - b[0]).abs() < EPSILON && (a[1] - b[1]).abs() < EPSILON
}
