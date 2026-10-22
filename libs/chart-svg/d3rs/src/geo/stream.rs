//! d3-geo 3.1.1 `src/stream.js`: the stream interface every stage of a projection pipeline implements,
//! and `geoStream`, which walks a GeoJSON object into one.

use crate::geojson::{GeoJson, Geometry, Position};

/// d3's stream object. A stage owns its downstream sink and forwards to it, the way d3's wrappers hold
/// `this.stream`.
pub trait GeoStream {
    /// `z` is the third coordinate of a GeoJSON position, which d3 passes through untouched.
    fn point(&mut self, x: f64, y: f64, z: Option<f64>);
    fn line_start(&mut self);
    fn line_end(&mut self);
    fn polygon_start(&mut self);
    fn polygon_end(&mut self);
    fn sphere(&mut self);
    /// The JavaScript error d3 would have thrown from this stage or one downstream. The stage stops at it:
    /// what the sink received before stays and nothing follows.
    fn error(&self) -> Option<&str> {
        None
    }
}

impl<S: GeoStream + ?Sized> GeoStream for &mut S {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        (**self).point(x, y, z);
    }
    fn line_start(&mut self) {
        (**self).line_start();
    }
    fn line_end(&mut self) {
        (**self).line_end();
    }
    fn polygon_start(&mut self) {
        (**self).polygon_start();
    }
    fn polygon_end(&mut self) {
        (**self).polygon_end();
    }
    fn sphere(&mut self) {
        (**self).sphere();
    }
    fn error(&self) -> Option<&str> {
        (**self).error()
    }
}

impl<S: GeoStream + ?Sized> GeoStream for Box<S> {
    fn point(&mut self, x: f64, y: f64, z: Option<f64>) {
        (**self).point(x, y, z);
    }
    fn line_start(&mut self) {
        (**self).line_start();
    }
    fn line_end(&mut self) {
        (**self).line_end();
    }
    fn polygon_start(&mut self) {
        (**self).polygon_start();
    }
    fn polygon_end(&mut self) {
        (**self).polygon_end();
    }
    fn sphere(&mut self) {
        (**self).sphere();
    }
    fn error(&self) -> Option<&str> {
        (**self).error()
    }
}

/// d3 reads `c[0]`, `c[1]`, `c[2]`; a missing member is `undefined`, which arrives as NaN.
fn stream_point(c: &Position, stream: &mut impl GeoStream) {
    let at = |i: usize| c.get(i).copied();
    stream.point(at(0).unwrap_or(f64::NAN), at(1).unwrap_or(f64::NAN), at(2));
}

fn stream_line(coordinates: &[Position], stream: &mut impl GeoStream, closed: usize) {
    let n = coordinates.len().saturating_sub(closed);
    stream.line_start();
    for c in &coordinates[..n] {
        stream_point(c, stream);
    }
    stream.line_end();
}

fn stream_polygon(coordinates: &[Vec<Position>], stream: &mut impl GeoStream) {
    stream.polygon_start();
    for ring in coordinates {
        stream_line(ring, stream, 1);
    }
    stream.polygon_end();
}

fn stream_geometry(geometry: &Geometry, stream: &mut impl GeoStream) {
    match geometry {
        Geometry::Sphere => stream.sphere(),
        Geometry::Point { coordinates } => stream_point(coordinates, stream),
        Geometry::MultiPoint { coordinates } => {
            for c in coordinates {
                stream_point(c, stream);
            }
        }
        Geometry::LineString { coordinates } => stream_line(coordinates, stream, 0),
        Geometry::MultiLineString { coordinates } => {
            for line in coordinates {
                stream_line(line, stream, 0);
            }
        }
        Geometry::Polygon { coordinates } => stream_polygon(coordinates, stream),
        Geometry::MultiPolygon { coordinates } => {
            for polygon in coordinates {
                stream_polygon(polygon, stream);
            }
        }
        Geometry::GeometryCollection { geometries } => {
            for g in geometries {
                stream_geometry(g, stream);
            }
        }
    }
}

/// d3-geo's `geoStream(object, stream)`. A ring's closing point is not streamed (`closed = 1`).
pub fn geo_stream(object: &GeoJson, stream: &mut impl GeoStream) {
    match object {
        GeoJson::Geometry(g) => stream_geometry(g, stream),
        GeoJson::Feature(f) => {
            if let Some(g) = &f.geometry {
                stream_geometry(g, stream);
            }
        }
        GeoJson::FeatureCollection(fc) => {
            for g in fc.features.iter().filter_map(|f| f.geometry.as_ref()) {
                stream_geometry(g, stream);
            }
        }
    }
}
