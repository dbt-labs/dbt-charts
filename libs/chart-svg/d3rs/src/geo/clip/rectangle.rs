//! d3-geo 3.1.1 `src/clip/rectangle.js`: clips to an axis-aligned rectangle. It is planar, so it runs after
//! projection (`projection.clipExtent`). Like d3's, the stream has no `sphere`: the pre-clip stage consumes it.

use super::buffer::{BufferPoint, ClipBuffer};
use super::line::clip_line;
use super::rejoin::clip_rejoin;
use crate::geo::math::EPSILON;
use crate::geo::stream::GeoStream;

const CLIP_MAX: f64 = 1e9;
const CLIP_MIN: f64 = -CLIP_MAX;

#[derive(Clone, Copy)]
struct Rect {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
}

impl Rect {
    fn visible(&self, x: f64, y: f64) -> bool {
        self.x0 <= x && x <= self.x1 && self.y0 <= y && y <= self.y1
    }

    fn interpolate(&self, from_to: Option<([f64; 2], [f64; 2])>, direction: i32, stream: &mut impl GeoStream) {
        let (mut a, mut a1) = (0, 0);
        let around = match from_to {
            None => true,
            Some((from, to)) => {
                a = self.corner(from, direction);
                a1 = self.corner(to, direction);
                a != a1 || (self.compare_point(from, to) < 0.0) ^ (direction > 0)
            }
        };
        if around {
            loop {
                stream.point(if a == 0 || a == 3 { self.x0 } else { self.x1 }, if a > 1 { self.y1 } else { self.y0 }, None);
                a = (a + direction + 4) % 4;
                if a == a1 {
                    break;
                }
            }
        } else if let Some((_, to)) = from_to {
            stream.point(to[0], to[1], None);
        }
    }

    fn corner(&self, p: [f64; 2], direction: i32) -> i32 {
        if (p[0] - self.x0).abs() < EPSILON {
            if direction > 0 { 0 } else { 3 }
        } else if (p[0] - self.x1).abs() < EPSILON {
            if direction > 0 { 2 } else { 1 }
        } else if (p[1] - self.y0).abs() < EPSILON {
            if direction > 0 { 1 } else { 0 }
        } else if direction > 0 {
            3
        } else {
            2 // abs(p[1] - y1) < epsilon
        }
    }

    fn compare_point(&self, a: [f64; 2], b: [f64; 2]) -> f64 {
        let ca = self.corner(a, 1);
        let cb = self.corner(b, 1);
        if ca != cb {
            f64::from(ca - cb)
        } else if ca == 0 {
            b[1] - a[1]
        } else if ca == 1 {
            a[0] - b[0]
        } else if ca == 2 {
            a[1] - b[1]
        } else {
            b[0] - a[0]
        }
    }
}

/// The buffered polygon between `polygonStart` and `polygonEnd`.
#[derive(Default)]
struct Pending {
    segments: Vec<Vec<BufferPoint>>,
    polygon: Vec<Vec<[f64; 2]>>,
}

pub struct ClipRectangle<S> {
    sink: S,
    rect: Rect,
    buffer: ClipBuffer,
    /// d3's `activeStream`: the buffer inside a polygon, else the sink.
    buffering: bool,
    pending: Option<Pending>,
    in_line: bool,
    first_point: [f64; 2], // x__, y__
    first_visible: bool,   // v__
    previous: [f64; 2],    // x_, y_
    previous_visible: bool, // v_
    first: bool,
    clean: bool,
    /// What stopped the stage: the rejoin sort refused a NaN key.
    error: Option<String>,
}

pub fn clip_rectangle<S: GeoStream>(x0: f64, y0: f64, x1: f64, y1: f64, sink: S) -> ClipRectangle<S> {
    ClipRectangle {
        sink,
        rect: Rect { x0, y0, x1, y1 },
        buffer: ClipBuffer::default(),
        buffering: false,
        pending: None,
        in_line: false,
        first_point: [f64::NAN, f64::NAN],
        first_visible: false,
        previous: [f64::NAN, f64::NAN],
        previous_visible: false,
        first: false,
        clean: false,
        error: None,
    }
}

/// The winding number of the buffered polygon about the rectangle's top-left start.
fn polygon_inside(polygon: &[Vec<[f64; 2]>], x0: f64, y1: f64) -> i32 {
    let mut winding = 0;
    for ring in polygon {
        let (mut b0, mut b1) = (ring[0][0], ring[0][1]);
        for point in &ring[1..] {
            let (a0, a1) = (b0, b1);
            (b0, b1) = (point[0], point[1]);
            if a1 <= y1 {
                if b1 > y1 && (b0 - a0) * (y1 - a1) > (b1 - a1) * (x0 - a0) {
                    winding += 1;
                }
            } else if b1 <= y1 && (b0 - a0) * (y1 - a1) < (b1 - a1) * (x0 - a0) {
                winding -= 1;
            }
        }
    }
    winding
}

impl<S: GeoStream> ClipRectangle<S> {
    fn active_line_start(&mut self) {
        if self.buffering {
            self.buffer.line_start();
        } else {
            self.sink.line_start();
        }
    }

    fn active_point(&mut self, x: f64, y: f64) {
        if self.buffering {
            self.buffer.point(x, y, None);
        } else {
            self.sink.point(x, y, None);
        }
    }

    fn active_line_end(&mut self) {
        if self.buffering {
            self.buffer.line_end();
        } else {
            self.sink.line_end();
        }
    }

    fn line_point(&mut self, x: f64, y: f64) {
        let mut x = x;
        let mut y = y;
        let v = self.rect.visible(x, y);
        if let Some(pending) = &mut self.pending {
            pending.polygon.last_mut().expect("lineStart opened a ring").push([x, y]);
        }
        if self.first {
            self.first_point = [x, y];
            self.first_visible = v;
            self.first = false;
            if v {
                self.active_line_start();
                self.active_point(x, y);
            }
        } else if v && self.previous_visible {
            self.active_point(x, y);
        } else {
            self.previous = self.previous.map(|c| c.clamp(CLIP_MIN, CLIP_MAX));
            let mut a = self.previous;
            (x, y) = (x.clamp(CLIP_MIN, CLIP_MAX), y.clamp(CLIP_MIN, CLIP_MAX));
            let mut b = [x, y];
            if clip_line(&mut a, &mut b, self.rect.x0, self.rect.y0, self.rect.x1, self.rect.y1) {
                if !self.previous_visible {
                    self.active_line_start();
                    self.active_point(a[0], a[1]);
                }
                self.active_point(b[0], b[1]);
                if !v {
                    self.active_line_end();
                }
                self.clean = false;
            } else if v {
                // Only past the +-1e9 clamp: `v` is the unclamped point's, `b` is clamped and may miss.
                self.active_line_start();
                self.active_point(x, y);
                self.clean = false;
            }
        }
        self.previous = [x, y];
        self.previous_visible = v;
    }
}

impl<S: GeoStream> GeoStream for ClipRectangle<S> {
    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        if self.error.is_some() {
            return;
        }
        if self.in_line {
            self.line_point(x, y);
        } else if self.rect.visible(x, y) {
            self.active_point(x, y);
        }
    }

    fn line_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.in_line = true;
        if let Some(pending) = &mut self.pending {
            pending.polygon.push(Vec::new());
        }
        self.first = true;
        self.previous_visible = false;
        self.previous = [f64::NAN, f64::NAN];
    }

    fn line_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        if self.pending.is_some() {
            self.line_point(self.first_point[0], self.first_point[1]);
            if self.first_visible && self.previous_visible {
                self.buffer.rejoin();
            }
            let lines = self.buffer.result();
            self.pending.as_mut().expect("checked above").segments.extend(lines);
        }
        self.in_line = false;
        if self.previous_visible {
            self.active_line_end();
        }
    }

    // Buffer geometry within a polygon and then clip it en masse.
    fn polygon_start(&mut self) {
        if self.error.is_some() {
            return;
        }
        self.buffering = true;
        self.pending = Some(Pending::default());
        self.clean = true;
    }

    fn polygon_end(&mut self) {
        if self.error.is_some() {
            return;
        }
        let Pending { segments, polygon } = self.pending.take().expect("polygonEnd without polygonStart");
        let start_inside = polygon_inside(&polygon, self.rect.x0, self.rect.y1) != 0;
        let clean_inside = self.clean && start_inside;
        let visible = !segments.is_empty();
        self.buffering = false;
        if clean_inside || visible {
            self.sink.polygon_start();
            if clean_inside {
                self.sink.line_start();
                self.rect.interpolate(None, 1, &mut self.sink);
                self.sink.line_end();
            }
            if visible {
                let rect = self.rect;
                if let Err(message) = clip_rejoin(
                    segments,
                    |a, b| rect.compare_point(a, b),
                    start_inside,
                    |from_to, direction, stream| rect.interpolate(from_to, direction, stream),
                    &mut self.sink,
                ) {
                    self.error = Some(message);
                    return;
                }
            }
            self.sink.polygon_end();
        }
    }

    fn error(&self) -> Option<&str> {
        self.error.as_deref().or_else(|| self.sink.error())
    }

    fn sphere(&mut self) {
        unreachable!("clipRectangle has no sphere: the pre-clip stage consumes it");
    }
}
