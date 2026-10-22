//! d3-geo 3.1.1 `src/clip/buffer.js`: collects the lines a clipper emits.

use crate::geo::stream::GeoStream;

/// A buffered point. `m` marks a clipped line's cut points (2 leaving, 3 re-entering a clip circle);
/// `rejoin` reads it to tell a ring from a degenerate segment.
#[derive(Clone, Copy, Debug)]
pub struct BufferPoint {
    pub xy: [f64; 2],
    pub m: Option<f64>,
}

#[derive(Default)]
pub struct ClipBuffer {
    lines: Vec<Vec<BufferPoint>>,
}

/// Joins a ring's first and last lines, which are one line cut by the ring's start.
pub fn join_ends(lines: &mut Vec<Vec<BufferPoint>>) {
    if lines.len() > 1 {
        let mut last = lines.pop().expect("two lines or more");
        last.extend(lines.remove(0));
        lines.push(last);
    }
}

impl ClipBuffer {
    pub fn rejoin(&mut self) {
        join_ends(&mut self.lines);
    }

    pub fn result(&mut self) -> Vec<Vec<BufferPoint>> {
        std::mem::take(&mut self.lines)
    }
}

impl GeoStream for ClipBuffer {
    fn point(&mut self, x: f64, y: f64, m: Option<f64>) {
        let line = self.lines.last_mut().expect("clip buffer: point before lineStart");
        line.push(BufferPoint { xy: [x, y], m });
    }
    fn line_start(&mut self) {
        self.lines.push(Vec::new());
    }
    fn line_end(&mut self) {}
    fn polygon_start(&mut self) {
        unreachable!("a clip buffer only receives lines");
    }
    fn polygon_end(&mut self) {
        unreachable!("a clip buffer only receives lines");
    }
    fn sphere(&mut self) {
        unreachable!("a clip buffer only receives lines");
    }
}
