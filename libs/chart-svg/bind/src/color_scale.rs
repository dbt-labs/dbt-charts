//! The color-scale law a KPI's `{column, scale}` background reads: domain, nice widening, diverging hinge and arms,
//! and the stop interpolation. Ported from `chart-renderer` (`families/table/bind.rs`, `families/color_scale.rs`),
//! error wording included, since production's card prints it. `chart-renderer`'s copy goes at cutover.

use d3rs::color::{format_hex, interpolate_rgb, piecewise, Color, Rgb};
use serde_json::Value;

/// Ticks a nice-widened gradient domain lays out.
pub const GRADIENT_NICE_TICK_COUNT: usize = 6;

/// The data extent of `values`, `(min, max)` where an explicit bound wins outright (`(0, 1)` when no value is numeric).
pub fn scale_domain(values: impl IntoIterator<Item = f64>, min: Option<f64>, max: Option<f64>) -> (f64, f64) {
    if let (Some(lo), Some(hi)) = (min, max) {
        return (lo, hi);
    }
    let values: Vec<f64> = values.into_iter().collect();
    if values.is_empty() {
        return (min.unwrap_or(0.0), max.unwrap_or(1.0));
    }
    let lo = min.unwrap_or_else(|| values.iter().cloned().fold(f64::INFINITY, f64::min));
    let hi = max.unwrap_or_else(|| values.iter().cloned().fold(f64::NEG_INFINITY, f64::max));
    (lo, hi)
}

/// The nice tick ladder over a data extent: the one rounding law every gradient consumer
/// (heatmap, channel colors, maps, table columns) shares.
pub fn nice_ticks(lo: f64, hi: f64) -> Vec<f64> {
    d3rs::ticks::nice_tick_values(lo, hi, GRADIENT_NICE_TICK_COUNT, None)
}

/// A gradient's color domain over the data extent `(lo, hi)`: widened to `nice_ticks`' round ends when
/// `nice` and neither bound is authored (a single-sided bound fixes that edge exactly); otherwise the
/// extent itself. An empty (non-finite) extent stays empty. Callers resolve `nice` as the author's
/// value, else whether the scale's legend is drawn.
pub fn nice_domain(min: Option<f64>, max: Option<f64>, nice: bool, (lo, hi): (f64, f64)) -> (f64, f64) {
    if !nice || min.is_some() || max.is_some() || !lo.is_finite() || !hi.is_finite() {
        return (lo, hi);
    }
    let ticks = nice_ticks(lo, hi);
    (ticks[0], ticks[ticks.len() - 1])
}

pub fn expand_hex(c: &str) -> String {
    let h = c.trim_start_matches('#');
    if h.len() == 3 {
        let mut out = String::with_capacity(7);
        out.push('#');
        for ch in h.chars() {
            out.push(ch);
            out.push(ch);
        }
        out
    } else {
        format!("#{h}")
    }
}

/// `_parse_hex` over `_expand_hex`: a `#rgb` or `#rrggbb` stop, which production's `int(h[0:2], 16)` raises on
/// when it is anything else.
fn parse_stop(c: &str) -> Result<Rgb, String> {
    d3rs::color::parse_hex(&expand_hex(c)).ok_or_else(|| format!("table: scale palette stop {c:?} is not a #rgb or #rrggbb color"))
}

/// `_lerp_color` along the whole of `stops` (the palette, or one diverging arm) at `t`: production's
/// `segment = t * n_segments; i = min(int(segment), n_segments - 1)` is d3's `piecewise(interpolateRgb)`, and its
/// `int(a + (b - a) * t + 0.5)` is `Math.round` for the non-negative channels here.
fn lerp_stops(stops: &[Rgb], t: f64) -> Result<String, String> {
    let mixed = piecewise(interpolate_rgb, stops, t).map_err(|e| format!("table: scale palette {e}"))?;
    Ok(format_hex(Color::opaque(mixed)))
}

/// `resolve_hinge`: `None` for sequential; a degenerate domain (`lo == hi`)
/// falls back to sequential regardless of what was authored; an explicit
/// float short-circuits; `"auto"` runs the zero-crossing → percent-crosses-
/// one → midpoint decision tree.
pub fn resolve_hinge(
    hinge: Option<&Value>,
    lo: f64,
    hi: f64,
    col_format: Option<&str>,
) -> Option<f64> {
    let hinge = hinge?;
    if lo == hi {
        return None;
    }
    if let Some(f) = hinge.as_f64() {
        return Some(f);
    }
    if hinge.as_str() != Some("auto") {
        return None;
    }
    if lo < 0.0 && 0.0 <= hi {
        return Some(0.0);
    }
    let is_percent = col_format.is_some_and(|f| f.contains('%'));
    if is_percent && lo < 1.0 && 1.0 < hi {
        return Some(1.0);
    }
    Some((lo + hi) / 2.0)
}

/// `diverging_arm_denominators`: the distance each arm's ramp runs over, one
/// shared wider-arm width when asymmetric.
pub fn arm_denominators(pivot: f64, (lo, hi): (f64, f64), asymmetric: bool) -> (f64, f64) {
    let (neg, pos) = (pivot - lo, hi - pivot);
    if asymmetric {
        let wider = neg.max(pos).max(1e-12);
        return (wider, wider);
    }
    (neg.max(1e-12), pos.max(1e-12))
}

/// `interpolate_scale_color`: sequential (`hinge: None`) linearly
/// interpolates the whole palette across `[min_val, max_val]`; diverging
/// splits the (odd-length, >= 3 stop) palette at its middle stop and maps
/// each side of `hinge` onto its own arm, `arm_mode` deciding whether the
/// two arms share one intensity unit (`asymmetric`, the longer arm) or each
/// stretches fully to its own extreme (`symmetric`).
pub fn interpolate_scale_color(
    value: f64,
    min_val: f64,
    max_val: f64,
    palette: &[String],
    hinge: Option<f64>,
    arm_mode: &str,
) -> Result<String, String> {
    let palette: Vec<Rgb> = palette.iter().map(|c| parse_stop(c)).collect::<Result<_, _>>()?;
    let Some(hinge) = hinge else {
        if min_val == max_val {
            return Ok(format_hex(Color::opaque(palette[palette.len() / 2])));
        }
        let t = ((value - min_val) / (max_val - min_val)).clamp(0.0, 1.0);
        return lerp_stops(&palette, t);
    };
    if palette.len() < 3 || palette.len().is_multiple_of(2) {
        return Err(format!(
            "table: diverging palette must have an odd number of stops >= 3, got {}",
            palette.len()
        ));
    }
    let hinge = hinge.clamp(min_val, max_val);
    let (neg_denom, pos_denom) =
        arm_denominators(hinge, (min_val, max_val), arm_mode == "asymmetric");
    let mid = palette.len() / 2;
    let mut neg_stops: Vec<Rgb> = palette[..=mid].to_vec();
    neg_stops.reverse();
    let pos_stops: Vec<Rgb> = palette[mid..].to_vec();
    let clamped = value.clamp(min_val, max_val);
    if clamped <= hinge {
        lerp_stops(&neg_stops, ((hinge - clamped) / neg_denom).clamp(0.0, 1.0))
    } else {
        lerp_stops(&pos_stops, ((clamped - hinge) / pos_denom).clamp(0.0, 1.0))
    }
}
