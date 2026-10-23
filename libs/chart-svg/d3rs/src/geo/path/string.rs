//! d3-geo 3.1.1 `src/path/string.js`: `PathString`, the sink `geoPath` prints an SVG `d` string through.
//! Vega 6.2.0 draws a `shape` mark with this same sink (`path.context(null)`, `digits = 3`) and writes its
//! result into the `d` attribute untouched, so this is also the string vl-convert prints.

use crate::geo::stream::GeoStream;
use crate::js_math::js_round;

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
        js_number_string(self.rounding.map_or(v, |k| js_round(v * k) / k))
    }

    fn append(&self, command: char, x: f64, y: f64) -> String {
        format!("{command}{},{}", self.number(x), self.number(y))
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
                self.out.push_str(&self.append('M', x, y));
                self.point = Point::Rest;
            }
            Point::Rest => self.out.push_str(&self.append('L', x, y)),
            Point::Isolated => {
                self.out.push_str(&self.append('M', x, y));
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
