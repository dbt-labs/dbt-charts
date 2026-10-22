//! d3-color's `rgb`, `hsl`, `lab` and `hcl` as Vega's expression functions expose them: parsing, conversion
//! between the four spaces, and the `rgb(...)` text a color prints as.

use super::val::{is_js_space, num_to_str};
use regex::Regex;
use std::sync::OnceLock;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Space {
    Rgb,
    Hsl,
    Lab,
    Hcl,
}

/// A d3 color: its channels in constructor order (`r g b`, `h s l`, `l a b`, `h c l`) and its opacity.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Color {
    pub space: Space,
    pub c: [f64; 3],
    pub opacity: f64,
}

const REI: &str = r"\s*([+-]?\d+)\s*";
const REN: &str = r"\s*([+-]?(?:\d*\.)?\d+(?:[eE][+-]?\d+)?)\s*";
const REP: &str = r"\s*([+-]?(?:\d*\.)?\d+(?:[eE][+-]?\d+)?)%\s*";

fn patterns() -> &'static [Regex; 7] {
    static P: OnceLock<[Regex; 7]> = OnceLock::new();
    P.get_or_init(|| {
        let r = |s: String| Regex::new(&s).expect("a d3-color pattern");
        [
            r(format!(r"^rgb\({REI},{REI},{REI}\)$")),
            r(format!(r"^rgb\({REP},{REP},{REP}\)$")),
            r(format!(r"^rgba\({REI},{REI},{REI},{REN}\)$")),
            r(format!(r"^rgba\({REP},{REP},{REP},{REN}\)$")),
            r(format!(r"^hsl\({REN},{REP},{REP}\)$")),
            r(format!(r"^hsla\({REN},{REP},{REP},{REN}\)$")),
            r("^#([0-9a-f]{3,8})$".to_string()),
        ]
    })
}

impl Color {
    pub fn rgb(r: f64, g: f64, b: f64, opacity: f64) -> Self {
        Color { space: Space::Rgb, c: [r, g, b], opacity }
    }

    fn rgba(r: f64, g: f64, b: f64, a: f64) -> Self {
        if a <= 0.0 { Color::rgb(f64::NAN, f64::NAN, f64::NAN, a) } else { Color::rgb(r, g, b, a) }
    }

    fn rgbn(n: u32) -> Self {
        Color::rgb(f64::from(n >> 16 & 0xff), f64::from(n >> 8 & 0xff), f64::from(n & 0xff), 1.0)
    }

    fn hsla(h: f64, s: f64, l: f64, a: f64) -> Self {
        let (mut h, mut s, mut l) = (h, s, l);
        if a <= 0.0 {
            h = f64::NAN;
            s = f64::NAN;
            l = f64::NAN;
        } else if l <= 0.0 || l >= 1.0 {
            h = f64::NAN;
            s = f64::NAN;
        } else if s <= 0.0 {
            h = f64::NAN;
        }
        Color { space: Space::Hsl, c: [h, s, l], opacity: a }
    }

    /// `color(format)`: a CSS color string, or `None` when d3 cannot read it.
    pub fn parse(format: &str) -> Option<Self> {
        let f: String = format.trim_matches(is_js_space).to_lowercase();
        let p = patterns();
        let num = |caps: &regex::Captures, i: usize| caps[i].parse::<f64>().unwrap_or(f64::NAN);
        if let Some(m) = p[6].captures(&f) {
            let digits = &m[1];
            let n = u32::from_str_radix(digits, 16).ok()?;
            return match digits.len() {
                6 => Some(Color::rgbn(n)),
                3 => Some(Color::rgb(f64::from((n >> 8 & 0xf) | (n >> 4 & 0xf0)), f64::from((n >> 4 & 0xf) | (n & 0xf0)), f64::from(((n & 0xf) << 4) | (n & 0xf)), 1.0)),
                8 => Some(Color::rgba(f64::from(n >> 24 & 0xff), f64::from(n >> 16 & 0xff), f64::from(n >> 8 & 0xff), f64::from(n & 0xff) / 255.0)),
                4 => Some(Color::rgba(
                    f64::from((n >> 12 & 0xf) | (n >> 8 & 0xf0)),
                    f64::from((n >> 8 & 0xf) | (n >> 4 & 0xf0)),
                    f64::from((n >> 4 & 0xf) | (n & 0xf0)),
                    f64::from(((n & 0xf) << 4) | (n & 0xf)) / 255.0,
                )),
                _ => None,
            };
        }
        if let Some(m) = p[0].captures(&f) {
            return Some(Color::rgb(num(&m, 1), num(&m, 2), num(&m, 3), 1.0));
        }
        if let Some(m) = p[1].captures(&f) {
            return Some(Color::rgb(num(&m, 1) * 255.0 / 100.0, num(&m, 2) * 255.0 / 100.0, num(&m, 3) * 255.0 / 100.0, 1.0));
        }
        if let Some(m) = p[2].captures(&f) {
            return Some(Color::rgba(num(&m, 1), num(&m, 2), num(&m, 3), num(&m, 4)));
        }
        if let Some(m) = p[3].captures(&f) {
            return Some(Color::rgba(num(&m, 1) * 255.0 / 100.0, num(&m, 2) * 255.0 / 100.0, num(&m, 3) * 255.0 / 100.0, num(&m, 4)));
        }
        if let Some(m) = p[4].captures(&f) {
            return Some(Color::hsla(num(&m, 1), num(&m, 2) / 100.0, num(&m, 3) / 100.0, 1.0));
        }
        if let Some(m) = p[5].captures(&f) {
            return Some(Color::hsla(num(&m, 1), num(&m, 2) / 100.0, num(&m, 3) / 100.0, num(&m, 4)));
        }
        if let Some((r, g, b)) = d3rs::css_colors::named(&f) {
            return Some(Color::rgb(f64::from(r), f64::from(g), f64::from(b), 1.0));
        }
        (f == "transparent").then(|| Color::rgb(f64::NAN, f64::NAN, f64::NAN, 0.0))
    }

    /// `color.rgb()`.
    pub fn to_rgb(self) -> Color {
        match self.space {
            Space::Rgb => self,
            Space::Hsl => {
                let [h0, s0, l] = self.c;
                let h = h0 % 360.0 + if h0 < 0.0 { 360.0 } else { 0.0 };
                let s = if h.is_nan() || s0.is_nan() { 0.0 } else { s0 };
                let m2 = l + (if l < 0.5 { l } else { 1.0 - l }) * s;
                let m1 = 2.0 * l - m2;
                let ch = |h: f64| {
                    (if h < 60.0 {
                        m1 + (m2 - m1) * h / 60.0
                    } else if h < 180.0 {
                        m2
                    } else if h < 240.0 {
                        m1 + (m2 - m1) * (240.0 - h) / 60.0
                    } else {
                        m1
                    }) * 255.0
                };
                Color::rgb(ch(if h >= 240.0 { h - 240.0 } else { h + 120.0 }), ch(h), ch(if h < 120.0 { h + 240.0 } else { h - 120.0 }), self.opacity)
            }
            Space::Lab => {
                let [l, a, b] = self.c;
                let mut y = (l + 16.0) / 116.0;
                let mut x = if a.is_nan() { y } else { y + a / 500.0 };
                let mut z = if b.is_nan() { y } else { y - b / 200.0 };
                x = XN * lab2xyz(x);
                y = YN * lab2xyz(y);
                z = ZN * lab2xyz(z);
                Color::rgb(
                    lrgb2rgb(3.1338561 * x - 1.6168667 * y - 0.4906146 * z),
                    lrgb2rgb(-0.9787684 * x + 1.9161415 * y + 0.0334540 * z),
                    lrgb2rgb(0.0719453 * x - 0.2289914 * y + 1.4052427 * z),
                    self.opacity,
                )
            }
            Space::Hcl => self.hcl_to_lab().to_rgb(),
        }
    }

    fn hcl_to_lab(self) -> Color {
        let [h0, c, l] = self.c;
        if h0.is_nan() {
            return Color { space: Space::Lab, c: [l, 0.0, 0.0], opacity: self.opacity };
        }
        let h = h0 * (std::f64::consts::PI / 180.0);
        Color { space: Space::Lab, c: [l, libm::cos(h) * c, libm::sin(h) * c], opacity: self.opacity }
    }

    /// `hslConvert`.
    pub fn to_hsl(self) -> Color {
        if self.space == Space::Hsl {
            return self;
        }
        let o = self.to_rgb();
        let (r, g, b) = (o.c[0] / 255.0, o.c[1] / 255.0, o.c[2] / 255.0);
        let (min, max) = (r.min(g).min(b), r.max(g).max(b));
        let mut h = f64::NAN;
        let mut s = max - min;
        let l = (max + min) / 2.0;
        if s != 0.0 {
            if r == max {
                h = (g - b) / s + if g < b { 6.0 } else { 0.0 };
            } else if g == max {
                h = (b - r) / s + 2.0;
            } else {
                h = (r - g) / s + 4.0;
            }
            s /= if l < 0.5 { max + min } else { 2.0 - max - min };
            h *= 60.0;
        } else {
            s = if l > 0.0 && l < 1.0 { 0.0 } else { h };
        }
        Color { space: Space::Hsl, c: [h, s, l], opacity: o.opacity }
    }

    /// `labConvert`.
    pub fn to_lab(self) -> Color {
        match self.space {
            Space::Lab => self,
            Space::Hcl => self.hcl_to_lab(),
            _ => {
                let o = self.to_rgb();
                let (r, g, b) = (rgb2lrgb(o.c[0]), rgb2lrgb(o.c[1]), rgb2lrgb(o.c[2]));
                let y = xyz2lab((0.2225045 * r + 0.7168786 * g + 0.0606169 * b) / YN);
                let (x, z) = if r == g && g == b {
                    (y, y)
                } else {
                    (xyz2lab((0.4360747 * r + 0.3850649 * g + 0.1430804 * b) / XN), xyz2lab((0.0139322 * r + 0.0971045 * g + 0.7141733 * b) / ZN))
                };
                Color { space: Space::Lab, c: [116.0 * y - 16.0, 500.0 * (x - y), 200.0 * (y - z)], opacity: o.opacity }
            }
        }
    }

    /// `hclConvert`.
    pub fn to_hcl(self) -> Color {
        if self.space == Space::Hcl {
            return self;
        }
        let o = self.to_lab();
        let [l, a, b] = o.c;
        if a == 0.0 && b == 0.0 {
            return Color { space: Space::Hcl, c: [f64::NAN, if 0.0 < l && l < 100.0 { 0.0 } else { f64::NAN }, l], opacity: o.opacity };
        }
        let h = libm::atan2(b, a) * (180.0 / std::f64::consts::PI);
        Color { space: Space::Hcl, c: [if h < 0.0 { h + 360.0 } else { h }, (a * a + b * b).sqrt(), l], opacity: o.opacity }
    }

    /// The text `color.toString()` gives, whatever the space: `rgb(r, g, b)` or `rgba(r, g, b, a)`.
    pub fn to_css(self) -> String {
        let o = self.to_rgb();
        let a = if o.opacity.is_nan() { 1.0 } else { o.opacity.clamp(0.0, 1.0) };
        let ch = |v: f64| num_to_str(js_round(v).clamp(0.0, 255.0));
        if a == 1.0 {
            format!("rgb({}, {}, {})", ch(o.c[0]), ch(o.c[1]), ch(o.c[2]))
        } else {
            format!("rgba({}, {}, {}, {})", ch(o.c[0]), ch(o.c[1]), ch(o.c[2]), num_to_str(a))
        }
    }

    /// A channel by property name.
    pub fn channel(self, name: &str) -> Option<f64> {
        let names: [&str; 3] = match self.space {
            Space::Rgb => ["r", "g", "b"],
            Space::Hsl => ["h", "s", "l"],
            Space::Lab => ["l", "a", "b"],
            Space::Hcl => ["h", "c", "l"],
        };
        if name == "opacity" {
            return Some(self.opacity);
        }
        names.iter().position(|n| *n == name).map(|i| self.c[i])
    }
}

/// `Math.round(x) || 0`.
fn js_round(x: f64) -> f64 {
    let r = d3rs::js_math::js_round(x);
    if r.is_nan() || r == 0.0 { 0.0 } else { r }
}

const XN: f64 = 0.96422;
const YN: f64 = 1.0;
const ZN: f64 = 0.82521;
const T0: f64 = 4.0 / 29.0;
const T1: f64 = 6.0 / 29.0;

fn xyz2lab(t: f64) -> f64 {
    let t2 = 3.0 * T1 * T1;
    let t3 = T1 * T1 * T1;
    if t > t3 { t.powf(1.0 / 3.0) } else { t / t2 + T0 }
}

fn lab2xyz(t: f64) -> f64 {
    let t2 = 3.0 * T1 * T1;
    if t > T1 { t * t * t } else { t2 * (t - T0) }
}

fn lrgb2rgb(x: f64) -> f64 {
    255.0 * (if x <= 0.0031308 { 12.92 * x } else { 1.055 * x.powf(1.0 / 2.4) - 0.055 })
}

fn rgb2lrgb(x: f64) -> f64 {
    let x = x / 255.0;
    if x <= 0.04045 { x / 12.92 } else { ((x + 0.055) / 1.055).powf(2.4) }
}

/// WCAG relative luminance of a color's sRGB channels.
pub fn luminance(c: Color) -> f64 {
    let o = c.to_rgb();
    let channel = |v: f64| {
        let val = v / 255.0;
        if val <= 0.03928 { val / 12.92 } else { ((val + 0.055) / 1.055).powf(2.4) }
    };
    0.2126 * channel(o.c[0]) + 0.7152 * channel(o.c[1]) + 0.0722 * channel(o.c[2])
}

pub fn contrast(a: Color, b: Color) -> f64 {
    let (l1, l2) = (luminance(a), luminance(b));
    if l1.is_nan() || l2.is_nan() {
        return f64::NAN;
    }
    (l1.max(l2) + 0.05) / (l1.min(l2) + 0.05)
}
