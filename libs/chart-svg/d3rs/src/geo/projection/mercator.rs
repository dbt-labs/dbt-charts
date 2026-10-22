//! d3-geo 3.1.1 `src/projection/mercator.js`: `mercatorRaw`, `mercatorProjection` and the default `mercator`.
//!
//! `mercatorProjection` overrides `scale`, `translate`, `center` and `clipExtent` on its projection to
//! `reclip`: the post-clip becomes the world's square (`±π·scale` around where [0, 0] lands), intersected
//! with the authored `clipExtent`. `rotate` does not reclip, so a rotation takes effect on the clip at the
//! next scale, translate, center or clipExtent call.

use super::{projection, Projection, RawProjection};
use crate::geo::math::{HALF_PI, PI, TAU};
use crate::geo::rotation::geo_rotation;
use crate::js_math::{js_max, js_min};

#[derive(Clone)]
pub struct Mercator;

impl RawProjection for Mercator {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2] {
        [lambda, libm::log(libm::tan((HALF_PI + phi) / 2.0))]
    }

    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]> {
        Some([x, 2.0 * libm::atan(libm::exp(y)) - HALF_PI])
    }
}

/// The state `mercatorProjection` keeps in its closure.
#[derive(Clone, Copy)]
pub(super) struct MercatorClip {
    /// `project !== mercatorRaw`, which clamps y to the clip extent instead of x. transverseMercator is the
    /// only such projection, and its `center` and `rotate` overrides ride on the same flag.
    pub transverse: bool,
    /// The authored `clipExtent` (d3's `x0, y0, x1, y1`), which `clipExtent()` returns.
    pub extent: Option<[[f64; 2]; 2]>,
}

/// `mercatorProjection(project)`.
pub(super) fn mercator_projection(project: impl RawProjection + Clone + 'static, transverse: bool) -> Projection {
    let mut p = projection(project);
    p.mercator = Some(MercatorClip { transverse, extent: None });
    p.reclip_mercator();
    p
}

/// `mercator()`.
pub fn mercator() -> Projection {
    let mut p = mercator_projection(Mercator, false);
    p.set_scale(961.0 / TAU);
    p
}

impl Projection {
    pub(super) fn is_transverse(&self) -> bool {
        self.mercator.is_some_and(|mercator| mercator.transverse)
    }

    /// `reclip`; a projection that is not a `mercatorProjection` has none.
    pub(super) fn reclip_mercator(&mut self) -> &mut Self {
        let Some(MercatorClip { transverse, extent }) = self.mercator else {
            return self;
        };
        let k = PI * self.scale();
        let t = self.forward(geo_rotation(self.rotate()).invert([0.0, 0.0]));
        let clip = match extent {
            None => [[t[0] - k, t[1] - k], [t[0] + k, t[1] + k]],
            Some([[x0, y0], [x1, y1]]) if !transverse => {
                [[js_max(t[0] - k, x0), y0], [js_min(t[0] + k, x1), y1]]
            }
            Some([[x0, y0], [x1, y1]]) => [[x0, js_max(t[1] - k, y0)], [x1, js_min(t[1] + k, y1)]],
        };
        self.set_base_clip_extent(Some(clip))
    }
}
