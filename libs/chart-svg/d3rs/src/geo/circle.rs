//! d3-geo 3.1.1 `src/circle.js`: `circleStream`, the small-circle walk `clipCircle` interpolates along.

use super::cartesian::{cartesian, cartesian_normalize_in_place, spherical};
use super::math::{acos, EPSILON, TAU};
use super::stream::GeoStream;

/// Generates a circle centered at [0°, 0°], with a given radius and precision. `from_to` is d3's `t0`/`t1`
/// pair: both given, or both absent for the whole circle.
pub fn circle_stream(
    stream: &mut impl GeoStream,
    radius: f64,
    delta: f64,
    direction: i32,
    from_to: Option<([f64; 2], [f64; 2])>,
) {
    if delta == 0.0 || delta.is_nan() {
        return;
    }
    let cos_radius = libm::cos(radius);
    let sin_radius = libm::sin(radius);
    let direction = f64::from(direction);
    let step = direction * delta;
    let (mut t, t1) = match from_to {
        None => (radius + direction * TAU, radius - step / 2.0),
        Some((from, to)) => {
            let mut t0 = circle_radius(cos_radius, from);
            let t1 = circle_radius(cos_radius, to);
            if if direction > 0.0 { t0 < t1 } else { t0 > t1 } {
                t0 += direction * TAU;
            }
            (t0, t1)
        }
    };
    while if direction > 0.0 { t > t1 } else { t < t1 } {
        let point = spherical([cos_radius, -sin_radius * libm::cos(t), -sin_radius * libm::sin(t)]);
        stream.point(point[0], point[1], None);
        t -= step;
    }
}

/// Returns the signed angle of a cartesian point relative to [cosRadius, 0, 0].
fn circle_radius(cos_radius: f64, point: [f64; 2]) -> f64 {
    let mut point = cartesian(point[0], point[1]);
    point[0] -= cos_radius;
    cartesian_normalize_in_place(&mut point);
    let radius = acos(-point[1]);
    ((if -point[2] < 0.0 { -radius } else { radius }) + TAU - EPSILON) % TAU
}
