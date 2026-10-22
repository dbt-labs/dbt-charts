//! d3-geo 3.1.1 `src/projection/equirectangular.js`: `equirectangularRaw`, the identity on (λ, φ), and the
//! default `equirectangular`.

use super::{projection, Projection, RawProjection};

#[derive(Clone)]
pub struct Equirectangular;

impl RawProjection for Equirectangular {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [lambda, phi]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some([x, y])
    }
}

pub fn equirectangular() -> Projection {
    let mut p = projection(Equirectangular);
    p.set_scale(152.63);
    p
}
