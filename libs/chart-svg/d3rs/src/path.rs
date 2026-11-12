//! Port of d3-path's Path serializer — just the subset the generators need. A `Path` records the
//! commands d3-path would write, as plain numbers; turning them into a string, rounding them or
//! splitting an arc is the caller's business.
//! Reference: https://github.com/d3/d3-path/blob/main/src/path.js

use std::f64::consts::TAU;

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

/// One command of a path, in absolute coordinates.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Command {
    MoveTo(f64, f64),
    LineTo(f64, f64),
    /// `bezierCurveTo(x1, y1, x2, y2, x, y)`.
    BezierCurveTo(f64, f64, f64, f64, f64, f64),
    /// d3-path's `arc(x, y, r, a0, a1, ccw)` with a positive radius and a nonzero [`arc_sweep`],
    /// recorded as called; the `moveTo`/`lineTo` to its start precedes it as its own command.
    /// d3 writes the sweep as one SVG `A` (two for a full circle); the center is what
    /// lets a caller choose its own pieces.
    Arc { x: f64, y: f64, r: f64, a0: f64, a1: f64, ccw: bool },
    /// An SVG elliptical arc to `(x, y)`, as `d3-geo`, `d3-shape`'s `symbol` and `arcTo` write it.
    SvgArc { rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, x: f64, y: f64 },
    ClosePath,
}

/// The angle d3-path's `arc(.., a0, a1, ccw)` sweeps: `a0` to `a1` in the given direction, a full
/// turn when it reaches [`TAU`] within d3's epsilon, `0` when it is within d3's epsilon of nothing.
pub fn arc_sweep(a0: f64, a1: f64, ccw: bool) -> f64 {
    let mut da = if ccw { a0 - a1 } else { a1 - a0 };
    // Does the angle go the wrong way? Flip the direction.
    if da < 0.0 {
        da = da % TAU + TAU;
    }
    let sweep = if da > TAU_EPSILON { TAU } else { da };
    if sweep > EPSILON {
        sweep
    } else {
        0.0
    }
}

pub struct Path {
    commands: Vec<Command>,
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
        self.commands.push(Command::MoveTo(x, y));
    }

    pub fn line_to(&mut self, x: f64, y: f64) {
        self.x1 = Some(x);
        self.y1 = y;
        self.commands.push(Command::LineTo(x, y));
    }

    pub fn bezier_curve_to(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, x: f64, y: f64) {
        self.x1 = Some(x);
        self.y1 = y;
        self.commands.push(Command::BezierCurveTo(x1, y1, x2, y2, x, y));
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
            self.commands.push(Command::ClosePath);
        }
    }

    /// d3-path's Path.arc: the move or line to the arc's start, then the arc itself.
    pub fn arc(&mut self, x: f64, y: f64, r: f64, a0: f64, a1: f64, ccw: bool) {
        let x0 = x + r * a0.cos();
        let y0 = y + r * a0.sin();

        assert!(r >= 0.0, "negative radius: {r}");

        // Is this path empty? Move to (x0, y0).
        match self.x1 {
            None => {
                self.commands.push(Command::MoveTo(x0, y0));
                self.x1 = Some(x0);
                self.y1 = y0;
            }
            // Or is (x0, y0) not coincident with the previous point? Line to (x0, y0).
            Some(px1) => {
                if (px1 - x0).abs() > EPSILON || (self.y1 - y0).abs() > EPSILON {
                    self.commands.push(Command::LineTo(x0, y0));
                    self.x1 = Some(x0);
                    self.y1 = y0;
                }
            }
        }

        let sweep = arc_sweep(a0, a1, ccw);
        if r == 0.0 || sweep == 0.0 {
            return;
        }
        self.commands.push(Command::Arc { x, y, r, a0, a1, ccw });
        let (end_x, end_y) = if sweep == TAU {
            (x0, y0)
        } else {
            let end = a0 + if ccw { -sweep } else { sweep };
            (x + r * end.cos(), y + r * end.sin())
        };
        self.x1 = Some(end_x);
        self.y1 = end_y;
    }

    pub fn into_commands(self) -> Vec<Command> {
        self.commands
    }
}

impl Default for Path {
    fn default() -> Self {
        Self::new()
    }
}

/// Test support: reads a `d` string back into commands and compares outlines.
pub mod testing {
    use crate::invariant::{self, at, Invariant};

    use super::{arc_sweep, Command, PATH_COORD_TOLERANCE, TAU};

    /// The `d` d3 prints: absolute `M L C A Z`, plus the relative `h`/`v` of d3-path's `rect`.
    pub fn parse_d(d: &str) -> Vec<Command> {
        let mut commands: Vec<Command> = Vec::new();
        let mut rest = d;
        while !rest.is_empty() {
            let end = rest[1..].find(|c: char| c.is_ascii_alphabetic()).map_or(rest.len(), |i| i + 1);
            let (segment, tail) = rest.split_at(end);
            rest = tail;
            let n: Vec<f64> = segment[1..].split(',').filter(|t| !t.is_empty()).map(|t| t.parse().held("a number")).collect();
            commands.push(match (&segment[..1], n.as_slice()) {
                ("M", &[x, y]) => Command::MoveTo(x, y),
                ("L", &[x, y]) => Command::LineTo(x, y),
                ("C", &[a, b, c, d, x, y]) => Command::BezierCurveTo(a, b, c, d, x, y),
                ("A", &[rx, ry, rot, large, sweep, x, y]) => {
                    Command::SvgArc { rx, ry, rot, large: large == 1.0, sweep: sweep == 1.0, x, y }
                }
                ("Z", []) => Command::ClosePath,
                ("h", &[dx]) => {
                    let (x, y) = end_point(&commands);
                    Command::LineTo(x + dx, y)
                }
                ("v", &[dy]) => {
                    let (x, y) = end_point(&commands);
                    Command::LineTo(x, y + dy)
                }
                other => invariant::broken(&format!("unreadable path segment {other:?}")),
            });
        }
        commands
    }

    fn end_point(commands: &[Command]) -> (f64, f64) {
        match commands.last().held("a relative segment follows a command") {
            Command::MoveTo(x, y) | Command::LineTo(x, y) | Command::BezierCurveTo(.., x, y) | Command::SvgArc { x, y, .. } => (*x, *y),
            Command::Arc { .. } | Command::ClosePath => invariant::broken("a relative segment after an arc or Z"),
        }
    }

    /// The path's drawn outline as points about `step` apart (lines, circular arcs, curves).
    pub fn flatten(path: &[Command], step: f64) -> Vec<(f64, f64)> {
        let (mut at, mut start) = ((0.0, 0.0), (0.0, 0.0));
        let mut out = Vec::new();
        let segment = |out: &mut Vec<(f64, f64)>, length: f64, point: &dyn Fn(f64) -> (f64, f64)| {
            let n = (length / step).ceil().max(1.0) as usize;
            out.extend((0..=n).map(|i| point(i as f64 / n as f64)));
        };
        for command in path {
            match *command {
                Command::MoveTo(x, y) => {
                    at = (x, y);
                    start = at;
                    out.push(at);
                }
                Command::LineTo(x, y) => {
                    let from = at;
                    segment(&mut out, (x - from.0).hypot(y - from.1), &|t| (from.0 + t * (x - from.0), from.1 + t * (y - from.1)));
                    at = (x, y);
                }
                Command::ClosePath => {
                    let (from, to) = (at, start);
                    segment(&mut out, (to.0 - from.0).hypot(to.1 - from.1), &|t| (from.0 + t * (to.0 - from.0), from.1 + t * (to.1 - from.1)));
                    at = start;
                }
                Command::BezierCurveTo(a, b, c, d, x, y) => {
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
                Command::Arc { x, y, r, a0, a1, ccw } => {
                    let da = arc_sweep(a0, a1, ccw) * if ccw { -1.0 } else { 1.0 };
                    segment(&mut out, r * da.abs(), &|t| (x + r * (a0 + t * da).cos(), y + r * (a0 + t * da).sin()));
                    at = (x + r * (a0 + da).cos(), y + r * (a0 + da).sin());
                }
                Command::SvgArc { rx, large, sweep, x, y, .. } => {
                    let from = at;
                    let (mx, my) = ((from.0 + x) / 2.0, (from.1 + y) / 2.0);
                    let (cx, cy) = (x - from.0, y - from.1);
                    let chord = cx.hypot(cy);
                    let h = (rx * rx - chord * chord / 4.0).max(0.0).sqrt();
                    let (nx, ny) = (-cy / chord, cx / chord);
                    let cross = |c: (f64, f64)| (from.0 - c.0) * (y - c.1) - (from.1 - c.1) * (x - c.0);
                    let candidates = [(mx + h * nx, my + h * ny), (mx - h * nx, my - h * ny)];
                    let center = *candidates.iter().find(|c| ((cross(**c) > 0.0) == sweep) != large).held("one of the two arc centers matches the sweep");
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
            from.iter().map(|p| to.windows(2).map(|w| segment_distance(*p, *at(w, 0), *at(w, 1))).fold(f64::MAX, f64::min)).fold(0.0, f64::max)
        };
        reach(a, b).max(reach(b, a))
    }

    fn segment_distance(p: (f64, f64), a: (f64, f64), b: (f64, f64)) -> f64 {
        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
        let len2 = dx * dx + dy * dy;
        let t = if len2 == 0.0 { 0.0 } else { (((p.0 - a.0) * dx + (p.1 - a.1) * dy) / len2).clamp(0.0, 1.0) };
        (p.0 - a.0 - t * dx).hypot(p.1 - a.1 - t * dy)
    }

    fn numbers(command: &Command) -> Vec<f64> {
        match *command {
            Command::MoveTo(x, y) | Command::LineTo(x, y) => vec![x, y],
            Command::BezierCurveTo(a, b, c, d, x, y) => vec![a, b, c, d, x, y],
            Command::SvgArc { rx, ry, rot, x, y, .. } => vec![rx, ry, rot, x, y],
            Command::Arc { x, y, r, a0, a1, .. } => vec![x, y, r, a0, a1],
            Command::ClosePath => vec![],
        }
    }

    /// Whether `got` draws the outline of `want`, the `d` d3 printed, to the precision d3 printed it:
    /// every sample of one within two coordinate roundings' hypotenuse of the other (a relative `h`
    /// or `v` d3 prints carries the rounding of the point it starts from).
    pub fn outline_matches(got: &[Command], want: &str) -> Result<(), String> {
        let want_commands = parse_d(want);
        // Samples a few hundred to a side, so a large shape costs no more than a small one; the chord
        // error of that spacing stays far under the tolerance.
        let reach = flatten(&want_commands, f64::MAX).iter().fold(0.0, |m: f64, p| m.max(p.0.abs()).max(p.1.abs()));
        let step = (reach / 200.0).max(0.05);
        let (got_points, want_points) = (flatten(got, step), flatten(&want_commands, step));
        let tolerance = 2.0 * PATH_COORD_TOLERANCE * 2f64.sqrt();
        if got_points.len() < 2 && want_points.len() < 2 {
            // A point has no segments to measure between.
            return match (got_points.first(), want_points.first()) {
                (Some(g), Some(w)) if (g.0 - w.0).hypot(g.1 - w.1) <= tolerance => Ok(()),
                (None, None) => Ok(()),
                _ => Err(format!("{got:?} vs {want}")),
            };
        }
        let apart = hausdorff(&got_points, &want_points);
        if apart <= tolerance {
            Ok(())
        } else {
            Err(format!("{apart} apart: got {got:?}, want {want}"))
        }
    }

    /// `got` is the path d3 printed as `d`, to the precision d3 printed it.
    pub fn assert_path(got: &[Command], d: impl AsRef<str>) {
        let want = parse_d(d.as_ref());
        assert_eq!(got.len(), want.len(), "{got:?} vs {want:?}");
        for (g, w) in got.iter().zip(&want) {
            assert_eq!(std::mem::discriminant(g), std::mem::discriminant(w), "{got:?} vs {want:?}");
            if let (Command::SvgArc { large: gl, sweep: gs, .. }, Command::SvgArc { large: wl, sweep: ws, .. }) = (g, w) {
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
        assert_eq!(path.into_commands(), [Command::MoveTo(3.061665, -148.91853), Command::LineTo(0.0004, 12.5)]);
    }

    #[test]
    fn an_arc_is_recorded_as_called_after_its_start() {
        let mut path = Path::new();
        path.arc(1.0, 2.0, 10.0, 0.0, PI, true);
        assert_eq!(
            path.into_commands(),
            [Command::MoveTo(11.0, 2.0), Command::Arc { x: 1.0, y: 2.0, r: 10.0, a0: 0.0, a1: PI, ccw: true }]
        );
    }

    #[test]
    fn a_degenerate_arc_records_only_its_start() {
        for (r, a1) in [(0.0, PI), (10.0, 1e-9)] {
            let mut path = Path::new();
            path.arc(0.0, 0.0, r, 0.0, a1, false);
            assert_eq!(path.into_commands(), [Command::MoveTo(r, 0.0)], "r {r}, a1 {a1}");
        }
    }

    #[test]
    fn a_second_arc_starting_where_the_first_ended_adds_no_line() {
        let mut path = Path::new();
        path.arc(0.0, 0.0, 10.0, 0.0, PI, false);
        path.arc(0.0, 0.0, 10.0, PI, TAU, false);
        let commands = path.into_commands();
        assert!(!commands.iter().any(|c| matches!(c, Command::LineTo(..))), "{commands:?}");
    }

    #[test]
    fn a_sweep_is_the_directed_angle_and_a_full_turn_past_tau() {
        assert_eq!(arc_sweep(0.0, PI, false), PI);
        assert_eq!(arc_sweep(0.0, -PI, true), PI);
        assert_eq!(arc_sweep(0.0, -PI / 2.0, false), 1.5 * PI);
        assert_eq!(arc_sweep(0.0, 7.0, false), TAU);
        assert_eq!(arc_sweep(0.0, 1e-9, false), 0.0);
    }
}
