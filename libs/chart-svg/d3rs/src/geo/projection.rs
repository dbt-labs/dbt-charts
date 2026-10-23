//! d3-geo 3.1.1 `src/projection/index.js`: `projection`, `projectionMutator` and the scale/translate/rotate
//! transforms. A `Projection` wraps a raw (λ, φ) → (x, y) function with center, rotate, scale, translate,
//! reflection, pre- and post-clipping and adaptive resampling, and builds d3's stream chain.
//!
//! `stream(sink)` builds a fresh chain per call; an error d3 would throw reaches the caller through
//! `GeoStream::error`. `RawProjection::invert` returns `None` where d3's is absent or returns `null`.

pub mod albers;
pub mod albers_usa;
pub mod azimuthal;
pub mod azimuthal_equal_area;
pub mod azimuthal_equidistant;
pub mod conic;
pub mod conic_conformal;
pub mod conic_equal_area;
pub mod conic_equidistant;
pub mod cylindrical_equal_area;
pub mod equal_earth;
pub mod equirectangular;
pub mod fit;
pub mod gnomonic;
pub mod identity;
pub mod mercator;
pub mod mollweide;
pub mod natural_earth1;
pub mod orthographic;
pub mod resample;
pub mod stereographic;
pub mod transverse_mercator;
pub mod vega;

use super::clip::antimeridian::clip_antimeridian;
use super::clip::circle::clip_circle;
use super::clip::rectangle::clip_rectangle;
use super::compose::Invertible;
use super::math::{DEGREES, RADIANS};
use super::rotation::{rotate_radians, Rotation};
use super::stream::{geo_stream, GeoStream};
use super::transform::{geo_transform, TransformMethods, TransformStream};
use crate::geojson::GeoJson;
use crate::js_math::js_truthy;
use resample::resample;

/// A raw projection: λ and φ in radians to planar coordinates.
pub trait RawProjection {
    fn forward(&self, lambda: f64, phi: f64) -> [f64; 2];
    fn invert(&self, x: f64, y: f64) -> Option<[f64; 2]>;
}

type ProjectAt = Box<dyn Fn(&[f64]) -> Box<dyn RawProjection>>;
type Preclip = Box<dyn for<'a> Fn(Box<dyn GeoStream + 'a>) -> Box<dyn GeoStream + 'a>>;
type Postclip = Box<dyn for<'a> Fn(Box<dyn GeoStream + 'a>) -> Box<dyn GeoStream + 'a>>;

/// `scaleTranslate` and `scaleTranslateRotate`: d3 returns a different closure when `alpha` is falsy.
#[derive(Clone, Copy)]
enum ScaleTranslateRotate {
    ScaleTranslate { k: f64, dx: f64, dy: f64, sx: f64, sy: f64 },
    Rotated { a: f64, b: f64, ai: f64, bi: f64, ci: f64, fi: f64, dx: f64, dy: f64, sx: f64, sy: f64 },
}

fn scale_translate_rotate(k: f64, dx: f64, dy: f64, sx: f64, sy: f64, alpha: f64) -> ScaleTranslateRotate {
    if !js_truthy(alpha) {
        return ScaleTranslateRotate::ScaleTranslate { k, dx, dy, sx, sy };
    }
    let cos_alpha = libm::cos(alpha);
    let sin_alpha = libm::sin(alpha);
    ScaleTranslateRotate::Rotated {
        a: cos_alpha * k,
        b: sin_alpha * k,
        ai: cos_alpha / k,
        bi: sin_alpha / k,
        ci: (sin_alpha * dy - cos_alpha * dx) / k,
        fi: (sin_alpha * dx + cos_alpha * dy) / k,
        dx,
        dy,
        sx,
        sy,
    }
}

impl Invertible for ScaleTranslateRotate {
    fn forward(&self, x: f64, y: f64) -> [f64; 2] {
        match *self {
            Self::ScaleTranslate { k, dx, dy, sx, sy } => {
                let (x, y) = (x * sx, y * sy);
                [dx + k * x, dy - k * y]
            }
            Self::Rotated { a, b, dx, dy, sx, sy, .. } => {
                let (x, y) = (x * sx, y * sy);
                [a * x - b * y + dx, dy - b * x - a * y]
            }
        }
    }

    fn invert(&self, x: f64, y: f64) -> [f64; 2] {
        match *self {
            Self::ScaleTranslate { k, dx, dy, sx, sy } => [(x - dx) / k * sx, (dy - y) / k * sy],
            Self::Rotated { ai, bi, ci, fi, sx, sy, .. } => {
                [sx * (ai * x - bi * y + ci), sy * (fi - bi * x - ai * y)]
            }
        }
    }
}

/// `transformRadians`.
#[derive(Clone)]
struct ToRadians;

impl<S: GeoStream> TransformMethods<S> for ToRadians {
    fn point(&mut self, stream: &mut S, x: f64, y: f64) {
        stream.point(x * RADIANS, y * RADIANS, None);
    }
}

/// `transformRotate(rotate)`.
#[derive(Clone)]
struct RotateStage<'a>(&'a Rotation);

impl<S: GeoStream> TransformMethods<S> for RotateStage<'_> {
    fn point(&mut self, stream: &mut S, x: f64, y: f64) {
        let r = self.0.forward(x, y);
        stream.point(r[0], r[1], None);
    }
}

/// d3's `projection.stream(sink)`: radians, rotate, pre-clip, resample, post-clip, then the sink.
pub struct ProjectionStream<'a> {
    chain: TransformStream<TransformStream<Box<dyn GeoStream + 'a>, RotateStage<'a>>, ToRadians>,
}

impl GeoStream for ProjectionStream<'_> {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        self.chain.point(x, y, z);
    }
    fn line_start(&mut self) {
        self.chain.line_start();
    }
    fn line_end(&mut self) {
        self.chain.line_end();
    }
    fn polygon_start(&mut self) {
        self.chain.polygon_start();
    }
    fn polygon_end(&mut self) {
        self.chain.polygon_end();
    }
    fn sphere(&mut self) {
        self.chain.sphere();
    }
    fn error(&self) -> Option<&str> {
        self.chain.error()
    }
}

fn antimeridian_preclip() -> Preclip {
    Box::new(|sink| Box::new(clip_antimeridian(sink)))
}

fn identity_postclip() -> Postclip {
    Box::new(|sink| sink)
}

pub struct Projection {
    project_at: ProjectAt,
    project: Box<dyn RawProjection>,
    /// conic.js's phi0, phi1 in radians; `Some` only for a conic projection.
    parallels: Option<[f64; 2]>,
    k: f64,
    x: f64,
    y: f64,
    lambda: f64,
    phi: f64,
    delta_lambda: f64,
    delta_phi: f64,
    delta_gamma: f64,
    alpha: f64,
    sx: f64,
    sy: f64,
    /// d3's `theta`, as `theta * degrees` reads it: 0 for null, NaN for undefined (after `preclip`).
    theta: f64,
    preclip: Preclip,
    clip_extent: Option<[[f64; 2]; 2]>,
    postclip: Postclip,
    /// `Some` for `mercatorProjection`: its `reclip` override of scale, translate, center and clipExtent.
    mercator: Option<mercator::MercatorClip>,
    delta2: f64,
    rotate: Rotation,
    transform: ScaleTranslateRotate,
}

/// `projection(project)`.
pub fn projection(project: impl RawProjection + Clone + 'static) -> Projection {
    projection_mutator(move |_| Box::new(project.clone()), &[])
}

/// `projectionMutator(projectAt)` called once with `args`; [`Projection::reproject`] calls it again.
pub fn projection_mutator(
    project_at: impl Fn(&[f64]) -> Box<dyn RawProjection> + 'static,
    args: &[f64],
) -> Projection {
    let project = project_at(args);
    let mut p = Projection {
        project_at: Box::new(project_at),
        project,
        parallels: None,
        k: 150.0,
        x: 480.0,
        y: 250.0,
        lambda: 0.0,
        phi: 0.0,
        delta_lambda: 0.0,
        delta_phi: 0.0,
        delta_gamma: 0.0,
        alpha: 0.0,
        sx: 1.0,
        sy: 1.0,
        theta: 0.0,
        preclip: antimeridian_preclip(),
        clip_extent: None,
        postclip: identity_postclip(),
        mercator: None,
        delta2: 0.5,
        rotate: rotate_radians(0.0, 0.0, 0.0),
        transform: scale_translate_rotate(1.0, 0.0, 0.0, 1.0, 1.0, 0.0),
    };
    p.recenter();
    p
}

impl Projection {
    /// `projection(point)`: a [λ, φ] in degrees to planar coordinates.
    pub fn forward(&self, [lambda, phi]: [f64; 2]) -> [f64; 2] {
        let [lambda, phi] = self.rotate.forward(lambda * RADIANS, phi * RADIANS);
        let [x, y] = self.project.forward(lambda, phi);
        self.transform.forward(x, y)
    }

    /// `projection.invert(point)`: `None` where the raw projection has no inverse.
    pub fn invert(&self, [x, y]: [f64; 2]) -> Option<[f64; 2]> {
        let [x, y] = self.transform.invert(x, y);
        let [lambda, phi] = self.project.invert(x, y)?;
        let [lambda, phi] = self.rotate.invert(lambda, phi);
        Some([lambda * DEGREES, phi * DEGREES])
    }

    pub fn stream<'a, S: GeoStream + 'a>(&'a self, sink: S) -> ProjectionStream<'a> {
        let raw = &*self.project;
        let transform = self.transform;
        let project = move |lambda, phi| {
            let [x, y] = raw.forward(lambda, phi);
            transform.forward(x, y)
        };
        let postclipped = (self.postclip)(Box::new(sink));
        let resampled = resample(project, self.delta2, postclipped);
        let preclipped = (self.preclip)(resampled);
        let rotated = geo_transform(RotateStage(&self.rotate)).stream(preclipped);
        ProjectionStream { chain: geo_transform(ToRadians).stream(rotated) }
    }

    /// `geoStream(object, projection.stream(sink))`, failing with the error d3 would throw instead of
    /// returning the truncated output as though it were complete.
    pub fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        let mut stream = self.stream(sink);
        geo_stream(object, &mut stream);
        stream.error().map_or(Ok(()), |message| Err(message.to_string()))
    }

    pub fn set_preclip(
        &mut self,
        preclip: impl for<'a> Fn(Box<dyn GeoStream + 'a>) -> Box<dyn GeoStream + 'a> + 'static,
    ) -> &mut Self {
        self.preclip = Box::new(preclip);
        self.theta = f64::NAN;
        self
    }

    pub fn set_postclip(
        &mut self,
        postclip: impl for<'a> Fn(Box<dyn GeoStream + 'a>) -> Box<dyn GeoStream + 'a> + 'static,
    ) -> &mut Self {
        self.postclip = Box::new(postclip);
        self.clip_extent = None;
        self
    }

    /// `clipAngle(_)`: a falsy angle (0, NaN) is the antimeridian clip.
    pub fn set_clip_angle(&mut self, degrees: f64) -> &mut Self {
        if js_truthy(degrees) {
            let theta = degrees * RADIANS;
            self.theta = theta;
            self.preclip = Box::new(move |sink| Box::new(clip_circle(theta, sink)));
        } else {
            self.theta = 0.0;
            self.preclip = antimeridian_preclip();
        }
        self
    }

    pub fn clip_angle(&self) -> f64 {
        self.theta * DEGREES
    }

    /// `clipExtent(_)`: `None` is d3's `null`, which clears the post-clip. A mercator projection keeps the
    /// extent and clips to it within the world (see [`mercator`]).
    pub fn set_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        match &mut self.mercator {
            Some(mercator) => {
                mercator.extent = extent;
                self.reclip_mercator()
            }
            None => self.set_base_clip_extent(extent),
        }
    }

    fn set_base_clip_extent(&mut self, extent: Option<[[f64; 2]; 2]>) -> &mut Self {
        self.clip_extent = extent;
        self.postclip = match extent {
            None => identity_postclip(),
            Some([[x0, y0], [x1, y1]]) => Box::new(move |sink| Box::new(clip_rectangle(x0, y0, x1, y1, sink))),
        };
        self
    }

    pub fn clip_extent(&self) -> Option<[[f64; 2]; 2]> {
        self.mercator.as_ref().map_or(self.clip_extent, |mercator| mercator.extent)
    }

    pub fn set_scale(&mut self, k: f64) -> &mut Self {
        self.k = k;
        self.recenter();
        self.reclip_mercator()
    }

    pub fn scale(&self) -> f64 {
        self.k
    }

    pub fn set_translate(&mut self, [x, y]: [f64; 2]) -> &mut Self {
        self.x = x;
        self.y = y;
        self.recenter();
        self.reclip_mercator()
    }

    pub fn translate(&self) -> [f64; 2] {
        [self.x, self.y]
    }

    pub fn set_center(&mut self, center: [f64; 2]) -> &mut Self {
        let [lambda, phi] = if self.is_transverse() { [-center[1], center[0]] } else { center };
        self.lambda = lambda % 360.0 * RADIANS;
        self.phi = phi % 360.0 * RADIANS;
        self.recenter();
        self.reclip_mercator()
    }

    pub fn center(&self) -> [f64; 2] {
        let [lambda, phi] = [self.lambda * DEGREES, self.phi * DEGREES];
        if self.is_transverse() {
            [phi, -lambda]
        } else {
            [lambda, phi]
        }
    }

    /// `rotate(_)`: d3 reads a missing third angle as 0.
    pub fn set_rotate(&mut self, [lambda, phi, gamma]: [f64; 3]) -> &mut Self {
        let gamma = if self.is_transverse() { gamma + 90.0 } else { gamma };
        self.delta_lambda = lambda % 360.0 * RADIANS;
        self.delta_phi = phi % 360.0 * RADIANS;
        self.delta_gamma = gamma % 360.0 * RADIANS;
        self.recenter()
    }

    pub fn rotate(&self) -> [f64; 3] {
        let gamma = self.delta_gamma * DEGREES;
        [self.delta_lambda * DEGREES, self.delta_phi * DEGREES, if self.is_transverse() { gamma - 90.0 } else { gamma }]
    }

    pub fn set_angle(&mut self, degrees: f64) -> &mut Self {
        self.alpha = degrees % 360.0 * RADIANS;
        self.recenter()
    }

    pub fn angle(&self) -> f64 {
        self.alpha * DEGREES
    }

    pub fn set_reflect_x(&mut self, reflect: bool) -> &mut Self {
        self.sx = if reflect { -1.0 } else { 1.0 };
        self.recenter()
    }

    pub fn reflect_x(&self) -> bool {
        self.sx < 0.0
    }

    pub fn set_reflect_y(&mut self, reflect: bool) -> &mut Self {
        self.sy = if reflect { -1.0 } else { 1.0 };
        self.recenter()
    }

    pub fn reflect_y(&self) -> bool {
        self.sy < 0.0
    }

    pub fn set_precision(&mut self, precision: f64) -> &mut Self {
        self.delta2 = precision * precision;
        self
    }

    pub fn precision(&self) -> f64 {
        libm::sqrt(self.delta2)
    }

    /// conic.js's `parallels(_)` in degrees: builds the raw projection again from them.
    ///
    /// # Panics
    /// If the projection is not conic (see [`Projection::parallels`]).
    pub fn set_parallels(&mut self, [phi0, phi1]: [f64; 2]) -> &mut Self {
        assert!(self.parallels.is_some(), "set_parallels on a projection that is not conic");
        let radians = [phi0 * RADIANS, phi1 * RADIANS];
        self.parallels = Some(radians);
        self.reproject(&radians)
    }

    /// conic.js's `parallels()` in degrees; `None` when the projection has no such method.
    pub fn parallels(&self) -> Option<[f64; 2]> {
        self.parallels.map(|[phi0, phi1]| [phi0 * DEGREES, phi1 * DEGREES])
    }

    /// The call `projectionMutator` returns: replaces the raw projection with `project_at(args)`, keeping
    /// every other setting.
    fn reproject(&mut self, args: &[f64]) -> &mut Self {
        self.project = (self.project_at)(args);
        self.recenter()
    }

    fn recenter(&mut self) -> &mut Self {
        let [x, y] = self.project.forward(self.lambda, self.phi);
        let center = scale_translate_rotate(self.k, 0.0, 0.0, self.sx, self.sy, self.alpha).forward(x, y);
        self.transform = scale_translate_rotate(
            self.k,
            self.x - center[0],
            self.y - center[1],
            self.sx,
            self.sy,
            self.alpha,
        );
        self.rotate = rotate_radians(self.delta_lambda, self.delta_phi, self.delta_gamma);
        self
    }
}
