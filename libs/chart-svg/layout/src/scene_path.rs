//! The one place a d3rs path becomes a `scene::PathData`: `scene_path(&[d3rs::path::Command]) -> PathData`
//! is the only conversion from a d3rs path into scene geometry, so every caller of a d3rs generator goes
//! through it. `d3rs` cannot depend on `scene`, and `scene` depends on nothing.
//!
//! One departure from d3: an `Arc` command (d3-path's `arc(x, y, r, a0, a1, ccw)`, which d3 writes as
//! one SVG `A`, two for a circle) becomes equal pieces of at most 90 degrees. `print` rounds geometry
//! to 0.01, which bends a near-semicircular arc whose center is ill-conditioned; pieces of a quarter
//! turn at most stay well conditioned, and the drawn circle is the same.

use d3rs::path::{arc_sweep, Command};
use scene::{PathCommand, PathData};
use std::f64::consts::{FRAC_PI_2, TAU};

/// `commands` as scene path commands; every `Arc` is split, everything else maps one to one.
pub fn scene_path(commands: &[Command]) -> PathData {
    let mut out = Vec::with_capacity(commands.len());
    for &command in commands {
        match command {
            Command::MoveTo(x, y) => out.push(PathCommand::M(x, y)),
            Command::LineTo(x, y) => out.push(PathCommand::L(x, y)),
            Command::BezierCurveTo(x1, y1, x2, y2, x, y) => out.push(PathCommand::C(x1, y1, x2, y2, x, y)),
            Command::SvgArc { rx, ry, rot, large, sweep, x, y } => out.push(PathCommand::A { rx, ry, rot, large, sweep, x, y }),
            Command::ClosePath => out.push(PathCommand::Z),
            Command::Arc { x, y, r, a0, a1, ccw } => {
                let sweep_angle = arc_sweep(a0, a1, ccw);
                let pieces = ((sweep_angle / FRAC_PI_2) - 1e-9).ceil().max(1.0) as usize;
                let direction = if ccw { -1.0 } else { 1.0 };
                for piece in 1..=pieces {
                    let (px, py) = if piece == pieces && sweep_angle == TAU {
                        (x + r * a0.cos(), y + r * a0.sin())
                    } else {
                        let angle = a0 + direction * sweep_angle * piece as f64 / pieces as f64;
                        (x + r * angle.cos(), y + r * angle.sin())
                    };
                    out.push(PathCommand::A { rx: r, ry: r, rot: 0.0, large: false, sweep: !ccw, x: px, y: py });
                }
            }
        }
    }
    PathData(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use d3rs::path::Path;
    use std::f64::consts::PI;

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

    fn arc_path(a1: f64, ccw: bool) -> PathData {
        let mut path = Path::new();
        path.arc(0.0, 0.0, 10.0, 0.0, a1, ccw);
        scene_path(&path.into_commands())
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
        let rounded = d3rs::path::testing::parse_d(&print::path_d(&scene_path(&path.into_commands())));
        for (mx, my, rx) in arc_midpoints(&scene_path(&rounded)) {
            let off = mx.hypot(my) - rx;
            assert!(off.abs() <= 0.01, "an arc midpoint sits {off} from the circle");
        }
    }

    #[test]
    fn a_sweep_is_split_in_equal_pieces_of_at_most_ninety_degrees() {
        let pieces = |a1: f64, ccw: bool| arc_path(a1, ccw).commands().iter().filter(|c| matches!(c, PathCommand::A { .. })).count();
        assert_eq!(pieces(PI / 2.0, false), 1);
        assert_eq!(pieces(PI / 2.0 + 0.01, false), 2);
        assert_eq!(pieces(PI, false), 2);
        assert_eq!(pieces(-PI, true), 2);
        assert_eq!(pieces(TAU, false), 4);
    }

    #[test]
    fn a_full_circle_is_four_quarters_and_a_short_one_is_small() {
        let full = arc_path(TAU, false);
        let [PathCommand::M(..), PathCommand::A { large: false, sweep: true, .. }, PathCommand::A { .. }, PathCommand::A { .. }, PathCommand::A { x, y, .. }] =
            full.commands()
        else {
            panic!("a circle is a move and four quarter arcs")
        };
        assert!((x - 10.0).abs() < 1e-9 && y.abs() < 1e-9, "the last quarter closes on the start");
        let [PathCommand::M(..), PathCommand::A { large: false, sweep: false, .. }] = arc_path(-1.0, true).commands() else {
            panic!("a short counterclockwise arc is a move and one small arc")
        };
    }
}
