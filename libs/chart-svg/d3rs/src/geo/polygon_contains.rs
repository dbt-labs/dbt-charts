//! d3-geo 3.1.1 `src/polygonContains.js`.

use super::cartesian::{cartesian, cartesian_cross, cartesian_normalize_in_place};
use super::math::{asin, EPSILON, EPSILON2, HALF_PI, PI, QUARTER_PI, TAU};
use super::Adder;

fn longitude(point: [f64; 2]) -> f64 {
    if point[0].abs() <= PI {
        point[0]
    } else {
        point[0].signum() * ((point[0].abs() + PI) % TAU - PI)
    }
}

/// Whether `point` (radians) is inside `polygon`, the rings of radian points without their closing point.
/// Every ring has at least one point (`ringEnd` indexes `ring[0]`).
pub fn polygon_contains(polygon: &[Vec<[f64; 2]>], point: [f64; 2]) -> bool {
    let lambda = longitude(point);
    let mut phi = point[1];
    let sin_phi = libm::sin(phi);
    let normal = [libm::sin(lambda), -libm::cos(lambda), 0.0];
    let mut angle = 0.0;
    let mut winding = 0_i32;
    let mut sum = Adder::default();

    if sin_phi == 1.0 {
        phi = HALF_PI + EPSILON;
    } else if sin_phi == -1.0 {
        phi = -HALF_PI - EPSILON;
    }

    for ring in polygon {
        let mut point0 = ring[ring.len() - 1];
        let mut lambda0 = longitude(point0);
        let phi0 = point0[1] / 2.0 + QUARTER_PI;
        let mut sin_phi0 = libm::sin(phi0);
        let mut cos_phi0 = libm::cos(phi0);

        for &point1 in ring {
            let lambda1 = longitude(point1);
            let phi1 = point1[1] / 2.0 + QUARTER_PI;
            let sin_phi1 = libm::sin(phi1);
            let cos_phi1 = libm::cos(phi1);
            let delta = lambda1 - lambda0;
            let sign = if delta >= 0.0 { 1.0 } else { -1.0 };
            let abs_delta = sign * delta;
            let antimeridian = abs_delta > PI;
            let k = sin_phi0 * sin_phi1;

            sum.add(libm::atan2(k * sign * libm::sin(abs_delta), cos_phi0 * cos_phi1 + k * libm::cos(abs_delta)));
            angle += if antimeridian { delta + sign * TAU } else { delta };

            // Are the longitudes either side of the point's meridian (lambda),
            // and are the latitudes smaller than the parallel (phi)?
            if antimeridian ^ (lambda0 >= lambda) ^ (lambda1 >= lambda) {
                let mut arc = cartesian_cross(cartesian(point0[0], point0[1]), cartesian(point1[0], point1[1]));
                cartesian_normalize_in_place(&mut arc);
                let mut intersection = cartesian_cross(normal, arc);
                cartesian_normalize_in_place(&mut intersection);
                let reversed = antimeridian ^ (delta >= 0.0);
                let phi_arc = (if reversed { -1.0 } else { 1.0 }) * asin(intersection[2]);
                if phi > phi_arc || phi == phi_arc && (arc[0] != 0.0 || arc[1] != 0.0) {
                    winding += if reversed { 1 } else { -1 };
                }
            }

            lambda0 = lambda1;
            sin_phi0 = sin_phi1;
            cos_phi0 = cos_phi1;
            point0 = point1;
        }
    }

    // First, determine whether the South pole is inside or outside:
    //
    // It is inside if:
    // * the polygon winds around it in a clockwise direction.
    // * the polygon does not (cumulatively) wind around it, but has a negative
    //   (counter-clockwise) area.
    //
    // Second, count the (signed) number of times a segment crosses a lambda
    // from the point to the South pole.  If it is zero, then the point is the
    // same side as the South pole.

    (angle < -EPSILON || angle < EPSILON && sum.value() < -EPSILON2) ^ ((winding & 1) == 1)
}
