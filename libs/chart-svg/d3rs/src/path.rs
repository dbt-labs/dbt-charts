//! Port of d3-path's Path serializer — just the subset arc() emission needs.
//! Reference: https://github.com/d3/d3-path/blob/main/src/path.js
//!
//! One deliberate departure: `arc` records a sweep as equal pieces of at most 90 degrees, where d3
//! writes one `A` (two for a circle). `print` rounds geometry to 0.01, which bends a near-semicircular
//! arc whose center is ill-conditioned; the drawn circle is the same.

use scene::{PathCommand, PathData};
use std::f64::consts::{FRAC_PI_2, TAU};

const EPSILON: f64 = 1e-6;
const TAU_EPSILON: f64 = TAU - EPSILON;

/// d3-shape@3.2.0 `src/path.js` `withPath`: `let digits = 3; return () => new Path(digits)`,
/// the path every `line`, `area`, `symbol`, `link` and `arc` generator builds. The recorded
/// commands keep exact coordinates; this is the rounding of d3's own printed `d`.
pub const WITH_PATH_DIGITS: u32 = 3;

/// The most a coordinate of d3's printed `d` can sit from the position it was computed from:
/// `Math.round(v * k) / k` (d3-path@3.1.0 `src/path.js`) with `k = 10^WITH_PATH_DIGITS` moves
/// `v` by at most half a unit of the last digit.
pub const PATH_COORD_TOLERANCE: f64 = 0.0005;

pub struct Path {
    commands: Vec<PathCommand>,
    x0: f64,
    y0: f64,
    x1: Option<f64>,
    y1: f64,
}

impl Path {
    pub fn new() -> Self {
        Path { commands: Vec::new(), x0: 0.0, y0: 0.0, x1: None, y1: 0.0 }
    }

    pub fn move_to(&mut self, x: f64, y: f64) {
        self.x0 = x;
        self.y0 = y;
        self.x1 = Some(x);
        self.y1 = y;
        self.commands.push(PathCommand::M(x, y));
    }

    pub fn line_to(&mut self, x: f64, y: f64) {
        self.x1 = Some(x);
        self.y1 = y;
        self.commands.push(PathCommand::L(x, y));
    }

    pub fn bezier_curve_to(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, x: f64, y: f64) {
        self.x1 = Some(x);
        self.y1 = y;
        self.commands.push(PathCommand::C(x1, y1, x2, y2, x, y));
    }

    /// d3-path's `rect(x, y, w, h)`: a closed subpath; `x1` ends back at its start.
    pub fn rect(&mut self, x: f64, y: f64, w: f64, h: f64) {
        self.move_to(x, y);
        self.line_to(x + w, y);
        self.line_to(x + w, y + h);
        self.line_to(x, y + h);
        self.close_path();
    }

    pub fn close_path(&mut self) {
        if self.x1.is_some() {
            self.x1 = Some(self.x0);
            self.y1 = self.y0;
            self.commands.push(PathCommand::Z);
        }
    }

    /// d3-path's Path.arc: center-parameterized arc → SVG `A` commands.
    pub fn arc(&mut self, x: f64, y: f64, r: f64, a0: f64, a1: f64, ccw: bool) {
        let dx = r * a0.cos();
        let dy = r * a0.sin();
        let x0 = x + dx;
        let y0 = y + dy;
        let cw = !ccw;
        let mut da = if ccw { a0 - a1 } else { a1 - a0 };

        assert!(r >= 0.0, "negative radius: {r}");

        // Is this path empty? Move to (x0, y0).
        match self.x1 {
            None => {
                self.commands.push(PathCommand::M(x0, y0));
                self.x1 = Some(x0);
                self.y1 = y0;
            }
            // Or is (x0, y0) not coincident with the previous point? Line to (x0, y0).
            Some(px1) => {
                if (px1 - x0).abs() > EPSILON || (self.y1 - y0).abs() > EPSILON {
                    self.commands.push(PathCommand::L(x0, y0));
                    self.x1 = Some(x0);
                    self.y1 = y0;
                }
            }
        }

        if r == 0.0 {
            return;
        }

        // Does the angle go the wrong way? Flip the direction.
        if da < 0.0 {
            da = da % TAU + TAU;
        }

        let sweep_angle = if da > TAU_EPSILON { TAU } else { da };
        if sweep_angle > EPSILON {
            let pieces = ((sweep_angle / FRAC_PI_2) - 1e-9).ceil().max(1.0) as usize;
            let direction = if ccw { -1.0 } else { 1.0 };
            for piece in 1..=pieces {
                let (px, py) = if piece == pieces && sweep_angle == TAU {
                    (x0, y0)
                } else {
                    let angle = a0 + direction * sweep_angle * piece as f64 / pieces as f64;
                    (x + r * angle.cos(), y + r * angle.sin())
                };
                self.commands.push(PathCommand::A { rx: r, ry: r, rot: 0.0, large: false, sweep: cw, x: px, y: py });
                self.x1 = Some(px);
                self.y1 = py;
            }
        }
    }

    pub fn into_path_data(self) -> PathData {
        PathData(self.commands)
    }
}

impl Default for Path {
    fn default() -> Self {
        Self::new()
    }
}

/// Test support: reads a `d` string back into commands and compares outlines.
pub mod testing {
    use super::{PathCommand, PathData, PATH_COORD_TOLERANCE, TAU};

    /// The `d` d3 prints: absolute `M L C A Z`, plus the relative `h`/`v` of d3-path's `rect`.
    pub fn parse_d(d: &str) -> PathData {
        let mut commands: Vec<PathCommand> = Vec::new();
        let mut rest = d;
        while !rest.is_empty() {
            let end = rest[1..].find(|c: char| c.is_ascii_alphabetic()).map_or(rest.len(), |i| i + 1);
            let (segment, tail) = rest.split_at(end);
            rest = tail;
            let n: Vec<f64> = segment[1..].split(',').filter(|t| !t.is_empty()).map(|t| t.parse().expect("a number")).collect();
            commands.push(match (&segment[..1], n.as_slice()) {
                ("M", &[x, y]) => PathCommand::M(x, y),
                ("L", &[x, y]) => PathCommand::L(x, y),
                ("C", &[a, b, c, d, x, y]) => PathCommand::C(a, b, c, d, x, y),
                ("A", &[rx, ry, rot, large, sweep, x, y]) => {
                    PathCommand::A { rx, ry, rot, large: large == 1.0, sweep: sweep == 1.0, x, y }
                }
                ("Z", []) => PathCommand::Z,
                ("h", &[dx]) => {
                    let (x, y) = end_point(&commands);
                    PathCommand::L(x + dx, y)
                }
                ("v", &[dy]) => {
                    let (x, y) = end_point(&commands);
                    PathCommand::L(x, y + dy)
                }
                other => panic!("unreadable path segment {other:?}"),
            });
        }
        PathData(commands)
    }

    fn end_point(commands: &[PathCommand]) -> (f64, f64) {
        match commands.last().expect("a relative segment follows a command") {
            PathCommand::M(x, y) | PathCommand::L(x, y) | PathCommand::C(.., x, y) | PathCommand::A { x, y, .. } => (*x, *y),
            PathCommand::Z => panic!("a relative segment after Z"),
        }
    }

    /// The path's drawn outline as points about `step` apart (lines, circular arcs, curves).
    pub fn flatten(path: &PathData, step: f64) -> Vec<(f64, f64)> {
        let (mut at, mut start) = ((0.0, 0.0), (0.0, 0.0));
        let mut out = Vec::new();
        let mut segment = |out: &mut Vec<(f64, f64)>, length: f64, point: &dyn Fn(f64) -> (f64, f64)| {
            let n = (length / step).ceil().max(1.0) as usize;
            out.extend((0..=n).map(|i| point(i as f64 / n as f64)));
        };
        for command in path.commands() {
            match *command {
                PathCommand::M(x, y) => {
                    at = (x, y);
                    start = at;
                    out.push(at);
                }
                PathCommand::L(x, y) => {
                    let from = at;
                    segment(&mut out, (x - from.0).hypot(y - from.1), &|t| (from.0 + t * (x - from.0), from.1 + t * (y - from.1)));
                    at = (x, y);
                }
                PathCommand::Z => {
                    let (from, to) = (at, start);
                    segment(&mut out, (to.0 - from.0).hypot(to.1 - from.1), &|t| (from.0 + t * (to.0 - from.0), from.1 + t * (to.1 - from.1)));
                    at = start;
                }
                PathCommand::C(a, b, c, d, x, y) => {
                    let from = at;
                    let length = (a - from.0).hypot(b - from.1) + (c - a).hypot(d - b) + (x - c).hypot(y - d);
                    segment(&mut out, length, &|t| {
                        let u = 1.0 - t;
                        (
                            u * u * u * from.0 + 3.0 * u * u * t * a + 3.0 * u * t * t * c + t * t * t * x,
                            u * u * u * from.1 + 3.0 * u * u * t * b + 3.0 * u * t * t * d + t * t * t * y,
                        )
                    });
                    at = (x, y);
                }
                PathCommand::A { rx, large, sweep, x, y, .. } => {
                    let from = at;
                    let (mx, my) = ((from.0 + x) / 2.0, (from.1 + y) / 2.0);
                    let (cx, cy) = (x - from.0, y - from.1);
                    let chord = cx.hypot(cy);
                    let h = (rx * rx - chord * chord / 4.0).max(0.0).sqrt();
                    let (nx, ny) = (-cy / chord, cx / chord);
                    let cross = |c: (f64, f64)| (from.0 - c.0) * (y - c.1) - (from.1 - c.1) * (x - c.0);
                    let candidates = [(mx + h * nx, my + h * ny), (mx - h * nx, my - h * ny)];
                    let center = *candidates.iter().find(|c| ((cross(**c) > 0.0) == sweep) != large).unwrap();
                    let a0 = (from.1 - center.1).atan2(from.0 - center.0);
                    let mut da = (y - center.1).atan2(x - center.0) - a0;
                    if sweep && da < 0.0 {
                        da += TAU;
                    } else if !sweep && da > 0.0 {
                        da -= TAU;
                    }
                    segment(&mut out, rx * da.abs(), &|t| (center.0 + rx * (a0 + t * da).cos(), center.1 + rx * (a0 + t * da).sin()));
                    at = (x, y);
                }
            }
        }
        out
    }

    /// The farthest either outline's sample sits from the other's.
    pub fn hausdorff(a: &[(f64, f64)], b: &[(f64, f64)]) -> f64 {
        let reach = |from: &[(f64, f64)], to: &[(f64, f64)]| {
            from.iter().map(|p| to.windows(2).map(|w| segment_distance(*p, w[0], w[1])).fold(f64::MAX, f64::min)).fold(0.0, f64::max)
        };
        reach(a, b).max(reach(b, a))
    }

    fn segment_distance(p: (f64, f64), a: (f64, f64), b: (f64, f64)) -> f64 {
        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
        let len2 = dx * dx + dy * dy;
        let t = if len2 == 0.0 { 0.0 } else { (((p.0 - a.0) * dx + (p.1 - a.1) * dy) / len2).clamp(0.0, 1.0) };
        (p.0 - a.0 - t * dx).hypot(p.1 - a.1 - t * dy)
    }

    fn numbers(command: &PathCommand) -> Vec<f64> {
        match *command {
            PathCommand::M(x, y) | PathCommand::L(x, y) => vec![x, y],
            PathCommand::C(a, b, c, d, x, y) => vec![a, b, c, d, x, y],
            PathCommand::A { rx, ry, rot, x, y, .. } => vec![rx, ry, rot, x, y],
            PathCommand::Z => vec![],
        }
    }

    /// `got` is the path d3 printed as `d`, to the precision d3 printed it.
    pub fn assert_path(got: &PathData, d: impl AsRef<str>) {
        let want = parse_d(d.as_ref());
        assert_eq!(got.0.len(), want.0.len(), "{got:?} vs {want:?}");
        for (g, w) in got.0.iter().zip(&want.0) {
            assert_eq!(std::mem::discriminant(g), std::mem::discriminant(w), "{got:?} vs {want:?}");
            if let (PathCommand::A { large: gl, sweep: gs, .. }, PathCommand::A { large: wl, sweep: ws, .. }) = (g, w) {
                assert_eq!((gl, gs), (wl, ws), "{got:?} vs {want:?}");
            }
            for (a, b) in numbers(g).into_iter().zip(numbers(w)) {
                assert!((a - b).abs() <= PATH_COORD_TOLERANCE + 1e-9, "{a} vs {b} in {got:?} vs {want:?}");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f64::consts::PI;

    #[test]
    fn the_coordinate_tolerance_is_half_a_unit_of_the_last_digit() {
        assert_eq!(PATH_COORD_TOLERANCE, 0.5 / 10f64.powi(WITH_PATH_DIGITS as i32));
    }

    #[test]
    fn a_path_records_exact_coordinates() {
        let mut path = Path::new();
        path.move_to(3.061665, -148.91853);
        path.line_to(0.0004, 12.5);
        assert_eq!(
            path.into_path_data().commands(),
            [PathCommand::M(3.061665, -148.91853), PathCommand::L(0.0004, 12.5)]
        );
    }

    /// Every circular arc's midpoint as an SVG renderer reads the command, for a path whose
    /// `A`s are all `large = false`.
    fn arc_midpoints(path: &PathData) -> Vec<(f64, f64, f64)> {
        let mut at = (0.0, 0.0);
        let mut out = Vec::new();
        for command in path.commands() {
            match *command {
                PathCommand::M(x, y) | PathCommand::L(x, y) => at = (x, y),
                PathCommand::A { rx, large, sweep, x, y, .. } => {
                    assert!(!large, "a piece of at most 90 degrees is never large");
                    let (mx, my) = ((at.0 + x) / 2.0, (at.1 + y) / 2.0);
                    let (cx, cy) = (x - at.0, y - at.1);
                    let chord = cx.hypot(cy);
                    let h = (rx * rx - chord * chord / 4.0).max(0.0).sqrt();
                    let (nx, ny) = (-cy / chord, cx / chord);
                    let cross = |c: (f64, f64)| (at.0 - c.0) * (y - c.1) - (at.1 - c.1) * (x - c.0);
                    let candidates = [(mx + h * nx, my + h * ny), (mx - h * nx, my - h * ny)];
                    let center = *candidates.iter().find(|c| (cross(**c) > 0.0) == sweep).unwrap();
                    let (dx, dy) = (mx - center.0, my - center.1);
                    let d = dx.hypot(dy);
                    out.push((center.0 + rx * dx / d, center.1 + rx * dy / d, rx));
                    at = (x, y);
                }
                _ => {}
            }
        }
        out
    }

    /// `print` writes geometry to 0.01, so an arc whose chord nearly equals its diameter must
    /// not depend on its endpoints' last digits: its center sits `sqrt(r^2 - (chord/2)^2)` from the
    /// chord, and a hundredth of endpoint error moves that by far more than a hundredth.
    #[test]
    fn a_near_semicircle_survives_rounding_its_endpoints_to_a_hundredth() {
        let r = 140.4;
        let (a0, a1) = (-PI / 2.0 + 0.0218, PI / 2.0 - 0.0218);
        let mut path = Path::new();
        path.arc(0.0, 0.0, r, a0, a1, false);
        let rounded = testing::parse_d(&print::path_d(&path.into_path_data()));
        for (mx, my, rx) in arc_midpoints(&rounded) {
            let off = mx.hypot(my) - rx;
            assert!(off.abs() <= 0.01, "an arc midpoint sits {off} from the circle");
        }
    }

    #[test]
    fn a_sweep_records_in_equal_pieces_of_at_most_ninety_degrees() {
        let pieces = |a1: f64, ccw: bool| {
            let mut path = Path::new();
            path.arc(0.0, 0.0, 10.0, 0.0, a1, ccw);
            path.into_path_data().0.iter().filter(|c| matches!(c, PathCommand::A { .. })).count()
        };
        assert_eq!(pieces(PI / 2.0, false), 1);
        assert_eq!(pieces(PI / 2.0 + 0.01, false), 2);
        assert_eq!(pieces(PI, false), 2);
        assert_eq!(pieces(-PI, true), 2);
        assert_eq!(pieces(TAU, false), 4);
    }

    #[test]
    fn a_full_circle_is_four_quarters_and_a_short_one_is_small() {
        let mut full = Path::new();
        full.arc(0.0, 0.0, 10.0, 0.0, TAU, false);
        let full = full.into_path_data();
        let [PathCommand::M(..), PathCommand::A { large: false, sweep: true, .. }, PathCommand::A { .. }, PathCommand::A { .. }, PathCommand::A { x, y, .. }] =
            full.commands()
        else {
            panic!("a circle is a move and four quarter arcs")
        };
        assert!((x - 10.0).abs() < 1e-9 && y.abs() < 1e-9, "the last quarter closes on the start");
        let mut short = Path::new();
        short.arc(0.0, 0.0, 10.0, 0.0, -1.0, true);
        let [PathCommand::M(..), PathCommand::A { large: false, sweep: false, .. }] = short.into_path_data().commands() else {
            panic!("a short counterclockwise arc is a move and one small arc")
        };
    }
}
