//! d3-geo 3.1.1 `src/projection/resample.js`: adaptive resampling. It projects a stream and subdivides each
//! segment until the projected midpoint is within `sqrt(delta2)` of the straight line, the midpoint is not
//! near an end, and the angular distance is under 30 degrees, to a depth of 16.

use crate::geo::cartesian::cartesian;
use crate::geo::math::{asin, EPSILON, RADIANS};
use crate::geo::stream::GeoStream;
use crate::js_math::js_truthy;
use crate::geo::transform::{geo_transform, TransformMethods};

const MAX_DEPTH: i32 = 16;

fn cos_min_distance() -> f64 {
    libm::cos(30.0 * RADIANS)
}

/// `resample(project, delta2)`: `+delta2` is falsy for 0 and NaN, which is no resampling.
pub fn resample<'a, P, S>(project: P, delta2: f64, sink: S) -> Box<dyn GeoStream + 'a>
where
    P: Fn(f64, f64) -> [f64; 2] + Clone + 'a,
    S: GeoStream + 'a,
{
    if js_truthy(delta2) {
        Box::new(Resample::new(project, delta2, sink))
    } else {
        Box::new(geo_transform(ResampleNone(project)).stream(sink))
    }
}

/// `resampleNone`: a transformer that only projects points.
#[derive(Clone)]
struct ResampleNone<P>(P);

impl<P: Fn(f64, f64) -> [f64; 2], S: GeoStream> TransformMethods<S> for ResampleNone<P> {
    fn point(&mut self, stream: &mut S, x: f64, y: f64) {
        let [x, y] = (self.0)(x, y);
        stream.point(x, y, None);
    }
}

/// A projected point with its unit vector (a, b, c): d3's `x0, y0, lambda0, a0, b0, c0` variables.
#[derive(Clone, Copy)]
struct Vertex {
    x: f64,
    y: f64,
    lambda: f64,
    a: f64,
    b: f64,
    c: f64,
}

const UNSET: Vertex = Vertex { x: f64::NAN, y: f64::NAN, lambda: f64::NAN, a: f64::NAN, b: f64::NAN, c: f64::NAN };

/// What `resampleStream.point` points at.
#[derive(Clone, Copy)]
enum PointMode {
    Point,
    Line,
    Ring,
}

/// `resampleStream.lineStart` and `.lineEnd` switch to their ring versions inside a polygon.
#[derive(Clone, Copy, PartialEq)]
enum LineMode {
    Line,
    Ring,
}

struct Resample<P, S> {
    project: P,
    delta2: f64,
    stream: S,
    first: Vertex,
    previous: Vertex,
    point: PointMode,
    line_start: LineMode,
    line_end: LineMode,
}

impl<P: Fn(f64, f64) -> [f64; 2], S: GeoStream> Resample<P, S> {
    fn new(project: P, delta2: f64, stream: S) -> Self {
        Self {
            project,
            delta2,
            stream,
            first: UNSET,
            previous: UNSET,
            point: PointMode::Point,
            line_start: LineMode::Line,
            line_end: LineMode::Line,
        }
    }

    fn resample_line_to(&mut self, v0: Vertex, v1: Vertex, mut depth: i32) {
        let dx = v1.x - v0.x;
        let dy = v1.y - v0.y;
        let d2 = dx * dx + dy * dy;
        // d3 writes `d2 > 4 * delta2 && depth--`.
        let deeper = d2 > 4.0 * self.delta2 && {
            let before = depth;
            depth -= 1;
            before != 0
        };
        if !deeper {
            return;
        }
        let a = v0.a + v1.a;
        let b = v0.b + v1.b;
        let mut c = v0.c + v1.c;
        let m = libm::sqrt(a * a + b * b + c * c);
        c /= m;
        let phi2 = asin(c);
        let lambda2 = if (c.abs() - 1.0).abs() < EPSILON || (v0.lambda - v1.lambda).abs() < EPSILON {
            (v0.lambda + v1.lambda) / 2.0
        } else {
            libm::atan2(b, a)
        };
        let [x2, y2] = (self.project)(lambda2, phi2);
        let dx2 = x2 - v0.x;
        let dy2 = y2 - v0.y;
        let dz = dy * dx2 - dx * dy2;
        if dz * dz / d2 > self.delta2 // perpendicular projected distance
            || ((dx * dx2 + dy * dy2) / d2 - 0.5).abs() > 0.3 // midpoint close to an end
            || v0.a * v1.a + v0.b * v1.b + v0.c * v1.c < cos_min_distance()
        // angular distance
        {
            let mid = Vertex { x: x2, y: y2, lambda: lambda2, a: a / m, b: b / m, c };
            self.resample_line_to(v0, mid, depth);
            self.stream.point(x2, y2, None);
            self.resample_line_to(mid, v1, depth);
        }
    }

    fn project_point(&mut self, x: f64, y: f64) {
        let [x, y] = (self.project)(x, y);
        self.stream.point(x, y, None);
    }

    fn line_point(&mut self, lambda: f64, phi: f64) {
        let [a, b, c] = cartesian(lambda, phi);
        let [x, y] = (self.project)(lambda, phi);
        let next = Vertex { x, y, lambda, a, b, c };
        self.resample_line_to(self.previous, next, MAX_DEPTH);
        self.previous = next;
        self.stream.point(x, y, None);
    }

    fn start_line(&mut self) {
        self.previous.x = f64::NAN;
        self.point = PointMode::Line;
        self.stream.line_start();
    }

    fn end_line(&mut self) {
        self.point = PointMode::Point;
        self.stream.line_end();
    }
}

impl<P: Fn(f64, f64) -> [f64; 2], S: GeoStream> GeoStream for Resample<P, S> {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        match self.point {
            PointMode::Point => self.project_point(x, y),
            PointMode::Line => self.line_point(x, y),
            PointMode::Ring => {
                self.line_point(x, y);
                self.first = self.previous;
                self.point = PointMode::Line;
            }
        }
    }

    fn line_start(&mut self) {
        self.start_line();
        if self.line_start == LineMode::Ring {
            self.point = PointMode::Ring;
            self.line_end = LineMode::Ring;
        }
    }

    fn line_end(&mut self) {
        if self.line_end == LineMode::Ring {
            self.resample_line_to(self.previous, self.first, MAX_DEPTH);
            self.line_end = LineMode::Line;
        }
        self.end_line();
    }

    fn polygon_start(&mut self) {
        self.stream.polygon_start();
        self.line_start = LineMode::Ring;
    }

    fn polygon_end(&mut self) {
        self.stream.polygon_end();
        self.line_start = LineMode::Line;
    }

    fn error(&self) -> Option<&str> {
        self.stream.error()
    }

    fn sphere(&mut self) {
        unreachable!("d3's resampleStream has no sphere: the pre-clip stage consumes it");
    }
}
