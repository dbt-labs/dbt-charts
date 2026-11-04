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

/// The points of an elliptical arc where it is extreme along x or y (SVG 1.1
/// F.6.5: the endpoint form to the center form).
fn arc_extrema(from: (f64, f64), rx: f64, ry: f64, rot: f64, large: bool, sweep: bool, to: (f64, f64)) -> Vec<(f64, f64)> {
    let (mut rx, mut ry) = (rx.abs(), ry.abs());
    if rx == 0.0 || ry == 0.0 || from == to {
        return Vec::new();
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
    let at = |theta: f64| {
        let (ex, ey) = (rx * theta.cos(), ry * theta.sin());
        (cos * ex - sin * ey + cx, sin * ex + cos * ey + cy)
    };
    let along_x = (-ry * sin).atan2(rx * cos);
    let along_y = (ry * cos).atan2(rx * sin);
    let within = |theta: f64| {
        let turn = (theta - theta1) * delta.signum();
        let turn = turn.rem_euclid(std::f64::consts::TAU);
        turn <= delta.abs()
    };
    [along_x, along_x + std::f64::consts::PI, along_y, along_y + std::f64::consts::PI]
        .into_iter()
        .filter(|&theta| within(theta))
        .map(at)
        .collect()
}
