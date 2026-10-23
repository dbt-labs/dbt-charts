//! d3-geo 3.1.1 `src/projection/identity.js`: `geoIdentity`, the planar projection Vega registers as `identity`.
//! It has no sphere, so no rotation, center, pre-clip or resampling; it shares [`Fit`] with [`super::Projection`].

use super::fit::Fit;
use crate::geo::clip::rectangle::clip_rectangle;
use crate::geo::math::{DEGREES, RADIANS};
use crate::geo::stream::{geo_stream, GeoStream};
use crate::geo::transform::{geo_transform, TransformMethods, TransformStream};
use crate::geojson::GeoJson;
use crate::js_math::js_truthy;

pub struct Identity {
    k: f64,
    tx: f64,
    ty: f64,
    sx: f64,
    sy: f64,
    alpha: f64,
    clip_extent: Option<[[f64; 2]; 2]>,
}

/// d3's `projection(p)` closure, copied out so a stream owns it.
#[derive(Clone, Copy)]
pub struct Planar {
    kx: f64,
    ky: f64,
    alpha: f64,
    tx: f64,
    ty: f64,
}

impl Planar {
    fn forward(&self, [px, py]: [f64; 2]) -> [f64; 2] {
        let mut x = px * self.kx;
        let mut y = py * self.ky;
        if js_truthy(self.alpha) {
            let (sa, ca) = (libm::sin(self.alpha), libm::cos(self.alpha));
            let t = y * ca - x * sa;
            x = x * ca + y * sa;
            y = t;
        }
        [x + self.tx, y + self.ty]
    }
}

impl<S: GeoStream> TransformMethods<S> for Planar {
    fn point(&mut self, stream: &mut S, x: f64, y: f64) {
        let [x, y] = self.forward([x, y]);
        stream.point(x, y, None);
    }
}

pub fn identity() -> Identity {
    Identity { k: 1.0, tx: 0.0, ty: 0.0, sx: 1.0, sy: 1.0, alpha: 0.0, clip_extent: None }
}

impl Identity {
    fn planar(&self) -> Planar {
        Planar { kx: self.k * self.sx, ky: self.k * self.sy, alpha: self.alpha, tx: self.tx, ty: self.ty }
    }

    pub fn forward(&self, point: [f64; 2]) -> [f64; 2] {
        self.planar().forward(point)
    }

    pub fn invert(&self, [px, py]: [f64; 2]) -> [f64; 2] {
        let Planar { kx, ky, alpha, tx, ty } = self.planar();
        let mut x = px - tx;
        let mut y = py - ty;
        if js_truthy(alpha) {
            let (sa, ca) = (libm::sin(alpha), libm::cos(alpha));
            let t = y * ca + x * sa;
            x = x * ca - y * sa;
            y = t;
        }
        [x / kx, y / ky]
    }

    /// d3 caches the chain per sink; see [`super::Projection::stream`] for why this builds a fresh one.
    pub fn stream<'a, S: GeoStream + 'a>(&self, sink: S) -> TransformStream<Box<dyn GeoStream + 'a>, Planar> {
        let postclipped: Box<dyn GeoStream + 'a> = match self.clip_extent {
            None => Box::new(sink),
            Some([[x0, y0], [x1, y1]]) => Box::new(clip_rectangle(x0, y0, x1, y1, sink)),
        };
        geo_transform(self.planar()).stream(postclipped)
    }

    pub fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        self.clip_extent = extent;
        self
    }

    pub fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        self.clip_extent
    }

    pub fn set_scale(&mut self, k: f64) -> &mut Self {
        self.k = k;
        self
    }

    pub fn scale(&self) -> f64 {
        self.k
    }

    pub fn set_translate(&mut self, [x, y]: [f64; 2]) -> &mut Self {
        self.tx = x;
        self.ty = y;
        self
    }

    pub fn translate(&self) -> [f64; 2] {
        [self.tx, self.ty]
    }

    pub fn set_angle(&mut self, degrees: f64) -> &mut Self {
        self.alpha = degrees % 360.0 * RADIANS;
        self
    }

    pub fn angle(&self) -> f64 {
        self.alpha * DEGREES
    }

    pub fn set_reflect_x(&mut self, reflect: bool) -> &mut Self {
        self.sx = if reflect { -1.0 } else { 1.0 };
        self
    }

    pub fn reflect_x(&self) -> bool {
        self.sx < 0.0
    }

    pub fn set_reflect_y(&mut self, reflect: bool) -> &mut Self {
        self.sy = if reflect { -1.0 } else { 1.0 };
        self
    }

    pub fn reflect_y(&self) -> bool {
        self.sy < 0.0
    }
}

impl Fit for Identity {
    fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        Identity::clip_extent(self)
    }

    fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        Identity::set_clip_extent(self, extent)
    }

    fn set_scale(&mut self, k: f64) -> &mut Self {
        Identity::set_scale(self, k)
    }

    fn set_translate(&mut self, translate: [f64; 2]) -> &mut Self {
        Identity::set_translate(self, translate)
    }

    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        let mut stream = self.stream(sink);
        geo_stream(object, &mut stream);
        stream.error().map_or(Ok(()), |message| Err(message.to_string()))
    }
}
