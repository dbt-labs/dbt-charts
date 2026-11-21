//! Path commands: the geometry of a `Path` shape. Coordinates are exact `f64`;
//! `print` is the only place they become text.

use crate::geom::Bounds;

/// One command of d3-path's compact form: arcs are the `A` of an SVG path (d3
/// writes `rot` as 0).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum PathCommand {
    M(f64, f64),
    L(f64, f64),
    /// Cubic: two control points, then the end.
    C(f64, f64, f64, f64, f64, f64),
    A { rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, x: f64, y: f64 },
    Z,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct PathData(pub Vec<PathCommand>);

impl PathData {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn commands(&self) -> &[PathCommand] {
        &self.0
    }

    pub fn move_to(mut self, x: f64, y: f64) -> Self {
        self.0.push(PathCommand::M(x, y));
        self
    }

    pub fn line_to(mut self, x: f64, y: f64) -> Self {
        self.0.push(PathCommand::L(x, y));
        self
    }

    /// A circular arc of radius `r` to `(x, y)`, the sweep clockwise on screen when `sweep`.
    pub fn arc_to(mut self, r: f64, sweep: bool, x: f64, y: f64) -> Self {
        self.0.push(PathCommand::A { rx: r, ry: r, rot: 0.0, large: false, sweep, x, y });
        self
    }

    pub fn close(mut self) -> Self {
        self.0.push(PathCommand::Z);
        self
    }

    /// The box of the drawn outline, no stroke: endpoints plus the interior
    /// extrema of every curve and arc. `None` for a path with no point.
    pub fn bounds(&self) -> Option<Bounds> {
        let mut points: Vec<(f64, f64)> = Vec::new();
        let mut at = (0.0, 0.0);
        let mut start = (0.0, 0.0);
        for command in &self.0 {
            match *command {
                PathCommand::M(x, y) => {
                    at = (x, y);
                    start = at;
                    points.push(at);
                }
                PathCommand::L(x, y) => {
                    at = (x, y);
                    points.push(at);
                }
                PathCommand::C(x1, y1, x2, y2, x, y) => {
                    points.push((x, y));
                    for t in cubic_extrema(at.0, x1, x2, x).into_iter().flatten() {
                        points.push((cubic_at(at.0, x1, x2, x, t), cubic_at(at.1, y1, y2, y, t)));
                    }
                    for t in cubic_extrema(at.1, y1, y2, y).into_iter().flatten() {
                        points.push((cubic_at(at.0, x1, x2, x, t), cubic_at(at.1, y1, y2, y, t)));
                    }
                    at = (x, y);
                }
                PathCommand::A { rx, ry, rot, large, sweep, x, y } => {
                    points.push((x, y));
                    points.extend(arc_extrema(at, rx, ry, rot, large, sweep, (x, y)));
                    at = (x, y);
                }
                PathCommand::Z => at = start,
            }
        }
        Bounds::of_points(points)
    }
}

/// The stroke length of the longest subpath. SVG restarts the dash pattern at
/// every `M`, so one dasharray can fit only one subpath; the longest is the one
/// whose ends a reader sees. Curves are measured, not flattened to a fixed
/// grid: cubics by Gravesen's adaptive chord/control-polygon estimate (error
/// under 1e-6 px), arcs by 512 chords of the ellipse (relative error ~1e-6).
impl PathData {
    pub fn length(&self) -> f64 {
        let (mut longest, mut run) = (0.0_f64, 0.0_f64);
        let mut at = (0.0, 0.0);
        let mut start = at;
        for command in &self.0 {
            let to = match *command {
                PathCommand::M(x, y) => {
                    longest = longest.max(run);
                    run = 0.0;
                    start = (x, y);
                    (x, y)
                }
                PathCommand::L(x, y) => {
                    run += distance(at, (x, y));
                    (x, y)
                }
                PathCommand::C(x1, y1, x2, y2, x, y) => {
                    run += cubic_length([at, (x1, y1), (x2, y2), (x, y)]);
                    (x, y)
                }
                PathCommand::A { rx, ry, rot, large, sweep, x, y } => {
                    run += arc_length(at, rx, ry, rot, large, sweep, (x, y));
                    (x, y)
                }
                PathCommand::Z => {
                    run += distance(at, start);
                    start
                }
            };
            at = to;
        }
        longest.max(run)
    }
}

fn distance(a: (f64, f64), b: (f64, f64)) -> f64 {
    (a.0 - b.0).hypot(a.1 - b.1)
}

fn cubic_length(p: [(f64, f64); 4]) -> f64 {
    let chord = distance(p[0], p[3]);
    let polygon = distance(p[0], p[1]) + distance(p[1], p[2]) + distance(p[2], p[3]);
    if polygon - chord < 1e-7 {
        return (2.0 * chord + polygon) / 3.0;
    }
    let mid = |a: (f64, f64), b: (f64, f64)| ((a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0);
    let (p01, p12, p23) = (mid(p[0], p[1]), mid(p[1], p[2]), mid(p[2], p[3]));
    let (p012, p123) = (mid(p01, p12), mid(p12, p23));
    let p0123 = mid(p012, p123);
    cubic_length([p[0], p01, p012, p0123]) + cubic_length([p0123, p123, p23, p[3]])
}

fn arc_length(from: (f64, f64), rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, to: (f64, f64)) -> f64 {
    let Some(arc) = ArcCenter::of(from, rx, ry, rot, large, sweep, to) else {
        return distance(from, to);
    };
    const CHORDS: usize = 512;
    let point = |i: usize| arc.at(arc.theta1 + arc.delta * i as f64 / CHORDS as f64);
    (0..CHORDS).map(|i| distance(point(i), point(i + 1))).sum()
}

fn cubic_at(p0: f64, p1: f64, p2: f64, p3: f64, t: f64) -> f64 {
    let u = 1.0 - t;
    u * u * u * p0 + 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t * p3
}

/// The `t` in (0, 1) where a cubic's derivative along one axis vanishes.
fn cubic_extrema(p0: f64, p1: f64, p2: f64, p3: f64) -> [Option<f64>; 2] {
    let a = 3.0 * (-p0 + 3.0 * p1 - 3.0 * p2 + p3);
    let b = 6.0 * (p0 - 2.0 * p1 + p2);
    let c = 3.0 * (p1 - p0);
    let inside = |t: f64| (t > 0.0 && t < 1.0).then_some(t);
    if a.abs() < 1e-12 {
        return [if b.abs() < 1e-12 { None } else { inside(-c / b) }, None];
    }
    let disc = b * b - 4.0 * a * c;
    if disc < 0.0 {
        return [None, None];
    }
    let root = disc.sqrt();
    [inside((-b + root) / (2.0 * a)), inside((-b - root) / (2.0 * a))]
}

/// An elliptical arc in center form (SVG 1.1 F.6.5, from the endpoint form).
struct ArcCenter {
    cx: f64,
    cy: f64,
    rx: f64,
    ry: f64,
    sin: f64,
    cos: f64,
    theta1: f64,
    delta: f64,
}

impl ArcCenter {
    /// `None` for a degenerate arc, which SVG draws as the straight line.
    fn of(from: (f64, f64), rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, to: (f64, f64)) -> Option<Self> {
        let (mut rx, mut ry) = (rx.abs(), ry.abs());
        if rx == 0.0 || ry == 0.0 || from == to {
            return None;
        }
        let (sin, cos) = rot.to_radians().sin_cos();
        let (dx, dy) = ((from.0 - to.0) / 2.0, (from.1 - to.1) / 2.0);
        let (x1p, y1p) = (cos * dx + sin * dy, -sin * dx + cos * dy);
        let lambda = (x1p / rx).powi(2) + (y1p / ry).powi(2);
        if lambda > 1.0 {
            rx *= lambda.sqrt();
            ry *= lambda.sqrt();
        }
        let num = (rx * ry).powi(2) - (rx * y1p).powi(2) - (ry * x1p).powi(2);
        let den = (rx * y1p).powi(2) + (ry * x1p).powi(2);
        let factor = if large == sweep { -1.0 } else { 1.0 } * (num / den).max(0.0).sqrt();
        let (cxp, cyp) = (factor * rx * y1p / ry, -factor * ry * x1p / rx);
        let (cx, cy) = (cos * cxp - sin * cyp + (from.0 + to.0) / 2.0, sin * cxp + cos * cyp + (from.1 + to.1) / 2.0);
        let angle = |ux: f64, uy: f64| uy.atan2(ux);
        let theta1 = angle((x1p - cxp) / rx, (y1p - cyp) / ry);
        let theta2 = angle((-x1p - cxp) / rx, (-y1p - cyp) / ry);
        let mut delta = theta2 - theta1;
        if sweep && delta < 0.0 {
            delta += std::f64::consts::TAU;
        } else if !sweep && delta > 0.0 {
            delta -= std::f64::consts::TAU;
        }
        Some(Self { cx, cy, rx, ry, sin, cos, theta1, delta })
    }

    fn at(&self, theta: f64) -> (f64, f64) {
        let (ex, ey) = (self.rx * theta.cos(), self.ry * theta.sin());
        (self.cos * ex - self.sin * ey + self.cx, self.sin * ex + self.cos * ey + self.cy)
    }
}

/// The points of an elliptical arc where it is extreme along x or y.
fn arc_extrema(from: (f64, f64), rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, to: (f64, f64)) -> Vec<(f64, f64)> {
    let Some(arc) = ArcCenter::of(from, rx, ry, rot, large, sweep, to) else {
        return Vec::new();
    };
    let within = |theta: f64| {
        let turn = (theta - arc.theta1) * arc.delta.signum();
        let turn = turn.rem_euclid(std::f64::consts::TAU);
        turn <= arc.delta.abs()
    };
    let along_x = (-arc.ry * arc.sin).atan2(arc.rx * arc.cos);
    let along_y = (arc.ry * arc.cos).atan2(arc.rx * arc.sin);
    [along_x, along_x + std::f64::consts::PI, along_y, along_y + std::f64::consts::PI]
        .into_iter()
        .filter(|&theta| within(theta))
        .map(|theta| arc.at(theta))
        .collect()
}
