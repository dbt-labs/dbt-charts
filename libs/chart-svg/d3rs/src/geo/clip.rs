//! d3-geo 3.1.1 `src/clip/index.js`: the generic clipper. It buffers a polygon's rings, cuts each against
//! the clip geometry's line clipper, and rejoins the visible segments along the clip edge. `antimeridian`
//! and `circle` supply the geometry; `rectangle` is planar and keeps its own stream.

pub mod antimeridian;
pub mod buffer;
pub mod circle;
pub mod line;
pub mod rectangle;
pub mod rejoin;

use super::math::{EPSILON, HALF_PI};
use super::polygon_contains::polygon_contains;
use super::stream::GeoStream;
use buffer::{join_ends, ClipBuffer};
use rejoin::clip_rejoin;

/// d3's `clipLine(stream)` object: cuts a line into its visible segments and writes them to `out`. d3's
/// object owns `stream`; here the caller passes it, since the clipper also writes to it directly.
pub trait ClipLine {
    fn line_start(&mut self, out: &mut impl GeoStream);
    fn point(&mut self, out: &mut impl GeoStream, lambda: f64, phi: f64);
    fn line_end(&mut self, out: &mut impl GeoStream);
    /// 0: there were intersections or the line was empty; 1: no intersections; 2: there were intersections,
    /// and the first and last segments should be rejoined.
    fn clean(&self) -> u8;
    /// The JavaScript error d3's clipper would have thrown from `point`.
    fn error(&self) -> Option<&str> {
        None
    }
}

/// What JavaScript throws reading `[0]` of `undefined`: d3 does so on an empty ring and on a line clip that
/// finds no crossing.
const READ_OF_UNDEFINED: &str = "Cannot read properties of undefined (reading '0')";

/// The four arguments of d3's `clip(pointVisible, clipLine, interpolate, start)`.
pub trait ClipGeometry {
    type Line: ClipLine;
    fn point_visible(&self, lambda: f64, phi: f64) -> bool;
    fn new_line(&self) -> Self::Line;
    /// `from_to` is d3's `from`/`to` pair, absent for the whole clip edge.
    fn interpolate(&self, from_to: Option<([f64; 2], [f64; 2])>, direction: i32, stream: &mut impl GeoStream);
    fn start(&self) -> [f64; 2];
}

pub struct Clip<S, G: ClipGeometry> {
    sink: S,
    geometry: G,
    line: G::Line,
    ring_buffer: ClipBuffer,
    ring_line: G::Line,
    polygon_started: bool,
    in_polygon: bool,
    in_line: bool,
    polygon: Vec<Vec<[f64; 2]>>,
    segments: Vec<Vec<buffer::BufferPoint>>,
    ring: Vec<[f64; 2]>,
    /// The JavaScript error d3 throws on an empty ring. The first one stops the stage, as the throw stops the
    /// stream; the events written before it stay written.
    error: Option<String>,
}

impl<S: GeoStream, G: ClipGeometry> Clip<S, G> {
    pub fn new(geometry: G, sink: S) -> Self {
        Self {
            sink,
            line: geometry.new_line(),
            ring_line: geometry.new_line(),
            geometry,
            ring_buffer: ClipBuffer::default(),
            polygon_started: false,
            in_polygon: false,
            in_line: false,
            polygon: Vec::new(),
            segments: Vec::new(),
            ring: Vec::new(),
            error: None,
        }
    }

    fn ring_end(&mut self) {
        let Some(&first) = self.ring.first() else {
            self.error = Some(READ_OF_UNDEFINED.to_string());
            return;
        };
        self.ring.push(first);
        self.ring_line.point(&mut self.ring_buffer, first[0], first[1]);
        if let Some(message) = self.ring_line.error() {
            self.error = Some(message.to_string());
            return;
        }
        self.ring_line.line_end(&mut self.ring_buffer);

        let clean = self.ring_line.clean();
        let mut ring_segments = self.ring_buffer.result();

        self.ring.pop();
        self.polygon.push(std::mem::take(&mut self.ring));

        if ring_segments.is_empty() {
            return;
        }

        // No intersections.
        if clean & 1 != 0 {
            let segment = &ring_segments[0];
            if segment.len() > 1 {
                if !self.polygon_started {
                    self.sink.polygon_start();
                    self.polygon_started = true;
                }
                self.sink.line_start();
                for p in &segment[..segment.len() - 1] {
                    self.sink.point(p.xy[0], p.xy[1], None);
                }
                self.sink.line_end();
            }
            return;
        }

        // Rejoin connected segments.
        if clean & 2 != 0 {
            join_ends(&mut ring_segments);
        }

        self.segments.extend(ring_segments.into_iter().filter(|segment| segment.len() > 1));
    }
}

/// Intersections are sorted along the clip edge. For both antimeridian cutting and circle clipping, the same
/// comparison is used.
fn compare_intersection(a: [f64; 2], b: [f64; 2]) -> f64 {
    (if a[0] < 0.0 { a[1] - HALF_PI - EPSILON } else { HALF_PI - a[1] })
        - (if b[0] < 0.0 { b[1] - HALF_PI - EPSILON } else { HALF_PI - b[1] })
}

impl<S: GeoStream, G: ClipGeometry> GeoStream for Clip<S, G> {
    fn point(&mut self, lambda: f64, phi: f64, _z: Option<f64>) {
        if self.error.is_some() {
            return;
        }
        if self.in_polygon {
            self.ring.push([lambda, phi]);
            self.ring_line.point(&mut self.ring_buffer, lambda, phi);
            self.error = self.ring_line.error().map(String::from);
        } else if self.in_line {
            self.line.point(&mut self.sink, lambda, phi);
            self.error = self.line.error().map(String::from);
        } else if self.geometry.point_visible(lambda, phi) {
            self.sink.point(lambda, phi, None);
        }
    }

    fn line_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        if self.in_polygon {
            self.ring_line.line_start(&mut self.ring_buffer);
            self.ring = Vec::new();
        } else {
            self.in_line = true;
            self.line.line_start(&mut self.sink);
        }
    }

    fn line_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        if self.in_polygon {
            self.ring_end();
        } else {
            self.in_line = false;
            self.line.line_end(&mut self.sink);
        }
    }

    fn polygon_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.in_polygon = true;
        self.segments = Vec::new();
        self.polygon = Vec::new();
    }

    fn polygon_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.in_polygon = false;
        let segments = std::mem::take(&mut self.segments);
        let polygon = std::mem::take(&mut self.polygon);
        let start_inside = polygon_contains(&polygon, self.geometry.start());
        if !segments.is_empty() {
            if !self.polygon_started {
                self.sink.polygon_start();
                self.polygon_started = true;
            }
            let geometry = &self.geometry;
            if let Err(message) = clip_rejoin(
                segments,
                compare_intersection,
                start_inside,
                |from_to, direction, stream| geometry.interpolate(from_to, direction, stream),
                &mut self.sink,
            ) {
                self.error = Some(message);
                return;
            }
        } else if start_inside {
            if !self.polygon_started {
                self.sink.polygon_start();
                self.polygon_started = true;
            }
            self.sink.line_start();
            self.geometry.interpolate(None, 1, &mut self.sink);
            self.sink.line_end();
        }
        if self.polygon_started {
            self.sink.polygon_end();
            self.polygon_started = false;
        }
    }

    fn error(&self) -> Option<&str> {
        self.error.as_deref().or_else(|| self.sink.error())
    }

    fn sphere(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.sink.polygon_start();
        self.sink.line_start();
        self.geometry.interpolate(None, 1, &mut self.sink);
        self.sink.line_end();
        self.sink.polygon_end();
    }
}
