//! Port of d3-path's Path serializer — just the subset arc() emission needs.
//! Reference: https://github.com/d3/d3-path/blob/main/src/path.js

use crate::js_math::js_round;
use std::f64::consts::{PI, TAU};
use std::fmt::Write;

const EPSILON: f64 = 1e-6;
const TAU_EPSILON: f64 = TAU - EPSILON;

/// d3-shape@3.2.0 `src/path.js` `withPath`: `let digits = 3; return () => new Path(digits)`,
/// the path every `line`, `area`, `symbol`, `link` and `arc` generator builds.
pub const WITH_PATH_DIGITS: u32 = 3;

/// The most a printed path coordinate can sit from the position it was computed from:
/// `Math.round(v * k) / k` (d3-path@3.1.0 `src/path.js`) with `k = 10^WITH_PATH_DIGITS` moves
/// `v` by at most half a unit of the last digit.
pub const PATH_COORD_TOLERANCE: f64 = 0.0005;

pub struct Path {
    /// d3-path's `new Path(digits)`: every printed coordinate is
    /// `Math.round(v * 10^digits) / 10^digits`.
    digits: u32,
    out: String,
    x0: f64,
    y0: f64,
    x1: Option<f64>,
    y1: f64,
}

impl Path {
    /// d3-path's `pathRound(digits)` (`digits` at most 15, as there).
    pub fn rounded(digits: u32) -> Self {
        assert!(digits <= 15, "invalid digits: {digits}");
        Path { digits, out: String::with_capacity(256), x0: 0.0, y0: 0.0, x1: None, y1: 0.0 }
    }

    pub fn move_to(&mut self, x: f64, y: f64) {
        let d = self.digits;
        self.x0 = x;
        self.y0 = y;
        self.x1 = Some(x);
        self.y1 = y;
        let _ = write!(self.out, "M{},{}", fmt(x, d), fmt(y, d));
    }

    pub fn line_to(&mut self, x: f64, y: f64) {
        let d = self.digits;
        self.x1 = Some(x);
        self.y1 = y;
        let _ = write!(self.out, "L{},{}", fmt(x, d), fmt(y, d));
    }

    pub fn bezier_curve_to(&mut self, x1: f64, y1: f64, x2: f64, y2: f64, x: f64, y: f64) {
        let d = self.digits;
        self.x1 = Some(x);
        self.y1 = y;
        let _ = write!(
            self.out,
            "C{},{},{},{},{},{}",
            fmt(x1, d),
            fmt(y1, d),
            fmt(x2, d),
            fmt(y2, d),
            fmt(x, d),
            fmt(y, d)
        );
    }

    pub fn close_path(&mut self) {
        if self.x1.is_some() {
            self.x1 = Some(self.x0);
            self.y1 = self.y0;
            self.out.push('Z');
        }
    }

    /// d3-path's Path.arc: center-parameterized arc → SVG `A` commands.
    pub fn arc(&mut self, x: f64, y: f64, r: f64, a0: f64, a1: f64, ccw: bool) {
        let d = self.digits;
        let dx = r * a0.cos();
        let dy = r * a0.sin();
        let x0 = x + dx;
        let y0 = y + dy;
        let cw = if ccw { 0 } else { 1 };
        let mut da = if ccw { a0 - a1 } else { a1 - a0 };

        assert!(r >= 0.0, "negative radius: {r}");

        // Is this path empty? Move to (x0, y0).
        match self.x1 {
            None => {
                let _ = write!(self.out, "M{},{}", fmt(x0, d), fmt(y0, d));
                self.x1 = Some(x0);
                self.y1 = y0;
            }
            // Or is (x0, y0) not coincident with the previous point? Line to (x0, y0).
            Some(px1) => {
                if (px1 - x0).abs() > EPSILON || (self.y1 - y0).abs() > EPSILON {
                    let _ = write!(self.out, "L{},{}", fmt(x0, d), fmt(y0, d));
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

        if da > TAU_EPSILON {
            // Is this a complete circle? Draw two arcs to complete the circle.
            let _ = write!(
                self.out,
                "A{},{},0,1,{},{},{}A{},{},0,1,{},{},{}",
                fmt(r, d),
                fmt(r, d),
                cw,
                fmt(x - dx, d),
                fmt(y - dy, d),
                fmt(r, d),
                fmt(r, d),
                cw,
                fmt(x0, d),
                fmt(y0, d)
            );
            self.x1 = Some(x0);
            self.y1 = y0;
        } else if da > EPSILON {
            // Is this arc non-empty? Draw an arc.
            let x1 = x + r * a1.cos();
            let y1 = y + r * a1.sin();
            let _ = write!(
                self.out,
                "A{},{},0,{},{},{},{}",
                fmt(r, d),
                fmt(r, d),
                i32::from(da >= PI),
                cw,
                fmt(x1, d),
                fmt(y1, d)
            );
            self.x1 = Some(x1);
            self.y1 = y1;
        }
    }

    pub fn into_string(self) -> String {
        self.out
    }
}

fn fmt(v: f64, digits: u32) -> String {
    let scale = 10f64.powi(digits as i32);
    let rounded = js_round(v * scale) / scale;
    // `String(-0)` is "0".
    if rounded == 0.0 { "0".to_string() } else { rounded.to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// d3-path's `pathRound(digits)` appends `Math.round(v * 10^digits) /
    /// 10^digits`: a tie goes toward +infinity and a negative value that
    /// rounds to zero prints `0`, never `-0`.
    #[test]
    fn rounded_path_rounds_like_javascript() {
        let mut path = Path::rounded(0);
        path.move_to(2.5, -2.5);
        path.line_to(-0.5, -0.4);
        path.line_to(0.4999999999999999, 1e20);
        assert_eq!(path.into_string(), "M3,-2L0,0L0,100000000000000000000");
    }

    #[test]
    fn the_coordinate_tolerance_is_half_a_unit_of_the_last_digit() {
        assert_eq!(PATH_COORD_TOLERANCE, 0.5 / 10f64.powi(WITH_PATH_DIGITS as i32));
    }

    #[test]
    fn rounded_path_trims_to_the_digits() {
        let mut path = Path::rounded(3);
        path.move_to(3.061665, -148.91853);
        path.line_to(0.0004, 12.5);
        assert_eq!(path.into_string(), "M3.062,-148.919L0,12.5");
    }
}
