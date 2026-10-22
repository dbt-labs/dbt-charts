//! d3-geo 3.1.1 `src/projection/conic.js`: `conicProjection`, which gives a mutator its `parallels`.

use super::{projection_mutator, Projection, RawProjection};
use crate::geo::math::PI;

/// `project_at(phi0, phi1)` builds the raw projection from the two standard parallels, in radians.
pub fn conic_projection(project_at: impl Fn(f64, f64) -> Box<dyn RawProjection> + 'static) -> Projection {
    let parallels = [0.0, PI / 3.0];
    let mut p = projection_mutator(move |args| project_at(args[0], args[1]), &parallels);
    p.parallels = Some(parallels);
    p
}
