//! d3-geo 3.1.1 `src/path/string.js`: `PathString`, the sink `geoPath` prints an SVG `d` string through.
//! Vega 6.2.0 draws a `shape` mark with this same sink (`path.context(null)`, `digits = 3`) and writes its
//! result into the `d` attribute untouched, so this is also the string vl-convert prints.

use crate::geo::stream::GeoStream;
use crate::js_math::js_round;
use std::fmt::Write;

#[derive(Clone, Copy)]
enum Point {
    /// Outside a line: `point` is an isolated point, drawn as a circle.
    Isolated,
    First,
    Rest,
}

pub struct PathString {
    /// `10 ** digits` when coordinates are rounded; d3 skips rounding for `digits` null or above 15.
    rounding: Option<f64>,
    radius: f64,
    out: String,
    in_polygon: bool,
    point: Point,
    /// Built on the first isolated point: the radius is fixed per sink.
    circle: Option<String>,
}

impl PathString {
    pub fn new(digits: Option<u32>, radius: f64) -> Self {
        Self {
            rounding: digits.filter(|d| *d <= 15).map(|d| 10f64.powi(d as i32)),
            radius,
            out: String::new(),
            in_polygon: false,
            point: Point::Isolated,
            circle: None,
        }
    }

    /// The path, or `None` for the empty string (d3 returns null); the sink starts over.
    pub fn result(&mut self) -> Option<String> {
        let result = std::mem::take(&mut self.out);
        (!result.is_empty()).then_some(result)
    }

    fn number(&self, v: f64) -> String {
        let mut out = String::new();
        push_number(&mut out, self.rounding, v);
        out
    }

    /// `command` and the point, written straight into the path.
    fn append(&mut self, command: char, x: f64, y: f64) {
        self.out.push(command);
        push_number(&mut self.out, self.rounding, x);
        self.out.push(',');
        push_number(&mut self.out, self.rounding, y);
    }

    fn circle_fragment(&self) -> String {
        let (r, diameter) = (self.number(self.radius), self.number(2.0 * self.radius));
        let negative_diameter = self.number(-2.0 * self.radius);
        format!("m0,{r}a{r},{r} 0 1,1 0,{negative_diameter}a{r},{r} 0 1,1 0,{diameter}z")
    }
}

impl GeoStream for PathString {
    fn polygon_start(&mut self) {
        self.in_polygon = true;
    }

    fn polygon_end(&mut self) {
        self.in_polygon = false;
    }

    fn line_start(&mut self) {
        self.point = Point::First;
    }

    fn line_end(&mut self) {
        if self.in_polygon {
            self.out.push('Z');
        }
        self.point = Point::Isolated;
    }

    fn point(&mut self, x: f64, y: f64, _z: Option<f64>) {
        match self.point {
            Point::First => {
                self.append('M', x, y);
                self.point = Point::Rest;
            }
            Point::Rest => self.append('L', x, y),
            Point::Isolated => {
                self.append('M', x, y);
                if self.circle.is_none() {
                    self.circle = Some(self.circle_fragment());
                }
                self.out.push_str(self.circle.as_deref().unwrap());
            }
        }
    }

    fn sphere(&mut self) {
        unreachable!("d3's PathString has no sphere: a projection's pre-clip consumes it, and geoPath rejects it otherwise");
    }
}

/// `v` rounded to `rounding` as d3 does, then printed as JavaScript prints a number.
///
/// Between 1e-6 and 1e21 JavaScript prints the shortest round-trip digits in positional notation, which is what
/// Rust's `Display` prints, so those write straight into `out`; the rest take [`js_number_string`].
fn push_number(out: &mut String, rounding: Option<f64>, v: f64) {
    let v = rounding.map_or(v, |k| js_round(v * k) / k);
    if (1e-6..1e21).contains(&v.abs()) {
        write!(out, "{v}").expect("writing to a String cannot fail");
    } else {
        out.push_str(&js_number_string(v));
    }
}

/// JavaScript's `Number.prototype.toString` for a double: shortest round-trip digits, positional notation
/// for exponents in [-7, 21), else `d.ddde+x`.
fn js_number_string(v: f64) -> String {
    if v.is_nan() {
        return "NaN".to_string();
    }
    if v.is_infinite() {
        return if v > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    if v == 0.0 {
        return "0".to_string();
    }
    let sign = if v < 0.0 { "-" } else { "" };
    let scientific = format!("{:e}", v.abs());
    let (mantissa, exponent) = scientific.split_once('e').unwrap();
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let n = exponent.parse::<i32>().unwrap() + 1;
    let k = digits.len() as i32;
    let body = if k <= n && n <= 21 {
        format!("{digits}{}", "0".repeat((n - k) as usize))
    } else if 0 < n && n <= 21 {
        format!("{}.{}", &digits[..n as usize], &digits[n as usize..])
    } else if -6 < n && n <= 0 {
        format!("0.{}{digits}", "0".repeat((-n) as usize))
    } else {
        let e = n - 1;
        let exp = format!("e{}{}", if e < 0 { '-' } else { '+' }, e.abs());
        if k == 1 {
            format!("{digits}{exp}")
        } else {
            format!("{}.{}{exp}", &digits[..1], &digits[1..])
        }
    };
    format!("{sign}{body}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The positional fast path must print what JavaScript's algorithm does for every double it takes.
    #[test]
    fn the_fast_number_path_prints_what_javascript_prints() {
        // A xorshift stream of doubles across magnitudes, rounded to three digits as paths are and raw.
        let mut state = 0x9E37_79B9_7F4A_7C15u64;
        let mut next = move || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        let mut checked = 0;
        for _ in 0..400_000 {
            let bits = next();
            let magnitude = 10f64.powi((bits % 30) as i32 - 8);
            let unit = (next() >> 11) as f64 / (1u64 << 53) as f64;
            let sign = if next() & 1 == 0 { 1.0 } else { -1.0 };
            let raw = sign * unit * magnitude;
            for (rounding, v) in [(None, raw), (Some(1000.0), raw), (Some(1000.0), (raw * 1000.0).round() / 1000.0)] {
                let mut fast = String::new();
                push_number(&mut fast, rounding, v);
                let expected = js_number_string(rounding.map_or(v, |k| js_round(v * k) / k));
                assert_eq!(fast, expected, "{v:e}");
                checked += 1;
            }
        }
        for v in [1e-6, 9.999999999999999e-7, 1e-7, 1e21, 9.999999999999999e20, 123456789012345680000.0, -0.0, 0.0, 5e-324, f64::MAX] {
            let mut fast = String::new();
            push_number(&mut fast, None, v);
            assert_eq!(fast, js_number_string(v), "{v:e}");
        }
        assert!(checked > 1_000_000);
    }
}
