//! d3-geo 3.1.1 `src/transform.js`: `geoTransform` and `transformer`.

use super::stream::GeoStream;

/// The methods a `geoTransform({...})` object overrides. Each default is `TransformStream.prototype`'s:
/// forward to the downstream stream (`point` drops `z`, as d3's does). An override receives `stream`, d3's
/// `this.stream`.
pub trait TransformMethods<S: GeoStream> {
    fn point(&mut self, stream: &mut S, x: f64, y: f64) {
        stream.point(x, y, None);
    }
    fn line_start(&mut self, stream: &mut S) {
        stream.line_start();
    }
    fn line_end(&mut self, stream: &mut S) {
        stream.line_end();
    }
    fn polygon_start(&mut self, stream: &mut S) {
        stream.polygon_start();
    }
    fn polygon_end(&mut self, stream: &mut S) {
        stream.polygon_end();
    }
    fn sphere(&mut self, stream: &mut S) {
        stream.sphere();
    }
}

/// d3's `TransformStream` with its overrides applied.
pub struct TransformStream<S, M> {
    pub stream: S,
    methods: M,
}

impl<S: GeoStream, M: TransformMethods<S>> GeoStream for TransformStream<S, M> {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        self.methods.point(&mut self.stream, x, y);
    }
    fn line_start(&mut self) {
        self.methods.line_start(&mut self.stream);
    }
    fn line_end(&mut self) {
        self.methods.line_end(&mut self.stream);
    }
    fn polygon_start(&mut self) {
        self.methods.polygon_start(&mut self.stream);
    }
    fn polygon_end(&mut self) {
        self.methods.polygon_end(&mut self.stream);
    }
    fn sphere(&mut self) {
        self.methods.sphere(&mut self.stream);
    }
    fn error(&self) -> Option<&str> {
        self.stream.error()
    }
}

/// `geoTransform(methods)`: `{stream}` is the returned object's only member.
#[derive(Clone)]
pub struct GeoTransform<M> {
    methods: M,
}

pub fn geo_transform<M>(methods: M) -> GeoTransform<M> {
    GeoTransform { methods }
}

impl<M: Clone> GeoTransform<M> {
    pub fn stream<S: GeoStream>(&self, stream: S) -> TransformStream<S, M>
    where
        M: TransformMethods<S>,
    {
        TransformStream { stream, methods: self.methods.clone() }
    }
}
