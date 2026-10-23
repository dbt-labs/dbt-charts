//! d3-geo 3.1.1 `src/path/index.js`: `geoPath(projection)` with no canvas context, so it prints SVG path data.

use super::area::PathArea;
use super::bounds::Bounds;
use super::centroid::PathCentroid;
use super::measure::PathMeasure;
use super::string::PathString;
use crate::geo::projection::albers_usa::AlbersUsa;
use crate::geo::projection::fit::Fit;
use crate::geo::projection::identity::Identity;
use crate::geo::projection::Projection;
use crate::geo::stream::{geo_stream, GeoStream};
use crate::geojson::GeoJson;

/// What `geoPath(projection)` streams an object through.
pub trait PathProjection {
    /// `geoStream(object, projection.stream(sink))`; the error is what d3 would throw.
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String>;
}

impl PathProjection for Projection {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        Projection::stream_object(self, object, sink)
    }
}

impl PathProjection for AlbersUsa {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        Fit::stream_object(self, object, sink)
    }
}

/// The error d3 throws where a planar sink receives a sphere: only a spherical projection's pre-clip turns
/// one into a polygon. `message` is the TypeError's text, which names the receiver.
struct RejectSphere<S> {
    sink: S,
    message: &'static str,
    rejected: bool,
}

impl<S: GeoStream> GeoStream for RejectSphere<S> {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        self.sink.point(x, y, z);
    }
    fn line_start(&mut self) {
        self.sink.line_start();
    }
    fn line_end(&mut self) {
        self.sink.line_end();
    }
    fn polygon_start(&mut self) {
        self.sink.polygon_start();
    }
    fn polygon_end(&mut self) {
        self.sink.polygon_end();
    }
    fn sphere(&mut self) {
        self.rejected = true;
    }
    fn error(&self) -> Option<&str> {
        self.rejected.then_some(self.message).or_else(|| self.sink.error())
    }
}

/// `geoPath(null)`: coordinates are already planar.
pub struct NoProjection;

impl PathProjection for NoProjection {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        let mut guard = RejectSphere { sink, message: "stream.sphere is not a function", rejected: false };
        geo_stream(object, &mut guard);
        guard.error().map_or(Ok(()), |message| Err(message.to_string()))
    }
}

impl PathProjection for Identity {
    fn stream_object(&self, object: &GeoJson, sink: impl GeoStream) -> Result<(), String> {
        let guard = RejectSphere { sink, message: "this.stream.sphere is not a function", rejected: false };
        let mut stream = self.stream(guard);
        geo_stream(object, &mut stream);
        stream.error().map_or(Ok(()), |message| Err(message.to_string()))
    }
}

/// `pointRadius`: a constant, or a function of the object being drawn (d3 calls it with the object).
pub enum PointRadius {
    Constant(f64),
    PerObject(Box<dyn Fn(&GeoJson) -> f64>),
}

pub struct GeoPath<P> {
    projection: P,
    /// d3's `digits`; `None` is `digits(null)`, which prints coordinates unrounded.
    digits: Option<u32>,
    point_radius: PointRadius,
}

impl<P: PathProjection> GeoPath<P> {
    /// `geoPath(projection)`: three digits, point radius 4.5.
    pub fn new(projection: P) -> Self {
        Self { projection, digits: Some(3), point_radius: PointRadius::Constant(4.5) }
    }

    /// `path.digits(digits)`: `None` for null. d3 floors it and throws a `RangeError` below zero or NaN.
    pub fn with_digits(mut self, digits: Option<f64>) -> Result<Self, String> {
        self.digits = digits
            .map(|digits| {
                let floored = digits.floor();
                if floored >= 0.0 {
                    Ok(floored as u32)
                } else {
                    Err(format!("invalid digits: {digits}"))
                }
            })
            .transpose()?;
        Ok(self)
    }

    pub fn with_point_radius(mut self, point_radius: PointRadius) -> Self {
        self.point_radius = point_radius;
        self
    }

    fn radius(&self, object: &GeoJson) -> f64 {
        match &self.point_radius {
            PointRadius::Constant(r) => *r,
            PointRadius::PerObject(f) => f(object),
        }
    }

    /// `path(object)`: the SVG path data, `None` where d3 returns null (nothing drawn).
    pub fn path(&self, object: &GeoJson) -> Result<Option<String>, String> {
        let mut sink = PathString::new(self.digits, self.radius(object));
        self.projection.stream_object(object, &mut sink)?;
        Ok(sink.result())
    }

    pub fn area(&self, object: &GeoJson) -> Result<f64, String> {
        let mut sink = PathArea::default();
        self.projection.stream_object(object, &mut sink)?;
        Ok(sink.result())
    }

    pub fn measure(&self, object: &GeoJson) -> Result<f64, String> {
        let mut sink = PathMeasure::default();
        self.projection.stream_object(object, &mut sink)?;
        Ok(sink.result())
    }

    /// `[[x0, y0], [x1, y1]]`; `[[Infinity, Infinity], [-Infinity, -Infinity]]` when nothing is drawn.
    pub fn bounds(&self, object: &GeoJson) -> Result<[[f64; 2]; 2], String> {
        let mut sink = Bounds::default();
        self.projection.stream_object(object, &mut sink)?;
        Ok(sink.result())
    }

    pub fn centroid(&self, object: &GeoJson) -> Result<[f64; 2], String> {
        let mut sink = PathCentroid::default();
        self.projection.stream_object(object, &mut sink)?;
        Ok(sink.result())
    }
}
