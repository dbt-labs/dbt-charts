//! vega-util's zoom space: the lift a scale's domain is padded and panned in, and the ground back.

use d3rs::js_math::js_sign;
use d3rs::scale::Transform;

/// vega-util's lift into a scale's zoom space and the ground back (`zoomLog`,
/// `zoomPow`, `zoomSymlog`, and the identity of `zoomLinear`): d3's transform,
/// a log domain read through the sign of its first end.
pub fn lift_ground(
    transform: Transform,
    domain0: f64,
) -> (impl Fn(f64) -> f64, impl Fn(f64) -> f64) {
    let sign = if matches!(transform, Transform::Log { .. }) {
        js_sign(domain0)
    } else {
        1.0
    };
    (
        move |x| transform.apply(false, sign * x),
        move |x| sign * transform.undo(false, x),
    )
}
