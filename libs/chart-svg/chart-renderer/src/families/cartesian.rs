//! Shared cartesian scaffold — band/linear scales used by bar, line, area,
//! scatter, and heatmap.
//!
//! All computation is pure math: no SVG emission here. Families import these
//! types, compute their own margins (by measuring label text with FontBook), and
//! call the scale structs to place marks and axis decorations.

use crate::codes;
use crate::diagnostic::Diagnostic;
use serde_json::json;

/// Ordinal band scale: maps string values to pixel bands.
///
/// Follows the d3-scale band formula exactly:
/// ```text
/// step      = range_width / (n − padding_inner + 2 × padding_outer)
/// bandwidth = step × (1 − padding_inner)
/// origin    = range_start + step × padding_outer
/// position[i] = origin + step × i
/// ```
///
/// `padding_inner` is the fraction of `step` consumed by the gap between
/// adjacent bands; `padding_outer` is the fraction consumed by the half-gap at
/// each range end (matching d3's convention, where `paddingOuter = 0` means the
/// first band starts exactly at `range_start`).
#[derive(Clone)]
pub struct BandScale {
    domain: Vec<String>,
    geometry: Bands,
}

#[derive(Clone)]
enum Bands {
    Uniform(BandGeometry),
    /// Bands that sit where their data does (a time axis): one leading edge per domain entry, no shared width.
    Anchored(Vec<f64>),
}

/// d3's band layout for `n` bands over a pixel range: the one place the
/// step, bandwidth and first band's leading edge are derived.
#[derive(Clone, Copy)]
pub struct BandGeometry {
    step: f64,
    bandwidth: f64,
    /// Pixel position of the first band's leading edge.
    origin: f64,
}

impl BandGeometry {
    /// `n` bands over `[range_start, range_end]` with d3-style padding fractions.
    pub fn new(n: usize, range_start: f64, range_end: f64, padding_inner: f64, padding_outer: f64) -> Self {
        let n = n as f64;
        let range_w = range_end - range_start;
        // Denominator: n − paddingInner + 2·paddingOuter, at least 1 to avoid /0.
        let denom = (n - padding_inner + 2.0 * padding_outer).max(1.0);
        let step = if n == 0.0 { 0.0 } else { range_w / denom };
        BandGeometry {
            step,
            bandwidth: step * (1.0 - padding_inner),
            origin: range_start + step * padding_outer,
        }
    }

    /// Pixel position `frac` of the way across band `i` (`0` leading, `1` trailing).
    pub fn edge(&self, i: usize, frac: f64) -> f64 {
        self.origin + self.step * i as f64 + self.bandwidth * frac
    }

    /// Bandwidth: the pixel extent available to a mark within its band.
    pub fn bandwidth(&self) -> f64 {
        self.bandwidth
    }
}

impl BandScale {
    /// Construct from a domain, a pixel range, and d3-style padding fractions.
    pub fn new(
        domain: Vec<String>,
        range_start: f64,
        range_end: f64,
        padding_inner: f64,
        padding_outer: f64,
    ) -> Self {
        let geometry = Bands::Uniform(BandGeometry::new(domain.len(), range_start, range_end, padding_inner, padding_outer));
        BandScale { domain, geometry }
    }

    /// A band scale whose entry `i` leads at `starts[i]` pixels.
    pub fn anchored(domain: Vec<String>, starts: Vec<f64>) -> Self {
        assert_eq!(domain.len(), starts.len(), "an anchored band scale places every domain entry");
        BandScale { domain, geometry: Bands::Anchored(starts) }
    }

    /// Leading-edge pixel position of the band for `value`.
    ///
    /// Returns `None` when `value` is not in the domain.
    pub fn position(&self, value: &str) -> Option<f64> {
        let at = self.domain.iter().position(|d| d == value)?;
        Some(match &self.geometry {
            Bands::Uniform(g) => g.edge(at, 0.0),
            Bands::Anchored(starts) => starts[at],
        })
    }

    /// Bandwidth: the pixel extent available to a mark within its band.
    ///
    /// Equals `step × (1 − padding_inner)`. An anchored scale has none, its bands own their extents.
    pub fn bandwidth(&self) -> f64 {
        match &self.geometry {
            Bands::Uniform(g) => g.bandwidth(),
            Bands::Anchored(_) => unreachable!("an anchored band scale has no shared bandwidth"),
        }
    }
}

/// VL "point" scale: n evenly-spaced points across the range, no padding.
///
/// step = (range_end − range_start) / (n − 1), position[i] = range_start + step × i.
/// Matches VL's point scale with paddingOuter = 0 (default).
pub struct PointScale {
    domain: Vec<String>,
    step: f64,
    range_start: f64,
}

impl PointScale {
    pub fn new(domain: Vec<String>, range_start: f64, range_end: f64) -> Self {
        let n = domain.len();
        let step = if n <= 1 {
            0.0
        } else {
            (range_end - range_start) / (n - 1) as f64
        };
        PointScale { domain, step, range_start }
    }

    /// Pixel position of the point for `value`, or `None` if not in domain.
    pub fn position(&self, value: &str) -> Option<f64> {
        self.domain
            .iter()
            .position(|d| d == value)
            .map(|i| self.range_start + self.step * i as f64)
    }

    /// Pixel position by integer index.
    pub fn position_at(&self, i: usize) -> f64 {
        self.range_start + self.step * i as f64
    }

}

/// Extend a continuous domain to round tick-step boundaries (d3 nice()).
///
/// Applied when axis_y.position == "left" to match VL's domain extension.
/// Uses the same step-snapping candidates as d3-scale: {1, 2, 5, 10} × 10^k.
pub fn d3_nice_domain(lo: f64, hi: f64, count: usize) -> (f64, f64) {
    let span = hi - lo;
    if span == 0.0 || count < 2 {
        return (lo, hi);
    }
    let step_estimate = span / count as f64;
    let power = step_estimate.log10().floor() as i32;
    let mag = 10f64.powi(power);
    let factor = step_estimate / mag;
    // d3 thresholds: sqrt(2) ≈ 1.414, sqrt(10) ≈ 3.162, sqrt(50) ≈ 7.071
    let step = if factor < 2f64.sqrt() {
        mag
    } else if factor < 10f64.sqrt() {
        2.0 * mag
    } else if factor < 50f64.sqrt() {
        5.0 * mag
    } else {
        10.0 * mag
    };
    let nice_min = (lo / step).floor() * step;
    let nice_max = (hi / step).ceil() * step;
    (nice_min, nice_max)
}

/// An authored `scale.continuous.domain` pair as Vega draws it. The pair
/// reaches Vega verbatim (`vl_field_maps.py:114-119`) and resolve bakes no
/// `domain_min`/`domain_max` beside it, so `nice` is the one term that still
/// applies, only when the author set `scale.nice: true`: `configureDomain`
/// (`vega.js:21556-21559`) runs `scale.nice(null)`, d3's count 10.
pub fn authored_domain_pair(ax: &crate::common::AxisStyle, lo: f64, hi: f64) -> (f64, f64) {
    if ax.scale.get("nice").and_then(serde_json::Value::as_bool) == Some(true) {
        // d3's `nice` rounds a descending domain outward in its own order.
        if lo > hi {
            let (nice_hi, nice_lo) = d3rs::ticks::d3_nice(hi, lo, 10);
            (nice_lo, nice_hi)
        } else {
            d3rs::ticks::d3_nice(lo, hi, 10)
        }
    } else {
        (lo, hi)
    }
}

/// Vega-Lite's default `tickCount` for a quantitative or temporal axis with no
/// authored count: the signal `ceil(<extent>/40)` over the axis's pixel extent
/// (`compile/axis/properties.ts` `tickCount`; observable as `tickCount:
/// {signal: "ceil(height/40)"}` in `vegalite_to_vega` output, pinned by
/// `test_log_axis_compiles_no_tickcount_signal_unlike_linear`).
pub fn vl_default_tick_count(extent: f64) -> usize {
    (extent / 40.0).ceil() as usize
}

/// The d3 ticks Vega draws on a measure axis with no baked ladder, at
/// `count`, capped by the axis's resolved `tickMinStep` (`ticks.step`,
/// `d3rs::ticks::vega_tick_count`).
pub fn unbaked_ticks(ax: &crate::common::AxisStyle, start: f64, stop: f64, count: usize) -> Vec<f64> {
    d3rs::ticks::d3_ticks(start, stop, d3rs::ticks::vega_tick_count(start, stop, count, ax.ticks.step))
}

/// The rungs of a baked ladder a Vega axis draws over the domain `(a, b)`:
/// `validTicks` (`vega.js:14547-14572`, v6.2.0) drops every `axis.values`
/// entry whose scale position falls outside the range, so a rung past either
/// end of the domain (a ladder rounded outward to cover it) never paints. The
/// bounds may arrive in either order — an authored descending pair inverts
/// the axis but keeps the same rungs — and the ladder keeps its own order.
pub fn ladder_within_domain(ticks: &[f64], a: f64, b: f64) -> Vec<f64> {
    let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
    let eps = (hi - lo) * 1e-9;
    ticks.iter().copied().filter(|&tv| tv >= lo - eps && tv <= hi + eps).collect()
}

#[cfg(test)]
mod ladder_within_domain_tests {
    use super::*;

    #[test]
    fn drops_rungs_past_either_end_and_keeps_both_endpoints() {
        let ladder = [-500.0, 0.0, 500.0, 1000.0, 1500.0];
        assert_eq!(ladder_within_domain(&ladder, -150.0, 1000.0), [0.0, 500.0, 1000.0]);
        assert_eq!(ladder_within_domain(&ladder, -500.0, 1500.0), ladder);
    }

    #[test]
    fn a_descending_pair_filters_the_same_rungs_in_ladder_order() {
        let ladder = [1000.0, 500.0, 0.0, -500.0];
        assert_eq!(ladder_within_domain(&ladder, 1000.0, -150.0), [1000.0, 500.0, 0.0]);
    }
}

/// Linear scale: maps a continuous f64 domain to a pixel range.
///
/// The mapping is a straight affine transform — no clamping is applied here.
/// Callers that want to clip out-of-domain values must do so themselves.
pub struct LinearScale {
    pub domain_min: f64,
    pub domain_max: f64,
    pub range_start: f64,
    pub range_end: f64,
}

impl LinearScale {
    pub fn new(domain_min: f64, domain_max: f64, range_start: f64, range_end: f64) -> Self {
        LinearScale { domain_min, domain_max, range_start, range_end }
    }

    /// Map a domain value to a pixel position.
    ///
    /// G1: d3's own `interpolateNumber` is `a·(1−t) + b·t`, not
    /// `a + t·(b−a)` — the two are mathematically identical but round
    /// differently in floating point, and at an exact half-pixel tie that
    /// difference decides which whole pixel `tickRound` snaps a grid rule
    /// to (`line_endpoint_labels_legend_bottom`: domain [6000, 28000] on a
    /// 231px range puts 15000 at 136.49999999999997 under d3's order,
    /// 136.5 under the other — a one-pixel gridline drift at the tie).
    pub fn position(&self, value: f64) -> f64 {
        let span = self.domain_max - self.domain_min;
        // d3-scale `continuous.js` `normalize`: a zero-extent domain maps
        // every value to `constant(0.5)`, the midpoint of the range.
        let t = if span == 0.0 { 0.5 } else { (value - self.domain_min) / span };
        self.range_start * (1.0 - t) + self.range_end * t
    }
}

#[cfg(test)]
mod linear_scale_tests {
    use super::*;

    #[test]
    fn position_matches_d3_s_interpolatenumber_order_at_a_half_pixel_tie() {
        // Oracle: `series-label-placement/line_endpoint_labels_legend_bottom`
        // — the reference rule sits a hair under the half-pixel tie.
        let scale = LinearScale::new(6000.0, 28000.0, 231.0, 0.0);
        assert_eq!(scale.position(15000.0), 136.49999999999997);
    }

    /// d3-scale `continuous.js` `normalize`: a zero-extent domain maps every
    /// value to the range midpoint.
    #[test]
    fn a_zero_extent_domain_maps_to_the_range_midpoint() {
        let scale = LinearScale::new(0.0, 0.0, 399.0, 0.0);
        assert_eq!(scale.position(0.0), 199.5);
        assert_eq!(scale.position(7.0), 199.5);
    }
}

/// Log scale (base 10): maps a positive continuous domain to a pixel range.
///
/// d3-scale's `scaleLog` interpolates on the domain's natural log, not the
/// raw value — `t = (ln(value) − ln(domain_min)) / (ln(domain_max) −
/// ln(domain_min))`, then the SAME `interpolateNumber` term order
/// `LinearScale::position` uses (G1). The domain must be strictly positive;
/// callers refuse non-positive data before constructing one. The one zero floor
/// is an area layer's fill baseline joining the shared domain (see `position`).
pub struct LogScale {
    pub domain_min: f64,
    pub domain_max: f64,
    pub range_start: f64,
    pub range_end: f64,
}

impl LogScale {
    pub fn new(domain_min: f64, domain_max: f64, range_start: f64, range_end: f64) -> Self {
        LogScale { domain_min, domain_max, range_start, range_end }
    }

    pub fn position(&self, value: f64) -> f64 {
        // A zero floor makes every position `-Infinity / -Infinity`; Vega
        // emits that NaN as 0.
        if self.domain_min == 0.0 {
            return 0.0;
        }
        let span = self.domain_max.ln() - self.domain_min.ln();
        if span == 0.0 {
            return self.range_start;
        }
        let t = (value.ln() - self.domain_min.ln()) / span;
        self.range_start * (1.0 - t) + self.range_end * t
    }
}

/// Either continuous measure scale, sharing one `position()` call site —
/// callers that draw the same marks/gridlines/labels regardless of scale
/// type build whichever variant the axis calls for once, then place every
/// value through it unchanged.
pub enum MeasureScale {
    Linear(LinearScale),
    Log(LogScale),
}

impl MeasureScale {
    pub fn position(&self, value: f64) -> f64 {
        match self {
            MeasureScale::Linear(s) => s.position(value),
            MeasureScale::Log(s) => s.position(value),
        }
    }
}

/// d3-scale log `nice()`: floor/ceil the domain ends to base-10 powers
/// (`pows(floor(logs(x)))` / `pows(ceil(logs(x)))` — `log.js`'s `nice()`,
/// base 10 unconditionally since production authors no other log base).
/// Callers must have already rejected non-positive `lo`/`hi` — this has no
/// defined answer for either.
pub fn log_nice_domain(lo: f64, hi: f64) -> (f64, f64) {
    let exp_hi = hi.log10().ceil() as i32;
    // `pows(floor(logs(0)))` is `pow(-Infinity)`: a zero floor stays zero.
    let lo = if lo == 0.0 { 0.0 } else { 10f64.powi(lo.log10().floor() as i32) };
    (lo, 10f64.powi(exp_hi))
}

/// d3-scale log `ticks(count)` over an already-`nice`d domain (so `lo`/`hi`
/// are exact base-10 powers and their exponents are whole numbers).
///
/// Two branches of `log.js`'s `ticks()`, keyed on the decade span `r1 − r0`
/// against `count`:
/// - `r1 − r0 < count` (the common case — few decades): every 1×..9× decade
///   multiple inside `[lo, hi]`, across each whole decade `r0..=r1`.
/// - otherwise (many decades): d3-array `ticks(r0, r1, min(r1−r0, count))`
///   on the EXPONENTS, mapped back through `10^x` — reuses `d3_ticks`, the
///   same linear-ticks primitive Vega's own unbaked ladder already ports.
///
/// A single nice'd decade that yields very few multiples inside `[lo, hi]`
/// (`z.length * 2 < count`) falls back to a plain linear `ticks()` over the
/// domain, as `log.js` does.
pub fn log_ticks(lo: f64, hi: f64, count: usize) -> Vec<f64> {
    // `logs(0)` is `-Infinity`, so d3-array's tick step over it is empty.
    if lo == 0.0 {
        return Vec::new();
    }
    let exp_lo = lo.log10().round() as i32;
    let exp_hi = hi.log10().round() as i32;
    let decades = (exp_hi - exp_lo) as f64;
    if decades < count as f64 {
        let mut z = Vec::new();
        for r in exp_lo..=exp_hi {
            let decade = 10f64.powi(r);
            for k in 1..10 {
                let t = k as f64 * decade;
                if t < lo {
                    continue;
                }
                if t > hi {
                    break;
                }
                z.push(t);
            }
        }
        if z.len() * 2 < count {
            return d3rs::ticks::d3_ticks(lo, hi, count);
        }
        z
    } else {
        let n = (decades as usize).min(count);
        d3rs::ticks::d3_ticks(exp_lo as f64, exp_hi as f64, n)
            .into_iter()
            .map(|e| 10f64.powi(e.round() as i32))
            .collect()
    }
}

/// d3-scale log `tickFormat`'s minor-mantissa visibility rule — the mask a
/// caller applies when NO explicit axis format overrides `scale.tickFormat`
/// (an authored format string always wins over it; see `log_ticks`' caller
/// in `line.rs` for why that path isn't ported here).
///
/// `k = max(1, base * count / ticks.len())`; a tick is visible when its
/// mantissa (`d / 10^round(log10(d))`, corrected by ×10 when that leaves a
/// sub-1 mantissa past the `base − 0.5` rounding boundary) is `<= k`.
pub fn log_tick_mantissa_visible(ticks: &[f64], count: usize) -> Vec<bool> {
    let k = (10.0 * count as f64 / ticks.len() as f64).max(1.0);
    ticks
        .iter()
        .map(|&d| {
            let r = d.log10().round();
            let mut i = d / 10f64.powf(r);
            if i * 10.0 < 10.0 - 0.5 {
                i *= 10.0;
            }
            i <= k
        })
        .collect()
}

#[cfg(test)]
mod log_scale_tests {
    use super::*;

    #[test]
    fn position_matches_linear_scale_s_interpolatenumber_order_on_the_log_domain() {
        // t = (ln(v) - ln(lo)) / (ln(hi) - ln(lo)); same G1 term order as
        // LinearScale — verified against a hand-computed midpoint.
        let scale = LogScale::new(1.0, 100.0, 200.0, 0.0);
        // ln(10) is exactly halfway between ln(1)=0 and ln(100), so 10
        // lands at the range midpoint.
        assert!((scale.position(10.0) - 100.0).abs() < 1e-9);
        assert_eq!(scale.position(1.0), 200.0);
        assert_eq!(scale.position(100.0), 0.0);
    }

    #[test]
    fn nice_domain_floors_and_ceils_to_base_10_powers() {
        // Oracle: probe on `charts/axis-scales_0`'s own data extent
        // (990000000..7795000000) niced to [1e8, 1e10] by vl-convert.
        assert_eq!(log_nice_domain(990_000_000.0, 7_795_000_000.0), (1e8, 1e10));
    }

    #[test]
    fn ticks_fill_decade_multiples_when_span_is_short() {
        // Oracle: vl_convert scenegraph over [1e8, 1e10] (2 decades, count
        // 10) — 9 multiples per decade for the first two decades, 1 for the
        // partial third (only 1e10 itself lies inside the closed domain).
        let ticks = log_ticks(1e8, 1e10, 10);
        assert_eq!(ticks.len(), 19);
        assert_eq!(ticks[0], 1e8);
        assert_eq!(ticks[8], 9e8);
        assert_eq!(ticks[9], 1e9);
        assert_eq!(ticks[17], 9e9);
        assert_eq!(ticks[18], 1e10);
    }

    #[test]
    fn ticks_use_coarse_exponent_steps_when_decades_exceed_count() {
        // Oracle: vl_convert scenegraph over [1, 1e12] (12 decades > count
        // 10) — one tick per decade exponent, per d3-array's own ticks().
        let ticks = log_ticks(1.0, 1e12, 10);
        assert_eq!(ticks, vec![1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12]);
    }

    #[test]
    fn mantissa_visible_blanks_minors_past_the_count_derived_threshold() {
        // Oracle: vl_convert scenegraph over [1e8, 1e10] at the default
        // count 10 — k = 10*10/19 ≈ 5.263, so mantissas 1..5 stay labeled
        // (100M..500M, 1G..5G, 10G) and 6..9 blank.
        let ticks = log_ticks(1e8, 1e10, 10);
        let visible = log_tick_mantissa_visible(&ticks, 10);
        let expected = [
            true, true, true, true, true, false, false, false, false, // 1e8..9e8
            true, true, true, true, true, false, false, false, false, // 1e9..9e9
            true, // 1e10
        ];
        assert_eq!(visible, expected);
    }
}

/// Resolve a measure (value) axis's rendered `(min, max)` domain.
///
/// Models Vega's `Scale.js` `configureDomain` order: the zero union happens
/// first (callers pass `raw_data_min`/`raw_data_max` already zero-unioned),
/// then a baked `domain_min`/`domain_max` (production's headroom bake from
/// `compile/resolve/chart/_domain.py` — the ONLY way headroom reaches Vega)
/// rewrites the corresponding domain end, and `nice` runs LAST on the result
/// — `nice: true` passes null to `scale.nice()`, d3's default count 10, and
/// d3's nice loops until the re-derived increment stabilizes
/// (`d3rs::ticks::d3_nice`), which can climb a rung past a single-pass
/// estimate. An unbaked domain therefore never sees a headroom multiplier on
/// the nice path: production's resolver either baked the expanded bound or
/// never applied it.
///
/// `apply_nice == false` is the wired single-series right-axis fit arm,
/// whose compiled scales don't nice a BAKED bound: a baked end wins
/// verbatim, and the zoomed (`ticks.count` unset, `zero: false`) leaf keeps
/// the raw extent — that shape is oracle-pinned as-is (D1 doesn't reach or
/// change it; the raw-extent behavior there predates this change and no
/// law here explains it, only the fully unbaked leaf below does). The
/// fully unbaked leaf (no baked ends, `ticks.count` unset, zero-anchored)
/// nices on this arm exactly as it does everywhere else: D1 — production
/// bakes no `domainMax` there either, so Vega auto-fits and nices the same
/// data extent regardless of which side the axis renders on.
///
/// A flat (zero-span) raw extent on the zoomed leaf defeats `nice()`'s
/// span-preserving math, so that degenerate case falls back to the plain
/// headroom expansion rather than surfacing a hard error for data that
/// rendered fine before this leaf existed.
pub fn resolve_measure_domain(
    raw_data_min: f64,
    raw_data_max: f64,
    baked_domain_min: Option<f64>,
    baked_domain_max: Option<f64>,
    ticks_count: Option<f64>,
    headroom: f64,
    zero_false: bool,
    apply_nice: bool,
) -> (f64, f64) {
    if baked_domain_min.is_some() || baked_domain_max.is_some() {
        // Vega order (Scale.js configureDomain): zero union, then domainMin/
        // domainMax rewrite the domain ends, then `nice` runs LAST on the
        // result — `nice: true` passes null to scale.nice(), d3's default
        // count 10. Callers pass raw_data_min/max already zero-unioned.
        // d3's nice re-derives the increment until it stabilizes (ticks.rs
        // d3_nice), which can climb a rung past the single-pass estimate.
        if apply_nice {
            let lo = baked_domain_min.unwrap_or(raw_data_min);
            let hi = baked_domain_max.unwrap_or(raw_data_max);
            return d3rs::ticks::d3_nice(lo, hi, 10);
        }
        let lo = baked_domain_min.unwrap_or(raw_data_min);
        let hi = baked_domain_max.unwrap_or(raw_data_max * (1.0 + headroom));
        return (lo, hi);
    }
    if ticks_count.is_none() && zero_false {
        if raw_data_max <= raw_data_min {
            return (raw_data_min, raw_data_max * (1.0 + headroom));
        }
        return if apply_nice {
            d3rs::ticks::d3_nice(raw_data_min, raw_data_max, 10)
        } else {
            (raw_data_min, raw_data_max)
        };
    }
    // No baked domain bound reached the payload — production's resolver
    // never applied headroom (headroom only travels as a baked domainMax),
    // so Vega sees the plain data extent niced by d3 (count 10) — on EVERY
    // leaf, including the right-orient one whose compiled scale skips
    // `nice` on the other branches above (D1: a right-orient axis compiles
    // no `scale.nice` override either, so it nices exactly like a left one
    // once nothing is baked). A flat extent defeats nice()'s span math
    // (zero step returns it unchanged), so fall back to the headroom
    // expansion — the same guard the zoomed leaf carries — rather than
    // surfacing a degenerate-domain error for a constant series.
    if raw_data_max > raw_data_min {
        d3rs::ticks::d3_nice(raw_data_min, raw_data_max, 10)
    } else {
        (raw_data_min, raw_data_max * (1.0 + headroom))
    }
}

#[cfg(test)]
mod resolve_measure_domain_tests {
    use super::*;

    #[test]
    fn unauthored_ticks_count_and_zero_false_applies_nice_without_headroom() {
        // VL's default scale.nice is true for an unbinned quantitative field:
        // the raw extent is nice()-rounded, but never headroom-expanded.
        let (lo, hi) = resolve_measure_domain(42.5, 97.3, None, None, None, 0.05, true, true);
        assert_eq!((lo, hi), d3rs::ticks::d3_nice(42.5, 97.3, 10));
        assert_ne!(hi, 97.3 * 1.05, "must not apply headroom on this leaf");
    }

    #[test]
    fn unauthored_ticks_count_and_zero_false_without_nice_uses_raw_extent() {
        let (lo, hi) = resolve_measure_domain(42.5, 97.3, None, None, None, 0.05, true, false);
        assert_eq!((lo, hi), (42.5, 97.3));
    }

    #[test]
    fn unauthored_ticks_count_zero_false_flat_data_falls_back_to_headroom() {
        // Regression: flat positive data (raw_min == raw_max) collapses
        // nice()'s span-preserving math to a no-op. Before resolve_measure_domain
        // existed, headroom alone kept y_max > y_min and the chart rendered a
        // flat line; the raw-extent leaf must not turn that into a hard
        // "domain is degenerate" error.
        let (lo, hi) = resolve_measure_domain(120.0, 120.0, None, None, None, 0.08, true, true);
        assert_eq!(lo, 120.0);
        assert!(hi > lo, "flat data must still get a non-degenerate domain: {hi}");
        assert_eq!(hi, 120.0 * 1.08);
    }

    #[test]
    fn unbaked_domain_never_headroom_expands_on_the_nice_path() {
        // Headroom only reaches Vega as a baked domainMax — when the payload
        // carries no baked bound, production's resolver never applied it, and
        // the scale sees nice(data extent, 10). Oracle: dashes-palette-lab's
        // six_series_six_dashes (unbaked ladder, data max 60): the compiled
        // scale tops at 60 flush, not 60 × 1.08.
        let (_lo, hi) = resolve_measure_domain(0.0, 100.0, None, None, None, 0.05, false, true);
        assert_eq!(hi, 100.0, "nice(0, 100) is already nice — no headroom: {hi}");
        let (_lo, hi) = resolve_measure_domain(0.0, 60.0, None, None, Some(6.0), 0.08, false, true);
        assert_eq!(hi, 60.0);
    }

    #[test]
    fn baked_ends_override_first_then_nice_runs_last() {
        // Vega's Scale.js configureDomain order: zero union → domainMin/Max
        // rewrite the ends → nice (d3 default count 10, looping until the
        // increment stabilizes). Oracle: fixtures/axis-grid's grid_area —
        // baked [14920, 45080] nices to [10000, 50000] (the first pass gives
        // step 2000 → [14000, 46000], the re-derived increment climbs to
        // 5000), so the 50,000 tick draws flush at the plot top.
        let (lo, hi) =
            resolve_measure_domain(20000.0, 43000.0, Some(14920.0), Some(45080.0), Some(6.0), 0.08, true, true);
        assert_eq!((lo, hi), (10000.0, 50000.0));
        // A zero-anchored stacked area (charts/types_3's area_example):
        // domainMax 23900.4 overrides the top, nice rounds it to 24000 —
        // never up to the cover-ladder's 25000, so that rung is culled.
        let (lo, hi) =
            resolve_measure_domain(0.0, 22130.0, None, Some(23900.4), Some(6.0), 0.08, false, true);
        assert_eq!((lo, hi), (0.0, 24000.0));
    }

    #[test]
    fn baked_domain_bounds_always_win_regardless_of_zero_false() {
        // Baked bounds still route through the same nice()/no-nice choice the
        // pre-existing (unbaked) path always used — unchanged from before this
        // fix, per the "don't disturb baked-tick-values paths" invariant.
        let (lo, hi) =
            resolve_measure_domain(10.0, 90.0, Some(5.0), Some(95.0), None, 0.05, true, false);
        assert_eq!((lo, hi), (5.0, 95.0));
    }

    #[test]
    fn authored_ticks_count_keeps_existing_headroom_and_nice_behavior_even_when_zero_false() {
        let (lo, hi) =
            resolve_measure_domain(42.5, 97.3, None, None, Some(6.0), 0.05, true, true);
        assert_ne!((lo, hi), (42.5, 97.3), "ticks_count authored — old headroom/nice path applies");
    }

    #[test]
    fn unbaked_domain_nices_on_the_right_orient_leaf_too() {
        // D1: `_resolve_cartesian_ticks` (`_domain.py:383`) bakes no domain
        // bound when `ticks.count` is unset — production emits no
        // `domainMax`, so Vega auto-fits the (zero-unioned) data extent,
        // niced by d3 (count 10), on EVERY leaf, including the right-orient
        // one whose compiled scale skips `nice` elsewhere (`apply_nice ==
        // false`). Headroom only ever reaches Vega as a baked domainMax, so
        // it must not apply here either. Oracle:
        // `measured-label-alignment-estimated-lab/right_orient_numeric_unbaked`
        // (data max 1.5M, headroom 0.08) — the reference ladder tops at
        // 1.6M, not rust's pre-fix 1.62M.
        let (lo, hi) = resolve_measure_domain(0.0, 1_500_000.0, None, None, None, 0.08, false, false);
        assert_eq!((lo, hi), d3rs::ticks::d3_nice(0.0, 1_500_000.0, 10));
        assert_eq!(hi, 1_600_000.0, "d3 nice(0, 1.5M, 10) tops at 1.6M: {hi}");
        assert_ne!(hi, 1_500_000.0 * 1.08, "must not headroom-expand the unbaked right-orient leaf");
    }
}

/// Production's endpoint-rail label pane measurement, shared by the line
/// and bar rail arms: the widest series name plus the 4px edge gap
/// (`_endpoint_rail._LABEL_GAP_PX`), capped at a third of the chart width
/// (`ENDPOINT_LABEL_MAX_WIDTH_FRACTION`).
///
/// The names are measured at `font_weight` (`series_label.font_weight`, the
/// weight the labels paint at): `measure_label_pane_width` instances the font
/// at it (`_endpoint_rail.py:106`), so a bold name is not under-counted.
pub(crate) fn rail_pane_width(
    series: &[String],
    font_family: &str,
    font_size: f64,
    font_weight: &str,
    fonts: &crate::text::FontBook,
    chart_w: f64,
) -> Result<f64, String> {
    let widest = series.iter().try_fold(0.0_f64, |acc, s| {
        crate::common::measure_at_css_weight(fonts, s, font_family, font_size, font_weight)
            .map(|w| acc.max(w))
    })?;
    Ok((widest + RAIL_LABEL_GAP).min(rail_cap(chart_w)))
}

const RAIL_LABEL_GAP: f64 = 4.0;

/// The widest a rail may grow: `endpoint_labels.max_width_fraction` of the chart.
fn rail_cap(chart_w: f64) -> f64 {
    chart_w * crate::constants::marks::ENDPOINT_LABEL_MAX_WIDTH_FRACTION
}

/// `WARN-AXIS-LABEL-COLLISION`: every enabled skip/tilt strategy was tried
/// and the `label_count` labels measured still collide on the x axis.
pub(crate) fn warn_axis_label_collision(chart_id: &str, field: &str, label_count: usize) {
    crate::diagnostic::warn(Diagnostic::new(
        &codes::WARN_AXIS_LABEL_COLLISION,
        [("chart_id", json!(chart_id)), ("field", json!(field)), ("label_count", json!(label_count))],
    ));
}

/// `WARN-LAYER-X-DOMAIN-PAINT-ORDER`: a layer contributed `layer_only` x values
/// the base states no order to place, so the shared domain stays in paint order.
pub(crate) fn warn_x_domain_paint_order(chart_id: &str, x_field: &str, layer_only: &[serde_json::Value]) {
    const SAMPLE: usize = 3;
    let mut sample: Vec<String> = layer_only.iter().take(SAMPLE).map(crate::diagnostic::py_repr).collect();
    if layer_only.len() > SAMPLE {
        sample.push("…".to_string());
    }
    crate::diagnostic::warn(
        Diagnostic::new(
            &codes::WARN_LAYER_X_DOMAIN_PAINT_ORDER,
            [
                ("chart_id", json!(chart_id)),
                ("n_new", json!(layer_only.len())),
                ("plural", json!(if layer_only.len() == 1 { "y" } else { "ies" })),
                ("sample", json!(sample.join(", "))),
                ("x_field", json!(x_field)),
            ],
        )
        .with_sink_param("layer_only", json!(layer_only)),
    );
}

/// `WARN-SERIES-LABEL-TRUNCATED` for the rail names wider than the rail's cap
/// (`measure_label_pane_width`): Vega cuts them at the pane's mark limit.
#[allow(clippy::too_many_arguments)]
pub(crate) fn warn_rail_labels_cut(
    chart_id: &str,
    authored_field: &str,
    series: &[String],
    font_family: &str,
    font_size: f64,
    font_weight: &str,
    fonts: &crate::text::FontBook,
    chart_w: f64,
) -> Result<(), String> {
    let cap = rail_cap(chart_w);
    let mut cut = Vec::new();
    // Production lists the cut names in sorted order.
    let mut series: Vec<&String> = series.iter().collect();
    series.sort();
    for name in series {
        if crate::common::measure_at_css_weight(fonts, name, font_family, font_size, font_weight)? > cap {
            cut.push(name.as_str());
        }
    }
    if cut.is_empty() {
        return Ok(());
    }
    let many = cut.len() != 1;
    let quoted: Vec<String> = cut.iter().map(|n| crate::diagnostic::py_repr(&json!(n))).collect();
    let labels = match quoted.split_at_checked(3) {
        Some((head, rest)) if !rest.is_empty() => format!("{} and {} more", head.join(", "), rest.len()),
        _ => quoted.join(", "),
    };
    crate::diagnostic::warn(
        Diagnostic::new(
            &codes::WARN_SERIES_LABEL_TRUNCATED,
            [
                ("authored_field", json!(authored_field)),
                ("chart_id", json!(chart_id)),
                ("count", json!(cut.len())),
                ("labels", json!(labels)),
                ("labels_noun", json!(if many { "labels" } else { "label" })),
                ("were", json!(if many { "were" } else { "was" })),
            ],
        )
        .with_sink_param("series_names", json!(cut)),
    );
    Ok(())
}

/// Bidirectional greedy collision-avoidance over a set of rail anchors, in
/// data-space y units. Mirrors production's `_apply_label_cascade`
/// (`render/chart/features/endpoint_labels.py`): every anchor is
/// clamped into the caller's y-domain first — an anchor outside
/// `[y_domain_min, y_domain_max]` is off-domain, not a collision — then the
/// branch is chosen by comparing the anchor mean to the *domain* midpoint
/// (not the anchor span's own midpoint), and packed anchors are pushed no
/// closer than `min_gap` to the previously *placed* anchor. Returns the placed
/// labels and the names it dropped.
pub(crate) fn cascade_rail_anchors(
    anchors: &[(String, f64)],
    min_gap: f64,
    y_domain_min: f64,
    y_domain_max: f64,
) -> (Vec<(String, f64)>, Vec<String>) {
    let clamped: Vec<(String, f64)> = anchors
        .iter()
        .map(|(name, v)| (name.clone(), v.clamp(y_domain_min, y_domain_max)))
        .collect();
    if clamped.len() <= 1 || min_gap <= 0.0 {
        return (clamped, Vec::new());
    }
    let domain_mid = (y_domain_min + y_domain_max) / 2.0;
    let mean = clamped.iter().map(|(_, v)| *v).sum::<f64>() / clamped.len() as f64;

    let mut items = clamped;
    if mean >= domain_mid {
        items.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        pack_against_bound(items, |anchor, prev| anchor.min(prev - min_gap), |y| y < y_domain_min)
    } else {
        items.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
        pack_against_bound(items, |anchor, prev| anchor.max(prev + min_gap), |y| y > y_domain_max)
    }
}

/// `_pack_against_bound`: greedy-pack extremity-first `items` against a bound.
/// An item that would cross the bound evicts placed items off the top of the
/// stack, but only ones that were themselves pushed off their own anchor, so a
/// label with real room is never dropped for a neighbor's crowding.
fn pack_against_bound(
    items: Vec<(String, f64)>,
    push: impl Fn(f64, f64) -> f64,
    overflows: impl Fn(f64) -> bool,
) -> (Vec<(String, f64)>, Vec<String>) {
    let mut stack: Vec<(String, f64, f64)> = Vec::new(); // (name, forced_y, own_anchor)
    let mut dropped = Vec::new();
    for (name, anchor) in items {
        let forced = |stack: &[(String, f64, f64)]| stack.last().map_or(anchor, |top| push(anchor, top.1));
        let mut y = forced(&stack);
        while overflows(y) && stack.last().is_some_and(|top| top.1 != top.2) {
            dropped.push(stack.pop().expect("checked non-empty").0);
            y = forced(&stack);
        }
        if overflows(y) {
            dropped.push(name);
            continue;
        }
        stack.push((name, y, anchor));
    }
    (stack.into_iter().map(|(name, y, _)| (name, y)).collect(), dropped)
}

/// `_distribute_evenly`: the labels spread evenly across the domain in anchor
/// order, top first. Needs no slope.
fn spread_evenly(anchors: &[(String, f64)], y_domain_min: f64, y_domain_max: f64) -> Vec<(String, f64)> {
    let step = (y_domain_max - y_domain_min) / (anchors.len() - 1) as f64;
    let mut ordered: Vec<&(String, f64)> = anchors.iter().collect();
    ordered.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
    let mut placed: Vec<(String, f64)> =
        ordered.iter().enumerate().map(|(i, (name, _))| (name.clone(), y_domain_min + i as f64 * step)).collect();
    placed.reverse();
    placed
}

/// `recascade_endpoint_labels`: the rail's final label positions for an
/// intended `pixel_gap`, converted to data units through `px_per_unit` (`None`
/// when the y scale has no slope). A gap that cannot be honored spreads the
/// labels evenly across the domain, and a label that finds no room is
/// dropped; each is reported as the warning production records.
pub(crate) fn recascade_rail_anchors(
    chart_id: &str,
    anchors: &[(String, f64)],
    pixel_gap: f64,
    px_per_unit: Option<f64>,
    y_domain_min: f64,
    y_domain_max: f64,
) -> Vec<(String, f64)> {
    let n = anchors.len();
    if n <= 1 {
        return cascade_rail_anchors(anchors, 0.0, y_domain_min, y_domain_max).0;
    }
    // Tied anchors spread across a cascade domain with no span still tie, so no
    // slope is measurable from them either.
    let Some(px_per_unit) = px_per_unit.filter(|_| y_domain_max > y_domain_min) else {
        crate::diagnostic::warn(
            Diagnostic::new(&codes::WARN_ENDPOINT_LABEL_RAIL_TIED, [("chart_id", json!(chart_id)), ("series_count", json!(n))])
                .with_sink_param("gap_px", json!(pixel_gap)),
        );
        return spread_evenly(anchors, y_domain_min, y_domain_max);
    };
    let min_data_gap = pixel_gap / px_per_unit;
    let span = y_domain_max - y_domain_min;
    if span > 0.0 && (n - 1) as f64 * min_data_gap > span {
        crate::diagnostic::warn(Diagnostic::new(
            &codes::WARN_ENDPOINT_LABEL_GAP_OVERFLOW,
            [("chart_id", json!(chart_id)), ("gap_px", json!(pixel_gap)), ("series_count", json!(n))],
        ));
        return spread_evenly(anchors, y_domain_min, y_domain_max);
    }
    let (placed, dropped) = cascade_rail_anchors(anchors, min_data_gap, y_domain_min, y_domain_max);
    if !dropped.is_empty() {
        crate::diagnostic::warn(
            Diagnostic::new(
                &codes::WARN_ENDPOINT_LABEL_RAIL_OVERFLOW,
                [
                    ("chart_id", json!(chart_id)),
                    ("dropped_count", json!(dropped.len())),
                    ("gap_px", json!(pixel_gap)),
                    ("series_count", json!(n)),
                ],
            )
            .with_sink_param("dropped_series", json!(dropped)),
        );
    }
    placed
}

#[cfg(test)]
mod recascade_tests {
    use super::recascade_rail_anchors;
    use crate::diagnostic::collect;
    use serde_json::json;

    #[test]
    fn a_rail_on_a_scale_with_no_slope_spreads_evenly_and_reports_it() {
        let anchors = vec![("a".to_string(), 5.0), ("b".to_string(), 5.0), ("c".to_string(), 5.0)];
        let (placed, warnings) = collect(|| recascade_rail_anchors("c1", &anchors, 18.2, None, 0.0, 10.0));
        assert_eq!(placed, vec![("c".into(), 10.0), ("b".into(), 5.0), ("a".into(), 0.0)]);
        let [tied] = warnings.as_slice() else { panic!("{warnings:?}") };
        assert_eq!(tied.code, "WARN-ENDPOINT-LABEL-RAIL-TIED");
        assert_eq!(
            serde_json::to_value(&tied.params).unwrap(),
            json!({"chart_id": "c1", "series_count": 3, "gap_px": 18.2})
        );
    }
}

#[cfg(test)]
mod rail_weight_tests {
    use super::rail_pane_width;
    use crate::text::FontBook;

    fn fonts() -> FontBook {
        FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load")
    }

    fn pane(weight: &str) -> f64 {
        let names = ["Northeast Territory Revenue".to_string()];
        rail_pane_width(&names, "'Inter Variable', Inter", 14.0, weight, &fonts(), 2000.0).unwrap()
    }

    #[test]
    fn the_rail_widens_with_the_weight_the_labels_paint_at() {
        let (regular, bold) = (pane("400"), pane("700"));
        assert!(bold - regular > 2.0, "{regular} vs {bold}");
    }

    #[test]
    fn an_unparseable_series_label_weight_leaves_the_rail_at_the_regular_width() {
        assert_eq!(pane("semibold"), pane("400"));
    }
}
