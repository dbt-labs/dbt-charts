//! d3-color / d3-interpolate / d3-scale-chromatic pieces Vega's continuous
//! color scales run.
//!
//! Ported from:
//! - https://github.com/d3/d3-color/blob/main/src/lab.js (`rgb`↔`lab`↔`hcl`)
//! - https://github.com/d3/d3-interpolate/blob/main/src/piecewise.js, `rgb.js`, `hcl.js`
//! - vega-scale `palettes.js` (`yellowgreenblue`)

/// An sRGB color, one byte per channel.
pub type Rgb = (u8, u8, u8);

/// d3's `formatRgb` text of a color.
pub fn rgb_text((r, g, b): Rgb) -> String {
    format!("rgb({r}, {g}, {b})")
}

/// Sample `i` of Vega's `quantizeInterpolator(scheme, n)`: the scheme at
/// `(i + 1) / (n + 1)`.
pub fn quantize_sample(scheme: &[Rgb], n: usize, i: usize) -> Rgb {
    interpolate_rgb_piecewise(scheme, (i + 1) as f64 / (n + 1) as f64)
}

/// A scheme quantized to two colors and mixed in HCL: the ramp a time scale's
/// two-value domain takes, `t` along it.
pub fn quantized_pair_hcl(scheme: &[Rgb], t: f64) -> Rgb {
    interpolate_hcl(quantize_sample(scheme, 2, 0), quantize_sample(scheme, 2, 1), t)
}

/// A `#rrggbb` color; `None` for any other spelling.
pub fn parse_hex(hex: &str) -> Option<Rgb> {
    let digits = hex.strip_prefix('#').filter(|d| d.len() == 6 && d.is_ascii())?;
    let channel = |range: std::ops::Range<usize>| u8::from_str_radix(&digits[range], 16).ok();
    Some((channel(0..2)?, channel(2..4)?, channel(4..6)?))
}

/// A color as d3-color holds it: unclamped sRGB channels (NaN for the
/// transparent black) and an opacity.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Color {
    pub r: f64,
    pub g: f64,
    pub b: f64,
    pub a: f64,
}

impl Color {
    /// What d3 makes of text it cannot read (`color` returns null, `hcl(null)` is a
    /// color of NaN channels): an interpolation borrows the other end's color.
    pub const UNDEFINED: Color = Color { r: f64::NAN, g: f64::NAN, b: f64::NAN, a: f64::NAN };

    fn rgba(r: f64, g: f64, b: f64, a: f64) -> Color {
        // d3-color's `rgba`: a fully transparent color has no channels.
        if a <= 0.0 { Color { r: f64::NAN, g: f64::NAN, b: f64::NAN, a } } else { Color { r, g, b, a } }
    }

    /// An opaque byte color.
    pub fn opaque((r, g, b): Rgb) -> Color {
        Color { r: f64::from(r), g: f64::from(g), b: f64::from(b), a: 1.0 }
    }

    /// d3-color's `hsla(h, s, l, a)` read back as RGB (`Hsl.rgb`).
    fn hsla(h: f64, s: f64, l: f64, a: f64) -> Color {
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
        let h = h % 360.0 + if h < 0.0 { 360.0 } else { 0.0 };
        let s = if h.is_nan() || s.is_nan() { 0.0 } else { s };
        let m2 = l + (if l < 0.5 { l } else { 1.0 - l }) * s;
        let m1 = 2.0 * l - m2;
        let channel = |h: f64| {
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
        Color {
            r: channel(if h >= 240.0 { h - 240.0 } else { h + 120.0 }),
            g: channel(h),
            b: channel(if h < 120.0 { h + 240.0 } else { h - 120.0 }),
            a,
        }
    }
}

/// d3-color's `color(text)`: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`,
/// `rgb(i, i, i)`, `rgb(p%, p%, p%)`, `rgba(...)`, `hsl(h, s%, l%)`,
/// `hsla(...)`, a CSS keyword or `transparent`, case and edge whitespace
/// ignored; `None` for any other spelling.
pub fn parse_color(text: &str) -> Option<Color> {
    let text = text.trim().to_ascii_lowercase();
    if let Some(digits) = text.strip_prefix('#').filter(|d| d.is_ascii() && d.bytes().all(|c| c.is_ascii_hexdigit())) {
        let value = u32::from_str_radix(digits, 16).ok()?;
        let byte_at = |shift: u32| f64::from((value >> shift) & 0xff);
        let nibble = |shift: u32| f64::from((value >> shift) & 0xf);
        return match digits.len() {
            6 => Some(Color::opaque((byte_at(16) as u8, byte_at(8) as u8, byte_at(0) as u8))),
            3 => Some(Color { r: nibble(8) * 17.0, g: nibble(4) * 17.0, b: nibble(0) * 17.0, a: 1.0 }),
            8 => Some(Color::rgba(byte_at(24), byte_at(16), byte_at(8), byte_at(0) / 255.0)),
            4 => Some(Color::rgba(nibble(12) * 17.0, nibble(8) * 17.0, nibble(4) * 17.0, nibble(0) * 17.0 / 255.0)),
            _ => None,
        };
    }
    if text.starts_with('#') {
        return None;
    }
    let call = |name: &str| -> Option<Vec<&str>> {
        let args = text.strip_prefix(name)?.strip_prefix('(')?.strip_suffix(')')?;
        Some(args.split(',').collect())
    };
    let number = |s: &str| -> Option<f64> {
        let t = s.trim();
        // d3-color's `reN`: an optional sign, digits with an optional point, an optional exponent.
        let body = t.strip_prefix(['+', '-']).unwrap_or(t);
        let (mantissa, exponent) = match body.find('e') {
            Some(i) => (&body[..i], Some(&body[i + 1..])),
            None => (body, None),
        };
        let mantissa_ok = !mantissa.is_empty()
            && mantissa.bytes().all(|c| c.is_ascii_digit() || c == b'.')
            && mantissa.matches('.').count() <= 1
            && mantissa.bytes().any(|c| c.is_ascii_digit())
            && !mantissa.ends_with('.');
        let exponent_ok = exponent.is_none_or(|e| {
            let digits = e.strip_prefix(['+', '-']).unwrap_or(e);
            !digits.is_empty() && digits.bytes().all(|c| c.is_ascii_digit())
        });
        (mantissa_ok && exponent_ok).then(|| t.parse().ok()).flatten()
    };
    let integer = |s: &str| -> Option<f64> {
        let t = s.trim();
        let digits = t.strip_prefix(['+', '-']).unwrap_or(t);
        (!digits.is_empty() && digits.bytes().all(|c| c.is_ascii_digit())).then(|| t.parse().ok()).flatten()
    };
    // d3's `reP`: the number touches its `%`.
    let percent = |s: &str| -> Option<f64> {
        let body = s.trim().strip_suffix('%')?;
        if body.ends_with(char::is_whitespace) {
            return None;
        }
        number(body)
    };
    if let Some(p) = call("rgb") {
        if let [r, g, b] = p[..] {
            if let (Some(r), Some(g), Some(b)) = (integer(r), integer(g), integer(b)) {
                return Some(Color { r, g, b, a: 1.0 });
            }
            if let (Some(r), Some(g), Some(b)) = (percent(r), percent(g), percent(b)) {
                return Some(Color { r: r * 255.0 / 100.0, g: g * 255.0 / 100.0, b: b * 255.0 / 100.0, a: 1.0 });
            }
        }
        return None;
    }
    if let Some(p) = call("rgba") {
        if let [r, g, b, a] = p[..] {
            if let (Some(r), Some(g), Some(b), Some(a)) = (integer(r), integer(g), integer(b), number(a)) {
                return Some(Color::rgba(r, g, b, a));
            }
            if let (Some(r), Some(g), Some(b), Some(a)) = (percent(r), percent(g), percent(b), number(a)) {
                return Some(Color::rgba(r * 255.0 / 100.0, g * 255.0 / 100.0, b * 255.0 / 100.0, a));
            }
        }
        return None;
    }
    if let Some(p) = call("hsl") {
        if let [h, s, l] = p[..] {
            return Some(Color::hsla(number(h)?, percent(s)? / 100.0, percent(l)? / 100.0, 1.0));
        }
        return None;
    }
    if let Some(p) = call("hsla") {
        if let [h, s, l, a] = p[..] {
            return Some(Color::hsla(number(h)?, percent(s)? / 100.0, percent(l)? / 100.0, number(a)?));
        }
        return None;
    }
    if text == "transparent" {
        return Some(Color { r: f64::NAN, g: f64::NAN, b: f64::NAN, a: 0.0 });
    }
    crate::css_colors::named(&text).map(Color::opaque)
}

/// d3's `formatRgb` byte: `Math.round`, clamped, NaN printing as 0.
pub fn byte(v: f64) -> u8 {
    let rounded = crate::js_math::js_round(v);
    if rounded.is_nan() {
        0
    } else {
        rounded.clamp(0.0, 255.0) as u8
    }
}

/// `d3.piecewise(d3.interpolateRgb, colors)(t)`, the way vega-scale turns a
/// scheme's control colors into a continuous ramp: linear in each sRGB
/// channel between neighboring colors, rounded as the color string prints.
pub fn interpolate_rgb_piecewise(colors: &[Rgb], t: f64) -> Rgb {
    let n = (colors.len() - 1) as f64;
    let scaled = t * n;
    let i = scaled.floor().clamp(0.0, n - 1.0) as usize;
    let local = scaled - i as f64;
    let mix = |a: u8, b: u8| byte(f64::from(a) + local * (f64::from(b) - f64::from(a)));
    let (from, to) = (colors[i], colors[i + 1]);
    (mix(from.0, to.0), mix(from.1, to.1), mix(from.2, to.2))
}

const XN: f64 = 0.96422;
const YN: f64 = 1.0;
const ZN: f64 = 0.82521;
const T0: f64 = 4.0 / 29.0;
const T1: f64 = 6.0 / 29.0;
const T2: f64 = 3.0 * T1 * T1;
const T3: f64 = T1 * T1 * T1;

fn rgb_to_linear(channel: f64) -> f64 {
    let x = channel / 255.0;
    if x <= 0.04045 { x / 12.92 } else { ((x + 0.055) / 1.055).powf(2.4) }
}

fn linear_to_rgb(x: f64) -> f64 {
    255.0 * if x <= 0.0031308 { 12.92 * x } else { 1.055 * x.powf(1.0 / 2.4) - 0.055 }
}

fn xyz_to_lab(t: f64) -> f64 {
    if t > T3 { t.powf(1.0 / 3.0) } else { t / T2 + T0 }
}

fn lab_to_xyz(t: f64) -> f64 {
    if t > T1 { t * t * t } else { T2 * (t - T0) }
}

/// d3-color's `hcl(rgb)`: hue in degrees `[0, 360)`, chroma, lightness.
fn rgb_to_hcl(r: f64, g: f64, b: f64) -> (f64, f64, f64) {
    let (r, g, b) = (rgb_to_linear(r), rgb_to_linear(g), rgb_to_linear(b));
    let y = xyz_to_lab((0.2225045 * r + 0.7168786 * g + 0.0606169 * b) / YN);
    let (x, z) = if r == g && g == b {
        (y, y)
    } else {
        (
            xyz_to_lab((0.4360747 * r + 0.3850649 * g + 0.1430804 * b) / XN),
            xyz_to_lab((0.0139322 * r + 0.0971045 * g + 0.7141733 * b) / ZN),
        )
    };
    let (l, a, bb) = (116.0 * y - 16.0, 500.0 * (x - y), 200.0 * (y - z));
    if a == 0.0 && bb == 0.0 {
        return (f64::NAN, if l > 0.0 && l < 100.0 { 0.0 } else { f64::NAN }, l);
    }
    let h = bb.atan2(a).to_degrees();
    (if h < 0.0 { h + 360.0 } else { h }, a.hypot(bb), l)
}

/// The unrounded channels of an HCL color back in sRGB (`Hcl.rgb`).
fn hcl_to_rgb_channels((h, c, l): (f64, f64, f64)) -> (f64, f64, f64) {
    let (a, b) = if h.is_nan() { (0.0, 0.0) } else { (h.to_radians().cos() * c, h.to_radians().sin() * c) };
    let y = (l + 16.0) / 116.0;
    let (x, z) = (y + a / 500.0, y - b / 200.0);
    let (x, y, z) = (XN * lab_to_xyz(x), YN * lab_to_xyz(y), ZN * lab_to_xyz(z));
    (
        linear_to_rgb(3.1338561 * x - 1.6168667 * y - 0.4906146 * z),
        linear_to_rgb(-0.9787684 * x + 1.9161415 * y + 0.0334540 * z),
        linear_to_rgb(0.0719453 * x - 0.2289914 * y + 1.4052427 * z),
    )
}

/// d3-interpolate `hcl.js` over d3 colors: `hue` takes the shorter way
/// around, chroma, lightness and opacity are linear (`nogamma`), an undefined
/// channel borrows the other end's.
pub fn interpolate_hcl_color(start: Color, end: Color, t: f64) -> Color {
    let (h0, c0, l0) = rgb_to_hcl(start.r, start.g, start.b);
    let (h1, c1, l1) = rgb_to_hcl(end.r, end.g, end.b);
    let differs = |d: f64| d != 0.0 && !d.is_nan();
    let linear = |a: f64, b: f64| if differs(b - a) { a + t * (b - a) } else if a.is_nan() { b } else { a };
    let h = {
        let d = h1 - h0;
        if differs(d) {
            let d = if d > 180.0 || d < -180.0 { d - 360.0 * (d / 360.0).round() } else { d };
            h0 + t * d
        } else if h0.is_nan() {
            h1
        } else {
            h0
        }
    };
    let (r, g, b) = hcl_to_rgb_channels((h, linear(c0, c1), linear(l0, l1)));
    Color { r, g, b, a: linear(start.a, end.a) }
}

/// [`interpolate_hcl_color`] over opaque byte colors.
pub fn interpolate_hcl(start: Rgb, end: Rgb, t: f64) -> Rgb {
    let mixed = interpolate_hcl_color(Color::opaque(start), Color::opaque(end), t);
    (byte(mixed.r), byte(mixed.g), byte(mixed.b))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_hex_reads_only_six_digit_hex() {
        assert_eq!(parse_hex("#0a4a90"), Some((10, 74, 144)));
        assert_eq!(parse_hex("#FFFFFF"), Some((255, 255, 255)));
        for bad in ["0a4a90", "#fff", "#0a4a9", "#0a4a90ff", "#gg0000", "rgb(1, 2, 3)", ""] {
            assert_eq!(parse_hex(bad), None, "{bad}");
        }
    }

    /// Oracle: `vega.scheme('yellowgreenblue')` (vega 5.25) sampled where Vega's
    /// `quantize(scheme, 2)` samples a two-stop range.
    #[test]
    fn yellow_green_blue_matches_vega_s_scheme() {
        let s = |t| interpolate_rgb_piecewise(&crate::vega_schemes::YELLOWGREENBLUE, t);
        assert_eq!(s(0.0), (239, 249, 189));
        assert_eq!(s(1.0), (28, 49, 133));
        assert_eq!(s(1.0 / 3.0), (134, 208, 187));
        assert_eq!(s(2.0 / 3.0), (37, 139, 187));
    }

    /// Oracle: a vega `time` scale with `interpolate: hcl` over those two
    /// colors; Feb 1 sits 31 of the 60 days from Jan 1 to Mar 1.
    #[test]
    fn hcl_interpolation_matches_vega_s_scale() {
        assert_eq!(interpolate_hcl((134, 208, 187), (37, 139, 187), 31.0 / 60.0), (68, 175, 188));
        assert_eq!(interpolate_hcl((134, 208, 187), (37, 139, 187), 0.5), (70, 176, 188));
        assert_eq!(interpolate_hcl((134, 208, 187), (37, 139, 187), 0.0), (134, 208, 187));
    }

    /// `Math.round(0.49999999999999994)` is 0; `x + 0.5` rounds it up to 1.
    #[test]
    fn a_byte_rounds_like_math_round() {
        assert_eq!(byte(0.49999999999999994), 0);
        assert_eq!(byte(254.5), 255);
        assert_eq!(byte(f64::NAN), 0);
    }

    /// Oracle: `vega.scheme('blues')` (vega 5.25).
    #[test]
    fn blues_matches_vega_s_scheme() {
        let at = |t| interpolate_rgb_piecewise(&crate::vega_schemes::BLUES, t);
        assert_eq!(at(0.0), (207, 225, 242));
        assert_eq!(at(1.0 / 7.0), (181, 212, 233));
        assert_eq!(at(0.5), (91, 163, 207));
        assert_eq!(at(13.0 / 14.0), (16, 87, 158));
        assert_eq!(at(1.0), (10, 74, 144));
    }

    /// Oracle: `d3.interpolateHcl("rgb(128,128,128)", "blue")(0.5)`. A gray end
    /// has an undefined hue, so the blue end's hue holds the whole way; sweeping
    /// from 0 degrees instead lands on a magenta.
    #[test]
    fn a_gray_end_borrows_the_other_hue() {
        assert_eq!(interpolate_hcl((128, 128, 128), (0, 0, 255), 0.5), (110, 79, 192));
        assert_eq!(interpolate_hcl((0, 0, 255), (128, 128, 128), 0.5), (110, 79, 192));
    }

    /// Oracle: vega's `hcl` interpolator (d3): black and white have no hue
    /// either, and black's chroma is undefined, so the other end's holds.
    #[test]
    fn a_black_or_white_end_has_no_hue_or_chroma_of_its_own() {
        assert_eq!(interpolate_hcl((0, 0, 0), (77, 175, 74), 0.5), (0, 91, 0));
        assert_eq!(interpolate_hcl((255, 255, 255), (77, 175, 74), 0.5), (128, 226, 121));
    }

    /// A two-sample quantize lands on the scheme's thirds, and a longer one at
    /// `(i + 1) / (n + 1)`.
    #[test]
    fn quantize_samples_sit_between_the_scheme_ends() {
        let scheme = &crate::vega_schemes::YELLOWGREENBLUE;
        assert_eq!(quantize_sample(scheme, 2, 0), (134, 208, 187));
        assert_eq!(quantize_sample(scheme, 2, 1), (37, 139, 187));
        assert_eq!(quantize_sample(scheme, 3, 1), interpolate_rgb_piecewise(scheme, 0.5));
        assert_eq!(quantized_pair_hcl(scheme, 0.0), (134, 208, 187));
        assert_eq!(rgb_text((1, 2, 3)), "rgb(1, 2, 3)");
    }

    #[test]
    fn parse_color_reads_what_d3_color_reads() {
        assert_eq!(parse_color("#f00"), Some(Color::opaque((255, 0, 0))));
        assert_eq!(parse_color("#0a4a90"), Some(Color::opaque((10, 74, 144))));
        assert_eq!(parse_color("Blue"), Some(Color::opaque((0, 0, 255))));
        assert_eq!(parse_color(concat!("dark", "gre", "y")), parse_color("darkgray"));
        assert_eq!(parse_color("rgb(0, 255,0)"), Some(Color::opaque((0, 255, 0))));
        assert_eq!(parse_color("rgb(100%, 0%, 50%)"), Some(Color { r: 255.0, g: 0.0, b: 127.5, a: 1.0 }));
        assert_eq!(parse_color("rgba(0, 0, 255, 0.5)"), Some(Color { r: 0.0, g: 0.0, b: 255.0, a: 0.5 }));
        assert_eq!(parse_color("hsl(0, 100%, 50%)"), Some(Color::opaque((255, 0, 0))));
        assert_eq!(parse_color("#ff000080").map(|c| (c.r, c.a > 0.5)), Some((255.0, true)));
        assert!(parse_color("transparent").is_some_and(|c| c.r.is_nan() && c.a == 0.0));
        for bad in ["", "#ff", "#gggggg", "rgb(1, 2)", "rgb(1, 2, 3, 4)", "notacolor", "hsl(0, 100 %, 50%)"] {
            assert_eq!(parse_color(bad), None, "{bad}");
        }
    }

    /// d3's `hsla`: a fully transparent color has no channels at all.
    #[test]
    fn a_transparent_hsla_has_no_channels() {
        assert!(parse_color("hsla(0, 100%, 50%, 0)").is_some_and(|c| c.r.is_nan() && c.g.is_nan() && c.b.is_nan() && c.a == 0.0));
    }
}
