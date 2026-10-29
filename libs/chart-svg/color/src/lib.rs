//! The slice of `core/colors.py` and `resolve/style/palette.py`
//! the compile-stage cascade needs to derive label ink live — `mark_ink`
//! (one `dark_companion_palette` entry) and `ink_canvas` (the opaque canvas
//! a scope's own background composites to).
//!
//! Every float step mirrors the Python arithmetic operation for operation
//! (`x ** (1/3)` stays `powf(1.0 / 3.0)`, never `cbrt`; Python's `round()`
//! is half-to-even, so `round_ties_even`) — the outputs are 8-bit hex
//! strings compared byte-for-byte by the sweep, and a last-ulp drift in
//! lightness flips a channel at the rounding boundary.
//!
//! `variant()`'s four literal tiers (`light`/`dark`/`pale`/`deep`) are
//! ported for the trailing-variant color-token grammar an authored `style:`
//! can now carry (`category.blue.dark`). `label_ink`'s dark step is inlined
//! rather than reached through `variant("dark")`: it steps away from a
//! canvas, a different move on a dark one.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;
use serde::Deserialize;


const CSS_NAMED_COLORS_JSON: &str = include_str!("data/css_named_colors.json");
const COLOR_VARIANTS_JSON: &str = include_str!("data/color_variants.json");

fn color_variants() -> &'static ColorVariants {
    static CFG: OnceLock<ColorVariants> = OnceLock::new();
    CFG.get_or_init(|| {
        serde_json::from_str(COLOR_VARIANTS_JSON).expect("embedded color variants config is valid")
    })
}

/// The engine constants in `core/constants/color.py` and
/// `LABEL_INK_MIN_CONTRAST` in `core/constants/contrast.py`, embedded as
/// `data/color_variants.json`. Not theme values: no board or config authors
/// them.
#[derive(Debug, Deserialize)]
pub struct ColorVariants {
    pub light_k: f64,
    pub light_chroma: f64,
    pub light_min_gap: f64,
    pub light_pale_gap: f64,
    pub dark_pole: f64,
    pub dark_k: f64,
    pub pale_l: f64,
    pub pale_chroma: f64,
    pub deep_pole: f64,
    pub deep_k: f64,
    pub label_ink_min_contrast: f64,
}

/// `core/colors.py::VARIANT_WORDS` — the four literal tiers, in the order
/// `Variant = Literal["dark", "light", "pale", "deep"]` declares them.
pub const VARIANT_WORDS: [&str; 4] = ["dark", "light", "pale", "deep"];

pub type Rgba = (f64, f64, f64, f64);

// Every anchored pattern ends in `\n?$`: Python's `re.match(..., "$")` also
// matches just before one trailing newline, and these predicates decide
// whether a value is color data at all.

fn hex_alpha_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\n?$").unwrap()
    })
}

fn hex_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\n?$").unwrap())
}

fn function_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^(rgba?|hsla?)\(\s*(.+?)\s*\)\n?$").unwrap())
}

fn color_token_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(&format!(
            r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+|\[\d+\])(\.({}))?\n?$",
            VARIANT_WORDS.join("|")
        ))
        .unwrap()
    })
}

/// `core/colors.py::is_color_token`: the shape gate for a palette token —
/// `palette.slot` or `role[N]`, then an optional trailing variant word.
pub fn is_color_token(value: &str) -> bool {
    color_token_re().is_match(value)
}

fn bare_identifier_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[A-Za-z][A-Za-z0-9_-]*\n?$").unwrap())
}

const CSS_RESERVED_UNREADABLE_KEYWORDS: &[&str] =
    &["currentcolor", "inherit", "initial", "unset", "revert"];

fn css_named_colors() -> &'static HashMap<String, String> {
    static MAP: OnceLock<HashMap<String, String>> = OnceLock::new();
    MAP.get_or_init(|| {
        serde_json::from_str(CSS_NAMED_COLORS_JSON)
            .expect("embedded CSS color table is valid")
    })
}

/// `css_named_color_to_hex`: case-insensitive, `transparent` excluded.
pub fn css_named_color_to_hex(name: &str) -> Option<&'static str> {
    css_named_colors()
        .get(&name.to_lowercase())
        .map(String::as_str)
}

/// `is_sanitizable_color`: a 3/6-digit hex, or `transparent`/`none`.
pub fn is_sanitizable_color(color: &str) -> bool {
    hex_re().is_match(color) || matches!(color.to_lowercase().as_str(), "transparent" | "none")
}

/// `is_deferred_color_reference`: token-shaped (`palette.slot`, `role[N]`)
/// or a bare identifier — a possible theme-role reference rather than
/// literal color data — minus the CSS-reserved keywords a browser owns.
pub fn is_deferred_color_reference(value: &str) -> bool {
    if CSS_RESERVED_UNREADABLE_KEYWORDS.contains(&value.trim().to_lowercase().as_str()) {
        return false;
    }
    is_color_token(value) || bare_identifier_re().is_match(value)
}

/// `float(token)`, rejecting nan/inf. `float()` strips surrounding
/// whitespace itself (a `%` or `deg` suffix can leave a padded token, as in
/// `rgb(50 %, 0, 0)`), and its grammar allows a single `_` between two
/// digits; anything else with an underscore is rejected, as `float()`
/// rejects it. `pub`: `resolve/src/format.rs` reuses this for `core/utils.py::
/// coerce_numeric_cell`'s own string branch — the same `float(str)` grammar,
/// never a second hand-rolled parser.
pub fn finite_float(token: &str) -> Result<f64, String> {
    let token = token.trim();
    let cleaned = if token.contains('_') {
        let bytes = token.as_bytes();
        let well_placed = token.char_indices().all(|(i, ch)| {
            ch != '_'
                || (i > 0
                    && i + 1 < bytes.len()
                    && bytes[i - 1].is_ascii_digit()
                    && bytes[i + 1].is_ascii_digit())
        });
        if !well_placed {
            return Err(format!("could not convert string to float: {token:?}"));
        }
        token.replace('_', "")
    } else {
        token.to_string()
    };
    let value: f64 = cleaned
        .parse()
        .map_err(|_| format!("could not convert string to float: {token:?}"))?;
    if !value.is_finite() {
        return Err(format!("not a finite number: {token:?}"));
    }
    Ok(value)
}

fn parse_channel_0_1(token: &str) -> Result<f64, String> {
    let token = token.trim();
    let value = match token.strip_suffix('%') {
        Some(pct) => finite_float(pct)? / 100.0,
        None => finite_float(token)? / 255.0,
    };
    Ok(value.clamp(0.0, 1.0))
}

fn parse_unit_interval(token: &str) -> Result<f64, String> {
    let token = token.trim();
    let value = match token.strip_suffix('%') {
        Some(pct) => finite_float(pct)? / 100.0,
        None => finite_float(token)?,
    };
    Ok(value.clamp(0.0, 1.0))
}

fn parse_percentage(token: &str) -> Result<f64, String> {
    let token = token.trim();
    let Some(pct) = token.strip_suffix('%') else {
        return Err(format!("not a percentage: {token:?}"));
    };
    Ok((finite_float(pct)? / 100.0).clamp(0.0, 1.0))
}

fn split_function_channels(body: &str) -> Result<(Vec<&str>, Option<&str>), String> {
    if body.contains(',') {
        if body.contains('/') {
            return Err(format!("comma syntax cannot carry a slash alpha: {body:?}"));
        }
        let parts: Vec<&str> = body.split(',').map(str::trim).collect();
        if parts.iter().any(|p| p.is_empty()) || !(3..=4).contains(&parts.len()) {
            return Err(format!("expected 3 or 4 comma-separated values: {body:?}"));
        }
        return Ok(if parts.len() == 4 {
            (parts[..3].to_vec(), Some(parts[3]))
        } else {
            (parts, None)
        });
    }
    let (channels_part, alpha_token) = match body.split_once('/') {
        Some((channels, alpha)) => {
            let alpha = alpha.trim();
            if alpha.is_empty() {
                return Err(format!("empty alpha after slash: {body:?}"));
            }
            (channels, Some(alpha))
        }
        None => (body, None),
    };
    let parts: Vec<&str> = channels_part.split_whitespace().collect();
    if parts.len() != 3 {
        return Err(format!("expected 3 space-separated channels: {body:?}"));
    }
    Ok((parts, alpha_token))
}

fn hsl_to_rgb01(h_deg: f64, s: f64, lightness: f64) -> (f64, f64, f64) {
    let h = h_deg.rem_euclid(360.0);
    let c = (1.0 - (2.0 * lightness - 1.0).abs()) * s;
    let x = c * (1.0 - ((h / 60.0).rem_euclid(2.0) - 1.0).abs());
    let m = lightness - c / 2.0;
    let (rp, gp, bp) = if h < 60.0 {
        (c, x, 0.0)
    } else if h < 120.0 {
        (x, c, 0.0)
    } else if h < 180.0 {
        (0.0, c, x)
    } else if h < 240.0 {
        (0.0, x, c)
    } else if h < 300.0 {
        (x, 0.0, c)
    } else {
        (c, 0.0, x)
    };
    (rp + m, gp + m, bp + m)
}

fn hex_byte(digits: &str, at: usize) -> Result<f64, String> {
    u8::from_str_radix(&digits[at..at + 2], 16)
        .map(|b| f64::from(b) / 255.0)
        .map_err(|_| {
            format!(
                "invalid literal for int() with base 16: {:?}",
                &digits[at..at + 2]
            )
        })
}

/// `parse_css_color`: hex (3/4/6/8 digits), a CSS keyword name,
/// `transparent`/`none`, `rgb()`/`rgba()`, `hsl()`/`hsla()` → (r, g, b, a),
/// each 0-1. `Err` is Python's `InvalidColorError`.
pub fn parse_css_color(value: &str) -> Result<Rgba, String> {
    let stripped = value.trim();
    if matches!(stripped.to_lowercase().as_str(), "transparent" | "none") {
        return Ok((0.0, 0.0, 0.0, 0.0));
    }
    if let Some(m) = hex_alpha_re().captures(stripped) {
        let raw = &m[1];
        let digits: String = if raw.len() <= 4 {
            raw.chars().flat_map(|d| [d, d]).collect()
        } else {
            raw.to_string()
        };
        let a = if digits.len() == 8 {
            hex_byte(&digits, 6)?
        } else {
            1.0
        };
        return Ok((
            hex_byte(&digits, 0)?,
            hex_byte(&digits, 2)?,
            hex_byte(&digits, 4)?,
            a,
        ));
    }
    if let Some(named) = css_named_color_to_hex(stripped) {
        return Ok((
            hex_byte(named, 1)?,
            hex_byte(named, 3)?,
            hex_byte(named, 5)?,
            1.0,
        ));
    }
    let mut function_reason: Option<String> = None;
    if let Some(m) = function_re().captures(stripped) {
        let fn_name = m[1].to_lowercase();
        let parsed = (|| -> Result<Rgba, String> {
            let (channels, alpha_token) = split_function_channels(&m[2])?;
            let a = match alpha_token {
                Some(t) => parse_unit_interval(t)?,
                None => 1.0,
            };
            if fn_name.starts_with("rgb") {
                let r = parse_channel_0_1(channels[0])?;
                let g = parse_channel_0_1(channels[1])?;
                let b = parse_channel_0_1(channels[2])?;
                Ok((r, g, b, a))
            } else {
                let hue_token = channels[0];
                let hue_text = if hue_token.to_lowercase().ends_with("deg") {
                    &hue_token[..hue_token.len() - 3]
                } else {
                    hue_token
                };
                let h_deg = finite_float(hue_text)?;
                let s = parse_percentage(channels[1])?;
                let lightness = parse_percentage(channels[2])?;
                let (r, g, b) = hsl_to_rgb01(h_deg, s, lightness);
                Ok((r, g, b, a))
            }
        })();
        match parsed {
            Ok(rgba) => return Ok(rgba),
            Err(reason) => function_reason = Some(reason),
        }
    }
    match function_reason {
        Some(reason) => Err(format!("Invalid color value: {value:?} ({reason})")),
        None => Err(format!("Invalid color value: {value:?}")),
    }
}

/// Source-over compositing of `top` onto `under`, un-premultiplied.
pub fn composite_over(top: Rgba, under: Rgba) -> Rgba {
    let (rt, gt, bt, at) = top;
    let (ru, gu, bu, au) = under;
    let a_out = at + au * (1.0 - at);
    if a_out <= 0.0 {
        return (0.0, 0.0, 0.0, 0.0);
    }
    let under_weight = au * (1.0 - at);
    (
        (rt * at + ru * under_weight) / a_out,
        (gt * at + gu * under_weight) / a_out,
        (bt * at + bu * under_weight) / a_out,
        a_out,
    )
}

fn byte(c: f64) -> u8 {
    (c * 255.0).round_ties_even().clamp(0.0, 255.0) as u8
}

/// `rgb01_to_hex`: lowercase 6-digit hex, half-to-even rounding.
pub fn rgb01_to_hex(r: f64, g: f64, b: f64) -> String {
    format!("#{:02x}{:02x}{:02x}", byte(r), byte(g), byte(b))
}

fn srgb_to_linear(c: f64) -> f64 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn linear_to_srgb(c: f64) -> f64 {
    if c <= 0.0 {
        0.0
    } else if c <= 0.0031308 {
        c * 12.92
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

fn cbrt(x: f64) -> f64 {
    if x >= 0.0 {
        x.powf(1.0 / 3.0)
    } else {
        -((-x).powf(1.0 / 3.0))
    }
}

fn lrgb_to_oklab(r: f64, g: f64, b: f64) -> (f64, f64, f64) {
    let lo = cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    let m = cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    let s = cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    (
        0.2104542553 * lo + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * lo - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * lo + 0.7827717662 * m - 0.8086757660 * s,
    )
}

fn oklab_to_lrgb(l: f64, a: f64, b: f64) -> (f64, f64, f64) {
    let lo = (l + 0.3963377774 * a + 0.2158037573 * b).powf(3.0);
    let m = (l - 0.1055613458 * a - 0.0638541728 * b).powf(3.0);
    let s = (l - 0.0894841775 * a - 1.2914855480 * b).powf(3.0);
    (
        4.0767416621 * lo - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * lo + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * lo - 0.7034186147 * m + 1.7076147010 * s,
    )
}

fn hex_rgb01(hex: &str) -> Result<(f64, f64, f64), String> {
    let h = hex.trim_start_matches('#');
    if h.len() < 6 {
        return Err(format!("invalid literal for int() with base 16: {hex:?}"));
    }
    Ok((hex_byte(h, 0)?, hex_byte(h, 2)?, hex_byte(h, 4)?))
}

/// `hex_to_oklch`: sRGB hex → (lightness, chroma, hue-degrees in [0, 360)).
pub fn hex_to_oklch(hex: &str) -> Result<(f64, f64, f64), String> {
    let (r, g, b) = hex_rgb01(hex)?;
    let (l, a, bb) = lrgb_to_oklab(srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b));
    let c = (a * a + bb * bb).sqrt();
    let h = bb.atan2(a).to_degrees().rem_euclid(360.0);
    Ok((l, c, h))
}

/// `oklch_to_hex`: OKLCH → sRGB hex, gamut-clipped by a 30-step binary
/// search on chroma. `Err` is Python's `ValueError` for L outside [0, 1].
pub fn oklch_to_hex(l: f64, c: f64, h: f64) -> Result<String, String> {
    if !(0.0..=1.0).contains(&l) {
        return Err(format!("OKLCH lightness {l} is outside [0, 1]"));
    }
    let try_c = |cc: f64| -> Option<(f64, f64, f64)> {
        let a = cc * h.to_radians().cos();
        let b = cc * h.to_radians().sin();
        let (r, g, bb) = oklab_to_lrgb(l, a, b);
        let in_gamut = |v: f64| (-1e-5..=1.0001).contains(&v);
        if in_gamut(r) && in_gamut(g) && in_gamut(bb) {
            Some((r.clamp(0.0, 1.0), g.clamp(0.0, 1.0), bb.clamp(0.0, 1.0)))
        } else {
            None
        }
    };
    let (r, g, b) = try_c(c).unwrap_or_else(|| {
        let (mut lo, mut hi) = (0.0, c);
        for _ in 0..30 {
            let mid = (lo + hi) / 2.0;
            if try_c(mid).is_some() {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        // Chroma 0 is inside sRGB for every L in [0, 1] (guarded above), so
        // `lo` always lands on an in-gamut point.
        try_c(lo).unwrap_or((0.0, 0.0, 0.0))
    });
    Ok(rgb01_to_hex(
        linear_to_srgb(r).clamp(0.0, 1.0),
        linear_to_srgb(g).clamp(0.0, 1.0),
        linear_to_srgb(b).clamp(0.0, 1.0),
    ))
}

fn luminance((r, g, b): (f64, f64, f64)) -> f64 {
    0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)
}

fn relative_luminance(hex: &str) -> Result<f64, String> {
    Ok(luminance(hex_rgb01(hex)?))
}

fn contrast_of(a: f64, b: f64) -> f64 {
    let (hi, lo) = if a < b { (b, a) } else { (a, b) };
    (hi + 0.05) / (lo + 0.05)
}

/// The share of `color` that stays when it recedes toward `canvas`: the largest share of
/// the straight sRGB mix (`color-mix(in srgb, color share, canvas)`) whose WCAG contrast
/// against `canvas` is at most `target`. A color already at or below `target` keeps
/// everything (1.0): receding never makes a mark more prominent. The scan runs from the
/// full color down so a mix whose channels move in opposite directions cannot land on a
/// far branch of the contrast curve.
pub fn recede_share(color: &str, canvas: &str, target: f64) -> Result<f64, String> {
    let (mark, ground) = (hex_rgb01(color)?, hex_rgb01(canvas)?);
    let ground_luminance = luminance(ground);
    let at = |share: f64| {
        let mix = |m: f64, g: f64| share * m + (1.0 - share) * g;
        contrast_of(luminance((mix(mark.0, ground.0), mix(mark.1, ground.1), mix(mark.2, ground.2))), ground_luminance)
    };
    if at(1.0) <= target {
        return Ok(1.0);
    }
    const STEPS: u32 = 64;
    let below = (1..=STEPS).map(|i| 1.0 - f64::from(i) / f64::from(STEPS)).find(|&share| at(share) <= target).unwrap_or(0.0);
    let (mut lo, mut hi) = (below, below + 1.0 / f64::from(STEPS));
    for _ in 0..30 {
        let mid = (lo + hi) / 2.0;
        if at(mid) <= target {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    Ok(lo)
}

/// `wcag_contrast`: WCAG 2.1 contrast ratio between two 6-digit hex colors.
pub fn wcag_contrast(hex_a: &str, hex_b: &str) -> Result<f64, String> {
    let (mut l1, mut l2) = (relative_luminance(hex_a)?, relative_luminance(hex_b)?);
    if l1 < l2 {
        std::mem::swap(&mut l1, &mut l2);
    }
    Ok((l1 + 0.05) / (l2 + 0.05))
}

/// `is_light_canvas`: black ink has at least as much contrast against
/// `canvas` as white does. The crossover sits near relative luminance 0.18,
/// not 0.5 — both WCAG quantities are nonlinear.
pub fn is_light_canvas(canvas: &str) -> Result<bool, String> {
    Ok(wcag_contrast("#000000", canvas)? >= wcag_contrast("#ffffff", canvas)?)
}

/// `ensure_readable_ink`: the closest-to-original OKLCH lightness (hue and
/// chroma held) that clears `min_ratio` against `background`, searching
/// toward whichever of black/white has more contrast. `Err` is Python's
/// `ValueError` when neither pole clears the floor.
pub fn ensure_readable_ink(
    color: &str,
    background: &str,
    min_ratio: f64,
) -> Result<String, String> {
    if wcag_contrast(color, background)? >= min_ratio {
        return Ok(color.to_string());
    }
    let black_contrast = wcag_contrast("#000000", background)?;
    let white_contrast = wcag_contrast("#ffffff", background)?;
    let best = black_contrast.max(white_contrast);
    if best < min_ratio {
        return Err(format!(
            "no ink clears {min_ratio}:1 contrast against background {background:?} \
             (best achievable: {best:.2}:1)"
        ));
    }
    let (l, c, h) = hex_to_oklch(color)?;
    let (mut pass_l, mut fail_l) = if is_light_canvas(background)? {
        (0.0, l)
    } else {
        (1.0, l)
    };
    for _ in 0..30 {
        let mid = (pass_l + fail_l) / 2.0;
        if wcag_contrast(&oklch_to_hex(mid, c, h)?, background)? >= min_ratio {
            pass_l = mid;
        } else {
            fail_l = mid;
        }
    }
    oklch_to_hex(pass_l, c, h)
}

/// `label_ink`: ink for text painted on `canvas` about a mark of `color` —
/// the dark step away from the canvas (toward `dark_pole` on a light
/// canvas, clamped to darkening only; toward white on a dark one), then
/// `ensure_readable_ink`'s floor at `label_ink_min_contrast`.
pub fn label_ink(color: &str, canvas: &str) -> Result<String, String> {
    let cfg = color_variants();
    let (l, c, h) = hex_to_oklch(color)?;
    let stepped = if is_light_canvas(canvas)? {
        oklch_to_hex(l.min(l + (cfg.dark_pole - l) * cfg.dark_k), c, h)?
    } else {
        oklch_to_hex(l + (1.0 - l) * cfg.dark_k, c, h)?
    };
    ensure_readable_ink(&stepped, canvas, cfg.label_ink_min_contrast)
}

/// `_variant_cached`: a literal tier of `color`, no canvas — OKLCH lightness
/// moved a fraction of the way toward a fixed pole, hue held, chroma scaled,
/// gamut-clipped. `dark`/`deep` never lighten the base.
pub fn variant(color: &str, kind: &str) -> Result<String, String> {
    let cfg = color_variants();
    let (l, c, h) = hex_to_oklch(color)?;
    match kind {
        "light" => {
            // Two competing clamps: under the pale band, at least
            // `light_min_gap` above base; the floor wins, and stops at white.
            let ln = l + (1.0 - l) * cfg.light_k;
            let ln = (ln.min(cfg.pale_l - cfg.light_pale_gap))
                .max(l + cfg.light_min_gap)
                .min(1.0);
            oklch_to_hex(ln, c * cfg.light_chroma, h)
        }
        "dark" => oklch_to_hex(l.min(l + (cfg.dark_pole - l) * cfg.dark_k), c, h),
        "pale" => oklch_to_hex(cfg.pale_l, c * cfg.pale_chroma, h),
        "deep" => oklch_to_hex(l.min(l + (cfg.deep_pole - l) * cfg.deep_k), c, h),
        other => Err(format!(
            "unknown variant {other:?}; expected one of {}",
            VARIANT_WORDS.join(", ")
        )),
    }
}

/// `ink_canvas`: composite `layers` top-down (`layers[0]` on top) into one
/// opaque hex. A layer that doesn't parse contributes nothing, like a fully
/// transparent one. `Err` when the result is still not opaque — unreachable
/// through authoring, since every caller's bottom layer is a theme canvas.
pub fn ink_canvas(layers: &[&str]) -> Result<String, String> {
    let mut composited: Rgba = (0.0, 0.0, 0.0, 0.0);
    for layer in layers.iter().rev() {
        if let Ok(parsed) = parse_css_color(layer) {
            composited = composite_over(parsed, composited);
        }
    }
    if composited.3 < 1.0 - 1e-9 {
        return Err("theme canvas must be opaque".to_string());
    }
    Ok(rgb01_to_hex(composited.0, composited.1, composited.2))
}

/// `mark_ink`: `label_ink` for one mark color. A token or bare identifier
/// that is not already a real color defers (passes through) for the
/// palette-spec validator to judge; anything unparseable or fully
/// transparent has no ink to derive and passes through unchanged too.
pub fn mark_ink(color: &str, canvas: &str) -> Result<String, String> {
    let lowered = color.trim().to_lowercase();
    let is_real_color = matches!(lowered.as_str(), "transparent" | "none")
        || is_sanitizable_color(color)
        || css_named_color_to_hex(color).is_some();
    if !is_real_color && is_deferred_color_reference(color) {
        return Ok(color.to_string());
    }
    let Ok((r, g, b, a)) = parse_css_color(color) else {
        return Ok(color.to_string());
    };
    if a <= 0.0 {
        return Ok(color.to_string());
    }
    label_ink(&rgb01_to_hex(r, g, b), canvas)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Every expected value below is the Python engine's own output for the
    // same input (`resolve/style/palette.py`, `core/colors.py`), captured
    // when this rung was ported.

    #[test]
    fn label_ink_matches_python_on_light_and_dark_canvases() {
        let cases = [
            ("#40639c", "#ffffff", "#284980"),
            ("#779bc9", "#ffffff", "#4d709b"),
            ("#40639c", "#222222", "#6b90cc"),
            ("#e0e0e0", "#ffffff", "#777676"),
            ("#101010", "#161616", "#7f7f7e"),
            ("#a16723", "#FAF7F0", "#7c4900"),
            ("#ffffff", "#ffffff", "#777676"),
            ("#000000", "#000000", "#757474"),
        ];
        for (color, canvas, expected) in cases {
            assert_eq!(
                label_ink(color, canvas).unwrap(),
                expected,
                "{color} on {canvas}"
            );
        }
    }

    fn mixed_contrast(color: &str, canvas: &str, share: f64) -> f64 {
        let (m, g) = (hex_rgb01(color).unwrap(), hex_rgb01(canvas).unwrap());
        let mix = |a: f64, b: f64| share * a + (1.0 - share) * b;
        contrast_of(luminance((mix(m.0, g.0), mix(m.1, g.1), mix(m.2, g.2))), luminance(g))
    }

    #[test]
    fn a_receded_color_lands_on_the_target_contrast_on_light_and_dark_canvases() {
        for canvas in ["#ffffff", "#faf7f0", "#161616", "#0b0b0f"] {
            for color in ["#0073c2", "#d55e00", "#009e73", "#cc79a7", "#f0e442", "#000000"] {
                let share = recede_share(color, canvas, 1.5).unwrap();
                let contrast = mixed_contrast(color, canvas, share);
                if share < 1.0 {
                    assert!((contrast - 1.5).abs() < 0.01, "{color} on {canvas}: {contrast}");
                } else {
                    assert!(contrast <= 1.5, "{color} on {canvas}: {contrast}");
                }
            }
        }
    }

    #[test]
    fn a_color_already_at_or_below_the_target_is_left_alone() {
        assert_eq!(recede_share("#f4f4f4", "#ffffff", 1.5).unwrap(), 1.0);
        assert_eq!(recede_share("#1c1c1c", "#161616", 1.5).unwrap(), 1.0);
        assert_eq!(recede_share("#ffffff", "#ffffff", 1.5).unwrap(), 1.0);
    }

    #[test]
    fn a_dark_canvas_recedes_a_saturated_mark_further_than_a_fixed_blend_would() {
        // A fixed 70% blend leaves a saturated blue far above 1.5:1 on a near-black canvas.
        let share = recede_share("#0073c2", "#161616", 1.5).unwrap();
        assert!(share < 0.5, "{share}");
    }

    #[test]
    fn ink_canvas_composites_every_css_form_and_skips_the_unreadable() {
        assert_eq!(
            ink_canvas(&["rgba(0, 0, 0, 0.2)", "#ffffff"]).unwrap(),
            "#cccccc"
        );
        assert_eq!(
            ink_canvas(&["rgba(255 0 0 / 50%)", "#222222"]).unwrap(),
            "#901111"
        );
        assert_eq!(ink_canvas(&["transparent", "#FAF7F0"]).unwrap(), "#faf7f0");
        assert_eq!(
            ink_canvas(&["hsl(220, 60%, 50%)", "#fff"]).unwrap(),
            "#3366cc"
        );
        assert_eq!(ink_canvas(&["rebeccapurple", "#fff"]).unwrap(), "#663399");
        assert_eq!(
            ink_canvas(&["oklch(50% 0.1 20)", "#fff"]).unwrap(),
            "#ffffff"
        );
        assert_eq!(ink_canvas(&["#8888", "#ffffff"]).unwrap(), "#c0c0c0");
        assert_eq!(
            ink_canvas(&["rgb(50 %, 0, 0)", "#ffffff"]).unwrap(),
            "#800000"
        );
        assert_eq!(
            ink_canvas(&["rgba(0, 0, 0, 0.5)"]).unwrap_err(),
            "theme canvas must be opaque"
        );
    }

    #[test]
    fn is_light_canvas_pivots_near_luminance_0_18_not_0_5() {
        assert!(is_light_canvas("#808080").unwrap());
        assert!(is_light_canvas("#767676").unwrap());
        assert!(!is_light_canvas("#595959").unwrap());
        assert!(!is_light_canvas("#161616").unwrap());
        assert!(is_light_canvas("#FAF7F0").unwrap());
    }

    #[test]
    fn ensure_readable_ink_nudges_toward_the_higher_contrast_pole() {
        assert_eq!(
            ensure_readable_ink("#777777", "#ffffff", 4.5).unwrap(),
            "#777676"
        );
        assert_eq!(
            ensure_readable_ink("#777777", "#161616", 4.5).unwrap(),
            "#7f7f7e"
        );
        assert_eq!(
            ensure_readable_ink("#000000", "#ffffff", 4.5).unwrap(),
            "#000000"
        );
        assert!(ensure_readable_ink("#777777", "#808080", 6.0)
            .unwrap_err()
            .starts_with("no ink clears 6:1 contrast"));
    }

    #[test]
    fn mark_ink_defers_tokens_and_passes_the_unreadable_through() {
        let w = "#ffffff";
        let cases = [
            ("category[2]", "category[2]"),
            ("white", "#777676"),
            ("transparent", "transparent"),
            ("rgba(64,99,156,0.5)", "#284980"),
            ("oklch(50% 0.1 20)", "oklch(50% 0.1 20)"),
            ("currentColor", "currentColor"),
            ("#40639C", "#284980"),
            ("#468", "#2c4d6d"),
            ("  #40639c  ", "#284980"),
            ("none", "none"),
            ("dbt-grays.11", "dbt-grays.11"),
            ("Not A Color", "Not A Color"),
            ("rgb(64 99 156 / 0%)", "rgb(64 99 156 / 0%)"),
            ("rgb(50 %, 20 %, 0)", "#652600"),
            ("white\n", "white\n"),
            ("#fff\n", "#777676"),
        ];
        for (color, expected) in cases {
            assert_eq!(mark_ink(color, w).unwrap(), expected, "{color:?}");
        }
    }

    #[test]
    fn oklch_round_trip_matches_python_arithmetic() {
        let (l, c, h) = hex_to_oklch("#40639c").unwrap();
        assert!((l - 0.5003447109415566).abs() < 1e-12);
        assert!((c - 0.10015198855610985).abs() < 1e-12);
        assert!((h - 259.73540424734495).abs() < 1e-9);
        assert_eq!(oklch_to_hex(0.5, 0.3, 250.0).unwrap(), "#0065b0");
        assert_eq!(oklch_to_hex(0.86, 0.05, 100.0).unwrap(), "#d8d2ad");
        assert!(oklch_to_hex(1.2, 0.0, 0.0).is_err());
    }

    #[test]
    fn parse_css_color_rejects_mixed_and_malformed_function_bodies() {
        assert!(parse_css_color("rgb(1, 2, 3 / 0.5)")
            .unwrap_err()
            .contains("comma syntax"));
        assert!(parse_css_color("hsl(220, 60, 50%)")
            .unwrap_err()
            .contains("not a percentage"));
        assert!(parse_css_color("rgb(nan, 0, 0)")
            .unwrap_err()
            .contains("not a finite"));
        assert_eq!(parse_css_color("rgb(1_0, 0, 0)").unwrap().0, 10.0 / 255.0);
        assert!(parse_css_color("rgb(_10, 0, 0)").is_err());
        assert_eq!(
            parse_css_color("rgb(50 %, 0, 0)").unwrap(),
            (0.5, 0.0, 0.0, 1.0)
        );
        let (r, g, b, _) = parse_css_color("hsl(220 deg, 60%, 50%)").unwrap();
        assert_eq!(rgb01_to_hex(r, g, b), "#3366cc");
        let (r, g, b, a) = parse_css_color("HSL(220DEG 60% 50%)").unwrap();
        assert_eq!((rgb01_to_hex(r, g, b), a), ("#3366cc".to_string(), 1.0));
    }

    /// `_variant_cached`'s four tiers, pinned against production output
    /// (`variant(hex, kind)`): `dark`/`deep` never lighten, `light` clamps at
    /// white, `pale` lands on one flat band whatever the base.
    #[test]
    fn variant_matches_python_on_the_four_tiers() {
        for (base, expected) in [
            ("#0073c2", ["#005592", "#6c9dce", "#b9d4f0", "#00345d"]),
            ("#ffffff", ["#b1b1b1", "#ffffff", "#d1d1d1", "#444444"]),
            ("#101010", ["#101010", "#4d4d4d", "#d1d1d1", "#101010"]),
            ("#f4d35e", ["#b29300", "#f1df9f", "#dbd1b0", "#4d3e00"]),
        ] {
            for (kind, want) in VARIANT_WORDS.iter().zip(expected) {
                assert_eq!(variant(base, kind).unwrap(), want, "{base}.{kind}");
            }
        }
    }

    #[test]
    fn an_unknown_variant_kind_is_an_error() {
        assert!(variant("#0073c2", "darker").is_err());
    }

    /// `_COLOR_TOKEN_PATTERN`: an optional trailing variant word on either
    /// form — the four literal words only, never an arbitrary third segment.
    #[test]
    fn a_trailing_variant_word_is_part_of_the_token_shape() {
        for token in [
            "category.blue.dark",
            "category[2].dark",
            "vivid-10.3.pale",
            "chrome[11].deep",
            "category[2].light",
        ] {
            assert!(is_color_token(token), "{token}");
        }
        for token in ["category.blue.darker", "a.b.c", "category[2].dark.dark"] {
            assert!(!is_color_token(token), "{token}");
        }
    }
}
